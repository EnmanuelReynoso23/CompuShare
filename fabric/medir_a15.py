#!/usr/bin/env python3
"""Script de medición oficial del agente A15.
Mide latencia (ida y vuelta en ms) y ancho de banda (MB/s) con un eco TCP,
y palabras/s (tok/s de generación) de llama.cpp RPC con Qwen3.5-0.8B-Q8_0
en 4 escenarios: directo, tubo+relé Python, tubo+relé Go, y tubo+relé Go con simulador red ("mismo país").
Cada medición de llama.cpp se repite 3 veces y se reporta la mediana.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import socket
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
BIN_RELE_GO = RAIZ / "go" / "bin" / "compushare-rele"
BIN_TUBO_GO = RAIZ / "go" / "bin" / "compushare-tubo"
PYTHON_BIN = Path("/home/reyno/Proyectos/CompuShare/.venv/bin/python")
LLAMA_BENCH = Path("/home/reyno/.local/opt/llama.cpp/build/bin/llama-bench")
RPC_SERVER = Path("/home/reyno/.local/opt/llama.cpp/build/bin/ggml-rpc-server")
MODELO = Path("/home/reyno/.cache/fabric-descargas/modelos/Qwen3.5-0.8B-Q8_0.gguf")

CLAVE = "clave-medicion-a15"
THREADS = 4


def puerto_libre(inicio: int = 52000, fin: int = 52999) -> int:
    for p in range(inicio, fin + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError(f"no hay puertos libres en el rango {inicio}-{fin}")


async def servidor_eco_async(puerto: int):
    async def handle_eco(reader, writer):
        while True:
            datos = await reader.read(65536)
            if not datos:
                break
            writer.write(datos)
            await writer.drain()
        writer.close()

    server = await asyncio.start_server(handle_eco, "127.0.0.1", puerto)
    return server


async def medir_eco_canal(host: str, puerto: int, num_pings: int = 100, bytes_ancho: int = 50 * 1024 * 1024):
    """Mide ida y vuelta (RTT ms) y ancho de banda (MB/s)."""
    # 1. Medición de RTT con pings cortos (64 bytes)
    lector, escritor = await asyncio.open_connection(host, puerto)
    tiempos_ms = []
    ping_data = b"X" * 64
    for _ in range(num_pings):
        t0 = time.perf_counter()
        escritor.write(ping_data)
        await escritor.drain()
        resp = await lector.readexactly(64)
        t1 = time.perf_counter()
        tiempos_ms.append((t1 - t0) * 1000.0)
    escritor.close()
    await escritor.wait_closed()

    rtt_mediana_ms = statistics.median(tiempos_ms)
    rtt_min_ms = min(tiempos_ms)
    rtt_max_ms = max(tiempos_ms)

    # 2. Medición de Ancho de Banda con carga pesada
    lector, escritor = await asyncio.open_connection(host, puerto)
    trozo = b"B" * 65536
    total_trozos = bytes_ancho // len(trozo)

    t0 = time.perf_counter()

    async def escritor_carga():
        for _ in range(total_trozos):
            escritor.write(trozo)
            await escritor.drain()

    async def lector_carga():
        recibido = 0
        while recibido < bytes_ancho:
            datos = await lector.read(min(65536, bytes_ancho - recibido))
            if not datos:
                break
            recibido += len(datos)
        return recibido

    t_w = asyncio.create_task(escritor_carga())
    t_r = asyncio.create_task(lector_carga())
    await asyncio.gather(t_w, t_r)
    t1 = time.perf_counter()

    escritor.close()
    await escritor.wait_closed()

    duracion_s = t1 - t0
    mb_s = (bytes_ancho / (1024 * 1024)) / duracion_s if duracion_s > 0 else 0

    return {
        "rtt_mediana_ms": round(rtt_mediana_ms, 3),
        "rtt_min_ms": round(rtt_min_ms, 3),
        "rtt_max_ms": round(rtt_max_ms, 3),
        "ancho_mb_s": round(mb_s, 2),
        "duracion_ancho_s": round(duracion_s, 3),
    }


def esperar_puerto(host: str, puerto: int, timeout: float = 10.0) -> bool:
    limite = time.monotonic() + timeout
    while time.monotonic() < limite:
        try:
            with socket.create_connection((host, puerto), timeout=0.2):
                return True
        except OSError:
            time.sleep(0.05)
    return False


def ejecutar_llama_bench(rpc_host_port: str, repeticiones: int = 3) -> dict:
    """Ejecuta llama-bench contra el rpc especificado y devuelve las estadísticas."""
    cmd = [
        str(LLAMA_BENCH),
        "-m", str(MODELO),
        "-rpc", rpc_host_port,
        "-ngl", "99",
        "-p", "16",
        "-n", "64",
        "-t", str(THREADS),
        "-r", str(repeticiones),
        "-o", "json",
    ]
    t0 = time.perf_counter()
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, check=True)
    duracion = time.perf_counter() - t0

    datos = json.loads(res.stdout)
    gen_item = next((item for item in datos if item.get("n_gen") == 64), None)
    prompt_item = next((item for item in datos if item.get("n_prompt") == 16 and item.get("n_gen") == 0), None)

    gen_samples = [float(x) for x in gen_item.get("samples_ts", [])] if gen_item else []
    prompt_samples = [float(x) for x in prompt_item.get("samples_ts", [])] if prompt_item else []

    gen_mediana = round(statistics.median(gen_samples), 2) if gen_samples else 0.0
    prompt_mediana = round(statistics.median(prompt_samples), 2) if prompt_samples else 0.0

    return {
        "comando": " ".join(cmd),
        "gen_mediana_tok_s": gen_mediana,
        "gen_valores": [round(x, 2) for x in gen_samples],
        "gen_avg_tok_s": round(gen_item.get("avg_ts", 0.0), 2) if gen_item else 0.0,
        "prompt_mediana_tok_s": prompt_mediana,
        "prompt_valores": [round(x, 2) for x in prompt_samples],
        "prompt_avg_tok_s": round(prompt_item.get("avg_ts", 0.0), 2) if prompt_item else 0.0,
        "duracion_total_s": round(duracion, 3),
    }


def medir_escenario_llama(nombre: str, rpc_target: str, repeticiones: int = 3) -> dict:
    print(f"\n--- Midiendo llama.cpp RPC: {nombre} ({rpc_target}) ---")
    med = ejecutar_llama_bench(rpc_target, repeticiones=repeticiones)
    print(
        f"  Generación: {med['gen_mediana_tok_s']:.2f} tok/s (muestras: {med['gen_valores']}) | "
        f"Prompt: {med['prompt_mediana_tok_s']:.2f} tok/s (muestras: {med['prompt_valores']}, t={med['duracion_total_s']}s)"
    )
    return {
        "nombre": nombre,
        "rpc_target": rpc_target,
        "repeticiones": repeticiones,
        "comando": med["comando"],
        "gen_mediana_tok_s": med["gen_mediana_tok_s"],
        "gen_valores": med["gen_valores"],
        "prompt_mediana_tok_s": med["prompt_mediana_tok_s"],
        "prompt_valores": med["prompt_valores"],
        "duracion_total_s": med["duracion_total_s"],
    }


def obtener_cargas_sistema() -> dict:
    load1, load5, load15 = os.getloadavg()
    try:
        ps_out = subprocess.check_output(
            ["ps", "-eo", "pid,pcpu,pmem,comm,args", "--sort=-pcpu"],
            text=True
        ).splitlines()[:15]
    except Exception:
        ps_out = []
    return {
        "loadavg": [load1, load5, load15],
        "top_procesos": ps_out,
    }


def main():
    print(f"Iniciando mediciones A15 a las {datetime.now(timezone.utc).isoformat()}")
    cargas_ini = obtener_cargas_sistema()
    print(f"CPU: {os.cpu_count()} núcleos, Carga inicial: {cargas_ini['loadavg']}")

    # Asegurar binarios
    assert BIN_RELE_GO.exists(), f"No existe {BIN_RELE_GO}"
    assert BIN_TUBO_GO.exists(), f"No existe {BIN_TUBO_GO}"
    assert MODELO.exists(), f"No existe {MODELO}"
    assert RPC_SERVER.exists(), f"No existe {RPC_SERVER}"
    assert LLAMA_BENCH.exists(), f"No existe {LLAMA_BENCH}"

    resumen = {
        "fecha": datetime.now(timezone.utc).isoformat(),
        "carga_inicial": cargas_ini,
        "cpu_count": os.cpu_count(),
        "modelo": str(MODELO),
        "hilos_llama": THREADS,
        "rango_puertos": "52000-52999",
        "eco": {},
        "llama_rpc": {},
    }

    env_py = os.environ.copy()
    env_py["FABRIC_CLAVE"] = CLAVE
    env_py["PYTHONPATH"] = str(RAIZ)

    # =========================================================================
    # PARTE 1: Medición de Eco TCP (Ida y Vuelta RTT y MB/s)
    # =========================================================================
    print("\n=======================================================")
    print("PARTE 1: MEDICION DE ECO TCP (RTT e IDA Y VUELTA, MB/S)")
    print("=======================================================")

    puerto_eco = puerto_libre(52010, 52099)

    async def correr_mediciones_eco():
        servidor_eco = await servidor_eco_async(puerto_eco)

        # 1.1 Eco Directo TCP
        print("\n1.1 Midiendo Eco Directo TCP (127.0.0.1)...")
        resumen["eco"]["directo"] = await medir_eco_canal("127.0.0.1", puerto_eco)
        print(f"  Directo: RTT mediana = {resumen['eco']['directo']['rtt_mediana_ms']} ms, Ancho = {resumen['eco']['directo']['ancho_mb_s']} MB/s")

        # 1.2 Eco Tubo + Relé Python
        print("\n1.2 Midiendo Eco Tubo + Relé Python...")
        puerto_rele_py = puerto_libre(52100, 52149)
        puerto_tubo_py = puerto_libre(52150, 52199)
        proc_rele_py = subprocess.Popen(
            [str(PYTHON_BIN), "-m", "fabric.rele.rele"],
            cwd=str(RAIZ),
            env=dict(env_py, PUERTO=str(puerto_rele_py), TUBO_PUERTO_BASE=str(puerto_tubo_py)),
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        assert esperar_puerto("127.0.0.1", puerto_rele_py), "Relé Python no arrancó"

        proc_tubo_py = subprocess.Popen(
            [
                str(PYTHON_BIN), "-m", "fabric.tubo",
                "--rele", f"ws://127.0.0.1:{puerto_rele_py}",
                "--clave", CLAVE,
                "--nombre", "tubo-eco-py",
                "--puerto", str(puerto_eco),
            ],
            cwd=str(RAIZ),
            env=env_py,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        assert esperar_puerto("127.0.0.1", puerto_tubo_py), f"Puerto del tubo relé Python ({puerto_tubo_py}) no abrió"

        try:
            resumen["eco"]["tubo_rele_python"] = await medir_eco_canal("127.0.0.1", puerto_tubo_py)
            print(f"  Python: RTT mediana = {resumen['eco']['tubo_rele_python']['rtt_mediana_ms']} ms, Ancho = {resumen['eco']['tubo_rele_python']['ancho_mb_s']} MB/s")
        finally:
            proc_tubo_py.terminate()
            proc_tubo_py.wait()
            proc_rele_py.terminate()
            proc_rele_py.wait()

        # 1.3 Eco Tubo + Relé Go
        print("\n1.3 Midiendo Eco Tubo + Relé Go...")
        puerto_rele_go = puerto_libre(52200, 52249)
        puerto_tubo_go = puerto_libre(52250, 52299)
        proc_rele_go = subprocess.Popen(
            [
                str(BIN_RELE_GO),
                "--puerto", str(puerto_rele_go),
                "--clave", CLAVE,
                "--tubo-puerto-base", str(puerto_tubo_go),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        assert esperar_puerto("127.0.0.1", puerto_rele_go), "Relé Go no arrancó"

        proc_tubo_go = subprocess.Popen(
            [
                str(BIN_TUBO_GO),
                "--rele", f"ws://127.0.0.1:{puerto_rele_go}",
                "--clave", CLAVE,
                "--nombre", "tubo-eco-go",
                "--puerto", str(puerto_eco),
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        assert esperar_puerto("127.0.0.1", puerto_tubo_go), f"Puerto del tubo relé Go ({puerto_tubo_go}) no abrió"

        try:
            resumen["eco"]["tubo_rele_go"] = await medir_eco_canal("127.0.0.1", puerto_tubo_go)
            print(f"  Go: RTT mediana = {resumen['eco']['tubo_rele_go']['rtt_mediana_ms']} ms, Ancho = {resumen['eco']['tubo_rele_go']['ancho_mb_s']} MB/s")
        finally:
            proc_tubo_go.terminate()
            proc_tubo_go.wait()
            proc_rele_go.terminate()
            proc_rele_go.wait()

        servidor_eco.close()
        await servidor_eco.wait_closed()

    asyncio.run(correr_mediciones_eco())

    # =========================================================================
    # PARTE 2: Medición de llama.cpp RPC (Qwen3.5-0.8B-Q8_0)
    # =========================================================================
    print("\n=======================================================")
    print("PARTE 2: LLAMA.CPP RPC (QWEN3.5-0.8B-Q8_0, 3 REPETICIONES)")
    print("=======================================================")

    # 2.1 Directo: ggml-rpc-server directo
    puerto_rpc_directo = puerto_libre(52300, 52349)
    proc_rpc_dir = subprocess.Popen(
        [str(RPC_SERVER), "-H", "127.0.0.1", "-p", str(puerto_rpc_directo), "-t", str(THREADS)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert esperar_puerto("127.0.0.1", puerto_rpc_directo), "RPC server directo no arrancó"
    try:
        resumen["llama_rpc"]["directo"] = medir_escenario_llama("directo", f"127.0.0.1:{puerto_rpc_directo}")
    finally:
        proc_rpc_dir.terminate()
        proc_rpc_dir.wait()

    # 2.2 Tubo + Relé Python
    puerto_rpc_py = puerto_libre(52350, 52399)
    puerto_rele_py = puerto_libre(52400, 52449)
    puerto_tubo_py = puerto_libre(52450, 52499)
    proc_rele_py = subprocess.Popen(
        [str(PYTHON_BIN), "-m", "fabric.rele.rele"],
        cwd=str(RAIZ),
        env=dict(env_py, PUERTO=str(puerto_rele_py), TUBO_PUERTO_BASE=str(puerto_tubo_py)),
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert esperar_puerto("127.0.0.1", puerto_rele_py), "Relé Python no arrancó"

    proc_rpc_py = subprocess.Popen(
        [str(RPC_SERVER), "-H", "127.0.0.1", "-p", str(puerto_rpc_py), "-t", str(THREADS)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert esperar_puerto("127.0.0.1", puerto_rpc_py), "RPC server para Python no arrancó"

    proc_tubo_py = subprocess.Popen(
        [
            str(PYTHON_BIN), "-m", "fabric.tubo",
            "--rele", f"ws://127.0.0.1:{puerto_rele_py}",
            "--clave", CLAVE,
            "--nombre", "rpc-trabajador-py",
            "--puerto", str(puerto_rpc_py),
        ],
        cwd=str(RAIZ),
        env=env_py,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert esperar_puerto("127.0.0.1", puerto_tubo_py), f"Puerto del tubo relé Python ({puerto_tubo_py}) no abrió"

    try:
        resumen["llama_rpc"]["tubo_rele_python"] = medir_escenario_llama("tubo+relé Python", f"127.0.0.1:{puerto_tubo_py}")
    finally:
        proc_tubo_py.terminate()
        proc_tubo_py.wait()
        proc_rpc_py.terminate()
        proc_rpc_py.wait()
        proc_rele_py.terminate()
        proc_rele_py.wait()

    # 2.3 Tubo + Relé Go
    puerto_rpc_go = puerto_libre(52500, 52549)
    puerto_rele_go = puerto_libre(52550, 52599)
    puerto_tubo_go = puerto_libre(52600, 52649)
    proc_rele_go = subprocess.Popen(
        [
            str(BIN_RELE_GO),
            "--puerto", str(puerto_rele_go),
            "--clave", CLAVE,
            "--tubo-puerto-base", str(puerto_tubo_go),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert esperar_puerto("127.0.0.1", puerto_rele_go), "Relé Go no arrancó"

    proc_rpc_go = subprocess.Popen(
        [str(RPC_SERVER), "-H", "127.0.0.1", "-p", str(puerto_rpc_go), "-t", str(THREADS)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert esperar_puerto("127.0.0.1", puerto_rpc_go), "RPC server para Go no arrancó"

    proc_tubo_go = subprocess.Popen(
        [
            str(BIN_TUBO_GO),
            "--rele", f"ws://127.0.0.1:{puerto_rele_go}",
            "--clave", CLAVE,
            "--nombre", "rpc-trabajador-go",
            "--puerto", str(puerto_rpc_go),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert esperar_puerto("127.0.0.1", puerto_tubo_go), f"Puerto del tubo relé Go ({puerto_tubo_go}) no abrió"

    try:
        resumen["llama_rpc"]["tubo_rele_go"] = medir_escenario_llama("tubo+relé Go", f"127.0.0.1:{puerto_tubo_go}")
    finally:
        proc_tubo_go.terminate()
        proc_tubo_go.wait()
        proc_rpc_go.terminate()
        proc_rpc_go.wait()
        proc_rele_go.terminate()
        proc_rele_go.wait()

    # 2.4 Go detrás de fabric/simulador/red.py con el perfil "mismo país"
    puerto_rpc_sim = puerto_libre(52650, 52699)
    puerto_rele_sim = puerto_libre(52700, 52749)
    puerto_proxy_sim = puerto_libre(52750, 52799)
    puerto_tubo_sim = puerto_libre(52800, 52849)

    proc_rele_sim = subprocess.Popen(
        [
            str(BIN_RELE_GO),
            "--puerto", str(puerto_rele_sim),
            "--clave", CLAVE,
            "--tubo-puerto-base", str(puerto_tubo_sim),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert esperar_puerto("127.0.0.1", puerto_rele_sim), "Relé Go para simulador no arrancó"

    proc_sim = subprocess.Popen(
        [
            str(PYTHON_BIN), "-m", "fabric.simulador.red",
            "--escuchar", f"127.0.0.1:{puerto_proxy_sim}",
            "--destino", f"127.0.0.1:{puerto_rele_sim}",
            "--perfil", "mismo país",
        ],
        cwd=str(RAIZ),
        env=env_py,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert esperar_puerto("127.0.0.1", puerto_proxy_sim), "Simulador de red no arrancó"

    proc_rpc_sim = subprocess.Popen(
        [str(RPC_SERVER), "-H", "127.0.0.1", "-p", str(puerto_rpc_sim), "-t", str(THREADS)],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert esperar_puerto("127.0.0.1", puerto_rpc_sim), "RPC server para simulador no arrancó"

    proc_tubo_sim = subprocess.Popen(
        [
            str(BIN_TUBO_GO),
            "--rele", f"ws://127.0.0.1:{puerto_proxy_sim}",
            "--clave", CLAVE,
            "--nombre", "rpc-trabajador-sim",
            "--puerto", str(puerto_rpc_sim),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    assert esperar_puerto("127.0.0.1", puerto_tubo_sim), f"Puerto del tubo relé Go con simulador ({puerto_tubo_sim}) no abrió"

    try:
        resumen["llama_rpc"]["go_simulador_mismo_pais"] = medir_escenario_llama(
            "Go detrás de simulador (mismo país)", f"127.0.0.1:{puerto_tubo_sim}"
        )
    finally:
        proc_tubo_sim.terminate()
        proc_tubo_sim.wait()
        proc_rpc_sim.terminate()
        proc_rpc_sim.wait()
        proc_sim.terminate()
        proc_sim.wait()
        proc_rele_sim.terminate()
        proc_rele_sim.wait()

    cargas_fin = obtener_cargas_sistema()
    resumen["carga_final"] = cargas_fin

    archivo_salida = RAIZ / "fabric" / "resultado_medicion_a15.json"
    with open(archivo_salida, "w", encoding="utf-8") as f:
        json.dump(resumen, f, indent=2, ensure_ascii=False)

    print(f"\nMediciones finalizadas exitosamente. Guardadas en {archivo_salida}")


if __name__ == "__main__":
    main()
