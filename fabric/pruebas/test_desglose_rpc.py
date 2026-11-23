"""Prueba del proxy de desglose: cuenta pedidos y respuestas por comando, y no altera el flujo."""

from __future__ import annotations

import asyncio
import random
import socket
import struct

from fabric.perfiles.desglose_rpc import CON_RESPUESTA, Desglose
from fabric.pruebas import flujo_rpc_sintetico as sint
from fabric.rpc_codec import ParserPedidos


def puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


RESPUESTA = struct.pack("<Q", 4) + b"rsp!"


async def _servidor_falso(recibido: bytearray):
    """Contesta a cada pedido que lo necesita, igual que ggml-rpc: un pedido -> una respuesta, en orden."""

    async def atender(lector, escritor):
        parser = ParserPedidos()
        while trozo := await lector.read(65536):
            recibido.extend(trozo)
            for tipo, cmd, _, _ in parser.alimentar(trozo):
                if tipo == "cabecera" and cmd in CON_RESPUESTA:
                    escritor.write(RESPUESTA)
        await escritor.drain()
        escritor.close()

    puerto = puerto_libre()
    return await asyncio.start_server(atender, "127.0.0.1", puerto), puerto


def test_desglose_cuenta_por_comando_y_no_altera_el_flujo():
    flujo = sint.flujo(semilla=41, pasos=6, megas_pesos=1, tensores=60) + sint.comando(16, bytes(16))
    esperados_pedidos = {}
    cola_respuestas = 0
    for tipo, cmd, tam, _ in ParserPedidos().alimentar(flujo):
        if tipo == "cabecera":
            esperados_pedidos[cmd] = esperados_pedidos.get(cmd, 0) + 1
            cola_respuestas += cmd in CON_RESPUESTA

    async def ejecutar():
        recibido = bytearray()
        servidor_falso, puerto_srv = await _servidor_falso(recibido)
        d = Desglose()
        puerto_proxy = puerto_libre()
        servidor = await d.servir("127.0.0.1", puerto_proxy, "127.0.0.1", puerto_srv)
        try:
            lector, escritor = await asyncio.open_connection("127.0.0.1", puerto_proxy)
            escritor.write(flujo)
            await escritor.drain()
            escritor.write_eof()
            respuestas = await asyncio.wait_for(lector.read(), 10)
            escritor.close()
        finally:
            servidor.close()
            servidor_falso.close()
            await asyncio.gather(servidor.wait_closed(), servidor_falso.wait_closed(), return_exceptions=True)
        return d, bytes(recibido), respuestas

    d, recibido, respuestas = asyncio.run(ejecutar())
    assert recibido == flujo                      # el proxy no altera nada
    assert d.pedidos_bytes == len(flujo)
    assert d.respuestas_bytes == len(RESPUESTA) * cola_respuestas == len(respuestas)
    assert d.sin_comando_bytes == 0
    assert d.comandos["GRAPH_COMPUTE"]["pedidos"] == esperados_pedidos[10]
    assert d.comandos["GRAPH_RECOMPUTE"]["pedidos"] == 1
    assert d.resumen()["grafos"]["GRAPH_COMPUTE"] == esperados_pedidos[10]
    assert d.codec.crudo == len(flujo) and d.codec.enviado < len(flujo)
