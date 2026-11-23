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

from fabric import descubrir, rpc_codec

log = logging.getLogger("tubo")
TROZO = 256 * 1024
MAX_MENSAJE = 8 * 1024 * 1024
MAX_MENSAJE_CODEC = 80 * 1024 * 1024  # un registro del códec puede llevar un grafo entero (como en Go)
_CEROS = bytes(TROZO)  # relleno de la sonda de subida (ver rele.sonda_banda)


class Tubo:
    def __init__(self, rele: str, clave: str, nombre: str, puerto: int, host: str = "127.0.0.1",
                 info: dict | None = None, codec: bool = True) -> None:
        self.rele = rele
        # Con FABRIC_PERFIL=1 no se negocia el códec: el camino instrumentado va crudo.
        self.codec = codec and os.environ.get("FABRIC_PERFIL") != "1"
        self.clave = clave
        self.nombre = nombre
        self.puerto = puerto
        self.host = host
        self.info = info or {}
        self.puerto_remoto: int | None = None
        self.bytes = 0
        self.conexiones = 0
        self.listo = asyncio.Event()
        self._bajada: dict[str, Any] | None = None  # prueba de bajada en curso (sonda de banda)

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
            oferta = {"t": "ofrezco", "clave": self.clave, "nombre": self.nombre, "info": self.info}
            if self.codec:
                oferta["codecs"] = ["rpc1"]
            await control.send(json.dumps(oferta))
            hola = json.loads(await asyncio.wait_for(control.recv(), timeout=20))
            if hola.get("t") != "listo":
                raise RuntimeError(f"el relé no aceptó el tubo: {hola}")
            self.puerto_remoto = hola["puerto"]
            self.listo.set()
            log.info("tubo %s -> %s:%s listo (en el relé: 127.0.0.1:%s)", self.nombre, self.host, self.puerto, self.puerto_remoto)
            async for crudo in control:
                if isinstance(crudo, bytes):  # solo llegan en la sonda de banda; si no se esperan, se ignoran
                    await self._recibir_banda(crudo, control)
                    continue
                try:
                    m = json.loads(crudo)
                except ValueError:
                    continue
                if not isinstance(m, dict):
                    continue
                if m.get("t") == "abre":
                    asyncio.create_task(self._unir(url, str(m["id"]), m.get("codec")))
                elif m.get("t") == "banda_bajada":
                    await self._banda_bajada(control, m)
                elif m.get("t") == "banda":
                    asyncio.create_task(self._enviar_banda(control, m))
                elif os.environ.get("FABRIC_PERFIL") == "1" and m.get("t") == "perfil_fase":
                    from fabric import perfil_contador
                    if m.get("accion") == "iniciar":
                        perfil_contador.iniciar_fase_global(m["nombre"])
                    elif m.get("accion") == "terminar":
                        perfil_contador.terminar_fase_global(m["nombre"])

    # ── sonda de ancho de banda (ver rele.sonda_banda) ─────────────────────

    async def _banda_bajada(self, control: Any, m: dict) -> None:
        """El relé va a mandar `bytes` binarios: avisa que está listo y cuenta hasta el final."""
        n = int(m.get("bytes", 0))
        if n <= 0:
            return
        self._bajada = {"id": str(m.get("id", "")), "esperado": n, "recibido": 0}
        await control.send(json.dumps({"t": "banda_preparada", "id": self._bajada["id"]}))

    async def _recibir_banda(self, trozo: bytes, control: Any) -> None:
        b = self._bajada
        if b is None:
            return  # binario inesperado: se ignora
        b["recibido"] += len(trozo)
        if b["recibido"] >= b["esperado"]:
            self._bajada = None
            await control.send(json.dumps({"t": "banda_ok", "id": b["id"], "bytes": b["esperado"]}))

    async def _enviar_banda(self, control: Any, m: dict) -> None:
        """Manda `bytes` binarios y cierra con banda_fin. Corre en su propia tarea: no frena el control."""
        ident = str(m.get("id", ""))
        restante = int(m.get("bytes", 0))
        try:
            while restante > 0:
                k = min(TROZO, restante)
                await control.send(_CEROS if k == TROZO else _CEROS[:k])
                restante -= k
            await control.send(json.dumps({"t": "banda_fin", "id": ident}))
        except ConnectionClosed:
            pass

    async def _unir(self, url: str, ident: str, codec: str | None = None) -> None:
        # El códec de esta conexión es exactamente el que diga `abre` (ausente = crudo).
        decodificador = None
        if codec is not None:
            if codec != "rpc1" or not self.codec:
                log.error("el relé pide el códec %r y este tubo no lo entiende", codec)
                return
            decodificador = rpc_codec.Decodificador()
        try:
            lector, escritor = await asyncio.open_connection(self.host, self.puerto)
        except OSError as e:
            log.error("no hay nada escuchando en %s:%s (%s)", self.host, self.puerto, e)
            return
        self.conexiones += 1
        try:
            limite = MAX_MENSAJE_CODEC if decodificador is not None else MAX_MENSAJE
            async with connect(url, max_size=limite, open_timeout=30, compression=None) as ws:
                await ws.send(json.dumps({"t": "datos", "clave": self.clave, "id": ident}))
                await canalizar(ws, lector, escritor, self, decodificador)
        except (OSError, ConnectionClosed, rpc_codec.ErrorCodec) as e:
            log.warning("conexión del tubo cerrada: %s", e)
        finally:
            escritor.close()


async def canalizar(ws: Any, lector: asyncio.StreamReader, escritor: asyncio.StreamWriter, cuenta: Any = None,
                    decodificador: Any = None) -> None:
    if os.environ.get("FABRIC_PERFIL") == "1":
        from fabric.perfil_contador import canalizar_instrumentado
        await canalizar_instrumentado(ws, lector, escritor, TROZO, "tubo", cuenta)
        return

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
                plano = decodificador.decodificar(trozo) if decodificador is not None else trozo
                if plano:
                    escritor.write(plano)
                    await escritor.drain()

    tareas = [asyncio.create_task(hacia_ws()), asyncio.create_task(hacia_tcp())]
    try:
        hechas, _ = await asyncio.wait(tareas, return_when=asyncio.FIRST_COMPLETED)
        for t in hechas:
            if not t.cancelled() and t.exception() is not None:
                raise t.exception()  # p. ej. ErrorCodec: que llegue a _unir y cierre la conexión
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
    p.add_argument("--vram-gb", type=float, default=0, help="memoria que presta (para el reparto del maestro)")
    return p.parse_args(lista)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    a = argumentos()
    asyncio.run(Tubo(a.rele, a.clave, a.nombre, a.puerto, info={"vram_gb": a.vram_gb} if a.vram_gb else None).vivir())
