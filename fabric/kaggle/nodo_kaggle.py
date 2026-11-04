# Nodo de la red SynRAG corriendo en un cuaderno de Kaggle con GPU.
# Lo arma `lanzar.sh`: CONFIG y CODIGO se rellenan al empaquetar (no editar aquí).
#
# 1. Baja llama.cpp ya compilado con CUDA (si no corre en esta GPU, lo compila).
# 2. Baja el modelo GGUF de Hugging Face (en paralelo con el paso 1).
# 3. Levanta llama-server y entra a la red como nodo hasta que se acabe DURACION.
#
# Roles (CONFIG["rol"]): "nodo" (modelo entero aquí), "maestro" (aloja el relé y reparte las capas
# entre sus GPU y las del trabajador, vía llama.cpp RPC por el tubo del relé) y "trabajador" (presta
# sus GPU con ggml-rpc-server, que solo escucha en su 127.0.0.1, y las ofrece por el relé).
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
    pasos[1] += " -DGGML_RPC=ON"
    for paso in pasos:
        r = sh(paso)
        if r.returncode != 0:
            estado["error_llama"] = (r.stdout + r.stderr)[-2000:]
            log("falló:", paso, estado["error_llama"])
            return
    sh(f"cmake --build {fuente}/build --target ggml-rpc-server -j{os.cpu_count()}")  # solo lo usa el trabajador
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

ROL = CONFIG.get("rol", "nodo")
binario, entorno = estado["llama"]


def salud_local():
    with urllib.request.urlopen("http://127.0.0.1:8095/salud", timeout=5) as rr:
        return json.load(rr)


# ── 4t. trabajador: presta sus GPU por el tubo y no hace nada más ──────────
def trabajador():
    import asyncio
    import logging
    import socket

    from fabric import rpc_cache
    from fabric.tubo import Tubo

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    t0 = time.time()
    candidatos = sh(f"find {TRABAJO} -type f \\( -name ggml-rpc-server -o -name rpc-server \\)").stdout.split()
    if not candidatos:
        resumen["error"] = "no hay ggml-rpc-server en esta compilación de llama.cpp"
        guardar_resumen()
        raise SystemExit(resumen["error"])
    rpc_bin = candidatos[0]
    os.chmod(rpc_bin, 0o755)
    # La caché de ggml-rpc-server se arma desde el GGUF que bajamos: los pesos no cruzan el túnel.
    cache = f"{TRABAJO}/llcache"
    try:
        lleno = rpc_cache.llenar(estado["modelo"], f"{cache}/rpc")
        resumen["cache_rpc"] = {"tensores": lleno["tensores"], "gb": round(lleno["bytes"] / 1e9, 2)}
    except Exception as e:
        resumen["cache_rpc"] = {"error": repr(e)[:300]}
    log("caché RPC:", resumen["cache_rpc"])
    etapa("cache_rpc", t0)
    dispositivos = ",".join(f"CUDA{i}" for i in range(len(gpus))) or "CPU"
    rpc_log = open(f"{SALIDA}/rpc-server.log", "w")
    proceso = subprocess.Popen(
        [rpc_bin, "-d", dispositivos, "-H", "127.0.0.1", "-p", "50052", "-c"], stdout=rpc_log,
        stderr=subprocess.STDOUT, env=dict(entorno, LLAMA_CACHE=cache), cwd=os.path.dirname(rpc_bin),
    )
    for _ in range(120):
        try:
            socket.create_connection(("127.0.0.1", 50052), timeout=1).close()
            break
        except OSError:
            if proceso.poll() is not None:
                break
            time.sleep(1)
    if proceso.poll() is not None:
        rpc_log.flush()
        resumen["error"] = "ggml-rpc-server no arrancó: " + open(f"{SALIDA}/rpc-server.log").read()[-2000:]
        guardar_resumen()
        raise SystemExit(resumen["error"])
    log(f"ggml-rpc-server en 127.0.0.1:50052 con {dispositivos}")

    async def vivir():
        vram = round(sum(float(g[1].split()[0]) for g in gpus) / 1024, 1) if gpus else 0
        tubo = Tubo("buscar", CONFIG["clave"], CONFIG["tubo"], 50052, info={"vram_gb": vram})
        tarea = asyncio.create_task(tubo.vivir())
        fin = time.time() + CONFIG["duracion_min"] * 60
        while time.time() < fin and proceso.poll() is None:
            await asyncio.sleep(60)
            log(f"tubo {CONFIG['tubo']}: {tubo.bytes / 1e6:.1f} MB, {tubo.conexiones} conexiones")
        tarea.cancel()
        return tubo

    t0 = time.time()
    tubo = asyncio.run(vivir())
    resumen["tubo_mb"] = round(tubo.bytes / 1e6, 1)
    resumen["tubo_conexiones"] = tubo.conexiones
    resumen["rpc_server_vivo"] = proceso.poll() is None
    etapa("prestando_gpu", t0)
    proceso.terminate()
    rpc_log.close()
    guardar_resumen()


if ROL == "trabajador":
    trabajador()
    log("fin")
    sys.exit(0)

# ── 4m. maestro: espera a los trabajadores y elige cuáles usar (fabric/reparto.py) ─
puerto_tubo = None
rpc = []
if ROL == "maestro":
    from fabric import reparto

    t = time.time()
    prefijo = CONFIG.get("tubo_prefijo", "trabajador-")
    minimo = CONFIG.get("trabajadores_min", 1)
    limite = time.time() + CONFIG.get("espera_trabajador_min", 45) * 60
    tubos, gracia = {}, None
    while time.time() < limite:
        try:
            tubos = {n: d for n, d in salud_local().get("tubos", {}).items() if n.startswith(prefijo)}
        except Exception:
            pass
        if len(tubos) >= minimo:
            gracia = gracia or time.time() + CONFIG.get("gracia_s", 60)  # por si llegan más
            if time.time() >= gracia:
                break
        time.sleep(5)
    candidatos = [reparto.Candidato(n, d["puerto"], float((d.get("info") or {}).get("vram_gb", 0)))
                  for n, d in tubos.items()]
    for cand in candidatos:
        cand.rtt_ms = reparto.medir_rtt(cand.puerto)
    resumen["trabajadores"] = {cand.nombre: {"vram_gb": cand.vram_gb, "rtt_ms": round(cand.rtt_ms, 1)} for cand in candidatos}
    log("trabajadores:", resumen["trabajadores"])
    propio_gb = sum(float(g[1].split()[0]) for g in gpus) / 1024 if gpus else 0
    try:
        elegidos = reparto.elegir(candidatos, os.path.getsize(estado["modelo"]) / 1e9, propio_gb, minimo=minimo)
    except ValueError as e:
        resumen["error"] = str(e)
        guardar_resumen()
        raise SystemExit(resumen["error"])
    rpc = [f"127.0.0.1:{cand.puerto}" for cand in elegidos]
    puerto_tubo = rpc[0] if rpc else None
    resumen["elegidos"] = [cand.nombre for cand in elegidos]
    log("elegidos:", resumen["elegidos"])
    etapa("esperar_trabajador", t)
    tubo_antes = salud_local()["tubo_bytes"]

# ── 4. llama-server ─────────────────────────────────────────────────────────
t = time.time()
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
if rpc:
    # Sin puntos de control ni caché de prompts: en modelos híbridos (Qwen3.5) copian el estado capa por
    # capa con GET_TENSOR, cada uno espera una ida y vuelta por internet. Medido en local con 60 ms de
    # latencia: de 37-85 esperas por pregunta a 1, y la lectura del prompt de 3,3 a 15,6 tok/s.
    comando += ["--rpc", ",".join(rpc), "-ctxcp", "0", "--cache-ram", "0"]
    # La tabla de salida en el maestro: si no, vuelve el vocabulario entero por la red en cada palabra.
    from fabric.reparto import salida_en_el_maestro
    comando += salida_en_el_maestro(len(gpus))
if CONFIG.get("especulativo"):
    comando += ["--spec-type", CONFIG["especulativo"]]
log("arrancando:", " ".join(comando[1:]))
registro = open(f"{SALIDA}/llama-server.log", "w")
servidor = subprocess.Popen(comando, stdout=registro, stderr=subprocess.STDOUT, env=entorno, cwd=os.path.dirname(binario))
listo = False
for _ in range(3600 if puerto_tubo else 900):
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
import re  # noqa: E402

diagnostico = {"llama": str(estado.get("llama_origen"))}
reparto = []
for linea in open(f"{SALIDA}/llama-server.log", errors="replace"):
    if "offloaded" in linea:
        diagnostico["capas_gpu"] = linea.strip().split("offloaded", 1)[1].strip()[:60]
    m = re.search(r"(\S+) model buffer size =\s*([\d.]+) MiB", linea)
    if m:
        reparto.append(f"{m.group(1)}={float(m.group(2)) / 1024:.1f}GB")
if reparto:
    diagnostico["reparto"] = " ".join(reparto)
if puerto_tubo:
    diagnostico["equipos"] = 1 + len(rpc)
    diagnostico["rtt_ms"] = " ".join(f"{v['rtt_ms']}" for k, v in resumen["trabajadores"].items() if k in resumen["elegidos"])
    diagnostico["tubo_carga_mb"] = round((salud_local()["tubo_bytes"] - tubo_antes) / 1e6, 1)
    tubo_antes = salud_local()["tubo_bytes"]
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
    if tiempos.get("draft_n"):
        diagnostico["aciertos_mtp"] = f"{tiempos.get('draft_n_accepted', 0)}/{tiempos['draft_n']}"
    if puerto_tubo and tiempos.get("predicted_n"):
        n = tiempos["predicted_n"] + tiempos.get("prompt_n", 0)
        diagnostico["tubo_kb_por_token"] = round((salud_local()["tubo_bytes"] - tubo_antes) / 1e3 / n, 1)
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
