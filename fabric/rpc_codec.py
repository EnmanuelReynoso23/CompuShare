"""Códec `rpc1`: comprime sin pérdida el flujo maestro -> trabajador de ggml-rpc.

Especificación: `docs/CODEC-RPC.md`. El relé (`fabric/rele/rele.py`) pasa cada trozo crudo del TCP por
un `Codificador` y manda a WebSocket lo que devuelve; el tubo (`fabric/tubo.py`) pasa cada mensaje por un
`Decodificador` y escribe al TCP los bytes exactos. Ambos lados llevan su propia caché de grafos con la
misma secuencia de operaciones, así que no hace falta ningún mensaje extra.

Formato de cada registro (dentro de un mensaje WS) y decisiones que la especificación no fija y que aquí
quedan cerradas (el lado Go debe hacer lo mismo):

- GRAPH_DELTA toma como base cualquiera de las hasta 4 entradas más recientes de la caché con el mismo
  largo. Usar la base NO la reordena: solo GRAPH_REF marca como reciente.
- La cabecera de SET_TENSOR son 305 bytes: `rpc_tensor` (296, struct empaquetado) + `cache_flag` (u8) +
  `offset` (u64), según `ggml-rpc.cpp` de llama.cpp b11434 (`set_tensor`, línea ~1431). La especificación
  decía 304 porque omite `cache_flag`. El decodificador no interpreta la cabecera: usa el `hlen` del registro.
- Q8_0 cuantiza con la escala ya redondeada a float16 (la que lee el decodificador), no con la exacta.
"""

from __future__ import annotations

import hashlib
import math
import struct
import time
import zlib
from collections import OrderedDict
from typing import Any

# ── constantes del protocolo ggml-rpc (b11434) ─────────────────────────────────────────────────────────────

CABECERA = 9                       # cmd u8 + tamaño u64 LE
RPC_TENSOR = 296                   # sizeof(rpc_tensor), struct empaquetado (#pragma pack(1))
CABECERA_SET = RPC_TENSOR + 1 + 8  # SET_TENSOR: rpc_tensor | cache_flag u8 | offset u64 (= 305)
CMD_SET_TENSOR = 6
CMD_GRAPH_COMPUTE = 10
CMD_LIMITE = 64                    # cmd >= 64: flujo desconocido, se pasa crudo para siempre

CMD_NOMBRES = {
    0: "ALLOC_BUFFER", 1: "GET_ALIGNMENT", 2: "GET_MAX_SIZE", 3: "BUFFER_GET_BASE", 4: "FREE_BUFFER",
    5: "BUFFER_CLEAR", 6: "SET_TENSOR", 7: "SET_TENSOR_HASH", 8: "GET_TENSOR", 9: "COPY_TENSOR",
    10: "GRAPH_COMPUTE", 11: "GET_DEVICE_MEMORY", 12: "INIT_TENSOR", 13: "GET_ALLOC_SIZE", 14: "HELLO",
    15: "DEVICE_COUNT", 16: "GRAPH_RECOMPUTE", 17: "MEMSET_TENSOR",
}

# Tipos de registro del códec.
R_RAW, R_FULL, R_REF, R_DELTA, R_Q8 = 0x00, 0x01, 0x02, 0x03, 0x04
NOMBRE_REGISTRO = {R_RAW: "RAW", R_FULL: "GRAPH_FULL", R_REF: "GRAPH_REF", R_DELTA: "GRAPH_DELTA",
                   R_Q8: "SET_TENSOR_Q8"}

CACHE_ENTRADAS = 32
CACHE_BYTES = 64 << 20
CACHE_BASES = 4
GRAFO_MAX = 64 << 20
SET_BUFER_MAX = 16 << 20           # SET_TENSOR mayores se pasan crudos (no se almacenan)
Q8_MIN_FLOATS = 1024
NIVEL_ZLIB = 6
RAW_TROZO_MAX = 16 << 20

# Modos del codificador para un comando en curso.
_RAW, _GRAFO, _SET = "raw", "grafo", "set"


class ErrorCodec(Exception):
    """Flujo inválido: hash de caché desconocido, tipo de registro desconocido o zlib corrupto."""


def nombre_comando(cmd: int) -> str:
    return CMD_NOMBRES.get(cmd, f"CMD_{cmd}")


def _u32(n: int) -> bytes:
    return struct.pack("<I", n)


def _u64(n: int) -> bytes:
    return struct.pack("<Q", n)


def _cabecera(cmd: int, tam: int) -> bytes:
    return bytes([cmd]) + _u64(tam)


def _hash(payload: bytes) -> bytes:
    return hashlib.sha256(payload).digest()[:16]


def _xor(a: bytes, b: bytes) -> bytes:
    """XOR de dos bytestrings de igual largo sin numpy (los enteros de Python son rápidos aquí)."""
    return (int.from_bytes(a, "little") ^ int.from_bytes(b, "little")).to_bytes(len(a), "little")


def _descomprimir(z: bytes, largo: int) -> bytes:
    try:
        d = zlib.decompressobj()
        salida = d.decompress(z, largo + 1)
    except zlib.error as e:
        raise ErrorCodec(f"zlib corrupto: {e}") from e
    if len(salida) != largo:
        raise ErrorCodec(f"zlib da {len(salida)} bytes y se esperaban {largo}")
    return salida


def _leer(datos: bytes, pos: int, n: int) -> tuple[bytes, int]:
    if pos + n > len(datos):
        raise ErrorCodec("registro truncado")
    return datos[pos:pos + n], pos + n


def _leer_u32(datos: bytes, pos: int) -> tuple[int, int]:
    b, pos = _leer(datos, pos, 4)
    return struct.unpack("<I", b)[0], pos


def _leer_u64(datos: bytes, pos: int) -> tuple[int, int]:
    b, pos = _leer(datos, pos, 8)
    return struct.unpack("<Q", b)[0], pos


# ── parser incremental de pedidos ──────────────────────────────────────────────────────────────────────────


class ParserPedidos:
    """Parte el flujo de pedidos `cmd u8 | tamaño u64 LE | datos` en eventos, tolerando cortes en cualquier
    byte. `alimentar` devuelve una lista de tuplas `(tipo, cmd, tamaño, datos)` con tipo en
    `"cabecera"` (llega el encabezado completo), `"datos"` (un trozo del cuerpo) o `"fin"` (cuerpo completo).
    Los cuerpos de tamaño 0 emiten cabecera y fin seguidos."""

    def __init__(self) -> None:
        self._cab = bytearray()
        self._en_datos = False
        self._cmd = 0
        self._tam = 0
        self._resta = 0

    def alimentar(self, datos: bytes) -> list[tuple[str, int, int, bytes]]:
        eventos: list[tuple[str, int, int, bytes]] = []
        pos, n = 0, len(datos)
        while pos < n:
            if not self._en_datos:
                parte = datos[pos:pos + CABECERA - len(self._cab)]
                self._cab += parte
                pos += len(parte)
                if len(self._cab) < CABECERA:
                    continue
                self._cmd = self._cab[0]
                self._tam = struct.unpack_from("<Q", self._cab, 1)[0]
                self._resta = self._tam
                self._cab = bytearray()
                self._en_datos = True
                eventos.append(("cabecera", self._cmd, self._tam, b""))
                if self._tam == 0:
                    eventos.append(("fin", self._cmd, self._tam, b""))
                    self._en_datos = False
            else:
                k = min(self._resta, n - pos)
                eventos.append(("datos", self._cmd, self._tam, datos[pos:pos + k]))
                pos += k
                self._resta -= k
                if self._resta == 0:
                    eventos.append(("fin", self._cmd, self._tam, b""))
                    self._en_datos = False
        return eventos


# ── estadísticas ───────────────────────────────────────────────────────────────────────────────────────────


class Estadisticas:
    """Bytes de entrada y salida del códec, por comando y por tipo de registro.

    En el codificador `crudo` son los bytes del TCP y `enviado` los del WebSocket. En el decodificador,
    `crudo` son los bytes reconstruidos para el TCP y `enviado` los recibidos del WebSocket. En `comandos`
    `crudo` es cabecera + cuerpo del pedido y `enviado` lo que sale por el WS para ese comando (el cuerpo
    crudo o el registro codificado); el marco de los RAW va solo en `registros`."""

    def __init__(self) -> None:
        self.crudo = 0
        self.enviado = 0
        self.comandos: dict[str, list[int]] = {}   # nombre -> [pedidos, crudo, enviado]
        self.registros: dict[str, list[int]] = {}  # tipo -> [registros, bytes]

    def entrada(self, crudo: int, enviado: int) -> None:
        self.crudo += crudo
        self.enviado += enviado

    def comando(self, nombre: str, crudo: int) -> None:
        fila = self.comandos.setdefault(nombre, [0, 0, 0])
        fila[0] += 1
        fila[1] += crudo

    def comando_enviado(self, nombre: str, n: int) -> None:
        self.comandos.setdefault(nombre, [0, 0, 0])[2] += n

    def registro(self, tipo: str, n: int) -> None:
        fila = self.registros.setdefault(tipo, [0, 0])
        fila[0] += 1
        fila[1] += n

    def ahorro(self) -> float:
        return 1.0 - self.enviado / self.crudo if self.crudo else 0.0

    def resumen(self) -> dict[str, Any]:
        return {
            "crudo": self.crudo,
            "enviado": self.enviado,
            "ahorro": round(self.ahorro(), 4),
            "comandos": {k: {"pedidos": v[0], "crudo": v[1], "enviado": v[2]} for k, v in self.comandos.items()},
            "registros": {k: {"n": v[0], "bytes": v[1]} for k, v in self.registros.items()},
        }


# ── caché de grafos ────────────────────────────────────────────────────────────────────────────────────────


class CacheGrafos:
    """Caché `hash -> payload` idéntica en los dos lados. Límite: 32 entradas y 64 MiB; se sacan las más
    viejas. `tocar` (REF) la marca como reciente; `obtener` (base de DELTA) no la reordena."""

    def __init__(self) -> None:
        self._d: OrderedDict[bytes, bytes] = OrderedDict()
        self._bytes = 0

    def __contains__(self, h: bytes) -> bool:
        return h in self._d

    def obtener(self, h: bytes) -> bytes:
        try:
            return self._d[h]
        except KeyError:
            raise ErrorCodec("grafo no está en la caché (hash desconocido)") from None

    def tocar(self, h: bytes) -> None:
        self._d.move_to_end(h)

    def insertar(self, h: bytes, payload: bytes) -> None:
        if h in self._d:
            self._d.move_to_end(h)
            return
        self._d[h] = payload
        self._bytes += len(payload)
        while self._d and (len(self._d) > CACHE_ENTRADAS or self._bytes > CACHE_BYTES):
            _, viejo = self._d.popitem(last=False)
            self._bytes -= len(viejo)

    def bases(self, largo: int, n: int) -> list[tuple[bytes, bytes]]:
        """Hasta `n` entradas del mismo largo, de la más reciente a la más vieja, sin reordenar."""
        salida: list[tuple[bytes, bytes]] = []
        for h in reversed(self._d):
            if len(self._d[h]) == largo:
                salida.append((h, self._d[h]))
                if len(salida) == n:
                    break
        return salida


# ── Q8_0 ───────────────────────────────────────────────────────────────────────────────────────────────────


def _numpy() -> Any:
    """numpy si está instalado (se importa solo la primera vez), o None."""
    global _NP
    if _NP is _SIN_CARGAR:
        try:
            import numpy
            _NP = numpy
        except ImportError:
            _NP = None
    return _NP


_SIN_CARGAR = object()
_NP: Any = _SIN_CARGAR


def _q8_codificar(datos: bytes, n: int) -> bytes | None:
    """Bloques Q8_0 de `n` float32 (34 bytes por bloque de 32 valores), o None si no se puede cuantizar:
    algún valor no finito, una escala fuera de float16 o una escala que se redondea a cero con datos."""
    np = _numpy()
    if np is not None:
        return _q8_codificar_np(np, datos, n)
    return _q8_codificar_py(datos, n)


def _q8_codificar_np(np: Any, datos: bytes, n: int) -> bytes | None:
    x = np.frombuffer(datos, dtype="<f4", count=n).astype(np.float64)
    if not np.isfinite(x).all():
        return None
    nb = -(-n // 32)
    m = np.zeros(nb * 32, dtype=np.float64)
    m[:n] = x
    m = m.reshape(nb, 32)
    amax = np.abs(m).max(axis=1)
    d = amax / 127.0
    if (d > 65504.0).any():
        return None
    dh = d.astype("<f2")
    if ((dh == 0) & (amax > 0)).any():
        return None
    dh64 = dh.astype(np.float64)
    den = np.where(dh64 > 0, dh64, 1.0)
    v = m / den[:, None]
    a = np.abs(v)
    f = np.floor(a)
    r = np.minimum(f + ((a - f) >= 0.5), 127.0)   # redondeo a media unidad, lejos de cero, como roundf
    q = np.where(v < 0, -r, r).astype(np.int8)
    out = np.empty((nb, 34), dtype=np.uint8)
    out[:, :2] = dh.view(np.uint8).reshape(nb, 2)
    out[:, 2:] = q.view(np.uint8)
    return out.tobytes()


def _q8_codificar_py(datos: bytes, n: int) -> bytes | None:
    x = struct.unpack(f"<{n}f", datos)
    if not all(math.isfinite(v) for v in x):
        return None
    out = bytearray()
    for ini in range(0, n, 32):
        bloque = x[ini:ini + 32]
        if len(bloque) < 32:
            bloque = bloque + (0.0,) * (32 - len(bloque))
        amax = max(abs(v) for v in bloque)
        d = amax / 127.0
        if d > 65504.0:
            return None
        dh = struct.unpack("<e", struct.pack("<e", d))[0]
        if dh == 0.0:
            if amax > 0:
                return None
            out += struct.pack("<e", 0.0) + bytes(32)
            continue
        q = []
        for v in bloque:
            a = abs(v) / dh
            f = math.floor(a)
            r = f + (1 if a - f >= 0.5 else 0)
            if r > 127:
                r = 127
            q.append(-r if v < 0 else r)
        out += struct.pack("<e", dh) + bytes(qq & 0xFF for qq in q)
    return bytes(out)


def _q8_a_f32(bloques: bytes, n: int) -> bytes:
    """Reconstruye `n` float32 (little endian) a partir de los bloques Q8_0."""
    if n == 0:
        return b""
    np = _numpy()
    if np is not None:
        return _q8_a_f32_np(np, bloques, n)
    return _q8_a_f32_py(bloques, n)


def _q8_a_f32_np(np: Any, bloques: bytes, n: int) -> bytes:
    nb = -(-n // 32)
    a = np.frombuffer(bloques, dtype=np.uint8, count=nb * 34).reshape(nb, 34)
    dh = np.ascontiguousarray(a[:, :2]).view("<f2").reshape(nb).astype(np.float32)
    q = np.ascontiguousarray(a[:, 2:]).view(np.int8).astype(np.float32)
    vals = (dh[:, None] * q).reshape(-1)[:n]
    return vals.astype("<f4").tobytes()


def _q8_a_f32_py(bloques: bytes, n: int) -> bytes:
    nb = -(-n // 32)
    out = bytearray()
    for b in range(nb):
        base = b * 34
        dh = struct.unpack_from("<e", bloques, base)[0]
        qs = struct.unpack_from("<32b", bloques, base + 2)
        out += struct.pack("<32f", *[dh * q for q in qs])
    return bytes(out[:4 * n])


# ── codificador (lado relé / maestro) ──────────────────────────────────────────────────────────────────────


class Codificador:
    """Un flujo TCP maestro -> trabajador. `codificar(trozo)` devuelve bytes para un mensaje WS (puede estar
    vacío si el trozo solo deja a medias un grafo). Una instancia por conexión: la caché es de la conexión."""

    def __init__(self, q8: bool = False, estad: Estadisticas | None = None) -> None:
        self.q8 = bool(q8)
        self.estad = estad if estad is not None else Estadisticas()
        self._parser = ParserPedidos()
        self._cache = CacheGrafos()
        self._sin_interpretar = False
        self._grafo_visto = False
        self._actual: dict[str, Any] | None = None
        self._raw: list[tuple[str, bytes]] = []
        self._salida: list[bytes] = []

    def codificar(self, trozo: bytes) -> bytes:
        self._salida = []
        for evento in self._parser.alimentar(trozo):
            self._evento(*evento)
        self._volcar_raw()
        res = b"".join(self._salida)
        self._salida = []
        self.estad.entrada(len(trozo), len(res))
        return res

    def _evento(self, tipo: str, cmd: int, tam: int, datos: bytes) -> None:
        if tipo == "cabecera":
            nombre = nombre_comando(cmd)
            self.estad.comando(nombre, CABECERA + tam)
            if cmd >= CMD_LIMITE:
                self._sin_interpretar = True
            modo = _RAW
            if not self._sin_interpretar:
                if cmd == CMD_GRAPH_COMPUTE:
                    self._grafo_visto = True
                    if tam <= GRAFO_MAX:
                        modo = _GRAFO
                elif (cmd == CMD_SET_TENSOR and self.q8 and self._grafo_visto
                      and CABECERA_SET < tam <= SET_BUFER_MAX and (tam - CABECERA_SET) % 4 == 0):
                    modo = _SET
            self._actual = {"cmd": cmd, "nombre": nombre, "tam": tam, "modo": modo, "partes": []}
            if modo == _RAW:
                self._agregar_raw(nombre, _cabecera(cmd, tam))
        elif tipo == "datos":
            actual = self._actual
            if actual["modo"] == _RAW:
                self._agregar_raw(actual["nombre"], datos)
            else:
                actual["partes"].append(datos)
        else:  # fin
            actual = self._actual
            self._actual = None
            if actual["modo"] == _RAW:
                return
            payload = b"".join(actual["partes"])
            if actual["modo"] == _GRAFO:
                rec = self._grafo(payload)
            else:
                rec = self._set(payload)
            if rec is None:
                self._agregar_raw(actual["nombre"], _cabecera(actual["cmd"], actual["tam"]) + payload)
            else:
                self._registrar(actual["nombre"], rec)

    def _agregar_raw(self, nombre: str, datos: bytes) -> None:
        self._raw.append((nombre, datos))
        self.estad.comando_enviado(nombre, len(datos))

    def _volcar_raw(self) -> None:
        if not self._raw:
            return
        datos = b"".join(p for _, p in self._raw)
        self._raw = []
        for ini in range(0, len(datos), RAW_TROZO_MAX):
            trozo = datos[ini:ini + RAW_TROZO_MAX]
            rec = bytes([R_RAW]) + _u32(len(trozo)) + trozo
            self._salida.append(rec)
            self.estad.registro(NOMBRE_REGISTRO[R_RAW], len(rec))

    def _registrar(self, nombre: str, rec: bytes) -> None:
        self._volcar_raw()  # los RAW anteriores van antes para mantener el orden
        self._salida.append(rec)
        self.estad.comando_enviado(nombre, len(rec))
        self.estad.registro(NOMBRE_REGISTRO[rec[0]], len(rec))

    def _grafo(self, payload: bytes) -> bytes | None:
        largo = len(payload)
        h = _hash(payload)
        if h in self._cache:
            self._cache.tocar(h)
            return bytes([R_REF]) + h
        zf = zlib.compress(payload, NIVEL_ZLIB)
        mejor = bytes([R_FULL]) + _u32(largo) + _u32(len(zf)) + zf
        for hb, base in self._cache.bases(largo, CACHE_BASES):
            zd = zlib.compress(_xor(base, payload), NIVEL_ZLIB)
            if 1 + 16 + 4 + 4 + len(zd) < len(mejor):
                mejor = bytes([R_DELTA]) + hb + _u32(largo) + _u32(len(zd)) + zd
        if len(mejor) >= CABECERA + largo:
            return None  # no sale más chico que crudo
        self._cache.insertar(h, payload)
        return mejor

    def _set(self, payload: bytes) -> bytes | None:
        if payload[8:12] != b"\x00\x00\x00\x00":  # rpc_tensor.type != F32
            return None
        n = (len(payload) - CABECERA_SET) // 4
        if n < Q8_MIN_FLOATS:
            return None
        bloques = _q8_codificar(payload[CABECERA_SET:], n)
        if bloques is None:
            return None
        cab = payload[:CABECERA_SET]
        return bytes([R_Q8]) + _u32(len(cab)) + cab + _u64(n) + bloques


# ── decodificador (lado tubo / trabajador) ─────────────────────────────────────────────────────────────────


class Decodificador:
    """Un flujo WS -> TCP. `decodificar(mensaje)` devuelve los bytes exactos del flujo original. Una instancia
    por conexión. Lanza ErrorCodec ante un hash desconocido, un tipo desconocido o un registro truncado."""

    def __init__(self, estad: Estadisticas | None = None) -> None:
        self.estad = estad if estad is not None else Estadisticas()
        self._cache = CacheGrafos()

    def decodificar(self, mensaje: bytes) -> bytes:
        salida: list[bytes] = []
        pos, n = 0, len(mensaje)
        while pos < n:
            inicio = pos
            tipo = mensaje[pos]
            pos += 1
            if tipo == R_RAW:
                tam, pos = _leer_u32(mensaje, pos)
                datos, pos = _leer(mensaje, pos, tam)
                salida.append(datos)
            elif tipo == R_FULL:
                largo, pos = _leer_u32(mensaje, pos)
                zlen, pos = _leer_u32(mensaje, pos)
                z, pos = _leer(mensaje, pos, zlen)
                payload = _descomprimir(z, largo)
                self._cache.insertar(_hash(payload), payload)
                salida.append(bytes([CMD_GRAPH_COMPUTE]) + _u64(largo) + payload)
            elif tipo == R_REF:
                h, pos = _leer(mensaje, pos, 16)
                payload = self._cache.obtener(h)
                self._cache.tocar(h)
                salida.append(bytes([CMD_GRAPH_COMPUTE]) + _u64(len(payload)) + payload)
            elif tipo == R_DELTA:
                hb, pos = _leer(mensaje, pos, 16)
                largo, pos = _leer_u32(mensaje, pos)
                zlen, pos = _leer_u32(mensaje, pos)
                z, pos = _leer(mensaje, pos, zlen)
                base = self._cache.obtener(hb)
                if len(base) != largo:
                    raise ErrorCodec("la base de GRAPH_DELTA tiene otro largo")
                payload = _xor(base, _descomprimir(z, largo))
                self._cache.insertar(_hash(payload), payload)
                salida.append(bytes([CMD_GRAPH_COMPUTE]) + _u64(largo) + payload)
            elif tipo == R_Q8:
                hlen, pos = _leer_u32(mensaje, pos)
                cab, pos = _leer(mensaje, pos, hlen)
                nval, pos = _leer_u64(mensaje, pos)
                bloques, pos = _leer(mensaje, pos, (-(-nval // 32)) * 34)
                salida.append(bytes([CMD_SET_TENSOR]) + _u64(hlen + 4 * nval) + cab
                              + _q8_a_f32(bloques, nval))
            else:
                raise ErrorCodec(f"tipo de registro desconocido {tipo:#04x}")
            self.estad.registro(NOMBRE_REGISTRO[tipo], pos - inicio)
        res = b"".join(salida)
        self.estad.entrada(len(res), n)
        return res
