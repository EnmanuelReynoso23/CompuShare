# Nodo de la red SynRAG corriendo en un cuaderno de Kaggle con GPU.
# Lo arma `lanzar.sh`: CONFIG y CODIGO se rellenan al empaquetar (no editar aquí).
#
# 1. Baja llama.cpp ya compilado con CUDA (si no corre en esta GPU, lo compila).
# 2. Baja el modelo GGUF de Hugging Face (en paralelo con el paso 1).
# 3. Levanta llama-server y entra a la red como nodo hasta que se acabe DURACION.
# Todo lo que pasa queda en el log del cuaderno y en /kaggle/working/resumen.json.

import base64
import io
import json
import os
import shutil
import subprocess
import sys
import tarfile
import threading
import time
import urllib.request

CONFIG = json.loads(base64.b64decode("__CONFIG__"))
CODIGO = "__CODIGO__"

INICIO = time.time()
SALIDA = "/kaggle/working"
TRABAJO = "/tmp/fabric"
os.makedirs(TRABAJO, exist_ok=True)
resumen = {"config": {k: v for k, v in CONFIG.items() if k != "clave"}, "etapas": {}}


def log(*a):
    print(f"[{time.time() - INICIO:7.1f} s]", *a, flush=True)


def etapa(nombre, inicio):
    resumen["etapas"][nombre] = round(time.time() - inicio, 1)
    log(f"etapa {nombre}: {resumen['etapas'][nombre]} s")


def guardar_resumen():
    with open(f"{SALIDA}/resumen.json", "w") as f:
        json.dump(resumen, f, indent=2, ensure_ascii=False)


def sh(cmd, **kw):
    return subprocess.run(cmd, shell=True, text=True, capture_output=True, **kw)


# ── 0. equipo ───────────────────────────────────────────────────────────────
r = sh("nvidia-smi --query-gpu=name,memory.total,compute_cap,driver_version --format=csv,noheader")
log("GPU:", r.stdout.strip() or r.stderr.strip())
gpus = [l.split(", ") for l in r.stdout.strip().splitlines() if l]
resumen["gpus"] = r.stdout.strip().splitlines()
capacidad = gpus[0][2].replace(".", "") if gpus else "75"
log("disco:", sh(f"df -h /tmp {SALIDA} | tail -2").stdout.strip())

# ── 1. código de la red ─────────────────────────────────────────────────────
t = time.time()
with tarfile.open(fileobj=io.BytesIO(base64.b64decode(CODIGO)), mode="r:gz") as tar:
    tar.extractall(f"{TRABAJO}/codigo")
sys.path.insert(0, f"{TRABAJO}/codigo")
sh(f"{sys.executable} -m pip install -q 'websockets>=15' 'httpx>=0.27'")
etapa("codigo", t)

# ── 1b. el relé vive en este cuaderno (si le toca) ─────────────────────────
# Kaggle no acepta conexiones entrantes: el relé sale por un túnel rápido de Cloudflare
# (https://*.trycloudflare.com, cambia en cada arranque) y la dirección se publica en ntfy.sh
# bajo un tema que sale de la clave (fabric/descubrir.py).
RELE_LOCAL = "ws://127.0.0.1:8095"
if CONFIG["rele"] == "aqui":
    import re  # noqa: E402

    from fabric import descubrir  # noqa: E402

    t = time.time()
    rele_log = open(f"{SALIDA}/rele.log", "w")
    proceso_rele = subprocess.Popen(
        [sys.executable, "rele.py"], cwd=f"{TRABAJO}/codigo/fabric/rele", stdout=rele_log, stderr=subprocess.STDOUT,
        env=dict(os.environ, FABRIC_CLAVE=CONFIG["clave"], PUERTO="8095"),
    )
    binario_cf = f"{TRABAJO}/cloudflared"
    urllib.request.urlretrieve(
        f"https://github.com/cloudflare/cloudflared/releases/download/{CONFIG['cloudflared']}/cloudflared-linux-amd64", binario_cf
    )
    os.chmod(binario_cf, 0o755)
    cf_log_ruta = f"{SALIDA}/cloudflared.log"
    cf_log = open(cf_log_ruta, "w")
    proceso_cf = subprocess.Popen(
        [binario_cf, "tunnel", "--no-autoupdate", "--url", "http://127.0.0.1:8095"], stdout=cf_log, stderr=subprocess.STDOUT
    )
    publico = None
    for _ in range(120):
        m = re.search(r"https://[a-z0-9-]+\.trycloudflare\.com", open(cf_log_ruta).read())
        if m:
            publico = m.group(0)
            break
        time.sleep(1)
    if not publico:
        raise SystemExit("cloudflared no dio dirección; ver cloudflared.log")
    for _ in range(60):  # el nombre tarda unos segundos en resolver
        try:
            with urllib.request.urlopen(publico + "/salud", timeout=5) as rr:
                if rr.status == 200:
                    break
        except Exception:
            time.sleep(2)
    url_publica = "wss://" + publico.removeprefix("https://")
    descubrir.publicar(url_publica, CONFIG["clave"])
    resumen["rele_publico"] = url_publica
    log("relé publicado en", url_publica)
    etapa("rele", t)

# ── 2 y 3 en paralelo: llama.cpp y el modelo ───────────────────────────────
estado = {}


def preparar_llama():
    t0 = time.time()
    version = CONFIG["llama_cpp"]
    base = f"https://github.com/ggml-org/llama.cpp/releases/download/{version}"
    destino = f"{TRABAJO}/llama"
    os.makedirs(destino, exist_ok=True)
    try:
        for archivo in (f"llama-{version}-bin-ubuntu-cuda-12.8-x64.tar.gz", f"cudart-llama-{version}-bin-ubuntu-cuda-12.8-x64.tar.gz"):
            urllib.request.urlretrieve(f"{base}/{archivo}", f"{TRABAJO}/{archivo}")
            with tarfile.open(f"{TRABAJO}/{archivo}") as tar:
                tar.extractall(destino)
            os.remove(f"{TRABAJO}/{archivo}")
        servidores = sh(f"find {destino} -name llama-server -type f").stdout.split()
        if servidores:
            binario = servidores[0]
            carpeta = os.path.dirname(binario)
            libs = sorted({os.path.dirname(p) for p in sh(f"find {destino} -name '*.so*'").stdout.split()})
            entorno = dict(os.environ, LD_LIBRARY_PATH=":".join(libs + [os.environ.get("LD_LIBRARY_PATH", "")]))
            os.chmod(binario, 0o755)
            prueba = subprocess.run([binario, "--list-devices"], text=True, capture_output=True, env=entorno, cwd=carpeta)
            log("llama.cpp ya compilado:", (prueba.stdout + prueba.stderr).strip()[-400:])
            if prueba.returncode == 0 and "CUDA" in prueba.stdout + prueba.stderr:
                estado["llama"] = (binario, entorno)
                estado["llama_origen"] = "binario oficial"
                etapa("llama_cpp", t0)
                return
    except Exception as e:
        log("binario oficial no sirvió:", e)
    # Plan B: compilar solo llama-server para esta GPU.
    log(f"compilando llama.cpp {version} para sm_{capacidad}")
    fuente = f"{TRABAJO}/llama-fuente"
    pasos = [
        f"git clone -q --depth 1 --branch {version} https://github.com/ggml-org/llama.cpp {fuente}",
        f"cmake -S {fuente} -B {fuente}/build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES={capacidad} -DLLAMA_CURL=OFF -DCMAKE_BUILD_TYPE=Release",
        f"cmake --build {fuente}/build --target llama-server -j{os.cpu_count()}",
    ]
    for paso in pasos:
        r = sh(paso)
        if r.returncode != 0:
            estado["error_llama"] = (r.stdout + r.stderr)[-2000:]
            log("falló:", paso, estado["error_llama"])
            return
    estado["llama"] = (f"{fuente}/build/bin/llama-server", dict(os.environ))
    estado["llama_origen"] = "compilado"
    etapa("llama_cpp", t0)


def bajar_modelo():
    t0 = time.time()
    try:
        from huggingface_hub import hf_hub_download

        estado["modelo"] = hf_hub_download(CONFIG["modelo_repo"], CONFIG["modelo_archivo"], local_dir=f"{TRABAJO}/modelos")
        tam = os.path.getsize(estado["modelo"]) / 1e9
        log(f"modelo listo: {CONFIG['modelo_archivo']} ({tam:.1f} GB)")
        resumen["modelo_gb"] = round(tam, 1)
        etapa("modelo", t0)
    except Exception as e:
        estado["error_modelo"] = repr(e)
        log("no se pudo bajar el modelo:", e)


hilos = [threading.Thread(target=preparar_llama), threading.Thread(target=bajar_modelo)]
for h in hilos:
    h.start()
for h in hilos:
    h.join()
resumen["llama_origen"] = estado.get("llama_origen")
if "llama" not in estado or "modelo" not in estado:
    resumen["error"] = estado.get("error_llama") or estado.get("error_modelo")
    guardar_resumen()
    raise SystemExit("No hay llama.cpp o modelo; ver el log.")

# ── 4. llama-server ─────────────────────────────────────────────────────────
t = time.time()
binario, entorno = estado["llama"]
comando = [
    binario,
    "-m", estado["modelo"],
    "--host", "127.0.0.1",
    "--port", "8080",
    "-ngl", "999",
    "--split-mode", "layer",
    "-c", str(CONFIG["contexto"]),
    "--parallel", str(CONFIG["paralelo"]),
    "--jinja",
]
log("arrancando:", " ".join(comando[1:]))
registro = open(f"{SALIDA}/llama-server.log", "w")
servidor = subprocess.Popen(comando, stdout=registro, stderr=subprocess.STDOUT, env=entorno, cwd=os.path.dirname(binario))
listo = False
for _ in range(900):
    if servidor.poll() is not None:
        break
    try:
        with urllib.request.urlopen("http://127.0.0.1:8080/health", timeout=2) as rr:
            if rr.status == 200:
                listo = True
                break
    except Exception:
        pass
    time.sleep(1)
if not listo:
    registro.flush()
    resumen["error"] = "llama-server no arrancó: " + open(f"{SALIDA}/llama-server.log").read()[-3000:]
    guardar_resumen()
    raise SystemExit(resumen["error"])
etapa("llama_server", t)
log("VRAM en uso:", sh("nvidia-smi --query-gpu=memory.used,memory.total --format=csv,noheader").stdout.strip())

# ── 4b. diagnóstico: capas en GPU y velocidad medida aquí mismo, sin red ───
diagnostico = {"llama": str(estado.get("llama_origen"))}
for linea in open(f"{SALIDA}/llama-server.log", errors="replace"):
    if "offloaded" in linea:
        diagnostico["capas_gpu"] = linea.strip().split("offloaded", 1)[1].strip()[:60]
try:
    pedido = urllib.request.Request(
        "http://127.0.0.1:8080/completion",
        data=json.dumps({"prompt": "Explica en tres frases qué es un índice invertido.", "n_predict": 128}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(pedido, timeout=600) as rr:
        tiempos = json.load(rr).get("timings", {})
    diagnostico["local_tok_s"] = round(tiempos.get("predicted_per_second", 0), 1)
    diagnostico["local_prompt_tok_s"] = round(tiempos.get("prompt_per_second", 0), 1)
except Exception as e:
    diagnostico["local_error"] = repr(e)[:100]
resumen["diagnostico"] = diagnostico
log("diagnóstico:", diagnostico)

# ── 5. dentro de la red ─────────────────────────────────────────────────────
import asyncio  # noqa: E402
import logging  # noqa: E402

from fabric.nodo.nodo import argumentos, correr  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
args = [
    "--rele", RELE_LOCAL if CONFIG["rele"] == "aqui" else "buscar",
    "--clave", CONFIG["clave"],
    "--enjambre", CONFIG["enjambre"],
    "--nombre", CONFIG["nombre"],
    "--eco",
    "--llm-url", "http://127.0.0.1:8080",
    "--llm-modelo", CONFIG["modelo_nombre"],
    "--llm-concurrencia", str(CONFIG["paralelo"]),
    "--duracion", str(CONFIG["duracion_min"] * 60),
]
for k, v in diagnostico.items():
    args += ["--info", f"{k}={v}"]
if CONFIG.get("embeber"):
    args += ["--embeber", CONFIG["embeber"]]
if CONFIG.get("reordenar"):
    args += ["--reordenar", CONFIG["reordenar"]]
t = time.time()
nodo = asyncio.run(correr(argumentos(args)))
resumen["tareas_atendidas"] = nodo.atendidas
resumen["tareas_fallidas"] = nodo.fallidas
etapa("en_la_red", t)
servidor.terminate()
registro.close()
shutil.copy(f"{SALIDA}/llama-server.log", f"{SALIDA}/llama-server-final.log")
guardar_resumen()
log("fin")
