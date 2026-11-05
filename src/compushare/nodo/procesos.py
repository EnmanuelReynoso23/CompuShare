"""Arranque de los procesos de llama.cpp (llama-server, ggml-rpc-server) que lanzan los nodos.

Dos fallas encontradas en la prueba de punta a punta del 4-nov-2026:
- La salida iba a un PIPE que nadie leia: al llenarse (unos 64 KB de registro) llama-server se congela.
  Ahora va a un archivo en ~/.cache/compushare/registros/.
- Si el nodo moria (SIGTERM o SIGKILL), su llama-server seguia vivo y ocupando el puerto. En Linux el hijo
  ahora recibe SIGTERM cuando muere el padre (PR_SET_PDEATHSIG).
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
from pathlib import Path

DIR_REGISTROS = Path(os.environ.get("COMPUSHARE_REGISTROS", Path.home() / ".cache" / "compushare" / "registros"))


def _morir_con_el_padre() -> None:
    if not sys.platform.startswith("linux"):
        return
    try:
        import ctypes

        libc = ctypes.CDLL("libc.so.6", use_errno=True)
        libc.prctl(1, signal.SIGTERM)  # 1 = PR_SET_PDEATHSIG
    except Exception:
        pass


def lanzar(cmd: list[str], nombre: str) -> tuple[subprocess.Popen, Path]:
    """Lanza `cmd` con su salida en un archivo de registro. Devuelve el proceso y la ruta del registro."""
    DIR_REGISTROS.mkdir(parents=True, exist_ok=True)
    ruta = DIR_REGISTROS / f"{nombre}.log"
    if ruta.exists() and ruta.stat().st_size:
        # Al relanzar tras una caída, el registro anterior explica la caída: no se pisa.
        try:
            ruta.replace(ruta.with_suffix(".anterior.log"))
        except OSError:  # en Windows, si otro proceso todavía lo tiene abierto
            pass
    with open(ruta, "wb") as salida:
        proc = subprocess.Popen(
            cmd,
            stdout=salida,
            stderr=subprocess.STDOUT,
            preexec_fn=_morir_con_el_padre if sys.platform.startswith("linux") else None,
        )
    return proc, ruta


def cola_registro(ruta: Path | None, max_bytes: int = 3000) -> str:
    """Ultimos bytes del registro, para explicar por que fallo un proceso."""
    if ruta is None:
        return ""
    try:
        with open(ruta, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - max_bytes))
            return f.read().decode(errors="replace")
    except OSError:
        return ""
