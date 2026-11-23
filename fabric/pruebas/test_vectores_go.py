"""El decodificador de Python debe reconstruir exactamente lo que codificó el de Go (go/pkg/rpccodec/testdata)."""

from __future__ import annotations

import struct
from pathlib import Path

import pytest

from fabric import rpc_codec

DATOS = Path(__file__).resolve().parent.parent.parent / "go" / "pkg" / "rpccodec" / "testdata"


def _mensajes(crudo: bytes) -> list[bytes]:
    salida, i = [], 0
    while i < len(crudo):
        (n,) = struct.unpack_from("<I", crudo, i)
        salida.append(crudo[i + 4:i + 4 + n])
        i += 4 + n
    return salida


def test_python_decodifica_lo_que_codifico_go() -> None:
    flujo = DATOS / "flujo_crudo_go.bin"
    mensajes = DATOS / "mensajes_go.bin"
    if not flujo.exists() or not mensajes.exists():
        pytest.skip("faltan los vectores de Go")
    dec = rpc_codec.Decodificador()
    salida = b"".join(dec.decodificar(m) for m in _mensajes(mensajes.read_bytes()))
    assert salida == flujo.read_bytes()
    assert dec.estad.registros.get("GRAPH_REF") or dec.estad.registros.get("GRAPH_DELTA"), \
        "los vectores de Go deberían usar la caché de grafos"
