"""Canal directo punto a punto (P2P) usando WebRTC Data Channels (aiortc) y STUN público.

Permite que dos nodos intercambien tráfico binario continuo (como el flujo RPC de ggml-rpc)
directamente por UDP perforando NAT con STUN, sin pasar los datos por un servidor relé.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Callable

from aiortc import RTCConfiguration, RTCIceServer, RTCPeerConnection, RTCSessionDescription

log = logging.getLogger("directo.canal")

SERVIDORES_STUN_DEFAULT = [
    "stun:stun.l.google.com:19302",
    "stun:stun1.l.google.com:19302",
]
TROZO = 64 * 1024
LIMITE_BUFFER = 512 * 1024


def configurar_pc(servidores_stun: list[str] | None = None) -> RTCPeerConnection:
    stuns = servidores_stun or SERVIDORES_STUN_DEFAULT
    config = RTCConfiguration(iceServers=[RTCIceServer(urls=stuns)])
    return RTCPeerConnection(configuration=config)


class CanalWebRTC:
    """Gestiona una conexión WebRTC DataChannel para transporte binario."""

    def __init__(self, servidores_stun: list[str] | None = None) -> None:
        self.servidores_stun = servidores_stun or SERVIDORES_STUN_DEFAULT
        self.pc: RTCPeerConnection = configurar_pc(self.servidores_stun)
        self.dc: Any = None
        self.abierto = asyncio.Event()
        self.fallo = asyncio.Event()
        self.cola_mensajes: asyncio.Queue[bytes] = asyncio.Queue()
        self.bytes_enviados = 0
        self.bytes_recibidos = 0

        @self.pc.on("iceconnectionstatechange")
        def on_ice_state():
            estado = self.pc.iceConnectionState
            log.debug("estado ICE: %s", estado)
            if estado in ("failed", "disconnected", "closed"):
                self.fallo.set()

        @self.pc.on("datachannel")
        def on_datachannel(channel: Any):
            self._vincular_canal(channel)

    def _vincular_canal(self, channel: Any) -> None:
        self.dc = channel

        if getattr(channel, "readyState", "") == "open":
            log.info("canal directo ya abierto al vincular (etiqueta: %s)", channel.label)
            self.abierto.set()

        @channel.on("open")
        def on_open():
            log.info("canal directo abierto (etiqueta: %s)", channel.label)
            self.abierto.set()

        @channel.on("close")
        def on_close():
            log.info("canal directo cerrado")
            self.fallo.set()

        @channel.on("message")
        def on_message(datos: bytes):
            self.bytes_recibidos += len(datos)
            self.cola_mensajes.put_nowait(datos)

    def iniciar_como_oferente(self, etiqueta: str = "rpc") -> None:
        canal = self.pc.createDataChannel(etiqueta, ordered=True)
        self._vincular_canal(canal)

    async def obtener_oferta(self) -> dict[str, str]:
        oferta = await self.pc.createOffer()
        await self.pc.setLocalDescription(oferta)
        await self._esperar_recoleccion_ice()
        assert self.pc.localDescription is not None
        return {"sdp": self.pc.localDescription.sdp, "type": self.pc.localDescription.type}

    async def responder_oferta(self, oferta_remota: dict[str, str]) -> dict[str, str]:
        desc = RTCSessionDescription(sdp=oferta_remota["sdp"], type=oferta_remota["type"])
        await self.pc.setRemoteDescription(desc)
        respuesta = await self.pc.createAnswer()
        await self.pc.setLocalDescription(respuesta)
        await self._esperar_recoleccion_ice()
        assert self.pc.localDescription is not None
        return {"sdp": self.pc.localDescription.sdp, "type": self.pc.localDescription.type}

    async def aplicar_respuesta(self, respuesta_remota: dict[str, str]) -> None:
        desc = RTCSessionDescription(sdp=respuesta_remota["sdp"], type=respuesta_remota["type"])
        await self.pc.setRemoteDescription(desc)

    async def _esperar_recoleccion_ice(self, timeout_s: float = 3.0) -> None:
        t0 = asyncio.get_running_loop().time()
        while self.pc.iceGatheringState != "complete":
            if asyncio.get_running_loop().time() - t0 > timeout_s:
                log.warning("tiempo de recolección ICE agotado; usando candidatos reunidos hasta ahora")
                break
            await asyncio.sleep(0.02)

    async def esperar_listo(self, timeout_s: float = 5.0) -> None:
        tarea_abierto = asyncio.create_task(self.abierto.wait())
        tarea_fallo = asyncio.create_task(self.fallo.wait())
        listas, pendientes = await asyncio.wait(
            [tarea_abierto, tarea_fallo],
            timeout=timeout_s,
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pendientes:
            t.cancel()

        if self.fallo.is_set():
            raise ConnectionError(f"falló la conexión ICE directa: {self.pc.iceConnectionState}")
        if not self.abierto.is_set():
            raise TimeoutError(f"tiempo de espera agotado ({timeout_s}s) para abrir el canal directo")

    async def enviar(self, datos: bytes) -> None:
        if not self.abierto.is_set() or self.dc is None:
            raise ConnectionError("canal no está abierto")
        offset = 0
        total = len(datos)
        while offset < total:
            trozo = datos[offset : offset + TROZO]
            while self.dc.bufferedAmount > LIMITE_BUFFER:
                await asyncio.sleep(0.002)
            self.dc.send(trozo)
            self.bytes_enviados += len(trozo)
            offset += len(trozo)

    async def recibir(self) -> bytes:
        return await self.cola_mensajes.get()

    async def puente_tcp(self, lector: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        """Canaliza bidireccionalmente entre este DataChannel y un flujo TCP."""
        async def tcp_a_dc() -> None:
            while trozo := await lector.read(TROZO):
                await self.enviar(trozo)

        async def dc_a_tcp() -> None:
            while True:
                datos = await self.recibir()
                escritor.write(datos)
                await escritor.drain()

        tareas = [asyncio.create_task(tcp_a_dc()), asyncio.create_task(dc_a_tcp())]
        try:
            await asyncio.wait(tareas, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tareas:
                t.cancel()
            await asyncio.gather(*tareas, return_exceptions=True)
            escritor.close()

    async def cerrar(self) -> None:
        if self.dc:
            self.dc.close()
        await self.pc.close()
