"""Códec rpc1 entre piezas de distinto lenguaje: relé Python + tubo Go y relé Go + tubo Python.

Un flujo ggml-rpc sintético (pesos, grafos que se repiten, activaciones) entra por el puerto del relé y
tiene que llegar idéntico, byte por byte, al servidor del trabajador; además se comprueba que el códec
de verdad se negoció (por el WebSocket pasó bastante menos que el flujo crudo).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
import time
import urllib.request

import pytest

from fabric.pruebas import flujo_rpc_sintetico as sint
from fabric.pruebas.test_interop_go import BIN_RELE_GO, BIN_TUBO_GO, CLAVE, puerto_libre

pytestmark = pytest.mark.skipif(not BIN_RELE_GO.exists() or not BIN_TUBO_GO.exists(),
                                reason="Binarios Go no compilados en go/bin (cd go && go build -o bin/ ./cmd/...)")


async def _servidor_que_guarda(puerto: int) -> tuple[asyncio.AbstractServer, bytearray, asyncio.Event]:
    recibido = bytearray()
    cerrado = asyncio.Event()

    async def guardar(lector: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        while datos := await lector.read(1 << 16):
            recibido.extend(datos)
        escritor.close()
        cerrado.set()

    return await asyncio.start_server(guardar, "127.0.0.1", puerto), recibido, cerrado


async def _enviar(puerto: int, flujo: bytes) -> None:
    fin = time.monotonic() + 5
    while True:
        try:
            _, escritor = await asyncio.open_connection("127.0.0.1", puerto)
            break
        except OSError:
            if time.monotonic() > fin:
                raise
            await asyncio.sleep(0.1)
    for i in range(0, len(flujo), 100_003):  # cortes que no coinciden con los pedidos
        escritor.write(flujo[i:i + 100_003])
        await escritor.drain()
    escritor.close()
    await escritor.wait_closed()


def _esperar_salud(puerto: int) -> None:
    fin = time.monotonic() + 5
    while time.monotonic() < fin:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{puerto}/salud", timeout=1) as r:
                if r.status == 200:
                    return
        except Exception:
            time.sleep(0.05)
    raise AssertionError("el relé Go no respondió /salud")


def test_codec_rele_python_tubo_go(caplog: pytest.LogCaptureFixture) -> None:
    from fabric.rele import rele as modulo_rele

    flujo = sint.flujo(semilla=7, pasos=40, megas_pesos=1)

    async def caso() -> None:
        puerto_rele, puerto_srv, puerto_tubo = puerto_libre(53100, 53199), puerto_libre(53200, 53299), \
            puerto_libre(53300, 53399)
        servidor, recibido, cerrado = await _servidor_que_guarda(puerto_srv)
        modulo_rele.TUBO_PUERTO_BASE = puerto_tubo
        listo = asyncio.Event()
        tarea_rele = asyncio.create_task(modulo_rele.principal(CLAVE, puerto_rele, listo))
        await asyncio.wait_for(listo.wait(), 5)
        proc = subprocess.Popen([str(BIN_TUBO_GO), "--rele", f"ws://127.0.0.1:{puerto_rele}", "--clave", CLAVE,
                                 "--nombre", "trabajador-go", "--puerto", str(puerto_srv)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        try:
            await _enviar(puerto_tubo, flujo)
            await asyncio.wait_for(cerrado.wait(), 15)
        finally:
            proc.terminate()
            proc.wait(timeout=5)
            servidor.close()
            tarea_rele.cancel()
            await asyncio.gather(tarea_rele, return_exceptions=True)
        assert bytes(recibido) == flujo

    with caplog.at_level(logging.INFO):
        asyncio.run(caso())
    lineas = [r.getMessage() for r in caplog.records if "códec rpc1" in r.getMessage()]
    assert lineas, "el relé Python no negoció rpc1 con el tubo Go"
    crudo, enviado = map(int, re.search(r"(\d+) -> (\d+)", lineas[-1]).groups())
    assert crudo == len(flujo) and enviado < 0.9 * crudo


def test_codec_rele_go_tubo_python() -> None:
    from fabric.tubo import Tubo

    flujo = sint.flujo(semilla=8, pasos=40, megas_pesos=1)

    async def caso() -> int:
        puerto_rele, puerto_srv, puerto_tubo = puerto_libre(53100, 53199), puerto_libre(53200, 53299), \
            puerto_libre(53400, 53499)
        servidor, recibido, cerrado = await _servidor_que_guarda(puerto_srv)
        proc = subprocess.Popen([str(BIN_RELE_GO), "--puerto", str(puerto_rele), "--clave", CLAVE,
                                 "--tubo-puerto-base", str(puerto_tubo)],
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        tubo = Tubo(f"ws://127.0.0.1:{puerto_rele}", CLAVE, "trabajador-py", puerto_srv)
        tarea = None
        try:
            await asyncio.to_thread(_esperar_salud, puerto_rele)
            tarea = asyncio.create_task(tubo.vivir())
            await asyncio.wait_for(tubo.listo.wait(), 5)
            await _enviar(tubo.puerto_remoto, flujo)
            await asyncio.wait_for(cerrado.wait(), 15)
        finally:
            if tarea is not None:
                tarea.cancel()
                await asyncio.gather(tarea, return_exceptions=True)
            proc.terminate()
            proc.wait(timeout=5)
            servidor.close()
        assert bytes(recibido) == flujo
        return tubo.bytes  # lo que llegó por el WebSocket (el servidor no responde nada)

    por_ws = asyncio.run(caso())
    assert por_ws < 0.9 * len(flujo), f"el relé Go no negoció rpc1: {por_ws} de {len(flujo)} bytes"
