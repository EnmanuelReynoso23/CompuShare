"""Cliente de la red para Python: pedir trabajo a los nodos a través del relé.

    async with ClienteRed(url, clave, enjambre) as red:
        texto = await red.pedir("generar", {"mensajes": [...]}, al_parcial=print)

`pedir_sync` hace lo mismo sin asyncio (para el servidor MCP de SynRAG).
"""

from __future__ import annotations

import asyncio
import itertools
import json
import time
from typing import Any, Awaitable, Callable

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed


class ErrorRed(RuntimeError):
    """El relé o el nodo dijeron que no (sin nodos, clave, tarea fallida...)."""


AlParcial = Callable[[Any], None | Awaitable[None]]


class ClienteRed:
    def __init__(self, url: str, clave: str, enjambre: str, nombre: str = "cliente") -> None:
        self.url = url
        self.clave = clave
        self.enjambre = enjambre
        self.nombre = nombre
        self.id = ""
        self.nodos: list[dict[str, Any]] = []
        self._ws: ClientConnection | None = None
        self._lector: asyncio.Task | None = None
        self._latido: asyncio.Task | None = None
        self._pendientes: dict[str, tuple[asyncio.Future, AlParcial | None]] = {}
        self._contador = itertools.count(1)
        self._hay_nodos = asyncio.Event()

    async def __aenter__(self) -> "ClienteRed":
        await self.conectar()
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.cerrar()

    async def conectar(self) -> None:
        self._ws = await connect(self.url, max_size=8 * 1024 * 1024, open_timeout=20)
        await self._ws.send(
            json.dumps({"t": "hola", "clave": self.clave, "enjambre": self.enjambre, "rol": "cliente", "nombre": self.nombre})
        )
        hola = json.loads(await asyncio.wait_for(self._ws.recv(), timeout=20))
        if hola.get("t") != "bienvenido":
            raise ErrorRed(hola.get("motivo") or "el relé no aceptó la conexión")
        self.id = hola["id"]
        self._poner_nodos(hola.get("nodos") or [])
        self._lector = asyncio.create_task(self._leer())
        self._latido = asyncio.create_task(self._latir())

    async def _latir(self) -> None:
        # El relé corta a quien pasa 60 s sin hablar (LATIDO_VENCE_S).
        while self._ws:
            await asyncio.sleep(20)
            try:
                await self._ws.send(json.dumps({"t": "latido"}))
            except ConnectionClosed:
                return

    async def cerrar(self) -> None:
        for tarea in (self._lector, self._latido):
            if tarea:
                tarea.cancel()
        if self._ws:
            await self._ws.close()
        for futuro, _ in self._pendientes.values():
            if not futuro.done():
                futuro.set_exception(ErrorRed("conexión cerrada"))
        self._pendientes.clear()

    def _poner_nodos(self, nodos: list[dict[str, Any]]) -> None:
        self.nodos = nodos
        if nodos:
            self._hay_nodos.set()
        else:
            self._hay_nodos.clear()

    async def esperar_nodos(self, capacidad: str | None = None, cuantos: int = 1, timeout: float = 600) -> list[dict[str, Any]]:
        """Espera a que haya `cuantos` nodos (con esa capacidad) en el enjambre."""
        limite = time.monotonic() + timeout
        while True:
            utiles = [n for n in self.nodos if capacidad is None or capacidad in n.get("capacidades", [])]
            if len(utiles) >= cuantos:
                return utiles
            if self._lector and self._lector.done():
                raise ErrorRed("se perdió la conexión con el relé")
            if time.monotonic() > limite:
                raise ErrorRed(f"no llegaron {cuantos} nodos con {capacidad or 'algo'} en {timeout:.0f} s")
            await asyncio.sleep(1)

    async def _leer(self) -> None:
        assert self._ws
        try:
            async for crudo in self._ws:
                m = json.loads(crudo)
                t = m.get("t")
                if t == "nodos":
                    self._poner_nodos(m.get("nodos") or [])
                    continue
                pendiente = self._pendientes.get(str(m.get("id")))
                if not pendiente:
                    continue
                futuro, al_parcial = pendiente
                if t == "parcial" and al_parcial:
                    r = al_parcial(m.get("datos"))
                    if asyncio.iscoroutine(r):
                        await r
                elif t == "resultado":
                    self._pendientes.pop(str(m["id"]), None)
                    if not futuro.done():
                        datos = m.get("datos")
                        if isinstance(datos, dict):
                            datos.setdefault("nodo", m.get("nodo"))
                        futuro.set_result(datos)
                elif t == "error":
                    self._pendientes.pop(str(m["id"]), None)
                    if not futuro.done():
                        futuro.set_exception(ErrorRed(m.get("mensaje") or "error"))
        except Exception as e:  # conexión caída: falla todo lo pendiente
            for futuro, _ in self._pendientes.values():
                if not futuro.done():
                    futuro.set_exception(ErrorRed(f"conexión perdida: {e}"))
            self._pendientes.clear()

    async def pedir(
        self,
        tipo: str,
        datos: Any,
        al_parcial: AlParcial | None = None,
        timeout: float = 600,
        para: str | None = None,
    ) -> Any:
        assert self._ws, "falta conectar()"
        ident = f"t{next(self._contador)}"
        futuro: asyncio.Future = asyncio.get_running_loop().create_future()
        self._pendientes[ident] = (futuro, al_parcial)
        mensaje: dict[str, Any] = {"t": "tarea", "id": ident, "tipo": tipo, "datos": datos}
        if para:
            mensaje["para"] = para
        await self._ws.send(json.dumps(mensaje, ensure_ascii=False))
        try:
            return await asyncio.wait_for(futuro, timeout)
        except asyncio.TimeoutError:
            self._pendientes.pop(ident, None)
            await self._ws.send(json.dumps({"t": "cancelar", "id": ident}))
            raise ErrorRed(f"sin respuesta en {timeout:.0f} s")


def pedir_sync(url: str, clave: str, enjambre: str, tipo: str, datos: Any, timeout: float = 600) -> Any:
    """Una sola tarea, sin asyncio a la vista."""

    async def una() -> Any:
        async with ClienteRed(url, clave, enjambre, nombre="synrag") as red:
            return await red.pedir(tipo, datos, timeout=timeout)

    return asyncio.run(una())
