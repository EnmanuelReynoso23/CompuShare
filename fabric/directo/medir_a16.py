"""Script de medicion comparativa de rendimiento para el Agente A16:
1. TCP Directo Local (linea base)
2. Rele WebSocket Python (fabric.tubo)
3. Directo WebRTC Python (CanalWebRTC / aiortc + STUN)
4. CompuShare Directo Go (compushare-directo / Pion ICE + QUIC-go)
5. CompuShare Directo Go Fallback al Rele (-forzar-rele)

En dos modalidades:
- Local (sin simulacion)
- Con fabric/simulador/red.py (perfil 'mismo país': 40 ms latencia, 10 ms jitter, 50 Mb/s)

Cada prueba realiza 3 repeticiones midiendo:
- Tiempo de establecimiento de conexion (ms)
- Caudal util neto (MB/s) en transferencia bidireccional (eco)
- Latencia de ida y vuelta RTT (ms) con 20 muestras

Todos los puertos estan estrictamente en el rango de A16: 53000-53999.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from typing import Any

from websockets.asyncio.server import serve

from fabric.directo.canal import CanalWebRTC
from fabric.rele.rele import Rele
from fabric.simulador.red import Enlace, obtener_perfil
from fabric.tubo import Tubo

logging.basicConfig(level=logging.ERROR)

CLAVE_A16 = "compushare-a16-bench"
TROZO = 64 * 1024
PUERTO_RELE = 53000
PUERTO_ECO = 53052
PUERTO_MAESTRO = 53100
PUERTO_SIMULADOR = 53200


async def iniciar_eco(puerto: int) -> asyncio.Server:
    async def eco_handler(lector: asyncio.StreamReader, escritor: asyncio.StreamWriter):
        try:
            while datos := await lector.read(TROZO):
                escritor.write(datos)
                await escritor.drain()
        except (OSError, asyncio.CancelledError):
            pass
        finally:
            try:
                escritor.close()
                await escritor.wait_closed()
            except Exception:
                pass

    return await asyncio.start_server(eco_handler, "127.0.0.1", puerto)


async def medir_cliente_eco(host: str, puerto: int, volumen_bytes: int, muestras_rtt: int) -> tuple[float, float, float, float, float]:
    """Conecta al puerto especificado, mide RTT y mide caudal de transferencia eco."""
    t0_conn = time.perf_counter()
    lector, escritor = await asyncio.open_connection(host, puerto)
    t_conn = (time.perf_counter() - t0_conn) * 1000.0

    # 1. RTT (20 pings de 64 bytes)
    rtts: list[float] = []
    ping_payload = b"PING" * 16  # 64 bytes
    for _ in range(muestras_rtt):
        t0 = time.perf_counter()
        escritor.write(ping_payload)
        await escritor.drain()
        resp = await lector.readexactly(len(ping_payload))
        assert resp == ping_payload
        rtts.append((time.perf_counter() - t0) * 1000.0)

    # 2. Caudal de transferencia
    carga = os.urandom(volumen_bytes)
    t0_tx = time.perf_counter()

    async def enviar():
        escritor.write(carga)
        await escritor.drain()

    async def recibir():
        return await lector.readexactly(len(carga))

    _, vuelta = await asyncio.gather(enviar(), recibir())
    dur_tx = time.perf_counter() - t0_tx
    assert vuelta == carga

    escritor.close()
    await escritor.wait_closed()

    total_mb = volumen_bytes / (1024.0 * 1024.0)
    caudal_mb_s = total_mb / dur_tx
    rtt_medio = sum(rtts) / len(rtts)
    rtt_min = min(rtts)
    rtt_max = max(rtts)

    return t_conn, caudal_mb_s, rtt_medio, rtt_min, rtt_max


async def correr_caso_tcp_directo(con_simulador: bool, volumen_bytes: int, muestras_rtt: int) -> dict[str, Any]:
    srv_eco = await iniciar_eco(PUERTO_ECO)
    try:
        if con_simulador:
            enlace = Enlace(escuchar=PUERTO_SIMULADOR, destino=PUERTO_ECO, perfil="mismo país")
            await enlace.iniciar()
            try:
                t_conn, caudal, rtt_med, rtt_min, rtt_max = await medir_cliente_eco("127.0.0.1", PUERTO_SIMULADOR, volumen_bytes, muestras_rtt)
            finally:
                await enlace.detener()
        else:
            t_conn, caudal, rtt_med, rtt_min, rtt_max = await medir_cliente_eco("127.0.0.1", PUERTO_ECO, volumen_bytes, muestras_rtt)
    finally:
        srv_eco.close()
        await srv_eco.wait_closed()

    return {
        "tiempo_establecimiento_ms": round(t_conn, 2),
        "caudal_mb_s": round(caudal, 2),
        "rtt_medio_ms": round(rtt_med, 3),
        "rtt_min_ms": round(rtt_min, 3),
        "rtt_max_ms": round(rtt_max, 3),
    }


async def correr_caso_tubo_rele(con_simulador: bool, volumen_bytes: int, muestras_rtt: int) -> dict[str, Any]:
    srv_eco = await iniciar_eco(PUERTO_ECO)
    rele = Rele(CLAVE_A16)
    srv_rele = await serve(rele.atender, "127.0.0.1", PUERTO_RELE, process_request=rele.salud)
    url_rele = f"ws://127.0.0.1:{PUERTO_RELE}"

    tubo = Tubo(url_rele, CLAVE_A16, "trabajador-a16", PUERTO_ECO)
    tarea_tubo = asyncio.create_task(tubo.vivir())

    try:
        await asyncio.wait_for(tubo.listo.wait(), timeout=5.0)
        puerto_tubo = tubo.puerto_remoto
        assert puerto_tubo is not None

        if con_simulador:
            enlace = Enlace(escuchar=PUERTO_SIMULADOR, destino=puerto_tubo, perfil="mismo país")
            await enlace.iniciar()
            try:
                t_conn, caudal, rtt_med, rtt_min, rtt_max = await medir_cliente_eco("127.0.0.1", PUERTO_SIMULADOR, volumen_bytes, muestras_rtt)
            finally:
                await enlace.detener()
        else:
            t_conn, caudal, rtt_med, rtt_min, rtt_max = await medir_cliente_eco("127.0.0.1", puerto_tubo, volumen_bytes, muestras_rtt)
    finally:
        tarea_tubo.cancel()
        srv_rele.close()
        await srv_rele.wait_closed()
        srv_eco.close()
        await srv_eco.wait_closed()

    return {
        "tiempo_establecimiento_ms": round(t_conn, 2),
        "caudal_mb_s": round(caudal, 2),
        "rtt_medio_ms": round(rtt_med, 3),
        "rtt_min_ms": round(rtt_min, 3),
        "rtt_max_ms": round(rtt_max, 3),
    }


async def correr_caso_webrtc_python(con_simulador: bool, volumen_bytes: int, muestras_rtt: int) -> dict[str, Any]:
    srv_eco = await iniciar_eco(PUERTO_ECO)

    t0_conn = time.perf_counter()
    c_maestro = CanalWebRTC()
    c_trab = CanalWebRTC()
    c_maestro.iniciar_como_oferente("bench-a16")

    oferta = await c_maestro.obtener_oferta()
    respuesta = await c_trab.responder_oferta(oferta)
    await c_maestro.aplicar_respuesta(respuesta)

    await c_maestro.esperar_listo(timeout_s=5.0)
    await c_trab.esperar_listo(timeout_s=5.0)

    lector_eco, escritor_eco = await asyncio.open_connection("127.0.0.1", PUERTO_ECO)
    tarea_trab = asyncio.create_task(c_trab.puente_tcp(lector_eco, escritor_eco))

    async def atender_maestro(lector_cli, escritor_cli):
        await c_maestro.puente_tcp(lector_cli, escritor_cli)

    srv_maestro = await asyncio.start_server(atender_maestro, "127.0.0.1", PUERTO_MAESTRO)
    t_conn_webrtc = (time.perf_counter() - t0_conn) * 1000.0

    try:
        if con_simulador:
            enlace = Enlace(escuchar=PUERTO_SIMULADOR, destino=PUERTO_MAESTRO, perfil="mismo país")
            await enlace.iniciar()
            try:
                t_conn, caudal, rtt_med, rtt_min, rtt_max = await medir_cliente_eco("127.0.0.1", PUERTO_SIMULADOR, volumen_bytes, muestras_rtt)
            finally:
                await enlace.detener()
        else:
            t_conn, caudal, rtt_med, rtt_min, rtt_max = await medir_cliente_eco("127.0.0.1", PUERTO_MAESTRO, volumen_bytes, muestras_rtt)
    finally:
        tarea_trab.cancel()
        srv_maestro.close()
        await srv_maestro.wait_closed()
        try:
            escritor_eco.close()
            await escritor_eco.wait_closed()
        except Exception:
            pass
        srv_eco.close()
        await srv_eco.wait_closed()

    return {
        "tiempo_establecimiento_ms": round(t_conn_webrtc + t_conn, 2),
        "caudal_mb_s": round(caudal, 2),
        "rtt_medio_ms": round(rtt_med, 3),
        "rtt_min_ms": round(rtt_min, 3),
        "rtt_max_ms": round(rtt_max, 3),
    }


async def correr_caso_go(con_simulador: bool, forzar_rele: bool, volumen_bytes: int, muestras_rtt: int, bin_go: str) -> dict[str, Any]:
    srv_eco = await iniciar_eco(PUERTO_ECO)
    rele = Rele(CLAVE_A16)
    srv_rele = await serve(rele.atender, "127.0.0.1", PUERTO_RELE, process_request=rele.salud)
    url_rele = f"ws://127.0.0.1:{PUERTO_RELE}"

    # Iniciar trabajador Go
    cmd_trab = [
        bin_go,
        "-rol", "trabajador",
        "-rele", url_rele,
        "-clave", CLAVE_A16,
        "-id", "trabajador-go",
        "-destino", f"127.0.0.1:{PUERTO_ECO}",
    ]
    if forzar_rele:
        cmd_trab.append("-forzar-rele")

    proc_trab = subprocess.Popen(cmd_trab, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Esperar brevemente a que el trabajador se registre en el relé
    await asyncio.sleep(0.3)

    # Iniciar maestro Go
    cmd_maestro = [
        bin_go,
        "-rol", "maestro",
        "-rele", url_rele,
        "-clave", CLAVE_A16,
        "-id", "maestro-go",
        "-par", "trabajador-go",
        "-escuchar", f"127.0.0.1:{PUERTO_MAESTRO}",
    ]
    if forzar_rele:
        cmd_maestro.append("-forzar-rele")

    t0_conn = time.perf_counter()
    proc_maestro = subprocess.Popen(cmd_maestro, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    # Esperar a que el maestro abra su puerto
    puerto_listo = False
    for _ in range(50):
        try:
            r, w = await asyncio.open_connection("127.0.0.1", PUERTO_MAESTRO)
            w.close()
            await w.wait_closed()
            puerto_listo = True
            break
        except OSError:
            await asyncio.sleep(0.1)

    t_est_go = (time.perf_counter() - t0_conn) * 1000.0

    if not puerto_listo:
        proc_maestro.terminate()
        proc_trab.terminate()
        srv_rele.close()
        await srv_rele.wait_closed()
        srv_eco.close()
        await srv_eco.wait_closed()
        raise RuntimeError("No se abrio el puerto maestro de compushare-directo a tiempo")

    try:
        if con_simulador:
            enlace = Enlace(escuchar=PUERTO_SIMULADOR, destino=PUERTO_MAESTRO, perfil="mismo país")
            await enlace.iniciar()
            try:
                t_conn, caudal, rtt_med, rtt_min, rtt_max = await medir_cliente_eco("127.0.0.1", PUERTO_SIMULADOR, volumen_bytes, muestras_rtt)
            finally:
                await enlace.detener()
        else:
            t_conn, caudal, rtt_med, rtt_min, rtt_max = await medir_cliente_eco("127.0.0.1", PUERTO_MAESTRO, volumen_bytes, muestras_rtt)
    finally:
        # Matar procesos guardados de forma estricta por PID (siguiendo REGLAS.md)
        if proc_maestro.poll() is None:
            proc_maestro.terminate()
            try:
                proc_maestro.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc_maestro.kill()

        if proc_trab.poll() is None:
            proc_trab.terminate()
            try:
                proc_trab.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc_trab.kill()

        srv_rele.close()
        await srv_rele.wait_closed()
        srv_eco.close()
        await srv_eco.wait_closed()

    return {
        "tiempo_establecimiento_ms": round(t_est_go + t_conn, 2),
        "caudal_mb_s": round(caudal, 2),
        "rtt_medio_ms": round(rtt_med, 3),
        "rtt_min_ms": round(rtt_min, 3),
        "rtt_max_ms": round(rtt_max, 3),
    }


async def main_async():
    parser = argparse.ArgumentParser(description="Medición de rendimiento comparativo A16")
    parser.add_argument("--mb", type=int, default=5, help="Megabytes a transferir (defecto 5)")
    parser.add_argument("--rtt-muestras", type=int, default=20, help="Muestras RTT (defecto 20)")
    parser.add_argument("--repeticiones", type=int, default=3, help="Repeticiones por caso (defecto 3)")
    parser.add_argument("--salida", type=str, default="fabric/directo/resultado_medicion_a16.json", help="Ruta archivo salida")
    args = parser.parse_args()

    volumen_bytes = args.mb * 1024 * 1024
    bin_go = "/tmp/compushare-directo"

    # Verificar que el binario de Go exista, si no compilarlo estático
    if not os.path.exists(bin_go):
        print("Compilando binario estatico /tmp/compushare-directo...")
        res = subprocess.run(
            ["/home/reyno/.local/bin/go", "build", "-o", bin_go, "./cmd/compushare-directo"],
            cwd="/home/reyno/Proyectos/cs-a16/go",
            env={**os.environ, "CGO_ENABLED": "0"},
            capture_output=True,
            text=True,
        )
        if res.returncode != 0:
            print("Error compilando Go:", res.stderr)
            sys.exit(1)

    casos = [
        ("1. TCP Directo Local (referencia kernel)", False, "tcp_directo", False),
        ("1. TCP Directo con Simulador ('mismo país')", True, "tcp_directo", False),
        ("2. Relé WebSocket Python (fabric.tubo) - Local", False, "tubo_rele", False),
        ("2. Relé WebSocket Python (fabric.tubo) - Simulador ('mismo país')", True, "tubo_rele", False),
        ("3. Directo WebRTC Python (aiortc + STUN) - Local", False, "webrtc_python", False),
        ("3. Directo WebRTC Python (aiortc + STUN) - Simulador ('mismo país')", True, "webrtc_python", False),
        ("4. CompuShare Directo Go (Pion ICE + QUIC) - Local", False, "go_p2p", False),
        ("4. CompuShare Directo Go (Pion ICE + QUIC) - Simulador ('mismo país')", True, "go_p2p", False),
        ("5. CompuShare Directo Go Fallback a Relé - Local", False, "go_p2p", True),
        ("5. CompuShare Directo Go Fallback a Relé - Simulador ('mismo país')", True, "go_p2p", True),
    ]

    fecha_iso = datetime.now(timezone.utc).isoformat()
    loadavg = os.getloadavg()

    print(f"==========================================================================")
    print(f"  BENCHMARK COMPUSHARE A16 - COMPARATIVA MULTICANAL ({args.mb} MB, {args.repeticiones} rep)")
    print(f"  Fecha: {fecha_iso}")
    print(f"  Carga previa (1, 5, 15 min): {loadavg}")
    print(f"==========================================================================\n")

    todos_resultados: list[dict[str, Any]] = []

    for desc, sim, tipo, forzar_rele in casos:
        print(f"--- Evaluando: {desc} ---")
        rep_datos = []
        for rep in range(1, args.repeticiones + 1):
            if tipo == "tcp_directo":
                r = await correr_caso_tcp_directo(sim, volumen_bytes, args.rtt_muestras)
            elif tipo == "tubo_rele":
                r = await correr_caso_tubo_rele(sim, volumen_bytes, args.rtt_muestras)
            elif tipo == "webrtc_python":
                r = await correr_caso_webrtc_python(sim, volumen_bytes, args.rtt_muestras)
            elif tipo == "go_p2p":
                r = await correr_caso_go(sim, forzar_rele, volumen_bytes, args.rtt_muestras, bin_go)
            else:
                raise ValueError(tipo)

            r["caso"] = desc
            r["simulador"] = sim
            r["repeticion"] = rep
            rep_datos.append(r)
            todos_resultados.append(r)
            print(f"  Rep {rep}/{args.repeticiones}: Establecimiento: {r['tiempo_establecimiento_ms']} ms | Caudal: {r['caudal_mb_s']} MB/s | RTT medio: {r['rtt_medio_ms']} ms (min: {r['rtt_min_ms']}, max: {r['rtt_max_ms']})")

        # Promedios
        prom_est = sum(x["tiempo_establecimiento_ms"] for x in rep_datos) / len(rep_datos)
        prom_caudal = sum(x["caudal_mb_s"] for x in rep_datos) / len(rep_datos)
        prom_rtt = sum(x["rtt_medio_ms"] for x in rep_datos) / len(rep_datos)
        print(f"  -> PROMEDIO: Est: {prom_est:.2f} ms | Caudal: {prom_caudal:.2f} MB/s | RTT: {prom_rtt:.3f} ms\n")

    res_global = {
        "fecha": fecha_iso,
        "volumen_mb": args.mb,
        "repeticiones": args.repeticiones,
        "muestras_rtt": args.rtt_muestras,
        "carga_sistema_loadavg": list(loadavg),
        "resultados": todos_resultados,
    }

    os.makedirs(os.path.dirname(os.path.abspath(args.salida)), exist_ok=True)
    with open(args.salida, "w", encoding="utf-8") as f:
        json.dump(res_global, f, indent=2, ensure_ascii=False)

    print(f"Archivo de resultados generado en: {args.salida}")


def main():
    asyncio.run(main_async())


if __name__ == "__main__":
    main()
