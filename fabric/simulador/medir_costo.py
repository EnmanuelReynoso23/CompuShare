"""Medicion del costo propio del simulador de red TCP sin retrasos.

Mide la sobrecarga inherente del proxy frente a una conexion TCP directa:
- Latencia de ida y vuelta extra (RTT extra en milisegundos).
- Caudal maximo (MB/s maximos transmitidos por el proxy).
"""

from __future__ import annotations

import asyncio
import json
import os
import platform
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# Permitir ejecucion directa como script o como modulo
_raiz = Path(__file__).resolve().parents[2]
if str(_raiz) not in sys.path:
    sys.path.insert(0, str(_raiz))

from fabric.simulador.red import Enlace


async def _servidor_eco():
    tareas = set()

    async def eco(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        t = asyncio.current_task()
        if t:
            tareas.add(t)
        try:
            while True:
                datos = await reader.read(65536)
                if not datos:
                    break
                writer.write(datos)
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            if t:
                tareas.discard(t)

    servidor = await asyncio.start_server(eco, host="127.0.0.1", port=0)
    puerto = servidor.sockets[0].getsockname()[1]
    return servidor, puerto, tareas


async def medir_rtt(host: str, puerto: int, iteraciones: int = 500, tam_payload: int = 64) -> tuple[float, float, float]:
    """Devuelve (min, prom, max) de RTT en milisegundos."""
    reader, writer = await asyncio.open_connection(host, puerto)
    carga = b"P" * tam_payload
    rtts = []
    try:
        # Calentamiento
        for _ in range(10):
            writer.write(carga)
            await writer.drain()
            await reader.readexactly(tam_payload)

        # Medicion
        for _ in range(iteraciones):
            t0 = time.perf_counter()
            writer.write(carga)
            await writer.drain()
            await reader.readexactly(tam_payload)
            t1 = time.perf_counter()
            rtts.append((t1 - t0) * 1000.0)
    finally:
        writer.close()
        await writer.wait_closed()

    return min(rtts), sum(rtts) / len(rtts), max(rtts)


async def medir_caudal(host: str, puerto: int, total_bytes: int = 100 * 1024 * 1024) -> tuple[float, float]:
    """Mide el caudal en MB/s sobre una transferencia eco de total_bytes."""
    reader, writer = await asyncio.open_connection(host, puerto)
    bloque = b"Z" * 65536
    num_bloques = total_bytes // len(bloque)
    bytes_a_enviar = num_bloques * len(bloque)

    async def enviar():
        for _ in range(num_bloques):
            writer.write(bloque)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()
            await writer.drain()

    async def recibir() -> int:
        recibidos = 0
        while True:
            trozo = await reader.read(65536)
            if not trozo:
                break
            recibidos += len(trozo)
        return recibidos

    try:
        t0 = time.perf_counter()
        _, rec = await asyncio.gather(enviar(), recibir())
        t1 = time.perf_counter()
        duracion = t1 - t0
        mb_total = rec / (1024.0 * 1024.0)
        mb_s = mb_total / duracion
        return duracion, mb_s
    finally:
        writer.close()
        await writer.wait_closed()


async def main_async() -> dict:
    srv, puerto_eco, tareas_eco = await _servidor_eco()
    iteraciones_rtt = 500
    bytes_caudal = 100 * 1024 * 1024  # 100 MB

    try:
        # 1. Medicion directa (sin proxy)
        min_dir, prom_dir, max_dir = await medir_rtt("127.0.0.1", puerto_eco, iteraciones_rtt)
        dur_dir, caudal_dir = await medir_caudal("127.0.0.1", puerto_eco, bytes_caudal)

        # 2. Medicion a traves de Enlace sin retrasos
        async with Enlace(
            escuchar=("127.0.0.1", 0),
            destino=("127.0.0.1", puerto_eco),
            latencia_ms=0.0,
            variacion_ms=0.0,
            mbps=None,
            perdida=0.0,
        ) as enlace:
            min_prx, prom_prx, max_prx = await medir_rtt("127.0.0.1", enlace.puerto, iteraciones_rtt)
            dur_prx, caudal_prx = await medir_caudal("127.0.0.1", enlace.puerto, bytes_caudal)
    finally:
        srv.close()
        await srv.wait_closed()
        for t in list(tareas_eco):
            t.cancel()
        if tareas_eco:
            await asyncio.gather(*tareas_eco, return_exceptions=True)

    rtt_extra = prom_prx - prom_dir

    resultado = {
        "fecha": datetime.now(timezone.utc).isoformat(),
        "sistema": {
            "os": platform.system(),
            "kernel": platform.release(),
            "maquina": platform.machine(),
            "python": sys.version.split()[0],
        },
        "parametros": {
            "iteraciones_rtt": iteraciones_rtt,
            "bytes_caudal": bytes_caudal,
            "tamano_bloque": 65536,
        },
        "directo": {
            "rtt_min_ms": round(min_dir, 4),
            "rtt_prom_ms": round(prom_dir, 4),
            "rtt_max_ms": round(max_dir, 4),
            "caudal_mb_s": round(caudal_dir, 2),
            "duracion_caudal_s": round(dur_dir, 4),
        },
        "proxy": {
            "rtt_min_ms": round(min_prx, 4),
            "rtt_prom_ms": round(prom_prx, 4),
            "rtt_max_ms": round(max_prx, 4),
            "caudal_mb_s": round(caudal_prx, 2),
            "duracion_caudal_s": round(dur_prx, 4),
        },
        "costo_propio": {
            "rtt_extra_ms": round(rtt_extra, 4),
            "caudal_maximo_mb_s": round(caudal_prx, 2),
            "rendimiento_relativo_pct": round((caudal_prx / caudal_dir) * 100.0, 2),
        },
    }

    ruta_salida = Path(__file__).parent / "costo_proxy.json"
    ruta_salida.write_text(json.dumps(resultado, indent=2), encoding="utf-8")

    print("=== Medicion del costo propio del proxy TCP (fabric.simulador.red) ===")
    print(f"Fecha: {resultado['fecha']}")
    print(f"Python: {resultado['sistema']['python']} en {resultado['sistema']['os']} {resultado['sistema']['kernel']}")
    print("-" * 65)
    print(f"Directo: RTT prom = {resultado['directo']['rtt_prom_ms']} ms | Caudal = {resultado['directo']['caudal_mb_s']} MB/s")
    print(f"Proxy:   RTT prom = {resultado['proxy']['rtt_prom_ms']} ms | Caudal = {resultado['proxy']['caudal_mb_s']} MB/s")
    print("-" * 65)
    print(f"Ida y vuelta extra: {resultado['costo_propio']['rtt_extra_ms']} ms")
    print(f"MB/s maximos (proxy): {resultado['costo_propio']['caudal_maximo_mb_s']} MB/s")
    print(f"Rendimiento relativo: {resultado['costo_propio']['rendimiento_relativo_pct']} %")
    print(f"Archivo guardado: {ruta_salida}")
    return resultado


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
