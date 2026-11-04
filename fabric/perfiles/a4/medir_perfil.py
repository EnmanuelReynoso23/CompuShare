"""Script para medir rendimiento de llama.cpp RPC directo vs tubo en local.
Soporta modo directo y modo tubo, 3 repeticiones, y reporte de timings.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request
from typing import Any

LLAMA_BIN = "/home/reyno/.local/opt/llama.cpp/build/bin"
MODELO = "/home/reyno/.cache/fabric-descargas/modelos/Qwen3.5-0.8B-Q8_0.gguf"
PYTHON = sys.executable

RPC_PORT = 50152
RELE_PORT = 18095
TUBO_BASE_PORT = 50100
LLAMA_PORT = 18080

PROMPT = "La capital de Francia es"
N_PREDICT = 24
TEMPERATURE = 0.0


PY_SPY = "/home/reyno/Proyectos/CompuShare/.venv/bin/py-spy"


def matar_proceso(p: subprocess.Popen | None, es_pyspy: bool = False) -> None:
    if p is None or p.poll() is not None:
        return
    try:
        if es_pyspy:
            p.send_signal(signal.SIGINT)
            p.wait(timeout=5)
            return
        p.terminate()
        p.wait(timeout=3)
    except Exception:
        try:
            p.kill()
            p.wait(timeout=2)
        except Exception:
            pass


class EntornoBenchmark:
    def __init__(self, modo: str, rpc_threads: int | None = None, llama_threads: int | None = None,
                 perfil: bool = False, pyspy: bool = False, pyspy_dir: str = ".", ctxcp_zero: bool = False) -> None:
        self.modo = modo
        self.rpc_threads = rpc_threads
        self.llama_threads = llama_threads
        self.perfil = perfil
        self.pyspy = pyspy
        self.pyspy_dir = pyspy_dir
        self.ctxcp_zero = ctxcp_zero
        self.p_rpc: subprocess.Popen | None = None
        self.p_rele: subprocess.Popen | None = None
        self.p_tubo: subprocess.Popen | None = None
        self.p_llama: subprocess.Popen | None = None

    def arrancar(self) -> None:
        print(f"=== Arrancando entorno en modo: {self.modo} (perfil={self.perfil}, pyspy={self.pyspy}) ===")
        # 1. ggml-rpc-server
        rpc_cmd = [f"{LLAMA_BIN}/ggml-rpc-server", "-H", "127.0.0.1", "-p", str(RPC_PORT)]
        if self.rpc_threads:
            rpc_cmd += ["-t", str(self.rpc_threads)]
        self.p_rpc = subprocess.Popen(rpc_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        time.sleep(0.5)

        if self.modo == "tubo":
            # 2. Rele
            env_rele = dict(os.environ, FABRIC_CLAVE="test_clave", PUERTO=str(RELE_PORT), TUBO_PUERTO_BASE=str(TUBO_BASE_PORT))
            if self.perfil:
                env_rele["FABRIC_PERFIL"] = "1"
            rele_cmd = [PYTHON, "-m", "fabric.rele.rele"]
            if self.pyspy:
                rele_svg = os.path.join(self.pyspy_dir, "perfil_rele.svg")
                rele_cmd = [PY_SPY, "record", "-i", "-r", "250", "-o", rele_svg, "--"] + rele_cmd
            self.p_rele = subprocess.Popen(rele_cmd, env=env_rele, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(1.0)

            # 3. Tubo
            env_tubo = dict(os.environ)
            if self.perfil:
                env_tubo["FABRIC_PERFIL"] = "1"
            tubo_cmd = [
                PYTHON, "-m", "fabric.tubo",
                "--rele", f"ws://127.0.0.1:{RELE_PORT}",
                "--clave", "test_clave",
                "--nombre", "trabajador-1",
                "--puerto", str(RPC_PORT),
            ]
            if self.pyspy:
                tubo_svg = os.path.join(self.pyspy_dir, "perfil_tubo.svg")
                tubo_cmd = [PY_SPY, "record", "-i", "-r", "250", "-o", tubo_svg, "--"] + tubo_cmd
            self.p_tubo = subprocess.Popen(tubo_cmd, env=env_tubo, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            # Esperar a que el tubo esté abierto en el relé
            for _ in range(60):
                try:
                    s = urllib.request.urlopen(f"http://127.0.0.1:{RELE_PORT}/salud", timeout=1)
                    salud = json.load(s)
                    if "trabajador-1" in salud.get("tubos", {}):
                        break
                except Exception:
                    pass
                time.sleep(0.2)
            rpc_target = f"127.0.0.1:{TUBO_BASE_PORT}"
        else:
            rpc_target = f"127.0.0.1:{RPC_PORT}"

        # 4. llama-server
        llama_cmd = [
            f"{LLAMA_BIN}/llama-server",
            "-m", MODELO,
            "--host", "127.0.0.1",
            "--port", str(LLAMA_PORT),
            "-ngl", "99",
            "--split-mode", "layer",
            "-c", "2048",
            "--parallel", "1",
            "--jinja",
            "--rpc", rpc_target,
        ]
        if self.ctxcp_zero:
            llama_cmd += ["-ctxcp", "0", "--cache-ram", "0"]
        if self.llama_threads:
            llama_cmd += ["-t", str(self.llama_threads)]
        
        self.p_llama = subprocess.Popen(llama_cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

        # Esperar a que llama-server esté listo
        listo = False
        for _ in range(100):
            if self.p_llama.poll() is not None:
                raise RuntimeError(f"llama-server terminó inesperadamente con código {self.p_llama.poll()}")
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{LLAMA_PORT}/health", timeout=1) as resp:
                    if resp.status == 200:
                        listo = True
                        break
            except Exception:
                pass
            time.sleep(0.2)

        if not listo:
            raise RuntimeError("llama-server no respondió a /health en el tiempo límite")
        print("llama-server listo.")

    def pedir(self, prompt: str = PROMPT, n_predict: int = N_PREDICT, temperature: float = TEMPERATURE) -> dict[str, Any]:
        req_data = json.dumps({"prompt": prompt, "n_predict": n_predict, "temperature": temperature}).encode("utf-8")
        req = urllib.request.Request(
            f"http://127.0.0.1:{LLAMA_PORT}/completion",
            data=req_data,
            headers={"Content-Type": "application/json"}
        )
        t0 = time.perf_counter()
        with urllib.request.urlopen(req, timeout=120) as resp:
            data = json.load(resp)
        duracion_total = time.perf_counter() - t0
        data["duracion_pared_s"] = duracion_total
        return data

    def detener(self) -> None:
        matar_proceso(self.p_llama)
        matar_proceso(self.p_tubo, es_pyspy=self.pyspy)
        matar_proceso(self.p_rele, es_pyspy=self.pyspy)
        matar_proceso(self.p_rpc)
        print("Entorno detenido limpiamente.")


def ejecutar_benchmark(modo: str, repeticiones: int = 3, rpc_threads: int | None = None,
                       llama_threads: int | None = None, perfil: bool = False,
                       pyspy: bool = False, pyspy_dir: str = ".", ctxcp_zero: bool = False,
                       prompt: str = PROMPT, n_predict: int = N_PREDICT, temperature: float = TEMPERATURE) -> list[dict[str, Any]]:
    env = EntornoBenchmark(modo, rpc_threads=rpc_threads, llama_threads=llama_threads,
                           perfil=perfil, pyspy=pyspy, pyspy_dir=pyspy_dir, ctxcp_zero=ctxcp_zero)
    resultados = []
    try:
        env.arrancar()
        for i in range(1, repeticiones + 1):
            print(f"[{modo}] Repetición {i}/{repeticiones}...")
            r = env.pedir(prompt=prompt, n_predict=n_predict, temperature=temperature)
            timings = r.get("timings", {})
            pred_tok_s = timings.get("predicted_per_second", 0)
            prompt_tok_s = timings.get("prompt_per_second", 0)
            pred_n = timings.get("predicted_n", 0)
            print(f"  Gen: {pred_tok_s:.2f} tok/s ({pred_n} tokens) | Prompt: {prompt_tok_s:.2f} tok/s | Pared: {r['duracion_pared_s']:.2f} s")
            resultados.append({
                "rep": i,
                "predicted_per_second": pred_tok_s,
                "prompt_per_second": prompt_tok_s,
                "predicted_n": pred_n,
                "prompt_n": timings.get("prompt_n", 0),
                "predicted_ms": timings.get("predicted_ms", 0),
                "prompt_ms": timings.get("prompt_ms", 0),
                "duracion_pared_s": r["duracion_pared_s"],
                "texto_truncado": r.get("content", "")[:60]
            })
    finally:
        env.detener()
    return resultados


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--modo", choices=["directo", "tubo", "ambos"], default="ambos")
    parser.add_argument("--prompt", type=str, default=PROMPT)
    parser.add_argument("--n-predict", type=int, default=N_PREDICT)
    parser.add_argument("--temperature", type=float, default=TEMPERATURE)
    parser.add_argument("--repeticiones", type=int, default=3)
    parser.add_argument("--rpc-threads", type=int, default=None)
    parser.add_argument("--llama-threads", type=int, default=None)
    parser.add_argument("--perfil", action="store_true", help="Activa contadores instrumentados en tubo y rele")
    parser.add_argument("--pyspy", action="store_true", help="Lanza py-spy sobre rele y tubo")
    parser.add_argument("--pyspy-dir", type=str, default="/home/reyno/Proyectos/cs-a4", help="Directorio de salida SVG py-spy")
    parser.add_argument("--ctxcp-zero", action="store_true", help="Agrega -ctxcp 0 --cache-ram 0")
    parser.add_argument("--guardar", type=str, default=None)
    args = parser.parse_args()

    modos = ["directo", "tubo"] if args.modo == "ambos" else [args.modo]
    todos = {}
    for m in modos:
        res = ejecutar_benchmark(m, repeticiones=args.repeticiones, rpc_threads=args.rpc_threads,
                                 llama_threads=args.llama_threads, perfil=args.perfil,
                                 pyspy=args.pyspy, pyspy_dir=args.pyspy_dir, ctxcp_zero=args.ctxcp_zero,
                                 prompt=args.prompt, n_predict=args.n_predict, temperature=args.temperature)
        gens = [x["predicted_per_second"] for x in res]
        gens_sorted = sorted(gens)
        mediana = gens_sorted[len(gens_sorted) // 2]
        todos[m] = {
            "mediciones": res,
            "mediana_gen_tok_s": mediana,
            "min_gen_tok_s": min(gens),
            "max_gen_tok_s": max(gens),
        }
        print(f"==> Modo {m}: Mediana Gen = {mediana:.2f} tok/s (min={min(gens):.2f}, max={max(gens):.2f})")

    if args.guardar:
        with open(args.guardar, "w", encoding="utf-8") as f:
            json.dump(todos, f, indent=2, ensure_ascii=False)
        print(f"Resultados guardados en {args.guardar}")
