"""Sonda de ancho de banda por la conexión de control del tubo: relé y tubo reales en local, un tubo
viejo simulado (no contesta y no debe recibir binarios) y casos unitarios del lado del tubo."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
from websockets.asyncio.client import connect

from fabric.pruebas.test_red import CLAVE, red
from fabric.rele import rele as modulo_rele
from fabric.tubo import Tubo


async def _salud(url: str) -> dict:
    async with httpx.AsyncClient() as cliente:
        return (await cliente.get(url.replace("ws://", "http://") + "/salud", timeout=3)).json()


async def _esperar_sonda(url: str, nombre: str, plazo: float = 15.0) -> dict:
    fin = time.monotonic() + plazo
    while time.monotonic() < fin:
        tubos = (await _salud(url)).get("tubos", {})
        if nombre in tubos and "sonda" in tubos[nombre]["info"]:
            return tubos[nombre]["info"]
        await asyncio.sleep(0.1)
    raise AssertionError(f"el relé no publicó la sonda de {nombre}")


def test_sonda_mide_bajada_y_subida_en_un_tubo_real(monkeypatch):
    # 32 MiB: en loopback 1 o 2 MiB tardan menos de 2 ms y la sonda los declara inconclusos
    monkeypatch.setattr(modulo_rele, "SONDA_BYTES", 32 * 1024 * 1024)

    async def caso():
        async with red() as (url, _c, _n):
            tubo = Tubo(url, CLAVE, "trabajador-sonda", 1)
            vida = asyncio.create_task(tubo.vivir())
            try:
                await asyncio.wait_for(tubo.listo.wait(), 5)
                info = await _esperar_sonda(url, "trabajador-sonda")
                assert info["sonda"] == "ok"
                assert info["bajada_mbps"] > 0 and info["subida_mbps"] > 0
            finally:
                vida.cancel()
                await asyncio.gather(vida, return_exceptions=True)

    asyncio.run(caso())


def test_tubo_viejo_no_contesta_no_recibe_binarios_y_se_rinde(monkeypatch):
    monkeypatch.setattr(modulo_rele, "SONDA_BYTES", 1024 * 1024)
    monkeypatch.setattr(modulo_rele, "SONDA_ESPERA_S", 0.5)
    visto = {"binarios": 0, "json": []}

    async def tubo_viejo(url: str) -> None:
        # Como un tubo de antes de la sonda: ignora los JSON que no conoce y no contesta nada.
        async with connect(url + "/tubo") as ws:
            await ws.send(json.dumps({"t": "ofrezco", "clave": CLAVE, "nombre": "viejo", "info": {}}))
            await ws.recv()  # listo
            async for crudo in ws:
                if isinstance(crudo, bytes):
                    visto["binarios"] += 1
                else:
                    visto["json"].append(json.loads(crudo))

    async def caso():
        async with red() as (url, _c, _n):
            viejo = asyncio.create_task(tubo_viejo(url))
            try:
                info = await _esperar_sonda(url, "viejo")
                assert info["sonda"] == "sin_respuesta"
                assert "bajada_mbps" not in info and "subida_mbps" not in info
                assert any(m.get("t") == "banda_bajada" for m in visto["json"])  # la pregunta sí llegó
                assert visto["binarios"] == 0  # pero no se le mandó ningún binario
            finally:
                viejo.cancel()
                await asyncio.gather(viejo, return_exceptions=True)

    asyncio.run(caso())


def test_sonda_apagada_con_cero(monkeypatch):
    monkeypatch.setattr(modulo_rele, "SONDA_BYTES", 0)

    async def caso():
        async with red() as (url, _c, _n):
            tubo = Tubo(url, CLAVE, "sin-sonda", 1)
            vida = asyncio.create_task(tubo.vivir())
            try:
                await asyncio.wait_for(tubo.listo.wait(), 5)
                await asyncio.sleep(0.5)
                info = (await _salud(url))["tubos"]["sin-sonda"]["info"]
                assert "sonda" not in info and "subida_mbps" not in info
            finally:
                vida.cancel()
                await asyncio.gather(vida, return_exceptions=True)

    asyncio.run(caso())


class _Control:
    """Conexión de control de mentira: guarda lo que el tubo manda."""

    def __init__(self) -> None:
        self.enviados: list = []

    async def send(self, dato) -> None:
        self.enviados.append(dato)


def test_tubo_bajada_responde_solo_al_recibir_todos_los_bytes_y_descarta_lo_extrano():
    async def caso():
        tubo = Tubo("ws://no-se-usa", CLAVE, "t", 1)
        control = _Control()
        await tubo._recibir_banda(b"no pedido", control)  # binario inesperado: se ignora
        assert control.enviados == []
        await tubo._banda_bajada(control, {"id": "abc", "bytes": 10})
        assert json.loads(control.enviados[-1]) == {"t": "banda_preparada", "id": "abc"}
        await tubo._recibir_banda(bytes(4), control)
        assert len(control.enviados) == 1  # todavía faltan 6
        await tubo._recibir_banda(bytes(6), control)
        assert json.loads(control.enviados[-1]) == {"t": "banda_ok", "id": "abc", "bytes": 10}

    asyncio.run(caso())


def test_tubo_sube_los_bytes_pedidos_y_cierra_con_banda_fin():
    async def caso():
        tubo = Tubo("ws://no-se-usa", CLAVE, "t", 1)
        control = _Control()
        await tubo._enviar_banda(control, {"id": "q", "bytes": 300_000})
        binarios = [d for d in control.enviados if isinstance(d, bytes)]
        assert sum(len(b) for b in binarios) == 300_000
        assert json.loads(control.enviados[-1]) == {"t": "banda_fin", "id": "q"}

    asyncio.run(caso())
