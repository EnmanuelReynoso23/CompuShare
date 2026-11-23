"""Modulo del Nodo Maestro / Coordinador de CompuShare.
Gestiona el rele de orquestacion, la seleccion optima de trabajadores via calculo.py y reparto.py,
y el servidor llama-server en 127.0.0.1 (protocolo propio de llama.cpp: /completion, /apply-template).
"""

from __future__ import annotations

import asyncio
import logging
import math
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

# Longitud del borrador de la decodificación especulativa (ver calculo.ventana_optima).
# SUPUESTOS documentados; con la sonda de banda se usan los valores medidos del enlace.
ACIERTO_POR_DEFECTO = 0.75  # medido 89,7 % con ngram-mod,draft-mtp en Kaggle (4-nov-2026); se toma menos
SUBIDA_POR_DEFECTO_MBPS = 10.0  # sin sonda: subida típica de casa (docs/MATEMATICA.md)
BAJADA_POR_DEFECTO_MBPS = 10.0  # sin sonda: igual que la subida, por prudencia
GAMMA_SIN_PREDECIR = 3  # sin ventana calculada: el --spec-draft-n-max por defecto de llama.cpp
COSTO_BORRADOR_MTP = 0.28  # fracción de una pasada por palabra adivinada (docs/MATEMATICA.md, medido)
ESPERA_SONDA_S = 15.0  # tiempo máximo para que el relé termine la sonda de banda de los tubos


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
        puerto_api: int = 59000,
        puerto_rele: int | None = None,
        iniciar_rele_local: bool = True,
        llama_bin: str | Path | None = None,
        espera_trabajadores: float = 3.0,
        min_trabajadores: int = 0,
        threads: int = 4,
        ctx_size: int = 4096,
        tubo_puerto_base: int = 59100,
        gpus_locales: int | None = None,
        paralelo: int = 1,
        especulativa: str = "",
        espera_carga: float = 900.0,
        max_reinicios: int = 3,
        ventana: str | int = "auto",
        acierto: float = ACIERTO_POR_DEFECTO,
    ) -> None:
        self.modelo = Path(modelo)
        if not self.modelo.is_file():
            raise FileNotFoundError(f"No existe el archivo de modelo: {self.modelo}")
        self.rele_url = rele_url
        self.clave = clave
        self.puerto_api = puerto_api

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
        self.paralelo = max(1, paralelo)
        # Tipo de decodificacion especulativa de llama.cpp ("" = ninguna). Para copiar texto, ngram-mod
        # adivina desde la propia entrada: medido 4-nov-2026, 5 nodos a 50 ms, 1,1 -> 4,9 tok/s.
        self.especulativa = especulativa
        # Longitud del borrador: "auto" (la calcula con el enlace medido), un número fijo, o 0 = no tocar.
        if str(ventana) == "auto":
            self.ventana: str | int = "auto"
        else:
            self.ventana = int(ventana)
            if self.ventana < 0:
                raise ValueError("ventana no puede ser negativa")
        if not 0 <= acierto <= 1:
            raise ValueError("acierto debe estar entre 0 y 1")
        self.acierto = acierto
        self.gamma_optimo: int | None = None  # lo que calculó la última selección de trabajadores
        self.prediccion: calculo.Ventana | None = None
        # Cargar pesos por la red tarda: en Kaggle el maestro estuvo 142 s arrancando llama-server.
        self.espera_carga = espera_carga
        self.gpus_locales = gpus_locales if gpus_locales is not None else contar_gpus_locales(llama_bin)

        # Si llama-server se cae a mitad de trabajo (se cayó un trabajador), se relanza hasta estas veces.
        self.max_reinicios = max_reinicios
        self.reinicios = 0
        self.espera_recuperar = 600.0
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

    async def _esperar_sonda(self, nombres: list[str]) -> dict[str, Any]:
        """Espera (hasta ESPERA_SONDA_S) a que el relé termine la sonda de banda de esos tubos."""
        fin = time.monotonic() + ESPERA_SONDA_S
        while True:
            tubos = await self.obtener_tubos_registrados()
            if all("sonda" in tubos.get(n, {}).get("info", {}) for n in nombres) or time.monotonic() >= fin:
                return tubos
            await asyncio.sleep(0.5)

    def predecir_ventana(self, modelo: calculo.Modelo, maestro: calculo.Equipo,
                         elegidos: list[calculo.Equipo], enlaces: dict[str, dict[str, Any]]) -> calculo.Ventana:
        """γ* para los trabajadores elegidos, con el RTT y el ancho de banda de cada enlace."""
        nombres = [e.nombre for e in elegidos]
        rtt = sum(enlaces[n]["rtt_ms"] for n in nombres)
        bajada = min((enlaces[n]["bajada_mbps"] for n in nombres), default=math.inf)
        subida = min((enlaces[n]["subida_mbps"] for n in nombres), default=math.inf)
        try:
            ms_verificar = calculo.tiempo_por_palabra(modelo, maestro, elegidos, flujos=1,
                                                      contexto=self.ctx_size).ms_calculo
        except ValueError:  # no cabe con esos equipos: se estima leyendo los pesos activos una vez
            ms_verificar = 1000 * modelo.pesos_gb * modelo.activos_frac / (maestro.banda_gbs * calculo.EFICIENCIA_GPU)
        tipos = {t.strip() for t in self.especulativa.split(",") if t.strip()}
        return calculo.ventana_optima(
            alfa=self.acierto,
            rtt_ms=rtt,
            bajada_mbps=bajada,
            subida_mbps=subida,
            ms_verificar=ms_verificar,
            d_modelo=modelo.d_modelo,
            # Con el códec rpc1 los grafos repetidos viajan como una referencia de 17 bytes; sin él, cota
            # superior con todas las capas.
            bytes_grafo=0.0 if rele_mod.codec_activo() else calculo.bytes_grafo_sin_codec(modelo.capas),
            costo_borrador=COSTO_BORRADOR_MTP if "draft-mtp" in tipos else 0.0,
        )

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
        if self.especulativa and self.ventana == "auto" and tubos:
            tubos = await self._esperar_sonda(list(tubos.keys()))

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
        enlaces: dict[str, dict[str, Any]] = {}

        for nombre, det in tubos.items():
            puerto = int(det["puerto"])
            mapa_puertos[nombre] = puerto
            info = det.get("info", {})
            vram_gb = float(info.get("vram_gb", 4.0)) if info else 4.0
            medida = await asyncio.to_thread(reparto.medir_enlace, puerto, "127.0.0.1", 5, 10.0)
            rtt = medida["rtt_ms"]
            # Ancho de banda medido por la sonda del relé; sin sonda, el supuesto documentado.
            medido = "bajada_mbps" in info and "subida_mbps" in info
            bajada = float(info["bajada_mbps"]) if medido else BAJADA_POR_DEFECTO_MBPS
            subida = float(info["subida_mbps"]) if medido else SUBIDA_POR_DEFECTO_MBPS
            enlaces[nombre] = {"rtt_ms": rtt if rtt != float("inf") else 1000.0,
                               "bajada_mbps": bajada, "subida_mbps": subida}
            log.info("Tubo %s (puerto %d): ida y vuelta %.2f ms (abrir el tubo %.0f ms), VRAM=%.1f GB, "
                     "bajada %.1f / subida %.1f Mb/s (%s)", nombre, puerto, rtt, medida["conexion_ms"], vram_gb,
                     bajada, subida, "medido" if medido else "supuesto, sin sonda")
            candidatos_reparto.append(reparto.Candidato(nombre=nombre, puerto=puerto, vram_gb=vram_gb, rtt_ms=rtt))
            candidatos_calculo.append(
                calculo.Equipo(
                    nombre=nombre,
                    memoria_gb=vram_gb,
                    banda_gbs=320.0,
                    rtt_ms=rtt if rtt != float("inf") else 1000.0,
                    subida_mbps=subida,
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

        self._calcular_ventana(modelo_info, equipo_maestro, candidatos_calculo, enlaces, rpc_targets, mapa_puertos)
        return rpc_targets

    def _calcular_ventana(self, modelo_info: calculo.Modelo, equipo_maestro: calculo.Equipo,
                          candidatos: list[calculo.Equipo], enlaces: dict[str, dict[str, Any]],
                          rpc_targets: list[str], mapa_puertos: dict[str, int]) -> None:
        """Con especulativa y ventana automática: calcula γ* para los trabajadores que se van a usar."""
        self.gamma_optimo = None
        self.prediccion = None
        if not self.especulativa or self.ventana != "auto":
            return
        puerto_a_nombre = {p: n for n, p in mapa_puertos.items()}
        usados = {puerto_a_nombre[int(t.rsplit(":", 1)[1])] for t in rpc_targets}
        elegidos = [c for c in candidatos if c.nombre in usados]
        v = self.predecir_ventana(modelo_info, equipo_maestro, elegidos, enlaces)
        self.prediccion = v
        self.gamma_optimo = max(1, v.gamma)  # con 0 la especulativa no aporta: se deja 1 y se avisa
        if v.gamma == 0:
            log.warning("Especulativa: la predicción dice que no conviene borrar en este enlace (gamma*=0); "
                        "se usa gamma=1")
        tabla = "  ".join(f"{f['gamma']}:{f['tok_s']:.1f}" for f in v.tabla
                          if f["gamma"] in (0, 1, 2, 3, 4, 6, 8, 12, 16))
        log.info("Especulativa (acierto supuesto %.2f): gamma*=%d, %.1f tok/s predichos (sin especulativa %.1f). "
                 "Tok/s por gamma: %s", self.acierto, v.gamma, v.tok_s, v.tok_s_sin_especulativa, tabla)

    def _opciones_ventana(self) -> list[str]:
        """Longitud del borrador para llama-server. Nombres verificados en llama.cpp b11434 (common/arg.cpp):
        --spec-draft-n-max (por defecto 3) lo usa draft-mtp y los demás draft-*; --spec-ngram-mod-n-max
        (por defecto 64) lo usa ngram-mod. --draft-max ya no existe y aborta."""
        if self.ventana == 0 or not self.especulativa:
            return []
        if self.ventana == "auto":
            gamma = self.gamma_optimo if self.gamma_optimo is not None else GAMMA_SIN_PREDECIR
        else:
            gamma = int(self.ventana)
        tipos = {t.strip() for t in self.especulativa.split(",") if t.strip()}
        opciones: list[str] = []
        if any(t.startswith("draft-") for t in tipos):
            opciones += ["--spec-draft-n-max", str(gamma)]
        if "ngram-mod" in tipos:
            # El n_min de ngram-mod es 48 por defecto: con un gamma menor, sin este ajuste solo borraría
            # cuando los gamma tokens completos aparecen en la tabla.
            opciones += ["--spec-ngram-mod-n-max", str(gamma), "--spec-ngram-mod-n-min", str(min(48, gamma))]
        return opciones

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
            str(self.puerto_api),
            "-t",
            str(self.threads),
            "-c",
            str(self.ctx_size * self.paralelo),
            "--parallel",
            str(self.paralelo),
            "-ngl",
            "999",
            "--split-mode",
            "layer",
        ]
        if rpc_targets:
            cmd.extend(["--rpc", ",".join(rpc_targets)])
            # Sin puntos de control ni cache de prompts: en modelos hibridos (Qwen3.5) copian el estado
            # capa por capa con GET_TENSOR, una ida y vuelta por internet cada uno (medido: 3,3 -> 15,6 tok/s).
            cmd.extend(["-ctxcp", "0", "--cache-ram", "0"])
            cmd.extend(reparto.salida_en_el_maestro(self.gpus_locales))
        if self.especulativa:
            cmd.extend(["--spec-type", self.especulativa])
            cmd.extend(self._opciones_ventana())
        return cmd

    async def _arrancar_servidor(self, rpc_targets: list[str]) -> None:
        """Lanza llama-server con esos trabajadores y espera a que responda /health."""
        cmd = self.construir_comando_llama_server(rpc_targets)
        log.info("Iniciando llama-server con comando: %s", " ".join(cmd))
        self.server_proc, self.registro_server = procesos.lanzar(cmd, f"llama-server-{self.puerto_api}")
        log.info("Registro de llama-server: %s", self.registro_server)

        url_health = f"http://127.0.0.1:{self.puerto_api}/health"
        async with httpx.AsyncClient() as client:
            fin = time.monotonic() + self.espera_carga
            while time.monotonic() < fin:
                if self.server_proc.poll() is not None:
                    err = procesos.cola_registro(self.registro_server)
                    raise RuntimeError(
                        f"llama-server termino inesperadamente (codigo {self.server_proc.returncode}): {err}"
                    )
                try:
                    r = await client.get(url_health, timeout=1.0)
                    if r.status_code == 200:
                        log.info("Servidor listo en http://127.0.0.1:%d", self.puerto_api)
                        return
                except Exception:
                    pass
                await asyncio.sleep(0.5)
        raise TimeoutError(f"llama-server no respondio en {url_health} tras {self.espera_carga:.0f} segundos")

    async def _relanzar(self) -> bool:
        """llama-server se cayó (en general porque se cayó un trabajador): vuelve a elegir entre los tubos
        vivos y lo arranca de nuevo. Devuelve False si no lo logra."""
        self.reinicios += 1
        cola = procesos.cola_registro(self.registro_server) if self.registro_server else ""
        log.warning("llama-server se cayo (codigo %s), reinicio %d de %d. Ultimas lineas:\n%s",
                    self.server_proc.returncode if self.server_proc else None, self.reinicios,
                    self.max_reinicios, cola[-1500:])
        # El trabajador puede estar relanzando su ggml-rpc-server: se reintenta durante `espera_recuperar`.
        fin = time.monotonic() + self.espera_recuperar
        while True:
            try:
                await self._arrancar_servidor(await self.seleccionar_trabajadores())
                return True
            except (RuntimeError, TimeoutError, ValueError) as e:
                if self.server_proc is not None and self.server_proc.poll() is None:
                    self.server_proc.kill()
                if time.monotonic() >= fin:
                    log.error("No se pudo relanzar llama-server: %s", e)
                    return False
                log.warning("Todavia no se puede relanzar llama-server (%s); otro intento en 10 s", e)
                await asyncio.sleep(10)

    async def iniciar(self, duracion: float = 0.0) -> None:
        """Inicia el rele (si corresponde), selecciona tubos y arranca llama-server. Mientras corre, si
        llama-server se cae lo relanza (hasta `max_reinicios` veces) con los trabajadores que sigan vivos."""
        if self.iniciar_rele_local:
            await self._iniciar_rele()

        try:
            await self._arrancar_servidor(await self.seleccionar_trabajadores())
        except Exception:
            self.detener()
            raise

        try:
            fin = time.monotonic() + duracion if duracion > 0 else float("inf")
            while time.monotonic() < fin:
                if self.server_proc is not None and self.server_proc.poll() is not None:
                    if self.reinicios >= self.max_reinicios or not await self._relanzar():
                        break
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
