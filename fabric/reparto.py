"""Reparto de un modelo entre varias computadoras (llama.cpp RPC por los tubos del relé).

Con llama.cpp las capas van en fila: cada token pasa por todos los equipos elegidos y cada salto
cruza internet. Más equipos = más memoria pero más latencia por token. Por eso el maestro usa el
MENOR número de trabajadores que alcance para el modelo, y entre ellos los de menor latencia:

    1. Mide la ida y vuelta de cada tubo con comandos del protocolo RPC sobre una conexión abierta
       (llega hasta el ggml-rpc-server del trabajador, no se queda en el relé).
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
RPC_CMD_DEVICE_COUNT = 15  # sin datos; responde 4 bytes. Sirve de ping sobre una conexión abierta
HELLO_TAM = 24  # sizeof(rpc_msg_hello_req) en llama.cpp b12024 (protocolo v8)
MARGEN = 1.25  # pesos x 1,25: caché KV, búferes de cálculo y lo que la GPU no deja usar


@dataclass
class Candidato:
    nombre: str
    puerto: int
    vram_gb: float
    rtt_ms: float = float("inf")


def medir_enlace(puerto: int, host: str = "127.0.0.1", veces: int = 5, espera_s: float = 30) -> dict[str, float]:
    """Mide un tubo hasta el ggml-rpc-server del trabajador, por UNA conexión.

    - `conexion_ms`: abrir la conexión y el saludo HELLO. Por el relé incluye abrir el tubo y que el
      trabajador conecte con su ggml-rpc-server, así que es mucho más que una ida y vuelta.
    - `rtt_ms`: la mejor de `veces` idas y vueltas con DEVICE_COUNT sobre la conexión ya abierta. Es lo
      que paga llama-server en cada token.

    Hasta el 4-nov-2026 se informaba solo `conexion_ms` como si fuera el ping (664 ms en Kaggle).
    Si algo falla devuelve inf en lo que no se pudo medir.
    """
    res = {"conexion_ms": float("inf"), "rtt_ms": float("inf")}
    try:
        t = time.perf_counter()
        with socket.create_connection((host, puerto), timeout=espera_s) as s:
            s.settimeout(espera_s)
            s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            s.sendall(bytes([RPC_CMD_HELLO]) + struct.pack("<Q", HELLO_TAM) + bytes(HELLO_TAM))
            _respuesta(s)
            res["conexion_ms"] = (time.perf_counter() - t) * 1000
            for _ in range(veces):
                t = time.perf_counter()
                s.sendall(bytes([RPC_CMD_DEVICE_COUNT]) + struct.pack("<Q", 0))
                _respuesta(s)
                res["rtt_ms"] = min(res["rtt_ms"], (time.perf_counter() - t) * 1000)
    except (OSError, ValueError):
        pass
    return res


def medir_rtt(puerto: int, host: str = "127.0.0.1", veces: int = 5, espera_s: float = 30) -> float:
    """Milisegundos de ida y vuelta por el tubo ya abierto (ver `medir_enlace`); inf si no responde."""
    return medir_enlace(puerto, host, veces, espera_s)["rtt_ms"]


def _respuesta(s: socket.socket) -> bytes:
    (tam,) = struct.unpack("<Q", _recibir(s, 8))
    return _recibir(s, tam)


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



def salida_en_el_maestro(gpus_locales: int) -> list[str]:
    """Opciones de llama-server para que la tabla de salida (y la de entrada, que en muchos modelos es la
    misma) quede en el maestro y nunca en un trabajador.

    llama.cpp manda la capa de salida al primer equipo de la lista de capas (src/llama-model.cpp:1690):
    si el maestro no tiene GPU, con cualquier -ngl la salida queda en el trabajador y en cada palabra
    vuelven por la red las probabilidades de todo el vocabulario (248.320 x 4 bytes = 993 KB en Qwen3.5).
    Medido el 4-nov-2026 en local con Qwen3.5-0.8B: lo que baja por palabra pasa de 993,3 KB a 4,1 KB.

    El patrón va anclado (^...$): sin anclas también atrapa `blk.N.attn_output.weight` de cada capa de
    atención completa, esas proyecciones quedan en el maestro y cada token rebota maestro-trabajador
    una vez más por cada una (medido el 4-nov-2026: 4 cálculos por enlace y por token en vez de 1).
    """
    destino = f"CUDA{gpus_locales - 1}" if gpus_locales > 0 else "CPU"
    return ["-ot", rf"^(token_embd|output)\.weight$={destino}"]


def mtp_en_el_maestro(gpus_locales: int, patron_capa: str = r"blk\.24\..*") -> list[str]:
    """Opciones de llama-server para colocar los pesos de la cabeza MTP en el maestro.

    NOTA DE RENDIMIENTO: Medido empíricamente el 4-nov-2026 en la tarea A7. En llama.cpp b12024,
    mover los pesos MTP con -ot al maestro NO reduce gamma; al contrario, lo dispara de ~0,81 a
    ~2,29 (a k=4) y duplica el tiempo por palabra (de 410,5 ms a 946,7 ms).
    Causa: -ot mueve solo los tensores de peso a CPU, pero dev_layer[24] y la memoria de atención
    asociada permanecen asignados a RPC0 en el trabajador, causando transferencias cruzadas de red
    en cada paso del draft.
    """
    destino = f"CUDA{gpus_locales - 1}" if gpus_locales > 0 else "CPU"
    return ["-ot", rf"{patron_capa}={destino}"]

