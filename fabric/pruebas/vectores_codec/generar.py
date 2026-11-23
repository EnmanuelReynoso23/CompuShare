"""Genera los vectores de prueba del códec `rpc1` que comparten Python y Go (`go/pkg/rpccodec`).

    python fabric/pruebas/vectores_codec/generar.py [carpeta_destino]

Archivos (todos deterministas):

- flujo_crudo.bin: flujo ggml-rpc sin codificar (HELLO, búferes, activaciones, máscaras con -inf y grafos).
- mensajes.bin: los mensajes WS que produce el `Codificador` de Python (sin Q8) sobre flujo_crudo.bin, cada
  uno precedido por su largo u32 little endian. El decodificador de Go debe reconstruir flujo_crudo.bin.
- q8.bin: UN mensaje WS (sin prefijo) que contiene un registro SET_TENSOR_Q8 (además de un grafo FULL), codificado
  con `q8=True`.
- q8_esperado.bin: lo que el decodificador de Python saca de q8.bin. El de Go debe dar exactamente esto.

Antes de escribir, el script comprueba que el decodificador devuelve el flujo original, que el error de Q8 está
dentro de media escala por bloque, y que el total pesa menos de 1 MB.
"""

from __future__ import annotations

import pathlib
import random
import struct
import sys

RAIZ = pathlib.Path(__file__).resolve().parents[3]
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

from fabric import rpc_codec as rc  # noqa: E402
from fabric.pruebas import flujo_rpc_sintetico as sint  # noqa: E402

LIMITE_TOTAL = 1 << 20


def generar(destino: pathlib.Path) -> dict[str, bytes]:
    rng = random.Random(51)
    flujo = sint.flujo(semilla=51, pasos=6, megas_pesos=0, tensores=200)

    cod = rc.Codificador()
    mensajes: list[bytes] = []
    pos = 0
    while pos < len(flujo):
        k = rng.randint(1, 60000)
        salida = cod.codificar(flujo[pos:pos + k])
        if salida:
            mensajes.append(salida)
        pos += k
    dec = rc.Decodificador()
    assert b"".join(dec.decodificar(m) for m in mensajes) == flujo, "el decodificador no reconstruye el flujo"
    bytes_mensajes = b"".join(struct.pack("<I", len(m)) + m for m in mensajes)

    entrada_q8 = sint.grafo(0, 0, rng, tensores=40) + sint.set_activacion(rng, lote=2, ne0=1024)
    q8 = rc.Codificador(q8=True).codificar(entrada_q8)
    assert any(t == 0x04 for t in _tipos(q8)), "el vector Q8 no contiene SET_TENSOR_Q8"
    esperado = rc.Decodificador().decodificar(q8)
    _comprobar_error_q8(entrada_q8, esperado)

    salida = {
        "flujo_crudo.bin": flujo,
        "mensajes.bin": bytes_mensajes,
        "q8.bin": q8,
        "q8_esperado.bin": esperado,
    }
    total = sum(len(v) for v in salida.values())
    assert total < LIMITE_TOTAL, f"vectores de {total} bytes"
    destino.mkdir(parents=True, exist_ok=True)
    for nombre, datos in salida.items():
        (destino / nombre).write_bytes(datos)
    return salida


def _tipos(mensaje: bytes) -> list[int]:
    tipos, pos = [], 0
    while pos < len(mensaje):
        t = mensaje[pos]
        tipos.append(t)
        pos += 1
        if t == 0x00:
            pos += 4 + struct.unpack_from("<I", mensaje, pos)[0]
        elif t == 0x01:
            pos += 8 + struct.unpack_from("<I", mensaje, pos + 4)[0]
        elif t == 0x02:
            pos += 16
        elif t == 0x03:
            pos += 24 + struct.unpack_from("<I", mensaje, pos + 20)[0]
        elif t == 0x04:
            hlen = struct.unpack_from("<I", mensaje, pos)[0]
            n = struct.unpack_from("<Q", mensaje, pos + 4 + hlen)[0]
            pos += 4 + hlen + 8 + (-(-n // 32)) * 34
    return tipos


def _comprobar_error_q8(original: bytes, reconstruido: bytes) -> None:
    """Las activaciones F32 deben volver con error menor que media escala por bloque de 32."""
    cab = rc.CABECERA_SET
    xs = []
    ys = []
    pos_o = pos_r = 0
    while pos_o < len(original):
        tam = struct.unpack_from("<Q", original, pos_o + 1)[0]
        cuerpo = original[pos_o + 9:pos_o + 9 + tam]
        if original[pos_o] == 6 and tam > cab:
            xs.append(struct.unpack(f"<{(tam - cab) // 4}f", cuerpo[cab:]))
        pos_o += 9 + tam
    while pos_r < len(reconstruido):
        tam = struct.unpack_from("<Q", reconstruido, pos_r + 1)[0]
        cuerpo = reconstruido[pos_r + 9:pos_r + 9 + tam]
        if reconstruido[pos_r] == 6 and tam > cab:
            ys.append(struct.unpack(f"<{(tam - cab) // 4}f", cuerpo[cab:]))
        pos_r += 9 + tam
    assert len(xs) == len(ys)
    for a, b in zip(xs, ys):
        for i in range(0, len(a), 32):
            amax = max(abs(v) for v in a[i:i + 32])
            for x, y in zip(a[i:i + 32], b[i:i + 32]):
                assert abs(x - y) <= amax / 127.0 + 1e-6


if __name__ == "__main__":
    destino = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parent
    res = generar(destino)
    for nombre, datos in res.items():
        print(f"{nombre}: {len(datos)} bytes")
