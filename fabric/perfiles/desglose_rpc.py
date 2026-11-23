"""Desglose de bytes del protocolo ggml-rpc: un proxy TCP entre llama-server y un puerto RPC.

    python -m fabric.perfiles.desglose_rpc --escucha 50200 --destino 50100 [--salida desglose.json]

Se pone entre `llama-server --rpc` y el puerto que habla con el tubo (o con un ggml-rpc-server directo) y
cuenta, por comando y por dirección, cuántos bytes pasan. Las respuestas no llevan comando: se atribuyen al
pedido más antiguo que espera respuesta (el servidor contesta en orden). Solo contestan HELLO, DEVICE_COUNT,
ALLOC_BUFFER, GET_ALLOC_SIZE, GET_ALIGNMENT, GET_MAX_SIZE, BUFFER_GET_BASE, SET_TENSOR_HASH, GET_TENSOR,
COPY_TENSOR y GET_DEVICE_MEMORY (`ggml-rpc.cpp`, b11434).

Además corre el `Codificador` de `fabric/rpc_codec.py` sobre el flujo de pedidos, para informar cuánto
ahorraría el códec `rpc1`. No altera el flujo: lo reenvía tal cual.

Escribe un JSON cada segundo (con `--salida`) y un resumen al cerrar (Ctrl-C).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import struct
import time
from collections import deque
from typing import Any

from fabric.rpc_codec import Codificador, Estadisticas, ParserPedidos, nombre_comando

# Comandos a los que el servidor responde con `tamaño u64 | datos` (ggml-rpc.cpp, b11434).
CON_RESPUESTA = frozenset({0, 1, 2, 3, 7, 8, 9, 11, 13, 14, 15})
TROZO = 256 * 1024
SIN_COMANDO = "(respuesta sin pedido)"


class Desglose:
    def __init__(self, q8: bool = False) -> None:
        self.q8 = q8
        self.comandos: dict[str, dict[str, int]] = {}
        self.pedidos_bytes = 0
        self.respuestas_bytes = 0
        self.sin_comando_bytes = 0
        self.conexiones = 0
        self.codec = Estadisticas()
        self.codec_segundos = 0.0
        self.inicio = time.monotonic()

    def _fila(self, nombre: str) -> dict[str, int]:
        return self.comandos.setdefault(nombre, {"pedidos": 0, "bytes_pedidos": 0, "bytes_respuestas": 0})

    def pedido(self, cmd: int, tam: int, cola: deque[int]) -> None:
        fila = self._fila(nombre_comando(cmd))
        fila["pedidos"] += 1
        fila["bytes_pedidos"] += 9 + tam
        self.pedidos_bytes += 9 + tam
        if cmd in CON_RESPUESTA:
            cola.append(cmd)

    def respuesta(self, cmd: int | None, n: int) -> None:
        self.respuestas_bytes += n
        if cmd is None:
            self.sin_comando_bytes += n
        else:
            self._fila(nombre_comando(cmd))["bytes_respuestas"] += n

    def resumen(self) -> dict[str, Any]:
        cr = self.codec.resumen()
        comandos = dict(sorted(self.comandos.items(), key=lambda kv: -kv[1]["bytes_pedidos"]))
        return {
            "segundos": round(time.monotonic() - self.inicio, 1),
            "conexiones": self.conexiones,
            "pedidos_bytes": self.pedidos_bytes,
            "respuestas_bytes": self.respuestas_bytes,
            "respuestas_sin_pedido_bytes": self.sin_comando_bytes,
            "grafos": {
                "GRAPH_COMPUTE": self.comandos.get("GRAPH_COMPUTE", {}).get("pedidos", 0),
                "GRAPH_RECOMPUTE": self.comandos.get("GRAPH_RECOMPUTE", {}).get("pedidos", 0),
            },
            "comandos": comandos,
            "codec": {
                "crudo": cr["crudo"],
                "enviado": cr["enviado"],
                "ahorro": cr["ahorro"],
                "segundos_codificando": round(self.codec_segundos, 4),
                "registros": cr["registros"],
            },
        }

    def escribir(self, ruta: str | None) -> None:
        if not ruta:
            return
        tmp = ruta + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.resumen(), f, ensure_ascii=False, indent=1)
        os.replace(tmp, ruta)

    async def volcar_cada_segundo(self, ruta: str | None) -> None:
        while True:
            await asyncio.sleep(1)
            self.escribir(ruta)

    def imprimir(self) -> None:
        r = self.resumen()
        total = max(1, r["pedidos_bytes"])
        print(f"conexiones: {r['conexiones']}  segundos: {r['segundos']}")
        print(f"pedidos (salida -> servidor): {r['pedidos_bytes']:,} bytes".replace(",", "."))
        print(f"respuestas (servidor -> salida): {r['respuestas_bytes']:,} bytes".replace(",", "."))
        print(f"GRAPH_COMPUTE: {r['grafos']['GRAPH_COMPUTE']}  GRAPH_RECOMPUTE: {r['grafos']['GRAPH_RECOMPUTE']}")
        print("comandos por bytes de pedido:")
        for nombre, fila in list(r["comandos"].items())[:12]:
            print(f"  {nombre:<20} {fila['pedidos']:>7} pedidos {fila['bytes_pedidos']:>14,} B "
                  f"{100 * fila['bytes_pedidos'] / total:6.1f} %  respuestas {fila['bytes_respuestas']:,} B"
                  .replace(",", "."))
        c = r["codec"]
        mb = max(c["crudo"], 1) / 1e6
        print(f"codec rpc1 (estimado): {c['crudo']:,} -> {c['enviado']:,} B, ahorro {100 * c['ahorro']:.1f} %, "
              f"{c['segundos_codificando']:.3f} s codificando ({c['segundos_codificando'] / mb:.4f} s por MB)"
              .replace(",", "."))

    async def _atender(self, cli_r: asyncio.StreamReader, cli_w: asyncio.StreamWriter,
                       host: str, puerto: int) -> None:
        try:
            srv_r, srv_w = await asyncio.open_connection(host, puerto)
        except OSError:
            cli_w.close()
            return
        self.conexiones += 1
        cola: deque[int] = deque()
        cod = Codificador(q8=self.q8, estad=self.codec)
        resp = _ParserRespuestas(cola, self)

        async def pedidos() -> None:
            parser = ParserPedidos()
            while trozo := await cli_r.read(TROZO):
                for tipo, cmd, tam, _ in parser.alimentar(trozo):
                    if tipo == "cabecera":
                        self.pedido(cmd, tam, cola)
                t0 = time.perf_counter()
                cod.codificar(trozo)
                self.codec_segundos += time.perf_counter() - t0
                srv_w.write(trozo)
                await srv_w.drain()
            if srv_w.can_write_eof():
                srv_w.write_eof()

        async def respuestas() -> None:
            while trozo := await srv_r.read(TROZO):
                resp.alimentar(trozo)
                cli_w.write(trozo)
                await cli_w.drain()

        try:
            await asyncio.gather(pedidos(), respuestas())
        except (OSError, ConnectionError):
            pass
        finally:
            for w in (srv_w, cli_w):
                w.close()

    def servir(self, escucha_host: str, escucha: int, destino_host: str, destino: int) -> Any:
        return asyncio.start_server(lambda r, w: self._atender(r, w, destino_host, destino), escucha_host, escucha)


class _ParserRespuestas:
    """Respuestas `tamaño u64 | datos`: cada una se atribuye al comando que espera (FIFO)."""

    def __init__(self, cola: deque[int], desglose: Desglose) -> None:
        self._cola = cola
        self._d = desglose
        self._cab = bytearray()
        self._resta: int | None = None
        self._cmd: int | None = None

    def alimentar(self, datos: bytes) -> None:
        pos, n = 0, len(datos)
        while pos < n:
            if self._resta is None:
                parte = datos[pos:pos + 8 - len(self._cab)]
                self._cab += parte
                pos += len(parte)
                if len(self._cab) < 8:
                    continue
                tam = struct.unpack("<Q", bytes(self._cab))[0]
                self._cab = bytearray()
                self._cmd = self._cola.popleft() if self._cola else None
                self._d.respuesta(self._cmd, 8)
                self._resta = tam if tam > 0 else None
            else:
                k = min(self._resta, n - pos)
                self._d.respuesta(self._cmd, k)
                pos += k
                self._resta -= k
                if self._resta == 0:
                    self._resta = None


async def principal(a: argparse.Namespace) -> None:
    d = Desglose(q8=a.q8)
    servidor = await d.servir(a.escucha_host, a.escucha, a.host_destino, a.destino)
    tarea = asyncio.create_task(d.volcar_cada_segundo(a.salida))
    print(f"desglose_rpc: {a.escucha_host}:{a.escucha} -> {a.host_destino}:{a.destino}", flush=True)
    try:
        async with servidor:
            await servidor.serve_forever()
    finally:
        tarea.cancel()
        d.escribir(a.salida)
        d.imprimir()


def argumentos(lista: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Desglose de bytes ggml-rpc entre llama-server y un puerto RPC")
    p.add_argument("--escucha", type=int, required=True, help="puerto donde escucha llama-server")
    p.add_argument("--destino", type=int, required=True, help="puerto RPC real (tubo o ggml-rpc-server)")
    p.add_argument("--escucha-host", default="127.0.0.1")
    p.add_argument("--host-destino", default="127.0.0.1")
    p.add_argument("--salida", help="archivo JSON que se actualiza cada segundo")
    p.add_argument("--q8", action="store_true", help="estimar también con Q8_0 en las activaciones")
    return p.parse_args(lista)


if __name__ == "__main__":
    try:
        asyncio.run(principal(argumentos()))
    except KeyboardInterrupt:
        pass
