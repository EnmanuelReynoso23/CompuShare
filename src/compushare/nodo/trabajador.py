"""Modulo del Nodo Trabajador (Worker) de CompuShare.
Gestiona la ejecucion de ggml-rpc-server en 127.0.0.1 y el canal de tubo hacia el rele.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import platform
import shutil
import socket
import subprocess
import time
from pathlib import Path

from compushare.nodo import procesos
from typing import Any

from fabric.tubo import Tubo

log = logging.getLogger("compushare.trabajador")


def buscar_binario(nombre: str, llama_bin: str | Path | None = None) -> Path:
    """Busca un ejecutable de llama.cpp en llama_bin, en PATH o en ~/.local/opt/llama.cpp/build/bin."""
    candidatos: list[Path] = []
    if llama_bin:
        candidatos.append(Path(llama_bin) / nombre)
    en_path = shutil.which(nombre)
    if en_path:
        candidatos.append(Path(en_path))
    defecto = Path.home() / ".local/opt/llama.cpp/build/bin" / nombre
    candidatos.append(defecto)

    for c in candidatos:
        if c.is_file() and os.access(c, os.X_OK):
            return c
    rutas_str = ", ".join(str(p) for p in candidatos)
    raise FileNotFoundError(f"No se encontro el binario '{nombre}' en ninguna de las rutas: {rutas_str}")


def esperar_puerto_tcp(host: str, puerto: int, tiempo_limite: float = 10.0) -> bool:
    """Espera hasta que un puerto TCP este escuchando y acepte conexiones."""
    fin = time.monotonic() + tiempo_limite
    while time.monotonic() < fin:
        try:
            with socket.create_connection((host, puerto), timeout=0.2):
                return True
        except (OSError, ConnectionRefusedError):
            time.sleep(0.05)
    return False


def detectar_vram_gb() -> float:
    """Detecta la VRAM total disponible via nvidia-smi o torch; 0 si no hay GPU."""
    try:
        r = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            check=True,
            timeout=5,
        )
        lineas = [l.strip() for l in r.stdout.splitlines() if l.strip()]
        if lineas:
            total_mb = sum(float(l) for l in lineas)
            return round(total_mb / 1024.0, 2)
    except Exception:
        pass

    try:
        import torch

        if torch.cuda.is_available():
            n = torch.cuda.device_count()
            total_bytes = sum(torch.cuda.get_device_properties(i).total_memory for i in range(n))
            return round(total_bytes / 1e9, 2)
    except Exception:
        pass
    return 0.0


ESPERA_LLAMA_COMPLETO_S = 900.0
MOTORES_TUBO = ("python", "go")


class NodoTrabajador:
    def __init__(
        self,
        rele_url: str,
        clave: str,
        nombre: str = "",
        puerto_rpc: int = 59052,
        vram_gb: float = 0.0,
        llama_bin: str | Path | None = None,
        threads: int = 4,
        cache: bool = False,
        host: str = "127.0.0.1",
        modelo: str | Path | None = None,
        completo: bool = False,
        borrador: str | Path | None = None,
        especulativa: str = "",
        puerto_api: int = 59080,
        ctx_size: int = 4096,
        paralelo: int = 1,
        motor: str = "python",
        go_bin: str | Path | None = None,
    ) -> None:
        if not clave:
            raise ValueError("La clave no puede estar vacia")
        if motor not in MOTORES_TUBO:
            raise ValueError(f"motor desconocido {motor!r}; usa python o go")
        # Con motor go el tubo es el binario compushare-tubo (go/cmd/compushare-tubo) en vez de fabric/tubo.py.
        self.motor = motor
        self.binario_go: Path | None = (
            procesos.buscar_binario_go("compushare-tubo", go_bin) if motor == "go" else None
        )
        self.tubo_go_proc: subprocess.Popen[Any] | None = None
        self.registro_tubo_go: Path | None = None
        self.rele_url = rele_url
        self.clave = clave
        self.nombre = nombre or f"trabajador-{platform.node()[:16]}-{os.getpid()}"
        self.puerto_rpc = puerto_rpc
        self.vram_gb = vram_gb if vram_gb > 0 else detectar_vram_gb()
        self.llama_bin = llama_bin
        # Regla: maximo 6 hilos por proceso de llama.cpp
        self.threads = max(1, min(6, threads))
        self.cache = cache
        self.host = host
        # Modo completo: el trabajador tiene el modelo entero y ofrece su llama-server (no un ggml-rpc-server).
        self.completo = completo
        self.modelo = Path(modelo) if modelo else None
        self.borrador = Path(borrador) if borrador else None
        self.especulativa = especulativa
        self.puerto_api = puerto_api
        self.ctx_size = ctx_size
        self.paralelo = max(1, paralelo)
        if completo and (self.modelo is None or not self.modelo.is_file()):
            raise FileNotFoundError(f"El modo completo necesita el archivo del modelo: {self.modelo}")
        self.rpc_proc: subprocess.Popen[Any] | None = None
        self.registro_rpc: Path | None = None
        self.llama_proc: subprocess.Popen[Any] | None = None
        self.registro_llama: Path | None = None
        self.reinicios_rpc = 0
        self.max_reinicios_rpc = 5
        self.tubo: Tubo | None = None
        self._detenido = False

    def iniciar_rpc_server(self) -> subprocess.Popen[Any]:
        """Inicia ggml-rpc-server en 127.0.0.1."""
        bin_rpc = buscar_binario("ggml-rpc-server", self.llama_bin)
        cmd = [
            str(bin_rpc),
            "-H",
            "127.0.0.1",
            "-p",
            str(self.puerto_rpc),
            "-t",
            str(self.threads),
        ]
        if self.cache:
            cmd.append("-c")

        log.info("Iniciando RPC server: %s en puerto %d", bin_rpc.name, self.puerto_rpc)
        self.rpc_proc, self.registro_rpc = procesos.lanzar(cmd, f"ggml-rpc-server-{self.puerto_rpc}")

        if not esperar_puerto_tcp("127.0.0.1", self.puerto_rpc, tiempo_limite=10.0):
            err = procesos.cola_registro(self.registro_rpc)
            if self.rpc_proc.poll() is None:
                self.rpc_proc.kill()
            raise RuntimeError(f"ggml-rpc-server no respondio en 127.0.0.1:{self.puerto_rpc}. Detalle: {err}")
        return self.rpc_proc

    def iniciar_llama_completo(self) -> subprocess.Popen[Any]:
        """Inicia llama-server con el modelo entero en 127.0.0.1 (modo completo). Espera a que escuche el puerto
        (llama-server escucha antes de terminar de cargar; el maestro espera /health por el tubo)."""
        assert self.modelo is not None
        bin_server = buscar_binario("llama-server", self.llama_bin)
        cmd = [
            str(bin_server),
            "-m",
            str(self.modelo),
            "--host",
            "127.0.0.1",
            "--port",
            str(self.puerto_api),
            "-t",
            str(self.threads),
            "-c",
            str(self.ctx_size * self.paralelo),
            "--parallel",
            str(self.paralelo),
            "-ngl",
            "999",
        ]
        if self.borrador is not None:
            cmd.extend(["-md", str(self.borrador), "-ngld", "999"])
        if self.especulativa:
            cmd.extend(["--spec-type", self.especulativa])

        log.info("Iniciando llama-server completo: %s en 127.0.0.1:%d", self.modelo.name, self.puerto_api)
        self.llama_proc, self.registro_llama = procesos.lanzar(cmd, f"llama-server-completo-{self.puerto_api}")
        fin = time.monotonic() + ESPERA_LLAMA_COMPLETO_S
        while time.monotonic() < fin:
            if self.llama_proc.poll() is not None:
                err = procesos.cola_registro(self.registro_llama)
                raise RuntimeError(f"llama-server completo termino (codigo {self.llama_proc.returncode}): {err}")
            if esperar_puerto_tcp("127.0.0.1", self.puerto_api, tiempo_limite=0.5):
                return self.llama_proc
        err = procesos.cola_registro(self.registro_llama)
        self.llama_proc.kill()
        raise RuntimeError(f"llama-server completo no abrio 127.0.0.1:{self.puerto_api}. Detalle: {err}")

    def info_tubo(self) -> dict[str, Any]:
        """Lo que el trabajador dice de si mismo al relé (el relé deja 10 claves, int/float/str)."""
        if not self.completo:
            return {"vram_gb": self.vram_gb} if self.vram_gb > 0 else {}
        assert self.modelo is not None
        return {
            "modo": "completo",
            "modelo": self.modelo.name,
            "modelo_bytes": self.modelo.stat().st_size,
            "vram_gb": self.vram_gb,
        }

    async def _mantener_tubo_go(self, puerto: int, info: dict[str, Any] | None) -> None:
        """Lanza compushare-tubo (Go) y lo relanza si termina, con espera creciente (1 s a 30 s).
        La clave va por el entorno del proceso, no por la linea de comandos (ps la mostraria)."""
        assert self.binario_go is not None
        cmd = [str(self.binario_go), "--rele", self.rele_url, "--nombre", self.nombre,
               "--puerto", str(puerto), "--host", self.host]
        if self.vram_gb > 0:
            cmd += ["--vram-gb", str(self.vram_gb)]
        extra = {k: v for k, v in (info or {}).items() if k != "vram_gb"}
        if extra:
            cmd += ["--info-json", json.dumps(extra)]
        env = {**os.environ, "FABRIC_CLAVE": self.clave}
        espera = 1.0
        while True:
            inicio = time.monotonic()
            self.tubo_go_proc, self.registro_tubo_go = procesos.lanzar(cmd, f"compushare-tubo-{puerto}", env)
            log.info("Tubo Go %s: %s", self.nombre, self.binario_go.name)
            while self.tubo_go_proc.poll() is None:
                await asyncio.sleep(0.5)
            if self._detenido:
                return
            log.warning("compushare-tubo (Go) termino (codigo %s); reintento en %.0f s. Ultimas lineas:\n%s",
                        self.tubo_go_proc.returncode, espera, procesos.cola_registro(self.registro_tubo_go, 1500))
            await asyncio.sleep(espera)
            espera = 1.0 if time.monotonic() - inicio > 30 else min(espera * 2, 30.0)

    def _proceso_ofrecido(self) -> subprocess.Popen[Any] | None:
        return self.llama_proc if self.completo else self.rpc_proc

    def _lanzar_ofrecido(self) -> None:
        if self.completo:
            self.iniciar_llama_completo()
        else:
            self.iniciar_rpc_server()

    async def conectar(self, duracion: float = 0.0) -> None:
        """Inicia lo que se ofrece (ggml-rpc-server, o llama-server en modo completo) y conecta el tubo al relé."""
        self._lanzar_ofrecido()
        puerto = self.puerto_api if self.completo else self.puerto_rpc
        info = self.info_tubo() or None
        if self.motor == "go":
            tarea_tubo = asyncio.create_task(self._mantener_tubo_go(puerto, info))
        else:
            self.tubo = Tubo(
                rele=self.rele_url,
                clave=self.clave,
                nombre=self.nombre,
                puerto=puerto,
                host=self.host,
                info=info,
                # En modo completo viaja HTTP, no ggml-rpc: el códec no tiene nada que comprimir. (El tubo Go
                # lo anuncia igual, pero es inocuo: el primer byte de HTTP es >= 64 y todo va crudo.)
                codec=not self.completo,
            )
            tarea_tubo = asyncio.create_task(self.tubo.vivir())
        vigia = asyncio.create_task(self._vigilar_proceso())
        try:
            if duracion > 0:
                await asyncio.wait_for(asyncio.shield(tarea_tubo), timeout=duracion)
            else:
                await tarea_tubo
        except asyncio.TimeoutError:
            log.info("Duracion limite alcanzada (%s s)", duracion)
        finally:
            vigia.cancel()
            tarea_tubo.cancel()
            await asyncio.gather(tarea_tubo, vigia, return_exceptions=True)
            self.detener()

    async def _vigilar_proceso(self, cada_s: float = 2.0) -> None:
        """Si lo que se ofrece por el tubo se cae (ggml-rpc-server, o llama-server en modo completo), lo relanza
        en el mismo puerto: el tubo sigue abierto y el maestro puede volver a conectarse (en Kaggle, el
        4-nov-2026, se cayó en el trozo 43 y todo se detuvo)."""
        nombre = "llama-server completo" if self.completo else "ggml-rpc-server"
        while True:
            await asyncio.sleep(cada_s)
            proc = self._proceso_ofrecido()
            if proc is None or proc.poll() is None:
                continue
            if self.reinicios_rpc >= self.max_reinicios_rpc:
                log.error("%s se cayo %d veces; no se relanza mas", nombre, self.reinicios_rpc)
                return
            self.reinicios_rpc += 1
            registro = self.registro_llama if self.completo else self.registro_rpc
            log.warning("%s se cayo (codigo %s); relanzando (%d de %d). Ultimas lineas:\n%s",
                        nombre, proc.returncode, self.reinicios_rpc, self.max_reinicios_rpc,
                        procesos.cola_registro(registro, 1500))
            try:
                await asyncio.to_thread(self._lanzar_ofrecido)
            except RuntimeError as e:
                log.error("No se pudo relanzar %s: %s", nombre, e)
                return

    def detener(self) -> None:
        """Detiene lo que se ofrecia (ggml-rpc-server, llama-server completo, tubo Go), solo por PID."""
        if self._detenido:
            return
        self._detenido = True
        procesos.terminar(self.rpc_proc, "ggml-rpc-server")
        procesos.terminar(self.llama_proc, "llama-server completo")
        procesos.terminar(self.tubo_go_proc, "compushare-tubo (Go)")
