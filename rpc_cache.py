"""Arma la caché de `ggml-rpc-server -c` a partir del GGUF que el trabajador ya bajó por su cuenta.

Cuando `llama-server --rpc` sube un peso de más de 10 MiB, primero manda solo su hash FNV-1a de 64 bits
(SET_TENSOR_HASH). Si el servidor RPC tiene en su carpeta de caché un archivo llamado con ese hash
(`%016x`) con los bytes del tensor, lo usa y los bytes no cruzan la red. Este módulo escribe esos
archivos leyendo el GGUF: así por el túnel solo viajan los estados de cada token.

    python -m fabric.rpc_cache modelo.gguf ~/.cache/llama.cpp/rpc

Formato verificado en ggml-rpc.cpp (llama.cpp b12024): HASH_THRESHOLD = 10 MiB, `fnv_hash` con
offset 0xcbf29ce484222325 y primo 0x100000001b3, archivo `cache_dir/<%016 PRIx64>` con los datos crudos.
"""

from __future__ import annotations

import argparse
import shutil
import struct
import subprocess
import sys
import tempfile
from pathlib import Path

UMBRAL = 10 * 1024 * 1024

# tipo ggml -> (elementos por bloque, bytes por bloque). Un tipo desconocido no se cachea (solo se pierde ahorro).
TIPOS = {
    0: (1, 4), 1: (1, 2), 2: (32, 18), 3: (32, 20), 6: (32, 22), 7: (32, 24), 8: (32, 34), 9: (32, 36),
    10: (256, 84), 11: (256, 110), 12: (256, 144), 13: (256, 176), 14: (256, 210), 15: (256, 292),
    16: (256, 66), 17: (256, 74), 18: (256, 98), 19: (256, 50), 20: (32, 18), 21: (256, 110),
    22: (256, 82), 23: (256, 136), 24: (1, 1), 25: (1, 2), 26: (1, 4), 27: (1, 8), 28: (1, 8),
    29: (256, 56), 30: (1, 2), 34: (256, 54), 35: (256, 66), 39: (32, 17),
}

FNV_C = r"""
#include <stdio.h>
#include <stdint.h>
#include <stdlib.h>
int main(int argc, char **argv) {
    if (argc != 3) return 1;
    FILE *f = fopen(argv[1], "rb");
    if (!f) return 2;
    size_t B = 1 << 24;
    unsigned char *buf = malloc(B);
    unsigned long long off, size;
    char tmp[4096], out[4096];
    while (scanf("%llu %llu", &off, &size) == 2) {
        if (fseeko(f, (off_t)off, SEEK_SET)) return 3;
        snprintf(tmp, sizeof tmp, "%s/.parcial", argv[2]);
        FILE *o = fopen(tmp, "wb");
        if (!o) return 4;
        uint64_t h = 0xcbf29ce484222325ULL;
        unsigned long long left = size;
        while (left) {
            size_t n = left < B ? (size_t)left : B;
            if (fread(buf, 1, n, f) != n) return 5;
            for (size_t i = 0; i < n; i++) { h ^= buf[i]; h *= 0x100000001b3ULL; }
            if (fwrite(buf, 1, n, o) != n) return 6;
            left -= n;
        }
        if (fclose(o)) return 6;
        snprintf(out, sizeof out, "%s/%016llx", argv[2], (unsigned long long)h);
        if (rename(tmp, out)) return 7;
        printf("%016llx\n", (unsigned long long)h);
        fflush(stdout);
    }
    return 0;
}
"""


def _leer(f, fmt: str):
    return struct.unpack("<" + fmt, f.read(struct.calcsize("<" + fmt)))


def _cadena(f) -> str:
    (n,) = _leer(f, "Q")
    return f.read(n).decode("utf-8", "replace")


ESCALARES = {0: "B", 1: "b", 2: "H", 3: "h", 4: "I", 5: "i", 6: "f", 7: "?", 10: "Q", 11: "q", 12: "d"}


def _valor(f, tipo: int):
    if tipo in ESCALARES:
        return _leer(f, ESCALARES[tipo])[0]
    if tipo == 8:
        return _cadena(f)
    if tipo == 9:
        sub, n = _leer(f, "IQ")
        if sub in ESCALARES:  # arreglos largos (vocabulario): se saltan sin decodificar
            f.seek(n * struct.calcsize("<" + ESCALARES[sub]), 1)
            return None
        for _ in range(n):
            _valor(f, sub)
        return None
    raise ValueError(f"tipo GGUF desconocido {tipo}")


def tensores(ruta: str | Path) -> list[tuple[str, int, int]]:
    """(nombre, posición absoluta en el archivo, bytes) de cada tensor de tipo conocido."""
    with open(ruta, "rb") as f:
        if f.read(4) != b"GGUF":
            raise ValueError(f"{ruta} no es un GGUF")
        version, n_t, n_kv = _leer(f, "IQQ")
        if version < 2:
            raise ValueError(f"GGUF v{version} no soportado")
        alineacion = 32
        for _ in range(n_kv):
            clave = _cadena(f)
            (tipo,) = _leer(f, "I")
            v = _valor(f, tipo)
            if clave == "general.alignment" and isinstance(v, int):
                alineacion = v
        info = []
        for _ in range(n_t):
            nombre = _cadena(f)
            (dims,) = _leer(f, "I")
            forma = _leer(f, "Q" * dims)
            tipo, rel = _leer(f, "IQ")
            info.append((nombre, forma, tipo, rel))
        inicio = -(-f.tell() // alineacion) * alineacion
    salida = []
    for nombre, forma, tipo, rel in info:
        if tipo not in TIPOS:
            continue
        bloque, bytes_bloque = TIPOS[tipo]
        n = 1
        for d in forma:
            n *= d
        salida.append((nombre, inicio + rel, n // bloque * bytes_bloque))
    return salida


def _compilar(destino: Path) -> Path:
    exe = destino / ".fnvcopia"
    if exe.exists():
        return exe
    cc = shutil.which("cc") or shutil.which("gcc") or shutil.which("clang")
    if not cc:
        raise RuntimeError("no hay compilador de C (cc/gcc/clang) para el hash")
    with tempfile.TemporaryDirectory() as d:
        fuente = Path(d) / "fnv.c"
        fuente.write_text(FNV_C)
        subprocess.run([cc, "-O2", "-o", str(exe), str(fuente)], check=True)
    return exe


def llenar(gguf: str | Path, carpeta: str | Path) -> dict:
    """Escribe en `carpeta` un archivo por cada tensor de más de 10 MiB. Devuelve cuántos y cuántos bytes."""
    carpeta = Path(carpeta)
    carpeta.mkdir(parents=True, exist_ok=True)
    grandes = [t for t in tensores(gguf) if t[2] > UMBRAL]
    exe = _compilar(carpeta)
    entrada = "".join(f"{pos} {tam}\n" for _, pos, tam in grandes)
    r = subprocess.run([str(exe), str(gguf), str(carpeta)], input=entrada, capture_output=True, text=True)
    if r.returncode:
        raise RuntimeError(f"fnvcopia salió con {r.returncode}: {r.stderr.strip()}")
    hashes = r.stdout.split()
    return {"tensores": len(hashes), "bytes": sum(t[2] for t in grandes), "hashes": hashes}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Arma la caché de ggml-rpc-server desde un GGUF")
    p.add_argument("gguf")
    p.add_argument("carpeta")
    a = p.parse_args()
    res = llenar(a.gguf, a.carpeta)
    print(f"{res['tensores']} tensores, {res['bytes'] / 1e9:.2f} GB en {a.carpeta}", file=sys.stderr)
