"""Dónde está el relé hoy.

El relé corre dentro de un cuaderno de Kaggle y sale a internet por un túnel rápido de Cloudflare
(`*.trycloudflare.com`), así que su dirección cambia en cada arranque. El cuaderno la publica en un
tema de ntfy.sh cuyo nombre sale de la clave de la red: solo quien tiene la clave sabe dónde mirar, y
la dirección sola no sirve sin la clave.
"""

from __future__ import annotations

import hashlib
import json
import time
import urllib.request

NTFY = "https://ntfy.sh"


def tema(clave: str) -> str:
    return "synrag-red-" + hashlib.sha256(f"synrag-red:{clave}".encode()).hexdigest()[:24]


def publicar(url: str, clave: str) -> None:
    pedido = urllib.request.Request(f"{NTFY}/{tema(clave)}", data=url.encode(), method="POST",
                                    headers={"Cache": "yes", "Title": "rele"})
    urllib.request.urlopen(pedido, timeout=20).read()


def ultimo(clave: str) -> str | None:
    """La dirección publicada más reciente (ntfy guarda los mensajes ~12 h)."""
    with urllib.request.urlopen(f"{NTFY}/{tema(clave)}/json?poll=1&since=12h", timeout=20) as r:
        lineas = [json.loads(l) for l in r.read().decode().splitlines() if l.strip()]
    urls = [m["message"] for m in lineas if m.get("event") == "message" and str(m.get("message", "")).startswith("wss://")]
    # La más nueva que siga viva: las de cuadernos ya apagados quedan en el tema hasta 12 h.
    for url in reversed(urls):
        if vivo(url):
            return url
    return None


def vivo(url: str) -> bool:
    try:
        with urllib.request.urlopen("https://" + url.removeprefix("wss://") + "/salud", timeout=8) as r:
            return r.status == 200
    except OSError:
        return False


def buscar(clave: str, espera_s: float = 0, cada_s: float = 10) -> str:
    """Espera hasta `espera_s` a que aparezca una dirección."""
    limite = time.monotonic() + espera_s
    while True:
        try:
            url = ultimo(clave)
        except OSError:
            url = None
        if url:
            return url
        if time.monotonic() >= limite:
            raise RuntimeError("no hay relé publicado: arranca el cuaderno que lo aloja (lanzar.py)")
        time.sleep(cada_s)


def rele(valor: str | None, clave: str, espera_s: float = 0) -> str:
    """FABRIC_RELE vacío o 'buscar' => se busca; si no, se usa tal cual (p. ej. ws://127.0.0.1:8095)."""
    if valor and valor != "buscar":
        return valor
    return buscar(clave, espera_s)
