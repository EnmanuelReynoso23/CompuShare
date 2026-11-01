"""Sube a Kaggle los cuadernos-nodo (GPU T4 x2) y los pone a correr.

    python fabric/kaggle/lanzar.py               # los dos nodos (cada uno con su modelo entero)
    python fabric/kaggle/lanzar.py calidad       # solo uno
    python fabric/kaggle/lanzar.py --dividido    # UN modelo repartido entre dos cuadernos (llama.cpp RPC)
    python fabric/kaggle/lanzar.py --estado      # cómo van
    python fabric/kaggle/lanzar.py --bajar DIR   # traer logs y resumen.json

Lee la clave de ~/.config/synrag/fabric.env (FABRIC_CLAVE). El cuaderno `calidad` aloja el relé
(túnel de Cloudflare) y publica su dirección; los demás la buscan (fabric/descubrir.py). Nada pasa
por Oracle. Los cuadernos son PRIVADOS: la clave va dentro del código del cuaderno.
"""

from __future__ import annotations

import base64
import io
import json
import os
import re
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
RAIZ = AQUI.parent.parent  # raíz del repo (la que contiene fabric/)
USUARIO = os.environ.get("KAGGLE_USUARIO", "enmanuelreynoso")
LLAMA_CPP = "b12024"  # release de llama.cpp con binarios CUDA 12.8 (31-oct-2026)
CLOUDFLARED = "2026.10.0"  # túnel del relé; versión fija con semanas de uso

NODOS = {
    # El más nuevo que cabe en 2 x T4 (30 GB): denso, mejor calidad, más lento.
    # Además hace embeddings y reordenado (le sobra VRAM).
    "calidad": {
        "nombre": "kaggle-qwen3.8-27b",
        "rele": "aqui",  # este cuaderno aloja el relé; dura 10 min más que los otros
        "modelo_repo": "unsloth/Qwen3.8-27B-GGUF",
        "modelo_archivo": "Qwen3.8-27B-UD-Q4_K_M.gguf",
        "modelo_nombre": "Qwen3.8-27B",
        "embeber": "Qwen/Qwen3-Embedding-0.6B",
        "reordenar": "BAAI/bge-reranker-v2-m3",
        "contexto": 16384,
        "paralelo": 2,
    },
    # MoE con 3B activos: casi tan bueno y varias veces más rápido.
    "rapido": {
        "nombre": "kaggle-qwen3.6-35b-a3b",
        "rele": "buscar",
        "modelo_repo": "unsloth/Qwen3.6-35B-A3B-GGUF",
        "modelo_archivo": "Qwen3.6-35B-A3B-UD-Q4_K_M.gguf",
        "modelo_nombre": "Qwen3.6-35B-A3B",
        "embeber": "",
        "reordenar": "",
        "contexto": 16384,
        "paralelo": 2,
    },
    # Un solo modelo en dos computadoras: el maestro aloja el relé y llama-server; el trabajador
    # presta sus 2 GPU con ggml-rpc-server por el tubo del relé. Las capas se reparten según la
    # memoria libre de las 4 GPU (mitad y mitad). Modelo chico a propósito: la parte que no está en
    # la caché del trabajador cruza el túnel de Cloudflare.
    "maestro": {
        "rol": "maestro",
        "nombre": "kaggle-qwen3.5-9b-2-equipos",
        "rele": "aqui",
        "tubo": "trabajador-1",
        "espera_trabajador_min": 45,
        "modelo_repo": "unsloth/Qwen3.5-9B-GGUF",
        "modelo_archivo": "Qwen3.5-9B-Q8_0.gguf",
        "modelo_nombre": "Qwen3.5-9B (2 equipos)",
        "embeber": "",
        "reordenar": "",
        "contexto": 16384,
        "paralelo": 1,
    },
    "trabajador": {
        "rol": "trabajador",
        "nombre": "kaggle-trabajador-1",
        "rele": "buscar",
        "tubo": "trabajador-1",
        "modelo_repo": "unsloth/Qwen3.5-9B-GGUF",
        "modelo_archivo": "Qwen3.5-9B-Q8_0.gguf",
        "modelo_nombre": "",
        "embeber": "",
        "reordenar": "",
        "contexto": 0,
        "paralelo": 0,
    },
}
GRUPOS = {"normal": ["calidad", "rapido"], "dividido": ["maestro", "trabajador"]}


def entorno_red() -> dict[str, str]:
    valores: dict[str, str] = {}
    for linea in (Path.home() / ".config/synrag/fabric.env").read_text().splitlines():
        m = re.match(r"(?:export\s+)?(\w+)=(.*)", linea.strip())
        if m:
            valores[m.group(1)] = m.group(2).strip().strip("'\"")
    return valores


def codigo_empaquetado() -> str:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for ruta in ["fabric/__init__.py", "fabric/cliente.py", "fabric/descubrir.py", "fabric/nodo/__init__.py",
                     "fabric/nodo/nodo.py", "fabric/nodo/motores.py", "fabric/rele/__init__.py", "fabric/rele/rele.py",
                     "fabric/tubo.py", "fabric/rpc_cache.py"]:
            tar.add(RAIZ / ruta, arcname=ruta)
    return base64.b64encode(buf.getvalue()).decode()


def slug(clave: str) -> str:
    return f"synrag-red-nodo-{clave}"


def lanzar(clave: str, duracion_min: int, enjambre: str) -> None:
    red = entorno_red()
    if NODOS[clave]["rele"] == "aqui":
        duracion_min += 10
    if NODOS[clave].get("rol") in ("maestro", "trabajador"):
        duracion_min += 15  # el maestro espera al trabajador; el trabajador debe durar más que el maestro
    if NODOS[clave].get("rol") == "trabajador":
        duracion_min += 15
    config = dict(NODOS[clave], clave=red["FABRIC_CLAVE"], enjambre=enjambre,
                  duracion_min=duracion_min, llama_cpp=LLAMA_CPP, cloudflared=CLOUDFLARED)
    script = (AQUI / "nodo_kaggle.py").read_text()
    script = script.replace("__CONFIG__", base64.b64encode(json.dumps(config).encode()).decode())
    script = script.replace("__CODIGO__", codigo_empaquetado())
    with tempfile.TemporaryDirectory() as carpeta:
        Path(carpeta, "nodo.py").write_text(script)
        Path(carpeta, "kernel-metadata.json").write_text(json.dumps({
            "id": f"{USUARIO}/{slug(clave)}",
            "title": slug(clave),
            "code_file": "nodo.py",
            "language": "python",
            "kernel_type": "script",
            "is_private": True,
            "enable_gpu": True,
            "enable_tpu": False,
            "enable_internet": True,
            "dataset_sources": [],
            "competition_sources": [],
            "kernel_sources": [],
        }, indent=1))
        r = subprocess.run(["kaggle", "kernels", "push", "-p", carpeta, "--accelerator", "NvidiaTeslaT4"],
                           text=True, capture_output=True)
        print(f"[{clave}]", (r.stdout + r.stderr).strip())


def estado() -> None:
    for clave in NODOS:
        r = subprocess.run(["kaggle", "kernels", "status", f"{USUARIO}/{slug(clave)}"], text=True, capture_output=True)
        print(f"[{clave}]", (r.stdout + r.stderr).strip().splitlines()[-1:])


def bajar(destino: str) -> None:
    for clave in NODOS:
        carpeta = Path(destino, clave)
        carpeta.mkdir(parents=True, exist_ok=True)
        subprocess.run(["kaggle", "kernels", "output", f"{USUARIO}/{slug(clave)}", "-p", str(carpeta)],
                       text=True, capture_output=True)
        print(f"[{clave}]", sorted(p.name for p in carpeta.iterdir()))


if __name__ == "__main__":
    args = sys.argv[1:]
    if args[:1] == ["--estado"]:
        estado()
    elif args[:1] == ["--bajar"]:
        bajar(args[1] if len(args) > 1 else "resultados")
    else:
        cuales = [a for a in args if a in NODOS] or GRUPOS["dividido" if "--dividido" in args else "normal"]
        duracion = int(os.environ.get("DURACION_MIN", "50"))
        for clave in cuales:
            lanzar(clave, duracion, os.environ.get("FABRIC_ENJAMBRE", "publico"))
