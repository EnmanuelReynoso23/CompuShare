"""Arranque de los procesos de llama.cpp (llama-server, ggml-rpc-server) que lanzan los nodos.

Dos fallas encontradas en la prueba de punta a punta del 4-nov-2026:
- La salida iba a un PIPE que nadie leia: al llenarse (unos 64 KB de registro) llama-server se congela.
  Ahora va a un archivo en ~/.cache/compushare/registros/.
- Si el nodo moria (SIGTERM o SIGKILL), su llama-server seguia vivo y ocupando el puerto. En Linux el hijo
  ahora recibe SIGTERM cuando muere el padre (PR_SET_PDEATHSIG).
"""

from __future__ import annotations

import logging
import os
import shutil
import signal
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("compushare.procesos")

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


def lanzar(cmd: list[str], nombre: str, env: dict[str, str] | None = None) -> tuple[subprocess.Popen, Path]:
    """Lanza `cmd` con su salida en un archivo de registro. Devuelve el proceso y la ruta del registro.
    `env` (opcional) es el entorno completo del proceso; úsalo para pasar secretos sin ponerlos en la línea de
    comandos, que cualquier usuario de la máquina puede leer con ps."""
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
            env=env,
            preexec_fn=_morir_con_el_padre if sys.platform.startswith("linux") else None,
        )
    return proc, ruta


RAIZ_REPO = Path(__file__).resolve().parents[3]
COMO_COMPILAR_GO = "cd go && go build -o bin/ ./cmd/compushare-tubo ./cmd/compushare-rele"


def buscar_binario_go(nombre: str, directorio: str | Path | None = None) -> Path:
    """Busca un binario de Go (compushare-tubo, compushare-rele) en `directorio`, en PATH o en go/bin/ del repo."""
    candidatos: list[Path] = []
    if directorio:
        candidatos.append(Path(directorio) / nombre)
    en_path = shutil.which(nombre)
    if en_path:
        candidatos.append(Path(en_path))
    candidatos.append(RAIZ_REPO / "go" / "bin" / nombre)
    for c in candidatos:
        if c.is_file() and os.access(c, os.X_OK):
            return c
    buscados = ", ".join(str(c) for c in candidatos)
    raise FileNotFoundError(
        f"No se encontro el binario '{nombre}' (buscado en: {buscados}). "
        f"Compilalo en la raiz del repo con: {COMO_COMPILAR_GO}  (o pasa --go-bin con la carpeta del binario)."
    )


def terminar(proc: subprocess.Popen | None, nombre: str) -> None:
    """Detiene un hijo matando SOLO su PID: SIGTERM, y SIGKILL si no responde en 5 s."""
    if proc is None or proc.poll() is not None:
        return
    pid = proc.pid
    log.info("Deteniendo %s PID %d", nombre, pid)
    proc.terminate()
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        log.warning("%s PID %d no respondio a SIGTERM, enviando SIGKILL", nombre, pid)
        proc.kill()
        proc.wait(timeout=2)


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
