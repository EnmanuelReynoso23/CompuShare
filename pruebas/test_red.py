"""Pruebas de la red de punta a punta en la misma máquina: relé real, nodos reales
con el motor `eco` y un `generar` contra un servidor OpenAI de mentira."""

from __future__ import annotations

import asyncio
import json
import socket
from contextlib import asynccontextmanager

import pytest
from websockets.asyncio.client import connect

from fabric.cliente import ClienteRed, ErrorRed
from fabric.nodo import motores
from fabric.nodo.nodo import Nodo
from fabric.rele import rele as modulo_rele

CLAVE = "clave-de-prueba"


def puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@asynccontextmanager
async def red(*nodos_motores: list):
    puerto = puerto_libre()
    listo = asyncio.Event()
    servidor = asyncio.create_task(modulo_rele.principal(CLAVE, puerto, listo))
    await asyncio.wait_for(listo.wait(), 5)
    url = f"ws://127.0.0.1:{puerto}"
    nodos = [Nodo(url, CLAVE, "pruebas", f"n{i}", lista, {"nucleos": 1}) for i, lista in enumerate(nodos_motores)]
    vidas = [asyncio.create_task(n.vivir()) for n in nodos]
    try:
        async with ClienteRed(url, CLAVE, "pruebas") as cliente:
            if nodos:
                await cliente.esperar_nodos(cuantos=len(nodos), timeout=5)
            yield url, cliente, nodos
    finally:
        for v in vidas:
            v.cancel()
        servidor.cancel()
        await asyncio.gather(*vidas, servidor, return_exceptions=True)


class Lento:
    tipo = "eco"
    concurrencia = 4

    async def correr(self, datos, avance):
        await asyncio.sleep(datos.get("dormir", 0.2))
        return {"eco": datos}


class GenerarFalso:
    """Imita el streaming de un LLM: tres trozos con pausas."""

    tipo = "generar"
    concurrencia = 2

    async def correr(self, datos, avance):
        for trozo in ("Hola", " desde", " Kaggle"):
            await asyncio.sleep(0.3)
            await avance(trozo)
        return {"texto": "Hola desde Kaggle", "tokens_salida": 3}


def test_eco_ida_y_vuelta():
    async def caso():
        async with red([motores.Eco()]) as (_, cliente, _n):
            r = await cliente.pedir("eco", {"x": 1})
            assert r["eco"] == {"x": 1}
            assert r["nodo"]

    asyncio.run(caso())


def test_sin_nodos_responde_error():
    async def caso():
        async with red() as (_, cliente, _n):
            with pytest.raises(ErrorRed, match="sin_nodos"):
                await cliente.pedir("generar", {"prompt": "hola"})

    asyncio.run(caso())


def test_clave_incorrecta_rechazada():
    async def caso():
        async with red() as (url, _c, _n):
            with pytest.raises(ErrorRed, match="clave incorrecta"):
                async with ClienteRed(url, "otra", "pruebas"):
                    pass

    asyncio.run(caso())


def test_reparte_al_menos_cargado():
    async def caso():
        async with red([Lento()], [Lento()]) as (_, cliente, nodos):
            resultados = await asyncio.gather(*(cliente.pedir("eco", {"i": i, "dormir": 0.3}) for i in range(6)))
            usados = {r["nodo"] for r in resultados}
            assert len(usados) == 2, "las 6 tareas deberían repartirse entre los dos nodos"
            assert sum(n.atendidas for n in nodos) == 6

    asyncio.run(caso())


def test_generar_llega_por_partes():
    async def caso():
        partes: list[str] = []
        async with red([GenerarFalso()]) as (_, cliente, _n):
            r = await cliente.pedir("generar", {"prompt": "hola"}, al_parcial=lambda d: partes.append(d["texto"]))
            assert r["texto"] == "Hola desde Kaggle"
            assert "".join(partes) == "Hola desde Kaggle"

    asyncio.run(caso())


def test_nodo_caido_avisa_al_que_pidio():
    async def caso():
        async with red([Lento()]) as (_, cliente, nodos):
            pedido = asyncio.create_task(cliente.pedir("eco", {"dormir": 5}))
            await asyncio.sleep(0.3)
            await nodos[0].ws.close()
            with pytest.raises(ErrorRed, match="nodo_desconectado"):
                await pedido

    asyncio.run(caso())


def test_sala_de_chat_comparte_mensajes():
    async def caso():
        async with red([motores.Eco()]) as (url, _c, _n):
            async with connect(url) as a, connect(url) as b:
                for ws in (a, b):
                    await ws.send(json.dumps({"t": "hola", "clave": CLAVE, "enjambre": "pruebas", "rol": "cliente"}))
                    assert json.loads(await ws.recv())["t"] == "bienvenido"
                await a.send(json.dumps({"t": "mensaje", "texto": "hola sala"}))

                async def siguiente_mensaje(ws):
                    while True:
                        m = json.loads(await ws.recv())
                        if m["t"] == "mensaje":
                            return m["mensaje"]

                visto = await asyncio.wait_for(siguiente_mensaje(b), 3)
                assert visto["texto"] == "hola sala"
                assert visto["estado"] == "pendiente"
                # b no puede cerrar el mensaje de a
                await b.send(json.dumps({"t": "responder", "mensaje_id": visto["id"], "respuesta": "falso"}))
                await a.send(json.dumps({"t": "responder", "mensaje_id": visto["id"], "respuesta": "de verdad", "por": "n0"}))
                cerrado = await asyncio.wait_for(siguiente_mensaje(b), 3)
                assert cerrado["respuesta"] == "de verdad"
                assert cerrado["estado"] == "respondido"

    asyncio.run(caso())


def test_ritmo_corta_a_quien_inunda(monkeypatch):
    monkeypatch.setattr(modulo_rele, "RAFAGA", 5.0)
    monkeypatch.setattr(modulo_rele, "RITMO", 1.0)

    async def caso():
        async with red() as (url, _c, _n):
            async with connect(url) as ws:
                await ws.send(json.dumps({"t": "hola", "clave": CLAVE, "enjambre": "pruebas", "rol": "cliente"}))
                await ws.recv()
                for _ in range(20):
                    await ws.send(json.dumps({"t": "latido"}))
                motivos = []
                try:
                    async for crudo in ws:
                        motivos.append(json.loads(crudo).get("motivo"))
                except Exception:
                    pass
                assert "demasiados mensajes" in motivos

    asyncio.run(caso())


def test_motor_generar_lee_el_streaming_de_openai():
    import httpx

    trozos = [
        {"choices": [{"delta": {"content": "Hola"}}]},
        {"choices": [{"delta": {"reasoning_content": "pensando"}}]},
        {"choices": [{"delta": {"content": " mundo"}}]},
        {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 2}},
    ]
    cuerpo = "".join(f"data: {json.dumps(t)}\n\n" for t in trozos) + "data: [DONE]\n\n"
    pedidos = []

    def responder(pedido: httpx.Request) -> httpx.Response:
        pedidos.append(json.loads(pedido.content))
        return httpx.Response(200, text=cuerpo, headers={"content-type": "text/event-stream"})

    async def caso():
        motor = motores.Generar("http://llm.local", "Qwen-prueba")
        motor.http = httpx.AsyncClient(transport=httpx.MockTransport(responder))
        partes: list[str] = []

        async def avance(texto):
            partes.append(texto)

        r = await motor.correr({"prompt": "hola", "pensar": False}, avance)
        assert r["texto"] == "Hola mundo"
        assert "".join(partes) == "Hola mundo"
        assert r["tokens_salida"] == 2 and r["tokens_entrada"] == 7
        assert pedidos[0]["chat_template_kwargs"] == {"enable_thinking": False}
        assert pedidos[0]["messages"] == [{"role": "user", "content": "hola"}]

    asyncio.run(caso())


def test_reconexion_conserva_el_id():
    async def caso():
        async with red() as (url, _c, _n):
            hola = {"t": "hola", "clave": CLAVE, "enjambre": "pruebas", "rol": "cliente", "id": "navegador0001"}
            async with connect(url) as a:
                await a.send(json.dumps(hola))
                assert json.loads(await a.recv())["id"] == "navegador0001"
                # mientras la primera sigue abierta, otro no puede quedarse con ese id
                async with connect(url) as b:
                    await b.send(json.dumps(hola))
                    assert json.loads(await b.recv())["id"] != "navegador0001"
            async with connect(url) as c:
                await c.send(json.dumps(dict(hola, rol="nodo", capacidades=["generar"])))
                assert json.loads(await c.recv())["id"] == "navegador0001"

    asyncio.run(caso())

