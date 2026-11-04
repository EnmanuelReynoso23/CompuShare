"""Modulo del Nodo Maestro / Coordinador de CompuShare.
Gestiona el rele de orquestacion, la seleccion optima de trabajadores via calculo.py y reparto.py,
y el servidor llama-server exponiendo la API compatible con OpenAI en 127.0.0.1.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

from compushare.nodo import procesos
from typing import Any
from urllib.parse import urlparse

import httpx

from fabric import calculo, reparto
from fabric.rele import rele as rele_mod

log = logging.getLogger("compushare.maestro")


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


def contar_gpus_locales(llama_bin: str | Path | None = None) -> int:
    """Cuenta las GPU que llama.cpp puede usar de verdad (`llama-server --list-devices`).

    nvidia-smi no sirve para esto: una laptop con GPU NVIDIA y llama.cpp compilado solo para CPU
    decia 1 GPU y llama-server se caia con "-ot ...=CUDA0: unknown buffer type" (4-nov-2026).
    """
    try:
        bin_server = buscar_binario("llama-server", llama_bin)
        r = subprocess.run(
            [str(bin_server), "--list-devices"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        salida = (r.stdout or "") + (r.stderr or "")
        return sum(1 for l in salida.splitlines() if l.strip().startswith("CUDA") and ":" in l)
    except Exception:
        pass
    return 0


def detectar_memoria_maestro_gb() -> float:
    """Detecta memoria utilizable en el maestro (VRAM si hay GPU, o RAM disponible)."""
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
            return round(sum(float(l) for l in lineas) / 1024.0, 2)
    except Exception:
        pass

    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for linea in f:
                if linea.startswith("MemAvailable:"):
                    kb = float(linea.split()[1])
                    return round(kb / 1e6, 2)
    except Exception:
        pass
    return 8.0


def extraer_modelo_info(ruta: Path) -> calculo.Modelo:
    """Construye un objeto calculo.Modelo a partir de la ruta del archivo GGUF."""
    nombre = ruta.stem
    pesos_gb = round(ruta.stat().st_size / 1e9, 2)
    nombre_l = nombre.lower()
    if "0.8b" in nombre_l:
        return calculo.Modelo(nombre, pesos_gb, capas=24, d_modelo=1024)
    elif "9b" in nombre_l:
        return calculo.Modelo(nombre, pesos_gb, capas=32, d_modelo=4096)
    elif "27b" in nombre_l:
        return calculo.Modelo(nombre, pesos_gb, capas=64, d_modelo=5120)
    elif "35b" in nombre_l:
        return calculo.Modelo(nombre, pesos_gb, capas=48, d_modelo=2048, activos_frac=3 / 35)
    return calculo.Modelo(nombre, pesos_gb, capas=32, d_modelo=2048)


class NodoMaestro:
    def __init__(
        self,
        modelo: str | Path,
        rele_url: str = "ws://127.0.0.1:59095",
        clave: str = "",
        puerto_openai: int = 59000,
        puerto_rele: int | None = None,
        iniciar_rele_local: bool = True,
        llama_bin: str | Path | None = None,
        espera_trabajadores: float = 3.0,
        min_trabajadores: int = 0,
        threads: int = 4,
        ctx_size: int = 4096,
        tubo_puerto_base: int = 59100,
        gpus_locales: int | None = None,
    ) -> None:
        self.modelo = Path(modelo)
        if not self.modelo.is_file():
            raise FileNotFoundError(f"No existe el archivo de modelo: {self.modelo}")
        self.rele_url = rele_url
        self.clave = clave
        self.puerto_openai = puerto_openai

        if puerto_rele is not None:
            self.puerto_rele = puerto_rele
        else:
            u = urlparse(rele_url)
            self.puerto_rele = u.port or 59095

        self.iniciar_rele_local = iniciar_rele_local
        self.llama_bin = llama_bin
        self.espera_trabajadores = espera_trabajadores
        self.min_trabajadores = min_trabajadores
        # Regla: maximo 6 hilos por proceso de llama.cpp
        self.threads = max(1, min(6, threads))
        self.ctx_size = ctx_size
        self.tubo_puerto_base = tubo_puerto_base
        self.gpus_locales = gpus_locales if gpus_locales is not None else contar_gpus_locales(llama_bin)

        self.server_proc: subprocess.Popen[Any] | None = None
        self.registro_server: Path | None = None
        self._rele_task: asyncio.Task[Any] | None = None
        self._detenido = False

    async def _iniciar_rele(self) -> None:
        """Inicia el rele WebSocket local si esta configurado."""
        rele_mod.TUBO_PUERTO_BASE = self.tubo_puerto_base
        os.environ["TUBO_PUERTO_BASE"] = str(self.tubo_puerto_base)
        listo = asyncio.Event()
        self._rele_task = asyncio.create_task(
            rele_mod.principal(clave=self.clave, puerto=self.puerto_rele, listo=listo)
        )
        await asyncio.wait_for(listo.wait(), timeout=10.0)
        log.info("Rele local iniciado en puerto %d", self.puerto_rele)

    async def obtener_tubos_registrados(self) -> dict[str, Any]:
        """Consulta el estado del rele para listar los tubos conectados."""
        url_salud = f"http://127.0.0.1:{self.puerto_rele}/salud"
        async with httpx.AsyncClient() as client:
            try:
                r = await client.get(url_salud, timeout=5.0)
                if r.status_code == 200:
                    datos = r.json()
                    return datos.get("tubos", {})
            except Exception as e:
                log.warning("No se pudo consultar /salud del rele: %s", e)
        return {}

    async def seleccionar_trabajadores(self) -> list[str]:
        """Espera a los trabajadores y selecciona los optimos usando calculo.py y reparto.py."""
        fin = time.monotonic() + self.espera_trabajadores
        tubos: dict[str, Any] = {}
        while time.monotonic() < fin:
            tubos = await self.obtener_tubos_registrados()
            if len(tubos) >= max(1, self.min_trabajadores):
                break
            await asyncio.sleep(0.5)

        log.info("Tubos registrados en rele: %d (%s)", len(tubos), list(tubos.keys()))

        modelo_info = extraer_modelo_info(self.modelo)
        maestro_mem = detectar_memoria_maestro_gb()
        equipo_maestro = calculo.Equipo(
            "maestro",
            maestro_mem,
            banda_gbs=calculo.T4_GBS if self.gpus_locales > 0 else 40.0,
        )

        candidatos_reparto: list[reparto.Candidato] = []
        candidatos_calculo: list[calculo.Equipo] = []
        mapa_puertos: dict[str, int] = {}

        for nombre, det in tubos.items():
            puerto = int(det["puerto"])
            mapa_puertos[nombre] = puerto
            info = det.get("info", {})
            vram_gb = float(info.get("vram_gb", 4.0)) if info else 4.0
            rtt = await asyncio.to_thread(reparto.medir_rtt, puerto, "127.0.0.1", 2, 3.0)
            log.info("Tubo %s (puerto %d): RTT=%.2f ms, VRAM=%.1f GB", nombre, puerto, rtt, vram_gb)
            candidatos_reparto.append(reparto.Candidato(nombre=nombre, puerto=puerto, vram_gb=vram_gb, rtt_ms=rtt))
            candidatos_calculo.append(
                calculo.Equipo(
                    nombre=nombre,
                    memoria_gb=vram_gb,
                    banda_gbs=320.0,
                    rtt_ms=rtt if rtt != float("inf") else 1000.0,
                    subida_mbps=100.0,
                )
            )

        rpc_targets: list[str] = []

        if self.min_trabajadores > 0:
            elegidos = reparto.elegir(
                candidatos_reparto,
                modelo_gb=modelo_info.pesos_gb,
                propio_gb=maestro_mem,
                minimo=self.min_trabajadores,
            )
            rpc_targets = [f"127.0.0.1:{c.puerto}" for c in elegidos]
        elif candidatos_calculo:
            try:
                optimo = calculo.elegir_optimo(
                    modelo_info,
                    equipo_maestro,
                    candidatos_calculo,
                    flujos=1,
                    contexto=self.ctx_size,
                )
                log.info("Optimo calculado: %s (ms pasada: %.1f)", optimo.equipos, optimo.ms_por_pasada)
                for nombre in optimo.equipos:
                    if nombre != "maestro" and nombre in mapa_puertos:
                        rpc_targets.append(f"127.0.0.1:{mapa_puertos[nombre]}")
            except ValueError:
                if candidatos_reparto:
                    try:
                        elegidos = reparto.elegir(
                            candidatos_reparto,
                            modelo_gb=modelo_info.pesos_gb,
                            propio_gb=maestro_mem,
                        )
                        rpc_targets = [f"127.0.0.1:{c.puerto}" for c in elegidos]
                    except ValueError as e:
                        log.warning("No alcanza la memoria combinada: %s", e)

        return rpc_targets

    def construir_comando_llama_server(self, rpc_targets: list[str]) -> list[str]:
        """Construye los argumentos para ejecutar llama-server."""
        bin_server = buscar_binario("llama-server", self.llama_bin)
        cmd = [
            str(bin_server),
            "-m",
            str(self.modelo),
            "--host",
            "127.0.0.1",
            "--port",
            str(self.puerto_openai),
            "-t",
            str(self.threads),
            "-c",
            str(self.ctx_size),
        ]
        if rpc_targets:
            cmd.extend(["--rpc", ",".join(rpc_targets)])
            cmd.extend(reparto.salida_en_el_maestro(self.gpus_locales))
        return cmd

    async def iniciar(self, duracion: float = 0.0) -> None:
        """Inicia el rele (si corresponde), selecciona tubos y arranca llama-server."""
        if self.iniciar_rele_local:
            await self._iniciar_rele()

        rpc_targets = await self.seleccionar_trabajadores()
        cmd = self.construir_comando_llama_server(rpc_targets)
        log.info("Iniciando llama-server con comando: %s", " ".join(cmd))

        self.server_proc, self.registro_server = procesos.lanzar(cmd, f"llama-server-{self.puerto_openai}")
        log.info("Registro de llama-server: %s", self.registro_server)

        url_health = f"http://127.0.0.1:{self.puerto_openai}/health"
        listo = False
        async with httpx.AsyncClient() as client:
            fin = time.monotonic() + 30.0
            while time.monotonic() < fin:
                if self.server_proc.poll() is not None:
                    err = procesos.cola_registro(self.registro_server)
                    self.detener()
                    raise RuntimeError(
                        f"llama-server termino inesperadamente (codigo {self.server_proc.returncode}): {err}"
                    )
                try:
                    r = await client.get(url_health, timeout=1.0)
                    if r.status_code == 200:
                        listo = True
                        break
                except Exception:
                    pass
                await asyncio.sleep(0.5)

        if not listo:
            self.detener()
            raise TimeoutError(f"llama-server no respondio en {url_health} tras 30 segundos")

        log.info("Servidor listo en http://127.0.0.1:%d/v1", self.puerto_openai)

        try:
            if duracion > 0:
                await asyncio.sleep(duracion)
            else:
                while self.server_proc.poll() is None:
                    await asyncio.sleep(1.0)
        finally:
            self.detener()

    def detener(self) -> None:
        """Detiene llama-server y el rele local matando SOLO sus PIDs."""
        if self._detenido:
            return
        self._detenido = True
        if self.server_proc is not None and self.server_proc.poll() is None:
            pid = self.server_proc.pid
            log.info("Deteniendo llama-server PID %d", pid)
            self.server_proc.terminate()
            try:
                self.server_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                log.warning("llama-server PID %d no respondio a SIGTERM, enviando SIGKILL", pid)
                self.server_proc.kill()
                self.server_proc.wait(timeout=2)

        if self._rele_task is not None:
            self._rele_task.cancel()
