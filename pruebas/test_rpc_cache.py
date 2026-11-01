"""La caché de ggml-rpc-server armada desde un GGUF: nombres de archivo = FNV-1a 64 de los bytes del tensor."""

import os
import shutil
import struct

import pytest

from fabric import rpc_cache


def fnv(datos: bytes) -> int:
    h = 0xCBF29CE484222325
    for b in datos:
        h = ((h ^ b) * 0x100000001B3) & 0xFFFFFFFFFFFFFFFF
    return h


def cadena(s: str) -> bytes:
    b = s.encode()
    return struct.pack("<Q", len(b)) + b


def gguf(ruta, tensores):
    """GGUF v3 mínimo: un kv de texto, un arreglo, la alineación y tensores F32 de una dimensión."""
    cab = b"GGUF" + struct.pack("<IQQ", 3, len(tensores), 3)
    cab += cadena("general.name") + struct.pack("<I", 8) + cadena("prueba")
    cab += cadena("tokenizer.ggml.scores") + struct.pack("<IIQ", 9, 6, 3) + struct.pack("<3f", 1, 2, 3)
    cab += cadena("general.alignment") + struct.pack("<II", 4, 64)
    datos = b""
    for nombre, valores in tensores:
        cab += cadena(nombre) + struct.pack("<IQIQ", 1, len(valores), 0, len(datos))
        datos += struct.pack(f"<{len(valores)}f", *valores)
        datos += b"\0" * (-len(datos) % 64)
    cab += b"\0" * (-len(cab) % 64)
    ruta.write_bytes(cab + datos)


@pytest.mark.skipif(not (shutil.which("cc") or shutil.which("gcc")), reason="sin compilador de C")
def test_caché_con_los_mismos_hashes_que_llama_cpp(tmp_path, monkeypatch):
    a = [float(i) for i in range(300)]
    b = [float(-i) / 7 for i in range(1000)]
    archivo = tmp_path / "m.gguf"
    gguf(archivo, [("a", a), ("b", b)])

    info = rpc_cache.tensores(archivo)
    assert [(n, t) for n, _, t in info] == [("a", 1200), ("b", 4000)]

    monkeypatch.setattr(rpc_cache, "UMBRAL", 2000)  # solo "b" pasa el umbral, como un peso grande
    res = rpc_cache.llenar(archivo, tmp_path / "cache")
    esperado = f"{fnv(struct.pack('<1000f', *b)):016x}"
    assert res["hashes"] == [esperado]
    assert (tmp_path / "cache" / esperado).read_bytes() == struct.pack("<1000f", *b)
    assert not os.path.exists(tmp_path / "cache" / ".parcial")
