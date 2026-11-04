"""Delega trabajo real a los nodos de la red y mide qué tal lo hacen.

    python -m fabric.pruebas.delegar [carpeta_de_resultados]

Esta máquina solo reparte y anota (trocear Flask con `ast` tarda menos de un
segundo); todo lo pesado corre en los nodos:

A. Red: ida y vuelta de 20 tareas `eco` a cada nodo.
B. Búsqueda: Flask troceado por funciones/clases y por secciones de la
   documentación; los nodos calculan los embeddings y reordenan. Se mide con las
   22 preguntas de `benchmarks/preguntas` (acierto en el puesto 1 y en los 5 primeros).
C. Trabajo de IA: cada modelo revisa las funciones más largas del motor de SynRAG
   y dice si ve errores concretos. Las respuestas quedan para revisarlas a mano:
   NO se dan por buenas.
"""

from __future__ import annotations

import ast
import asyncio
import json
import os
import re
import statistics
import subprocess
import sys
import time
from pathlib import Path

from fabric import descubrir
from fabric.cliente import ClienteRed, ErrorRed

RAIZ = Path(__file__).resolve().parents[2]
FLASK = Path(os.environ.get("FLASK_DIR", "/tmp/claude-fabric-flask"))


def log(*a: object) -> None:
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ── troceado (local, barato) ────────────────────────────────────────────────


def trozos_python(ruta: Path, base: Path) -> list[dict]:
    texto = ruta.read_text(encoding="utf-8", errors="replace")
    lineas = texto.splitlines()
    salida = []
    for nodo in ast.walk(ast.parse(texto)):
        if isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            ini, fin = nodo.lineno, nodo.end_lineno or nodo.lineno
            cuerpo = "\n".join(lineas[ini - 1 : fin])
            salida.append({"archivo": str(ruta.relative_to(base)), "simbolo": nodo.name, "ini": ini, "fin": fin,
                           "texto": f"# {ruta.relative_to(base)}\n{cuerpo}"[:2000]})
    return salida


SUBRAYADO = re.compile(r"^([=\-~^\"'`#*+])\1{2,}\s*$")


def trozos_rst(ruta: Path, base: Path) -> list[dict]:
    lineas = ruta.read_text(encoding="utf-8", errors="replace").splitlines()
    titulos = [i for i in range(len(lineas) - 1) if lineas[i].strip() and SUBRAYADO.match(lineas[i + 1])
               and len(lineas[i + 1].strip()) >= len(lineas[i].strip())]
    salida = []
    for k, i in enumerate(titulos):
        fin = (titulos[k + 1] - 1) if k + 1 < len(titulos) else len(lineas)
        cuerpo = "\n".join(lineas[i:fin])
        salida.append({"archivo": str(ruta.relative_to(base)), "simbolo": lineas[i].strip(), "ini": i + 1, "fin": fin,
                       "texto": cuerpo[:2000]})
    return salida


def trocear_flask() -> list[dict]:
    if not FLASK.exists():
        subprocess.run(["git", "clone", "-q", "--depth", "1", "https://github.com/pallets/flask", str(FLASK)], check=True)
    trozos: list[dict] = []
    for ruta in sorted((FLASK / "src").rglob("*.py")):
        trozos += trozos_python(ruta, FLASK)
    for ruta in sorted((FLASK / "docs").rglob("*.rst")):
        trozos += trozos_rst(ruta, FLASK)
    return trozos


def acierta(pregunta: dict, trozo: dict) -> bool:
    if trozo["archivo"] != pregunta["archivo"]:
        return False
    if trozo["simbolo"].lower() == pregunta["simbolo"].lower():
        return True
    return trozo["ini"] <= pregunta["linea"] <= trozo["fin"] and trozo["fin"] - trozo["ini"] < 400


def coseno(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))  # ya vienen normalizados


# ── las pruebas ─────────────────────────────────────────────────────────────


async def prueba_red(red: ClienteRed) -> dict:
    salida = {}
    for nodo in red.nodos:
        tiempos = []
        for i in range(20):
            t = time.monotonic()
            await red.pedir("eco", {"i": i}, para=nodo["id"], timeout=30)
            tiempos.append((time.monotonic() - t) * 1000)
        salida[nodo["nombre"]] = {"mediana_ms": round(statistics.median(tiempos)), "max_ms": round(max(tiempos))}
        log("eco", nodo["nombre"], salida[nodo["nombre"]])
    return salida


async def prueba_busqueda(red: ClienteRed) -> dict:
    preguntas = []
    for nombre in ("flask.json", "flask-docs.json"):
        preguntas += json.loads((RAIZ / "benchmarks/preguntas" / nombre).read_text())
    trozos = trocear_flask()
    log(f"Flask: {len(trozos)} trozos, {len(preguntas)} preguntas")

    t = time.monotonic()
    # Lotes chicos y textos recortados: en el nodo `calidad` la GPU 0 ya tiene ~7,7 GB de llama.cpp
    # y un lote de 32 trozos largos se quedó sin memoria (31-oct).
    tam = int(os.environ.get("LOTE_EMBEBER", "8"))
    lotes = [trozos[i : i + tam] for i in range(0, len(trozos), tam)]
    vectores: list[list[float]] = []
    for lote in lotes:
        r = await red.pedir("embeber", {"textos": [x["texto"][:2000] for x in lote]}, timeout=300)
        vectores += r["vectores"]
    seg_embeber = time.monotonic() - t
    log(f"embeddings de {len(trozos)} trozos en {seg_embeber:.1f} s ({len(trozos) / seg_embeber:.0f} trozos/s, {r['modelo']})")
    consultas = (await red.pedir("embeber", {"textos": [p["consulta"] for p in preguntas], "consulta": True}))["vectores"]

    detalle = []
    a1 = a5 = r1 = r5 = 0
    t_reordenar = []
    for p, q in zip(preguntas, consultas):
        orden = sorted(range(len(trozos)), key=lambda i: -coseno(q, vectores[i]))[:20]
        pos = next((k for k, i in enumerate(orden) if acierta(p, trozos[i])), None)
        t = time.monotonic()
        rr = await red.pedir("reordenar", {"consulta": p["consulta"], "documentos": [trozos[i]["texto"][:1500] for i in orden]})
        t_reordenar.append(time.monotonic() - t)
        reorden = [orden[k] for k in sorted(range(len(orden)), key=lambda k: -rr["puntajes"][k])]
        pos_r = next((k for k, i in enumerate(reorden) if acierta(p, trozos[i])), None)
        a1 += pos == 0
        a5 += pos is not None and pos < 5
        r1 += pos_r == 0
        r5 += pos_r is not None and pos_r < 5
        detalle.append({"consulta": p["consulta"], "esperado": f"{p['archivo']}:{p['simbolo']}",
                        "puesto_embeddings": None if pos is None else pos + 1,
                        "puesto_reordenado": None if pos_r is None else pos_r + 1,
                        "primero": f"{trozos[reorden[0]]['archivo']}:{trozos[reorden[0]]['simbolo']}"})
    n = len(preguntas)
    resultado = {
        "trozos": len(trozos),
        "preguntas": n,
        "modelo_embeddings": r["modelo"],
        "modelo_reordenado": rr["modelo"],
        "segundos_embeber_todo": round(seg_embeber, 1),
        "trozos_por_segundo": round(len(trozos) / seg_embeber),
        "reordenar_20_mediana_s": round(statistics.median(t_reordenar), 2),
        "solo_embeddings": {"acierto_1": f"{a1}/{n}", "acierto_5": f"{a5}/{n}"},
        "con_reordenado": {"acierto_1": f"{r1}/{n}", "acierto_5": f"{r5}/{n}"},
        "detalle": detalle,
    }
    log("búsqueda:", {k: resultado[k] for k in ("solo_embeddings", "con_reordenado", "reordenar_20_mediana_s")})
    return resultado


def funciones_largas(n: int = 6) -> list[dict]:
    todas = []
    for ruta in sorted((RAIZ / "engine").glob("*.py")):
        for t in trozos_python(ruta, RAIZ):
            if t["fin"] - t["ini"] < 220 and not t["simbolo"][0].isupper():
                todas.append(dict(t, texto="\n".join(ruta.read_text().splitlines()[t["ini"] - 1 : t["fin"]])))
    todas.sort(key=lambda t: -(t["fin"] - t["ini"]))
    vistos, salida = set(), []
    for t in todas:
        if t["archivo"] not in vistos:
            vistos.add(t["archivo"])
            salida.append(t)
        if len(salida) == n:
            break
    return salida


INSTRUCCION = (
    "Eres revisor de código. Revisa esta función de SynRAG (Python). Lista como máximo 3 errores CONCRETOS "
    "que harían fallar el programa o dar un resultado equivocado, cada uno con la línea (número desde {ini}) "
    "y un caso que lo dispare. Si no ves ninguno claro, responde exactamente 'Sin errores claros.' "
    "No comentes estilo ni nombres. En español, breve.\n\nArchivo: {archivo}\n```python\n{codigo}\n```"
)


async def prueba_generar(red: ClienteRed) -> dict:
    generadores = [n for n in red.nodos if "generar" in n["capacidades"]]
    velocidad = {}
    for nodo in generadores:
        r = await red.pedir("generar", {"prompt": "Explica en 5 frases qué es un índice invertido.", "pensar": False,
                                        "max_tokens": 300}, para=nodo["id"], timeout=600)
        velocidad[nodo["nombre"]] = {k: r.get(k) for k in ("modelo", "tokens_salida", "tokens_por_segundo", "primer_token_s", "segundos")}
        log("velocidad", nodo["nombre"], velocidad[nodo["nombre"]])

    funciones = funciones_largas()
    trabajos = []

    async def revisar(nodo: dict, f: dict) -> dict:
        datos = {"prompt": INSTRUCCION.format(ini=f["ini"], archivo=f["archivo"], codigo=f["texto"]),
                 "pensar": False, "max_tokens": 900, "temperatura": 0.2}
        try:
            r = await red.pedir("generar", datos, para=nodo["id"], timeout=1200)
            return {"funcion": f"{f['archivo']}:{f['simbolo']} (líneas {f['ini']}-{f['fin']})", "nodo": nodo["nombre"],
                    "modelo": r.get("modelo"), "segundos": r.get("segundos"), "tokens_por_segundo": r.get("tokens_por_segundo"),
                    "respuesta": r.get("texto", "").strip()}
        except ErrorRed as e:
            return {"funcion": f["simbolo"], "nodo": nodo["nombre"], "error": str(e)}

    t = time.monotonic()
    trabajos = await asyncio.gather(*(revisar(n, f) for f in funciones for n in generadores))
    log(f"revisiones: {len(trabajos)} en {time.monotonic() - t:.0f} s")
    return {"velocidad": velocidad, "revisiones": list(trabajos), "segundos_revisiones": round(time.monotonic() - t)}


def informe(res: dict) -> str:
    l = ["# Prueba de la red en Kaggle", "", f"Fecha: {res['fecha']}", "", "## Nodos", ""]
    for n in res["nodos"]:
        info = n.get("info", {})
        l.append(f"- **{n['nombre']}**: {', '.join(n['capacidades'])}; {info.get('gpus', '?')} x {info.get('gpu', '?')} "
                 f"({info.get('vram_gb', '?')} GB); modelo {info.get('modelo', '-')}")
    l += ["", "## A. Red (ida y vuelta de una tarea vacía, relé en Oracle)", ""]
    for k, v in res.get("red", {}).items():
        l.append(f"- {k}: mediana {v['mediana_ms']} ms, máximo {v['max_ms']} ms")
    b = res.get("busqueda")
    if b:
        l += ["", "## B. Búsqueda en Flask (22 preguntas de benchmarks/preguntas)", "",
              f"- {b['trozos']} trozos; embeddings con {b['modelo_embeddings']}: {b['segundos_embeber_todo']} s "
              f"({b['trozos_por_segundo']} trozos/s)",
              f"- Solo embeddings: acierto en el 1.º {b['solo_embeddings']['acierto_1']}, en los 5 primeros {b['solo_embeddings']['acierto_5']}",
              f"- Con reordenado ({b['modelo_reordenado']}, 20 candidatos, {b['reordenar_20_mediana_s']} s): "
              f"1.º {b['con_reordenado']['acierto_1']}, 5 primeros {b['con_reordenado']['acierto_5']}"]
    g = res.get("generar")
    if g:
        l += ["", "## C. Modelos", ""]
        for k, v in g["velocidad"].items():
            l.append(f"- {k} ({v['modelo']}): {v['tokens_por_segundo']} tokens/s, primer token {v['primer_token_s']} s")
        l += ["", "### Revisiones de código delegadas (revisarlas a mano: no se dan por buenas)", ""]
        for r in g["revisiones"]:
            l += [f"#### {r['funcion']} -- {r['nodo']}", "", r.get("respuesta") or f"ERROR: {r.get('error')}", ""]
    for k, v in res.get("errores", {}).items():
        l += ["", f"ERROR en {k}: {v}"]
    return "\n".join(l) + "\n"


async def principal(carpeta: Path) -> None:
    clave = os.environ["FABRIC_CLAVE"]
    enjambre = os.environ.get("FABRIC_ENJAMBRE", "publico")
    espera = float(os.environ.get("ESPERA_NODOS_S", "2400"))
    log("buscando el relé...")
    rele = await asyncio.to_thread(descubrir.rele, os.environ.get("FABRIC_RELE"), clave, espera)
    log("relé:", rele)
    res: dict = {"fecha": time.strftime("%Y-%m-%d %H:%M"), "errores": {}}
    async with ClienteRed(rele, clave, enjambre, nombre="delegador") as red:
        log("esperando nodos con generar (2) y embeber (1)...")
        await red.esperar_nodos("generar", cuantos=int(os.environ.get("GENERADORES", "2")), timeout=espera)
        if "busqueda" in os.environ.get("PRUEBAS", "busqueda"):
            await red.esperar_nodos("embeber", cuantos=1, timeout=espera)
        res["nodos"] = red.nodos
        log("nodos:", [(n["nombre"], n["capacidades"]) for n in red.nodos])
        elegidas = set(filter(None, os.environ.get("PRUEBAS", "red,busqueda,generar").split(",")))
        for nombre, prueba in (("red", prueba_red), ("busqueda", prueba_busqueda), ("generar", prueba_generar)):
            if nombre not in elegidas:
                continue
            try:
                res[nombre] = await prueba(red)
            except Exception as e:  # una prueba caída no tumba las otras
                res["errores"][nombre] = f"{type(e).__name__}: {e}"
                log("falló", nombre, e)
            carpeta.mkdir(parents=True, exist_ok=True)
            (carpeta / "resultados.json").write_text(json.dumps(res, indent=1, ensure_ascii=False))
            (carpeta / "INFORME.md").write_text(informe(res))
    log("listo:", carpeta / "INFORME.md")


if __name__ == "__main__":
    destino = Path(sys.argv[1]) if len(sys.argv) > 1 else RAIZ / "fabric/kaggle/resultados" / time.strftime("%Y%m%d-%H%M")
    asyncio.run(principal(destino))
