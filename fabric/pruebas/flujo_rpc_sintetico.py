"""Generador de flujos ggml-rpc sintéticos para probar el códec `rpc1` sin llama.cpp.

Imita lo que manda llama-server al trabajador: HELLO, reserva de búferes, carga de pesos (SET_TENSOR grandes
antes del primer grafo), y luego pasos con activaciones y máscara (SET_TENSOR F32), y GRAPH_COMPUTE cuya forma
alterna entre tres tamaños de lote (como la decodificación especulativa). Todo es determinista por semilla.
"""

from __future__ import annotations

import random
import struct

RPC_TENSOR = struct.Struct("<QIQ4I4I" "I" "16i" "i" "10Q" "Q" "Q" "Q" "64s" "i")
assert RPC_TENSOR.size == 296, RPC_TENSOR.size

LOTES = (1, 4, 6)  # las tres formas de grafo que se alternan


def _u32(n: int) -> bytes:
    return struct.pack("<I", n)


def comando(cmd: int, cuerpo: bytes) -> bytes:
    return bytes([cmd]) + struct.pack("<Q", len(cuerpo)) + cuerpo


def rpc_tensor(id_: int, tipo: int, ne: tuple, nb: tuple, data: int, flags: int = 0, nombre: bytes = b"") -> bytes:
    ne = tuple(ne) + (1,) * (4 - len(ne))
    nb = tuple(nb) + (0,) * (4 - len(nb))
    return RPC_TENSOR.pack(id_, tipo, 0, *ne, *nb, 0, *([0] * 16), flags, *([0] * 10), 0, 0, data,
                           nombre[:64].ljust(64, b"\x00"), 0)


def hello() -> bytes:
    return comando(14, bytes(24))


def grafo(paso: int, forma: int, rng: random.Random, tensores: int = 400, nodos: int = 300) -> bytes:
    """Cuerpo de GRAPH_COMPUTE. `forma` elige el lote (ne[1], nb[2] y los punteros data cambian con él)."""
    lote = LOTES[forma]
    cuerpo = bytearray(struct.pack("<II", 0, nodos))
    for k in range(nodos):
        cuerpo += struct.pack("<Q", 0x5000 + k * 16 + forma * 2)
    cuerpo += _u32(tensores)
    for i in range(tensores):
        flags = 1 if (paso % 5 == 0 and i % 37 == 0) else 0  # variación pequeña entre pasos
        cuerpo += rpc_tensor(
            id_=0x1000 + i,
            tipo=0,
            ne=(4096, lote, 1, 1),
            nb=(4, 4 * 4096, 4 * 4096 * lote, 4 * 4096 * lote),
            data=0x7F0000000000 + i * 0x4000 + forma * 0x100,
            flags=flags,
            nombre=f"t{i}".encode(),
        )
    return comando(10, bytes(cuerpo))


def set_pesos(rng: random.Random, megas: int) -> list[bytes]:
    """SET_TENSOR de la carga de pesos: bytes al azar (no se pueden comprimir), en trozos de 1 MiB."""
    salida = []
    for i in range(megas):
        cab = rpc_tensor(0x9000 + i, 1, (1024, 1024), (2, 2048), 0x100000 + i * 0x100000)
        cuerpo = cab + b"\x00" + struct.pack("<Q", 0) + rng.randbytes(1 << 20)
        salida.append(comando(6, cuerpo))
    return salida


def _floats_finitos(rng: random.Random, n: int, escala: float = 0.5) -> bytes:
    return struct.pack(f"<{n}f", *[rng.gauss(0.0, escala) for _ in range(n)])


def set_activacion(rng: random.Random, lote: int, ne0: int = 1024) -> bytes:
    """Activaciones F32 [ne0, lote]: entran en Q8_0 si el códec está activo."""
    cab = rpc_tensor(0x7000, 0, (ne0, lote), (4, 4 * ne0), 0x200000)
    return comando(6, cab + b"\x00" + struct.pack("<Q", 0) + _floats_finitos(rng, ne0 * lote))


def set_mascara(rng: random.Random, lote: int, kv: int = 1024) -> bytes:
    """Máscara de atención F32 con -inf donde no se puede atender: va cruda (no es finita)."""
    valores = [rng.choice((0.0, float("-inf"))) for _ in range(kv * lote)]
    cuerpo = struct.pack(f"<{kv * lote}f", *valores)
    cab = rpc_tensor(0x7100, 0, (kv, lote), (4, 4 * kv), 0x300000)
    return comando(6, cab + b"\x00" + struct.pack("<Q", 0) + cuerpo)


def flujo(semilla: int = 1, pasos: int = 30, megas_pesos: int = 2, mascara: bool = True,
          tensores: int = 400) -> bytes:
    """Flujo completo: HELLO, búferes, pesos y `pasos` pasos de (activación, máscara, grafo)."""
    rng = random.Random(semilla)
    partes = [hello(), comando(0, struct.pack("<Q", 1 << 20)), comando(1, b""), comando(2, b"")]
    partes += set_pesos(rng, megas_pesos)
    partes.append(comando(3, struct.pack("<Q", 0x4000)))
    for paso in range(pasos):
        forma = paso % 3
        lote = LOTES[forma]
        partes.append(set_activacion(rng, lote))
        if mascara:
            partes.append(set_mascara(rng, lote))
        partes.append(grafo(paso, forma, rng, tensores=tensores))
        if paso % 4 == 3:
            partes.append(comando(8, bytes(32)))  # GET_TENSOR pequeño
    partes.append(comando(4, struct.pack("<Q", 0x1000)))
    return b"".join(partes)


def grafos(semilla: int = 2, pasos: int = 30, tensores: int = 400) -> bytes:
    """Solo grafos repetidos (sin pesos ni activaciones): para medir el ahorro sobre el grafo."""
    rng = random.Random(semilla)
    return b"".join(grafo(paso, paso % 3, rng, tensores=tensores) for paso in range(pasos))
