"""Reenvio TCP local: 127.0.0.1:puerto_local -> 127.0.0.1:puerto_destino.

Lo usa el maestro en modo completo: el llama-server del trabajador completo esta detras de un tubo (un puerto
local del relé), y el maestro lo expone en su propio puerto de API para que `compushare probar` y
`transcribir` funcionen igual que con su llama-server local.
"""

from __future__ import annotations

import asyncio
import logging

log = logging.getLogger("compushare.reenvio")


async def _copiar(lector: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
    try:
        while trozo := await lector.read(65536):
            escritor.write(trozo)
            await escritor.drain()
    except (OSError, ConnectionError):
        pass
    finally:
        escritor.close()


class Reenvio:
    def __init__(self, puerto_local: int, puerto_destino: int, host: str = "127.0.0.1") -> None:
        self.puerto_local = puerto_local
        self.puerto_destino = puerto_destino
        self.host = host
        self._servidor: asyncio.Server | None = None
        self._escritores: set[asyncio.StreamWriter] = set()

    async def iniciar(self) -> None:
        self._servidor = await asyncio.start_server(self._atender, self.host, self.puerto_local)
        log.info("reenvio %s:%d -> %s:%d", self.host, self.puerto_local, self.host, self.puerto_destino)

    async def _atender(self, lector_cliente: asyncio.StreamReader, escritor_cliente: asyncio.StreamWriter) -> None:
        try:
            lector_dest, escritor_dest = await asyncio.open_connection(self.host, self.puerto_destino)
        except OSError:
            escritor_cliente.close()
            return
        self._escritores.update((escritor_cliente, escritor_dest))
        try:
            await asyncio.gather(_copiar(lector_cliente, escritor_dest), _copiar(lector_dest, escritor_cliente))
        finally:
            self._escritores.difference_update((escritor_cliente, escritor_dest))

    def detener(self) -> None:
        """Deja de escuchar y corta las conexiones abiertas."""
        if self._servidor is not None:
            self._servidor.close()
            self._servidor = None
        for escritor in list(self._escritores):
            escritor.close()
        self._escritores.clear()
