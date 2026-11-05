"""Modulo del Nodo Trabajador (Worker) de CompuShare.
Gestiona la ejecucion de ggml-rpc-server en 127.0.0.1 y el canal de tubo hacia el rele.
"""

from __future__ import annotations

import asyncio
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
    ) -> None:
        if not clave:
            raise ValueError("La clave no puede estar vacia")
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
        self.rpc_proc: subprocess.Popen[Any] | None = None
        self.registro_rpc: Path | None = None
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

    async def conectar(self, duracion: float = 0.0) -> None:
        """Inicia el RPC local y conecta el tubo hacia el rele."""
        self.iniciar_rpc_server()
        info = {"vram_gb": self.vram_gb} if self.vram_gb > 0 else None
        self.tubo = Tubo(
            rele=self.rele_url,
            clave=self.clave,
            nombre=self.nombre,
            puerto=self.puerto_rpc,
            host=self.host,
            info=info,
        )

        tarea_tubo = asyncio.create_task(self.tubo.vivir())
        vigia = asyncio.create_task(self._vigilar_rpc())
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

    async def _vigilar_rpc(self, cada_s: float = 2.0) -> None:
        """Si ggml-rpc-server se cae, lo relanza en el mismo puerto: el tubo sigue abierto y el maestro
        puede volver a conectarse (en Kaggle, el 4-nov-2026, se cayó en el trozo 43 y todo se detuvo)."""
        while True:
            await asyncio.sleep(cada_s)
            if self.rpc_proc is None or self.rpc_proc.poll() is None:
                continue
            if self.reinicios_rpc >= self.max_reinicios_rpc:
                log.error("ggml-rpc-server se cayo %d veces; no se relanza mas", self.reinicios_rpc)
                return
            self.reinicios_rpc += 1
            log.warning("ggml-rpc-server se cayo (codigo %s); relanzando (%d de %d). Ultimas lineas:\n%s",
                        self.rpc_proc.returncode, self.reinicios_rpc, self.max_reinicios_rpc,
                        procesos.cola_registro(self.registro_rpc, 1500))
            try:
                await asyncio.to_thread(self.iniciar_rpc_server)
            except RuntimeError as e:
                log.error("No se pudo relanzar ggml-rpc-server: %s", e)
                return

    def detener(self) -> None:
        """Detiene ggml-rpc-server matando SOLO su PID especifico."""
        if self._detenido:
            return
        self._detenido = True
        if self.rpc_proc is not None and self.rpc_proc.poll() is None:
            pid = self.rpc_proc.pid
            log.info("Deteniendo ggml-rpc-server PID %d", pid)
            self.rpc_proc.terminate()
            try:
                self.rpc_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                log.warning("ggml-rpc-server PID %d no respondio a SIGTERM, enviando SIGKILL", pid)
                self.rpc_proc.kill()
                self.rpc_proc.wait(timeout=2)
