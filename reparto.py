"""Reparto de un modelo entre varias computadoras (llama.cpp RPC por los tubos del relé).

Con llama.cpp las capas van en fila: cada token pasa por todos los equipos elegidos y cada salto
cruza internet. Más equipos = más memoria pero más latencia por token. Por eso el maestro usa el
MENOR número de trabajadores que alcance para el modelo, y entre ellos los de menor latencia:

    1. Mide la ida y vuelta de cada tubo con el saludo HELLO del protocolo RPC (llega hasta el
       ggml-rpc-server del trabajador, no se queda en el relé).
    2. Si el modelo cabe en el maestro solo, no usa a nadie.
    3. Si no, agrega trabajadores de menor a mayor latencia hasta cubrir lo que falta (con margen
       para la caché KV y los búferes de cálculo).

Dentro de los elegidos, llama.cpp ya reparte las capas según la memoria libre de cada GPU.
"""

from __future__ import annotations

import socket
import struct
import time
from dataclasses import dataclass

RPC_CMD_HELLO = 14
HELLO_TAM = 24  # sizeof(rpc_msg_hello_req) en llama.cpp b12024 (protocolo v8)
MARGEN = 1.25  # pesos x 1,25: caché KV, búferes de cálculo y lo que la GPU no deja usar


@dataclass
class Candidato:
    nombre: str
    puerto: int
    vram_gb: float
    rtt_ms: float = float("inf")


def medir_rtt(puerto: int, host: str = "127.0.0.1", veces: int = 3, espera_s: float = 30) -> float:
    """Milisegundos de la mejor de `veces` idas y vueltas HELLO por el tubo; inf si no responde."""
    mejor = float("inf")
    for _ in range(veces):
        try:
            with socket.create_connection((host, puerto), timeout=espera_s) as s:
                s.settimeout(espera_s)
                s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
                t = time.perf_counter()
                s.sendall(bytes([RPC_CMD_HELLO]) + struct.pack("<Q", HELLO_TAM) + bytes(HELLO_TAM))
                cabecera = _recibir(s, 8)
                (tam,) = struct.unpack("<Q", cabecera)
                _recibir(s, tam)
                mejor = min(mejor, (time.perf_counter() - t) * 1000)
        except (OSError, ValueError):
            continue
    return mejor


def _recibir(s: socket.socket, n: int) -> bytes:
    datos = b""
    while len(datos) < n:
        trozo = s.recv(n - len(datos))
        if not trozo:
            raise ValueError("el servidor RPC cerró la conexión")
        datos += trozo
    return datos


def elegir(candidatos: list[Candidato], modelo_gb: float, propio_gb: float, margen: float = MARGEN,
           minimo: int = 0) -> list[Candidato]:
    """Los trabajadores a usar, en orden de menor latencia. `minimo` obliga a usar al menos esa cantidad
    (para pruebas o demostraciones). Lanza ValueError si no alcanza ni con todos."""
    falta = modelo_gb * margen - propio_gb
    vivos = sorted((c for c in candidatos if c.rtt_ms != float("inf") and c.vram_gb > 0), key=lambda c: c.rtt_ms)
    if len(vivos) < minimo:
        raise ValueError(f"se pidieron al menos {minimo} trabajadores y responden {len(vivos)}")
    elegidos: list[Candidato] = []
    for c in vivos:
        if falta <= 0 and len(elegidos) >= minimo:
            return elegidos
        elegidos.append(c)
        falta -= c.vram_gb
    if falta <= 0:
        return elegidos
    total = propio_gb + sum(c.vram_gb for c in vivos)
    raise ValueError(f"el modelo necesita ~{modelo_gb * margen:.1f} GB y entre todos hay {total:.1f} GB")

