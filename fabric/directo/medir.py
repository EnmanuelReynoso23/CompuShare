"""Herramienta de medición y comparación de rendimiento:
Canal directo WebRTC (aiortc + STUN) vs Tubo por relé WebSocket (fabric.tubo) vs TCP loopback local.

Mide con el mismo volumen de datos:
1. Rendimiento de transferencia (MB/s)
2. Latencia de ida y vuelta (RTT en ms)
3. Tiempo de establecimiento de conexión (ms)

Uso:
    PYTHONPATH=. /home/reyno/Proyectos/CompuShare/.venv/bin/python -m fabric.directo.medir [--mb 5] [--rtt-muestras 20]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

from websockets.asyncio.server import serve

from fabric.directo.canal import CanalWebRTC
from fabric.rele.rele import Rele
from fabric.tubo import Tubo

logging.basicConfig(level=logging.ERROR)

CLAVE_PRUEBA = "bench-clave-a9"
TROZO = 64 * 1024


async def medir_tcp_directo(volumen_bytes: int, muestras_rtt: int) -> dict[str, Any]:
    """Línea base: TCP directo en loopback sin relé ni WebRTC."""
    async def eco(lector: asyncio.StreamReader, escritor: asyncio.StreamWriter):
        while datos := await lector.read(TROZO):
            escritor.write(datos)
            await escritor.drain()
        escritor.close()

    srv = await asyncio.start_server(eco, "127.0.0.1", 0)
    puerto = srv.sockets[0].getsockname()[1]

    t0_conn = time.perf_counter()
    lector, escritor = await asyncio.open_connection("127.0.0.1", puerto)
    t_conn = (time.perf_counter() - t0_conn) * 1000

    # RTT
    rtts: list[float] = []
    ping_payload = b"PING"
    for _ in range(muestras_rtt):
        t0 = time.perf_counter()
        escritor.write(ping_payload)
        await escritor.drain()
        await lector.readexactly(len(ping_payload))
        rtts.append((time.perf_counter() - t0) * 1000)

    # Caudal con volumen_bytes (ida y vuelta: cliente -> eco -> cliente = 2x volumen)
    carga = os.urandom(volumen_bytes)
    t0_tx = time.perf_counter()
    escritor.write(carga)
    await escritor.drain()
    vuelta = await lector.readexactly(len(carga))
    dur_tx = time.perf_counter() - t0_tx
    assert vuelta == carga

    escritor.close()
    srv.close()

    total_mb = volumen_bytes / (1024 * 1024)
    # Caudal de la carga útil neta en el tiempo de ida y vuelta
    mb_s = total_mb / dur_tx

    return {
        "canal": "tcp_directo_local",
        "volumen_mb": total_mb,
        "tiempo_establecimiento_ms": round(t_conn, 2),
        "tiempo_transferencia_s": round(dur_tx, 4),
        "caudal_mb_s": round(mb_s, 2),
        "rtt_medio_ms": round(sum(rtts) / len(rtts), 3),
        "rtt_min_ms": round(min(rtts), 3),
        "rtt_max_ms": round(max(rtts), 3),
    }


async def medir_tubo_rele(volumen_bytes: int, muestras_rtt: int) -> dict[str, Any]:
    """Rendimiento con el relé WebSocket actual (fabric.tubo + fabric.rele)."""
    async def eco(lector: asyncio.StreamReader, escritor: asyncio.StreamWriter):
        while datos := await lector.read(TROZO):
            escritor.write(datos)
            await escritor.drain()
        escritor.close()

    srv_eco = await asyncio.start_server(eco, "127.0.0.1", 0)
    puerto_eco = srv_eco.sockets[0].getsockname()[1]

    rele = Rele(CLAVE_PRUEBA)
    async with serve(rele.atender, "127.0.0.1", 0, process_request=rele.salud) as srv_rele:
        puerto_rele = srv_rele.sockets[0].getsockname()[1]
        url = f"ws://127.0.0.1:{puerto_rele}"

        t0_conn = time.perf_counter()
        tubo = Tubo(url, CLAVE_PRUEBA, "bench-trabajador", puerto_eco)
        tarea_tubo = asyncio.create_task(tubo.vivir())
        await tubo.listo.wait()
        lector, escritor = await asyncio.open_connection("127.0.0.1", tubo.puerto_remoto)
        t_conn = (time.perf_counter() - t0_conn) * 1000

        # RTT
        rtts: list[float] = []
        ping_payload = b"PING"
        for _ in range(muestras_rtt):
            t0 = time.perf_counter()
            escritor.write(ping_payload)
            await escritor.drain()
            await lector.readexactly(len(ping_payload))
            rtts.append((time.perf_counter() - t0) * 1000)

        # Caudal
        carga = os.urandom(volumen_bytes)
        t0_tx = time.perf_counter()
        escritor.write(carga)
        await escritor.drain()
        vuelta = await lector.readexactly(len(carga))
        dur_tx = time.perf_counter() - t0_tx
        assert vuelta == carga

        escritor.close()
        tarea_tubo.cancel()
        srv_eco.close()

    total_mb = volumen_bytes / (1024 * 1024)
    mb_s = total_mb / dur_tx

    return {
        "canal": "rele_websocket_tubo",
        "volumen_mb": total_mb,
        "tiempo_establecimiento_ms": round(t_conn, 2),
        "tiempo_transferencia_s": round(dur_tx, 4),
        "caudal_mb_s": round(mb_s, 2),
        "rtt_medio_ms": round(sum(rtts) / len(rtts), 3),
        "rtt_min_ms": round(min(rtts), 3),
        "rtt_max_ms": round(max(rtts), 3),
    }


async def medir_directo_webrtc(volumen_bytes: int, muestras_rtt: int) -> dict[str, Any]:
    """Rendimiento con el canal directo WebRTC DataChannel (aiortc + STUN público)."""
    async def eco(lector: asyncio.StreamReader, escritor: asyncio.StreamWriter):
        while datos := await lector.read(TROZO):
            escritor.write(datos)
            await escritor.drain()
        escritor.close()

    srv_eco = await asyncio.start_server(eco, "127.0.0.1", 0)
    puerto_eco = srv_eco.sockets[0].getsockname()[1]

    t0_conn = time.perf_counter()
    c_maestro = CanalWebRTC()
    c_trab = CanalWebRTC()
    c_maestro.iniciar_como_oferente("bench-rpc")

    oferta = await c_maestro.obtener_oferta()
    respuesta = await c_trab.responder_oferta(oferta)
    await c_maestro.aplicar_respuesta(respuesta)

    await c_maestro.esperar_listo(timeout_s=5.0)
    await c_trab.esperar_listo(timeout_s=5.0)

    lector_eco, escritor_eco = await asyncio.open_connection("127.0.0.1", puerto_eco)
    tarea_trab = asyncio.create_task(c_trab.puente_tcp(lector_eco, escritor_eco))

    async def atiende_cliente(lector_cli, escritor_cli):
        await c_maestro.puente_tcp(lector_cli, escritor_cli)

    srv_maestro = await asyncio.start_server(atiende_cliente, "127.0.0.1", 0)
    puerto_maestro = srv_maestro.sockets[0].getsockname()[1]

    lector_cli, escritor_cli = await asyncio.open_connection("127.0.0.1", puerto_maestro)
    t_conn = (time.perf_counter() - t0_conn) * 1000

    # RTT
    rtts: list[float] = []
    ping_payload = b"PING"
    for _ in range(muestras_rtt):
        t0 = time.perf_counter()
        escritor_cli.write(ping_payload)
        await escritor_cli.drain()
        await lector_cli.readexactly(len(ping_payload))
        rtts.append((time.perf_counter() - t0) * 1000)

    # Caudal
    carga = os.urandom(volumen_bytes)
    t0_tx = time.perf_counter()
    escritor_cli.write(carga)
    await escritor_cli.drain()
    vuelta = await lector_cli.readexactly(len(carga))
    dur_tx = time.perf_counter() - t0_tx
    assert vuelta == carga

    escritor_cli.close()
    srv_maestro.close()
    srv_eco.close()
    tarea_trab.cancel()
    await c_maestro.cerrar()
    await c_trab.cerrar()

    total_mb = volumen_bytes / (1024 * 1024)
    mb_s = total_mb / dur_tx

    return {
        "canal": "directo_webrtc_stun",
        "volumen_mb": total_mb,
        "tiempo_establecimiento_ms": round(t_conn, 2),
        "tiempo_transferencia_s": round(dur_tx, 4),
        "caudal_mb_s": round(mb_s, 2),
        "rtt_medio_ms": round(sum(rtts) / len(rtts), 3),
        "rtt_min_ms": round(min(rtts), 3),
        "rtt_max_ms": round(max(rtts), 3),
    }


async def ejecutar_comparacion(volumen_mb: float = 5.0, muestras_rtt: int = 20) -> dict[str, Any]:
    volumen_bytes = int(volumen_mb * 1024 * 1024)
    ahora = datetime.now(timezone.utc).isoformat()

    print(f"=== Medicion Comparativa CompuShare A9 ({ahora}) ===")
    print(f"Volumen: {volumen_mb} MB ({volumen_bytes} bytes), Muestras RTT: {muestras_rtt}\n")

    res_tcp = await medir_tcp_directo(volumen_bytes, muestras_rtt)
    print(f"[1/3] TCP Directo Local:")
    print(f"      Caudal: {res_tcp['caudal_mb_s']} MB/s | RTT medio: {res_tcp['rtt_medio_ms']} ms | Establecimiento: {res_tcp['tiempo_establecimiento_ms']} ms")

    res_rele = await medir_tubo_rele(volumen_bytes, muestras_rtt)
    print(f"[2/3] Relé WebSocket (tubo.py):")
    print(f"      Caudal: {res_rele['caudal_mb_s']} MB/s | RTT medio: {res_rele['rtt_medio_ms']} ms | Establecimiento: {res_rele['tiempo_establecimiento_ms']} ms")

    res_webrtc = await medir_directo_webrtc(volumen_bytes, muestras_rtt)
    print(f"[3/3] Directo WebRTC DataChannel (aiortc + STUN):")
    print(f"      Caudal: {res_webrtc['caudal_mb_s']} MB/s | RTT medio: {res_webrtc['rtt_medio_ms']} ms | Establecimiento: {res_webrtc['tiempo_establecimiento_ms']} ms")

    resultado_total = {
        "fecha": ahora,
        "volumen_mb": volumen_mb,
        "muestras_rtt": muestras_rtt,
        "mediciones": {
            "tcp_directo": res_tcp,
            "tubo_rele": res_rele,
            "directo_webrtc": res_webrtc,
        },
    }
    return resultado_total


def main():
    parser = argparse.ArgumentParser(description="Mide rendimiento directo vs relé")
    parser.add_argument("--mb", type=float, default=5.0, help="Volumen de datos en MB (defecto: 5)")
    parser.add_argument("--rtt-muestras", type=int, default=20, help="Muestras de RTT (defecto: 20)")
    parser.add_argument("--guardar", default="fabric/directo/resultado_medicion.json", help="Ruta para guardar JSON")
    args = parser.parse_args()

    resultado = asyncio.run(ejecutar_comparacion(args.mb, args.rtt_muestras))
    if args.guardar:
        with open(args.guardar, "w", encoding="utf-8") as f:
            json.dump(resultado, f, indent=2, ensure_ascii=False)
        print(f"\nResultado guardado en: {args.guardar}")


if __name__ == "__main__":
    main()
