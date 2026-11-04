"""Tubo híbrido: intento de conexión directa P2P con fallback al relé WebSocket.

Intenta primero abrir un canal directo WebRTC DataChannel usando STUN público para perforar NAT.
Si la negociación directa falla o agota el tiempo límite (p. ej. NAT simétrico estricto o cortafuegos
bloqueando UDP), cae automáticamente al relé WebSocket habitual (fabric.tubo.Tubo).
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from fabric.directo.canal import CanalWebRTC
from fabric.tubo import Tubo

log = logging.getLogger("directo.hibrido")
TIMEOUT_DIRECTO_DEFAULT = 3.5


class TuboHibrido:
    """Tubo con negociación P2P preferente y fallback automático al relé."""

    def __init__(
        self,
        rele: str,
        clave: str,
        nombre: str,
        puerto: int,
        host: str = "127.0.0.1",
        info: dict | None = None,
        timeout_directo: float = TIMEOUT_DIRECTO_DEFAULT,
        forzar_rele: bool = False,
    ) -> None:
        self.rele = rele
        self.clave = clave
        self.nombre = nombre
        self.puerto = puerto
        self.host = host
        self.info = info or {}
        self.timeout_directo = timeout_directo
        self.forzar_rele = forzar_rele

        self.modo: str | None = None  # "directo" o "rele"
        self.puerto_remoto: int | None = None
        self.bytes = 0
        self.conexiones = 0
        self.listo = asyncio.Event()

        # Componentes activos
        self.canal_directo: CanalWebRTC | None = None
        self.tubo_rele: Tubo | None = None
        self._servidor_local: asyncio.Server | None = None
        self._tarea_vida: asyncio.Task | None = None

    async def conectar_directo(
        self,
        generador_oferta: Any,
        espera_respuesta: Any,
    ) -> bool:
        """Intenta establecer canal directo usando las funciones de señalización provistas."""
        if self.forzar_rele:
            log.info("forzar_rele activado; omitiendo intento directo")
            return False

        log.info("intentando conexión directa P2P con STUN público...")
        canal = CanalWebRTC()
        canal.iniciar_como_oferente(etiqueta=f"rpc-{self.nombre}")

        try:
            oferta = await canal.obtener_oferta()
            await generador_oferta(oferta)
            respuesta = await asyncio.wait_for(espera_respuesta(), timeout=self.timeout_directo)
            await canal.aplicar_respuesta(respuesta)
            await canal.esperar_listo(timeout_s=self.timeout_directo)

            self.canal_directo = canal
            self.modo = "directo"
            log.info("conexión directa P2P establecida exitosamente en modo '%s'", self.modo)
            return True
        except Exception as e:
            log.warning("intento directo falló (%s); cayendo a relé", e)
            await canal.cerrar()
            self.canal_directo = None
            return False

    async def arrancar_puente_directo(self, puerto_maestro: int = 0) -> int:
        """Inicia servidor TCP en el maestro que envía todo por el canal directo hacia el trabajador."""
        if not self.canal_directo or not self.canal_directo.abierto.is_set():
            raise RuntimeError("canal directo no disponible")

        async def atiende_cliente(lector: asyncio.StreamReader, escritor: asyncio.StreamWriter):
            self.conexiones += 1
            log.info("cliente conectado al puerto local del maestro (modo directo)")
            await self.canal_directo.puente_tcp(lector, escritor)

        self._servidor_local = await asyncio.start_server(atiende_cliente, "127.0.0.1", puerto_maestro)
        self.puerto_remoto = self._servidor_local.sockets[0].getsockname()[1]
        self.listo.set()
        log.info("puente directo maestro escuchando en 127.0.0.1:%s", self.puerto_remoto)
        return self.puerto_remoto

    async def atender_trabajador_directo(self) -> None:
        """En el trabajador: conecta el canal directo hacia el servicio local (ej. ggml-rpc-server)."""
        if not self.canal_directo or not self.canal_directo.abierto.is_set():
            raise RuntimeError("canal directo no disponible")

        lector_loc, escritor_loc = await asyncio.open_connection(self.host, self.puerto)
        log.info("trabajador enlazado con servicio local %s:%s", self.host, self.puerto)
        await self.canal_directo.puente_tcp(lector_loc, escritor_loc)

    async def arrancar_fallback_rele(self) -> None:
        """Inicia el tubo estándar por el relé WebSocket."""
        log.info("activando fallback al relé WebSocket...")
        self.modo = "rele"
        self.tubo_rele = Tubo(self.rele, self.clave, self.nombre, self.puerto, self.host, self.info)
        self._tarea_vida = asyncio.create_task(self.tubo_rele.vivir())
        await self.tubo_rele.listo.wait()
        self.puerto_remoto = self.tubo_rele.puerto_remoto
        self.listo.set()
        log.info("fallback a relé listo; puerto remoto: %s", self.puerto_remoto)

    async def cerrar(self) -> None:
        if self._servidor_local:
            self._servidor_local.close()
            await self._servidor_local.wait_closed()
        if self.canal_directo:
            await self.canal_directo.cerrar()
        if self._tarea_vida:
            self._tarea_vida.cancel()
        self.listo.clear()
