"""Medición de decodificación especulativa MTP en local con llama.cpp RPC y simulador de red.

Tarea de Agente A7 (CompuShare).
Mide rendimiento para k = 0, 1, 2, 3, 4, 6 con perfil 'mismo país' (40 ms latencia, 50 Mb/s).
Compara la cabeza MTP en el trabajador (defecto) vs en el maestro (-ot).
"""
from __future__ import annotations

import asyncio
import json
import os
import signal
import subprocess
import sys
import threading
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Asegurar import de fabric
RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from fabric import calculo
from fabric.simulador.red import Enlace

MODELO = "/home/reyno/.cache/fabric-descargas/modelos/Qwen3.5-0.8B-MTP-Q8_0.gguf"
BIN_DIR = "/home/reyno/.local/opt/llama.cpp/build/bin"
LLAMA_SERVER = f"{BIN_DIR}/llama-server"
RPC_SERVER = f"{BIN_DIR}/ggml-rpc-server"

RPC_PORT = 55100
PROXY_PORT = 55200
HTTP_PORT = 55300

PROMPT = "La capital de Francia es"
N_PREDICT_CORTO = 16
N_PREDICT_LARGO = 48


def obtener_carga_sistema() -> dict[str, Any]:
    load1, load5, load15 = os.getloadavg()
    try:
        proc = subprocess.run(["ps", "-eo", "pid,pcpu,pmem,comm", "--sort=-pcpu"],
                              capture_output=True, text=True, timeout=5)
        top_procs = proc.stdout.splitlines()[:8]
    except Exception:
        top_procs = []
    return {
        "load_avg": [load1, load5, load15],
        "top_procesos": top_procs,
    }


def ejecutar_solicitud(puerto: int, n_predict: int) -> dict[str, Any]:
    url = f"http://127.0.0.1:{puerto}/completion"
    payload = json.dumps({
        "prompt": PROMPT,
        "n_predict": n_predict,
        "temperature": 0.0,
        "ignore_eos": True,
        "cache_prompt": False,
    }).encode("utf-8")
    req = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json"})
    t0 = time.perf_counter()
    with urllib.request.urlopen(req, timeout=120) as resp:
        cuerpo = json.loads(resp.read().decode("utf-8"))
    dur_pared_s = time.perf_counter() - t0
    cuerpo["duracion_pared_s"] = dur_pared_s
    return cuerpo


def medir_caso(k: int, mtp_en_maestro: bool) -> dict[str, Any]:
    print(f"\n--- Iniciando medicion: k={k}, mtp_en_maestro={mtp_en_maestro} ---")

    # 1. Iniciar ggml-rpc-server
    p_rpc = subprocess.Popen(
        [RPC_SERVER, "-H", "127.0.0.1", "-p", str(RPC_PORT), "-t", "4"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(0.5)

    # 2. Iniciar Enlace proxy en un bucle asyncio dedicado
    loop = asyncio.new_event_loop()
    enlace = Enlace(escuchar=PROXY_PORT, destino=RPC_PORT, perfil="mismo país")

    def correr_proxy():
        asyncio.set_event_loop(loop)
        loop.run_until_complete(enlace.iniciar())
        loop.run_forever()

    th_proxy = threading.Thread(target=correr_proxy, daemon=True)
    th_proxy.start()
    time.sleep(0.5)

    # 3. Preparar argumentos de llama-server
    # salida_en_el_maestro(0): -ot (token_embd|output)\.weight=CPU
    if mtp_en_maestro:
        ot_arg = r"(token_embd|output)\.weight|blk\.24\..*=CPU"
    else:
        ot_arg = r"(token_embd|output)\.weight=CPU"

    llama_cmd = [
        LLAMA_SERVER,
        "-m", MODELO,
        "--rpc", f"127.0.0.1:{PROXY_PORT}",
        "-ngl", "13",
        "-ot", ot_arg,
        "-t", "4",
        "--port", str(HTTP_PORT),
        "-c", "2048",
        "-ctxcp", "0",
        "--cache-ram", "0",
    ]

    if k == 0:
        llama_cmd += ["--spec-type", "none"]
    else:
        llama_cmd += [
            "--spec-type", "draft-mtp",
            "--spec-draft-n-max", str(k),
        ]

    log_path = f"/tmp/llama_server_k{k}_{'cpu' if mtp_en_maestro else 'rpc'}.log"
    log_file = open(log_path, "w")
    p_llama = subprocess.Popen(llama_cmd, stdout=log_file, stderr=subprocess.STDOUT)

    try:
        # Esperar a que el servidor este listo
        t0_espera = time.time()
        listo = False
        for i in range(150):
            if p_llama.poll() is not None:
                raise RuntimeError(f"llama-server salio anticipadamente con codigo {p_llama.poll()}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{HTTP_PORT}/health", timeout=1) as resp:
                    if resp.status == 200:
                        listo = True
                        break
            except Exception:
                pass
            time.sleep(1)
            if i % 10 == 0 and i > 0:
                print(f"  esperando arranque ({i}s, {enlace.estadisticas.bytes_totales / 1e6:.1f} MB)...")

        if not listo:
            raise TimeoutError("llama-server no respondio en 150s")

        t_arranque = time.time() - t0_espera
        print(f"  servidor listo en {t_arranque:.1f}s")

        # Calentamiento (8 tokens)
        print("  ejecutando calentamiento (8 tokens)...")
        ejecutar_solicitud(HTTP_PORT, n_predict=8)
        time.sleep(0.5)

        # Medicion Consulta A (16 tokens)
        b_antes_a = enlace.estadisticas.bytes_totales
        p_antes_a = enlace.estadisticas.paquetes_totales
        res_a = ejecutar_solicitud(HTTP_PORT, n_predict=N_PREDICT_CORTO)
        b_desp_a = enlace.estadisticas.bytes_totales
        p_desp_a = enlace.estadisticas.paquetes_totales
        time.sleep(0.5)

        # Medicion Consulta B (48 tokens)
        b_antes_b = enlace.estadisticas.bytes_totales
        p_antes_b = enlace.estadisticas.paquetes_totales
        res_b = ejecutar_solicitud(HTTP_PORT, n_predict=N_PREDICT_LARGO)
        b_desp_b = enlace.estadisticas.bytes_totales
        p_desp_b = enlace.estadisticas.paquetes_totales

        # Metricas de red
        delta_bytes_a = b_desp_a - b_antes_a
        delta_pkts_a = p_desp_a - p_antes_a
        delta_bytes_b = b_desp_b - b_antes_b
        delta_pkts_b = p_desp_b - p_antes_b

        # Trafico puro de generacion cancelando el prompt: B - A
        bytes_gen = delta_bytes_b - delta_bytes_a
        pkts_gen = delta_pkts_b - delta_pkts_a

        t_a = res_a["timings"]
        t_b = res_b["timings"]

        tokens_gen = t_b["predicted_n"] - t_a["predicted_n"]
        draft_acc = t_b.get("draft_n_accepted", 0) - t_a.get("draft_n_accepted", 0)
        draft_tot = t_b.get("draft_n", 0) - t_a.get("draft_n", 0)
        alfa = (draft_acc / draft_tot) if draft_tot > 0 else 0.0

        # Pasadas del modelo objetivo = tokens generados - tokens aceptados
        pasadas = tokens_gen - draft_acc
        palabras_x_pasada = tokens_gen / pasadas if pasadas > 0 else 1.0

        # Tiempos
        ms_gen = t_b["predicted_ms"] - t_a["predicted_ms"]
        ms_por_palabra = ms_gen / tokens_gen if tokens_gen > 0 else t_b.get("predicted_per_token_ms", 0.0)
        tok_s = 1000.0 / ms_por_palabra if ms_por_palabra > 0 else 0.0

        bytes_x_pasada = bytes_gen / pasadas if pasadas > 0 else 0.0
        mensajes_x_pasada = pkts_gen / pasadas if pasadas > 0 else 0.0

        bytes_x_palabra = bytes_gen / tokens_gen if tokens_gen > 0 else 0.0
        mensajes_x_palabra = pkts_gen / tokens_gen if tokens_gen > 0 else 0.0

        resultado = {
            "k": k,
            "mtp_en_maestro": mtp_en_maestro,
            "ms_por_palabra": round(ms_por_palabra, 2),
            "palabras_por_segundo": round(tok_s, 2),
            "alfa": round(alfa, 4),
            "palabras_por_pasada": round(palabras_x_pasada, 3),
            "draft_n": draft_tot,
            "draft_n_accepted": draft_acc,
            "bytes_por_pasada": round(bytes_x_pasada, 1),
            "mensajes_por_pasada": round(mensajes_x_pasada, 1),
            "bytes_por_palabra": round(bytes_x_palabra, 1),
            "mensajes_por_palabra": round(mensajes_x_palabra, 1),
            "timings_a": t_a,
            "timings_b": t_b,
            "trafico_a": {"bytes": delta_bytes_a, "paquetes": delta_pkts_a},
            "trafico_b": {"bytes": delta_bytes_b, "paquetes": delta_pkts_b},
            "tiempo_arranque_s": round(t_arranque, 1),
        }
        print(f"  Resultado: {ms_por_palabra:.1f} ms/palabra ({tok_s:.2f} tok/s), "
              f"alfa={alfa:.3f}, bytes/pasada={bytes_x_pasada:.0f}, msjes/pasada={mensajes_x_pasada:.0f}")
        return resultado

    finally:
        for p in (p_llama, p_rpc):
            try:
                p.terminate()
                p.wait(timeout=2)
            except Exception:
                try:
                    p.kill()
                except Exception:
                    pass
        log_file.close()
        try:
            asyncio.run_coroutine_threadsafe(enlace.detener(), loop).result(timeout=5)
        except Exception:
            pass
        loop.call_soon_threadsafe(loop.stop)
        time.sleep(1.0)


def ejecutar_mediciones() -> dict[str, Any]:
    carga = obtener_carga_sistema()
    fecha_iso = datetime.now(timezone.utc).isoformat()
    ks = [0, 1, 2, 3, 4, 6]

    print("==================================================")
    print(f"Iniciando mediciones A7 a las {fecha_iso}")
    print(f"Carga del sistema: loadavg={carga['load_avg']}")
    print("==================================================")

    # 1. MTP en el trabajador (defecto con salida_en_el_maestro)
    print("\n>>> SUITE 1: Cabeza MTP en el Trabajador (RPC0, por defecto)")
    resultados_rpc: list[dict[str, Any]] = []
    for k in ks:
        res = medir_caso(k, mtp_en_maestro=False)
        resultados_rpc.append(res)

    ms_sin_rpc = resultados_rpc[0]["ms_por_palabra"]
    for r in resultados_rpc:
        if r["k"] == 0:
            r["gamma"] = 0.0
        else:
            r["gamma"] = round(calculo.calibrar_mtp(ms_sin_rpc, r["ms_por_palabra"], r["alfa"], r["k"]), 4)

    # 2. MTP en el maestro (-ot blk.24)
    print("\n>>> SUITE 2: Cabeza MTP en el Maestro (CPU con -ot)")
    resultados_cpu: list[dict[str, Any]] = []
    for k in ks:
        res = medir_caso(k, mtp_en_maestro=True)
        resultados_cpu.append(res)

    ms_sin_cpu = resultados_cpu[0]["ms_por_palabra"]
    for r in resultados_cpu:
        if r["k"] == 0:
            r["gamma"] = 0.0
        else:
            r["gamma"] = round(calculo.calibrar_mtp(ms_sin_cpu, r["ms_por_palabra"], r["alfa"], r["k"]), 4)

    datos_completos = {
        "fecha": fecha_iso,
        "carga_inicial": carga,
        "modelo": MODELO,
        "perfil_red": "mismo país (40 ms ida, 10 ms jitter, 50 Mb/s)",
        "salida_en_el_maestro": True,
        "suite_mtp_trabajador": resultados_rpc,
        "suite_mtp_maestro": resultados_cpu,
    }

    # Guardar resultados
    salida_json = RAIZ / "fabric" / "resultados_a7.json"
    with open(salida_json, "w", encoding="utf-8") as f:
        json.dump(datos_completos, f, indent=2, ensure_ascii=False)
    print(f"\nResultados guardados en {salida_json}")

    return datos_completos


if __name__ == "__main__":
    ejecutar_mediciones()
