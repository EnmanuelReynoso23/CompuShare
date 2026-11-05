"""Transcribir un texto con el modelo de la red: el modelo copia cada trozo y se compara letra por
letra con el original. Sirve de prueba de punta a punta (la salida se puede verificar entera) y es
el trabajo de la prueba en Kaggle.

Habla con llama-server por su protocolo propio: `/apply-template` arma el prompt con la plantilla del
modelo y `/completion` genera. Para copiar conviene arrancar el maestro con `--especulativa ngram-mod`
(adivina desde el propio texto de entrada).
"""

from __future__ import annotations

import difflib
import json
import re
import threading
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Callable

INSTRUCCION = "Eres un transcriptor. Copia el texto del usuario exactamente igual, letra por letra, sin agregar ni quitar nada."


def reparar_codificacion(linea: str) -> str:
    """Repara texto UTF-8 que alguien leyó como Latin-1 o cp1252 ("despertÃ³" -> "despertó").
    Pasaba con La metamorfosis bajada el 4-nov-2026: 279 de 289 párrafos venían así."""
    if "Ã" not in linea and "â" not in linea:
        return linea
    for codificacion in ("latin-1", "cp1252"):
        try:
            return linea.encode(codificacion).decode("utf-8")
        except (UnicodeEncodeError, UnicodeDecodeError):
            continue
    return linea


def limpiar(texto: str) -> str:
    """Repara la codificación y quita restos de HTML y marcas de capítulo de un texto bajado de la web."""
    texto = "\n".join(reparar_codificacion(linea) for linea in texto.splitlines())
    texto = re.sub(r"<[^>]+>", "", texto)
    texto = re.sub(r"^\s*center\s*$", "", texto, flags=re.M)
    texto = re.sub(r"^=== .* ===$", "", texto, flags=re.M)
    return texto


def trozos(texto: str, chars: int = 1500) -> list[str]:
    """Trozos de unos `chars` caracteres que no cortan párrafos."""
    salida: list[str] = []
    actual = ""
    for parrafo in (p.strip() for p in texto.split("\n")):
        if not parrafo:
            continue
        if actual and len(actual) + len(parrafo) > chars:
            salida.append(actual)
            actual = ""
        actual = f"{actual}\n\n{parrafo}" if actual else parrafo
    if actual:
        salida.append(actual)
    return salida


def trozos_de_archivo(ruta: str | Path, chars: int = 1500) -> list[str]:
    return trozos(limpiar(Path(ruta).read_text(encoding="utf-8")), chars)


def _pedir(url: str, cuerpo: dict[str, Any], espera: float) -> dict[str, Any]:
    req = urllib.request.Request(url, data=json.dumps(cuerpo).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=espera) as r:
        return json.load(r)


def transcribir_trozo(url: str, texto: str, espera: float = 3600) -> dict[str, Any]:
    """Pide al modelo que copie `texto` y devuelve la salida, la comparación y los tiempos de llama-server."""
    base = url.rstrip("/")
    mensajes = [{"role": "system", "content": INSTRUCCION}, {"role": "user", "content": texto}]
    prompt = _pedir(f"{base}/apply-template",
                    {"messages": mensajes, "chat_template_kwargs": {"enable_thinking": False}}, espera)["prompt"]
    t0 = time.time()
    r = _pedir(f"{base}/completion", {"prompt": prompt, "n_predict": int(len(texto) / 2.5) + 16,
                                      "temperature": 0.0, "cache_prompt": False}, espera)
    salida = (r.get("content") or "").strip()
    original = texto.strip()
    tm = r.get("timings") or {}
    return {
        "exacto": salida == original,
        "parecido": round(difflib.SequenceMatcher(None, salida, original).ratio(), 4),
        "chars_original": len(original),
        "chars_salida": len(salida),
        "prompt_n": tm.get("prompt_n"),
        "prompt_ms": tm.get("prompt_ms"),
        "gen_n": tm.get("predicted_n"),
        "gen_ms": tm.get("predicted_ms"),
        "gen_tok_s": tm.get("predicted_per_second"),
        "draft_n": tm.get("draft_n"),
        "draft_aceptados": tm.get("draft_n_accepted"),
        "pared_s": round(time.time() - t0, 1),
        "parada": r.get("stop_type"),
        "salida": salida,
    }


def resumir(trozos_total: list[str], filas: list[dict[str, Any] | None], pared_s: float) -> dict[str, Any]:
    hechos = [f for f in filas if f]
    buenos = [f for f in hechos if "error" not in f]
    gen = sum(f.get("gen_n") or 0 for f in buenos)
    draft = sum(f.get("draft_n") or 0 for f in buenos)
    return {
        "trozos_total": len(trozos_total),
        "trozos_hechos": len(hechos),
        "trozos_con_error": len(hechos) - len(buenos),
        "trozos_exactos": sum(1 for f in buenos if f.get("exacto")),
        "parecido_medio": round(sum(f.get("parecido", 0) for f in buenos) / max(1, len(buenos)), 4),
        "chars_hechos": sum(f["chars_original"] for f in hechos),
        "chars_total": sum(len(t) for t in trozos_total),
        "prompt_tokens": sum(f.get("prompt_n") or 0 for f in buenos),
        "gen_tokens": gen,
        "aciertos_especulativa": round(sum(f.get("draft_aceptados") or 0 for f in buenos) / draft, 3) if draft else None,
        "pared_s": round(pared_s, 1),
        "tok_s_total": round(gen / pared_s, 2) if pared_s > 0 else 0.0,
    }


def esperar_servidor(url: str, espera_s: float = 900, cada_s: float = 5) -> bool:
    """Espera a que llama-server vuelva a responder /health (por ejemplo mientras el maestro lo relanza
    tras la caída de un trabajador). True si volvió antes de `espera_s`."""
    fin = time.time() + espera_s
    while time.time() < fin:
        try:
            with urllib.request.urlopen(url.rstrip("/") + "/health", timeout=5) as r:
                if r.status == 200:
                    return True
        except Exception:  # noqa: BLE001 - todavía no está
            pass
        time.sleep(cada_s)
    return False


def _ya_hechos(carpeta: Path | None, textos: list[str]) -> dict[int, dict[str, Any]]:
    """Trozos bien hechos en una corrida anterior (transcripcion.json), para retomar sin repetirlos."""
    if not carpeta or not (carpeta / "transcripcion.json").exists():
        return {}
    try:
        previos = json.loads((carpeta / "transcripcion.json").read_text(encoding="utf-8"))["trozos"]
    except (ValueError, KeyError, OSError):
        return {}
    return {f["i"]: f for f in previos
            if "error" not in f and 0 <= f.get("i", -1) < len(textos)
            and f.get("chars_original") == len(textos[f["i"]].strip())}


def transcribir(
    url: str,
    textos: list[str],
    flujos: int = 1,
    salida_dir: str | Path | None = None,
    limite_s: float | None = None,
    al_terminar_trozo: Callable[[int, dict[str, Any]], None] | None = None,
    recuperar: Callable[[], bool] | None = None,
    reintentos: int = 2,
    retomar: bool = False,
) -> dict[str, Any]:
    """Transcribe `textos` con `flujos` peticiones a la vez (el maestro debe tener `--parallel` >= flujos).

    Si hay `salida_dir`, guarda `transcripcion.json` y `libro_transcrito.txt` después de cada trozo:
    si algo se corta, queda lo hecho hasta ahí. `limite_s` deja de empezar trozos nuevos pasado ese tiempo.

    Si un trozo falla y hay `recuperar`, se llama a `recuperar()` (una sola vez aunque fallen varios flujos a la vez) y, si
    devuelve True, se repite el trozo hasta `reintentos` veces. Si devuelve False se deja de empezar trozos:
    los que faltan no se marcan como hechos (el 4-nov-2026 en Kaggle, sin esto, 50 trozos fallaron en
    segundos tras caerse el trabajador). `retomar` salta los trozos ya bien hechos en `salida_dir`.
    """
    filas: list[dict[str, Any] | None] = [None] * len(textos)
    candado = threading.Lock()
    candado_recuperar = threading.Lock()
    caidas = {"n": 0, "recuperaciones": 0}
    parar = threading.Event()
    t_ini = time.time()
    carpeta = Path(salida_dir) if salida_dir else None
    if carpeta:
        carpeta.mkdir(parents=True, exist_ok=True)
    previos = _ya_hechos(carpeta, textos) if retomar else {}
    for i, fila in previos.items():
        filas[i] = fila

    def guardar() -> dict[str, Any]:
        res = resumir(textos, filas, time.time() - t_ini)
        res["flujos"] = flujos
        res["retomados"] = len(previos)
        res["recuperaciones"] = caidas["recuperaciones"]
        if carpeta:
            hechos = [f for f in filas if f]
            (carpeta / "transcripcion.json").write_text(
                json.dumps({"resumen": res, "trozos": hechos}, ensure_ascii=False, indent=1), encoding="utf-8")
            (carpeta / "libro_transcrito.txt").write_text(
                "\n\n".join(f["salida"] for f in filas if f), encoding="utf-8")
        return res

    def intentar_recuperar(caida_vista: int) -> bool:
        with candado_recuperar:
            if caidas["n"] != caida_vista:  # otro flujo ya recuperó esta caída
                return not parar.is_set()
            if recuperar is None:  # sin forma de recuperar: se anota el error y se sigue con el resto
                return False
            caidas["n"] += 1
            if parar.is_set() or not recuperar():
                parar.set()
                return False
            caidas["recuperaciones"] += 1
            return True

    def uno(i: int) -> None:
        if filas[i] is not None or parar.is_set():
            return
        if limite_s is not None and time.time() - t_ini > limite_s:
            return
        for intento in range(reintentos + 1):
            caida_vista = caidas["n"]
            try:
                fila = transcribir_trozo(url, textos[i])
                break
            except Exception as e:  # noqa: BLE001 - se registra y se sigue con el resto
                fila = {"error": repr(e)[:300], "salida": "", "chars_original": len(textos[i])}
                if intento == reintentos or not intentar_recuperar(caida_vista):
                    break
                fila["reintento"] = intento + 1
        if "error" in fila and parar.is_set():
            return  # sin servidor: no se cuenta como hecho, queda para retomar
        fila["i"] = i
        with candado:
            filas[i] = fila
            guardar()
        if al_terminar_trozo:
            al_terminar_trozo(i, fila)

    with ThreadPoolExecutor(max(1, flujos)) as ex:
        list(ex.map(uno, range(len(textos))))
    res = guardar()
    res["detenido_sin_servidor"] = parar.is_set()
    return res
