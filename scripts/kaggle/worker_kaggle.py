"""Guión de automatización para Nodos Trabajadores en Kaggle / Google Colab.
Descarga y configura llama-server con modelos optimizados (Qwen 2.5 Coder)
y conecta la GPU al Relé Maestro de CompuShare.
"""

import os
import sys
import time
import shutil
import subprocess
from pathlib import Path

# Configuración por defecto para Kaggle (modificar según sea necesario)
RELE_URL_POR_DEFECTO = os.environ.get("COMPUSHARE_RELE", "wss://tu-rele.ngrok-free.app/ws/worker")
MODELO_NOMBRE = os.environ.get("COMPUSHARE_MODELO", "qwen2.5-coder-7b")
MODELO_URL = os.environ.get(
    "COMPUSHARE_MODELO_URL",
    "https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF/resolve/main/qwen2.5-coder-7b-instruct-q4_k_m.gguf"
)
LLAMA_SERVER_PORT = int(os.environ.get("LLAMA_SERVER_PORT", "8080"))


def ejecutar(cmd: str, check: bool = True):
    print(f">> [Kaggle Worker] Ejecutando: {cmd}")
    return subprocess.run(cmd, shell=True, check=check)


def preparar_entorno():
    print("=" * 70)
    print("   ⚡ COMPUSHARE: NODO TRABAJADOR DISTRIBUIDO (KAGGLE / COLAB) ⚡   ")
    print("=" * 70)

    # 1. Instalar dependencias Python
    print("\n[1/4] Instalando paquetes requeridos...")
    ejecutar("pip install -q websockets aiohttp pydantic rich typer huggingface_hub")

    # 2. Descargar modelo GGUF si no existe
    work_dir = Path("/kaggle/working" if Path("/kaggle/working").exists() else "./compushare_worker_data")
    work_dir.mkdir(parents=True, exist_ok=True)
    modelo_path = work_dir / "model.gguf"

    if not modelo_path.exists():
        print(f"\n[2/4] Descargando modelo GGUF optimizado...")
        ejecutar(f"curl -L -o {modelo_path} {MODELO_URL}")
    else:
        print(f"\n[2/4] Modelo ya disponible en: {modelo_path}")

    # 3. Descargar o compilar llama-server
    bin_dir = work_dir / "bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    llama_server = bin_dir / "llama-server"

    if not llama_server.exists():
        print("\n[3/4] Descargando release precompilado de llama.cpp con aceleración CUDA...")
        # Release oficial de llama.cpp para Linux x86_64 con CUDA 12
        url_llama = "https://github.com/ggerganov/llama.cpp/releases/download/b3900/llama-b3900-bin-ubuntu-x64.zip"
        zip_path = bin_dir / "llama.zip"
        try:
            ejecutar(f"curl -L -o {zip_path} {url_llama}")
            ejecutar(f"unzip -o {zip_path} -d {bin_dir}")
            llama_server.chmod(0o755)
        except Exception as e:
            print(f"Aviso al descargar binario: {e}. Se intentará con llama-cpp-python.")
            ejecutar("pip install llama-cpp-python --extra-index-url https://abetlen.github.io/llama-cpp-python/whl/cu121")

    # 4. Iniciar llama-server en segundo plano
    print(f"\n[4/4] Levantando servidor local de inferencia en puerto {LLAMA_SERVER_PORT}...")
    if llama_server.exists():
        cmd_server = (
            f"{llama_server} -m {modelo_path} --host 127.0.0.1 --port {LLAMA_SERVER_PORT} "
            f"-ngl 99 -c 4096 --cont-batching > {work_dir}/llama_server.log 2>&1 &"
        )
        ejecutar(cmd_server)
    else:
        # Fallback a servidor compatible con llama-cpp-python
        cmd_py = (
            f"python -m llama_cpp.server --model {modelo_path} --host 127.0.0.1 --port {LLAMA_SERVER_PORT} "
            f"--n_gpu_layers -1 --n_ctx 4096 > {work_dir}/llama_server.log 2>&1 &"
        )
        ejecutar(cmd_py)

    print("Esperando inicio de llama-server...")
    time.sleep(5)


def conectar_a_rele(rele_url: str):
    print("\n" + "=" * 70)
    print(f"Conectando al Relé Maestro CompuShare: {rele_url}")
    print("=" * 70)

    # Clonar repo CompuShare si no está instalado
    if not Path("src/compushare").exists():
        ejecutar("git clone https://github.com/EnmanuelReynoso23/CompuShare.git /tmp/compushare")
        sys.path.insert(0, "/tmp/compushare/src")
    else:
        sys.path.insert(0, str(Path("src").resolve()))

    from compushare.nodo.trabajador import NodoTrabajador
    import asyncio

    trabajador = NodoTrabajador(
        rele_url=rele_url,
        modelo=MODELO_NOMBRE,
        backend="llama.cpp",
        backend_url=f"http://127.0.0.1:{LLAMA_SERVER_PORT}",
        max_slots=2,
        tokens_per_second=35.0,
    )

    asyncio.run(trabajador.conectar())


if __name__ == "__main__":
    url = sys.argv[1] if len(sys.argv) > 1 else RELE_URL_POR_DEFECTO
    preparar_entorno()
    conectar_a_rele(url)
