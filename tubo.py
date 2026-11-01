"""Lado trabajador del tubo TCP del relé: ofrece un puerto local (p. ej. un ggml-rpc-server que solo
escucha en 127.0.0.1) para que la máquina del relé lo use como si fuera suyo.

    python -m fabric.tubo --rele buscar --clave ... --nombre trabajador-1 --puerto 50052

El relé abre en su propio 127.0.0.1 un puerto (50100, 50101...) y cada conexión que llega ahí viaja por
un WebSocket propio hasta aquí. Se reconecta solo si se cae el relé.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from fabric import descubrir

log = logging.getLogger("tubo")
TROZO = 256 * 1024
MAX_MENSAJE = 8 * 1024 * 1024


class Tubo:
    def __init__(self, rele: str, clave: str, nombre: str, puerto: int, host: str = "127.0.0.1") -> None:
        self.rele = rele
        self.clave = clave
        self.nombre = nombre
        self.puerto = puerto
        self.host = host
        self.puerto_remoto: int | None = None
        self.bytes = 0
        self.conexiones = 0
        self.listo = asyncio.Event()

    async def _url(self) -> str:
        url = await asyncio.to_thread(descubrir.rele, self.rele, self.clave, 600) if self.rele == "buscar" else self.rele
        return url.rstrip("/") + "/tubo"

    async def vivir(self) -> None:
        espera = 1.0
        while True:
            try:
                await self._una_sesion()
                espera = 1.0
            except (OSError, ConnectionClosed, asyncio.TimeoutError, RuntimeError) as e:
                log.warning("tubo caído (%s); reintento en %.0f s", e, espera)
            self.listo.clear()
            await asyncio.sleep(espera)
            espera = min(espera * 2, 30)

    async def _una_sesion(self) -> None:
        url = await self._url()
        async with connect(url, max_size=MAX_MENSAJE, open_timeout=30, compression=None) as control:
            await control.send(json.dumps({"t": "ofrezco", "clave": self.clave, "nombre": self.nombre}))
            hola = json.loads(await asyncio.wait_for(control.recv(), timeout=20))
            if hola.get("t") != "listo":
                raise RuntimeError(f"el relé no aceptó el tubo: {hola}")
            self.puerto_remoto = hola["puerto"]
            self.listo.set()
            log.info("tubo %s -> %s:%s listo (en el relé: 127.0.0.1:%s)", self.nombre, self.host, self.puerto, self.puerto_remoto)
            async for crudo in control:
                m = json.loads(crudo)
                if m.get("t") == "abre":
                    asyncio.create_task(self._unir(url, str(m["id"])))

    async def _unir(self, url: str, ident: str) -> None:
        try:
            lector, escritor = await asyncio.open_connection(self.host, self.puerto)
        except OSError as e:
            log.error("no hay nada escuchando en %s:%s (%s)", self.host, self.puerto, e)
            return
        self.conexiones += 1
        try:
            async with connect(url, max_size=MAX_MENSAJE, open_timeout=30, compression=None) as ws:
                await ws.send(json.dumps({"t": "datos", "clave": self.clave, "id": ident}))
                await canalizar(ws, lector, escritor, self)
        except (OSError, ConnectionClosed) as e:
            log.warning("conexión del tubo cerrada: %s", e)
        finally:
            escritor.close()


async def canalizar(ws: Any, lector: asyncio.StreamReader, escritor: asyncio.StreamWriter, cuenta: Any = None) -> None:
    async def hacia_ws() -> None:
        while trozo := await lector.read(TROZO):
            if cuenta is not None:
                cuenta.bytes += len(trozo)
            await ws.send(trozo)

    async def hacia_tcp() -> None:
        async for trozo in ws:
            if isinstance(trozo, bytes):
                if cuenta is not None:
                    cuenta.bytes += len(trozo)
                escritor.write(trozo)
                await escritor.drain()

    tareas = [asyncio.create_task(hacia_ws()), asyncio.create_task(hacia_tcp())]
    try:
        await asyncio.wait(tareas, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tareas:
            t.cancel()
        await asyncio.gather(*tareas, return_exceptions=True)


def argumentos(lista: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Ofrece un puerto local por el relé")
    p.add_argument("--rele", default=os.environ.get("FABRIC_RELE", "buscar"))
    p.add_argument("--clave", default=os.environ.get("FABRIC_CLAVE", ""))
    p.add_argument("--nombre", required=True)
    p.add_argument("--puerto", type=int, default=50052)
    return p.parse_args(lista)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    a = argumentos()
    asyncio.run(Tubo(a.rele, a.clave, a.nombre, a.puerto).vivir())
