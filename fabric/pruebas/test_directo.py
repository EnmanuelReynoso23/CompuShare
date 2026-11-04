"""Pruebas del módulo de conexión directa P2P (fabric/directo) y fallback a relé."""

import asyncio
import os
import pytest
from websockets.asyncio.server import serve

from fabric.directo.canal import CanalWebRTC
from fabric.directo.tubo_hibrido import TuboHibrido
from fabric.rele.rele import Rele


def test_canal_directo_p2p_datos():
    """Dos canales WebRTC locales intercambian datos binarios directamente usando STUN."""
    async def caso():
        c1 = CanalWebRTC()
        c2 = CanalWebRTC()
        c1.iniciar_como_oferente("test-rpc")

        # Señalización de oferta y respuesta
        oferta = await c1.obtener_oferta()
        respuesta = await c2.responder_oferta(oferta)
        await c1.aplicar_respuesta(respuesta)

        await c1.esperar_listo(timeout_s=5.0)
        await c2.esperar_listo(timeout_s=5.0)

        # Enviar 512 KB de datos binarios aleatorios
        carga = os.urandom(512 * 1024)
        await c1.enviar(carga)

        recibido = bytearray()
        while len(recibido) < len(carga):
            trozo = await asyncio.wait_for(c2.recibir(), timeout=5.0)
            recibido.extend(trozo)

        assert bytes(recibido) == carga

        await c1.cerrar()
        await c2.cerrar()

    asyncio.run(caso())


def test_puente_tcp_directo_integridad():
    """Un servicio de eco TCP cruzando el canal directo P2P preserva 1 MB intacto."""
    TROZO = 64 * 1024

    async def eco(lector, escritor):
        while datos := await lector.read(TROZO):
            escritor.write(datos)
            await escritor.drain()
        escritor.close()

    async def caso():
        srv_eco = await asyncio.start_server(eco, "127.0.0.1", 0)
        puerto_eco = srv_eco.sockets[0].getsockname()[1]

        c_maestro = CanalWebRTC()
        c_trab = CanalWebRTC()
        c_maestro.iniciar_como_oferente("rpc")

        oferta = await c_maestro.obtener_oferta()
        respuesta = await c_trab.responder_oferta(oferta)
        await c_maestro.aplicar_respuesta(respuesta)

        await c_maestro.esperar_listo(timeout_s=5.0)
        await c_trab.esperar_listo(timeout_s=5.0)

        # Trabajador enlaza con el servicio eco
        lector_eco, escritor_eco = await asyncio.open_connection("127.0.0.1", puerto_eco)
        tarea_trab = asyncio.create_task(c_trab.puente_tcp(lector_eco, escritor_eco))

        # Maestro abre servidor TCP local
        async def atiende_cliente(lector_cli, escritor_cli):
            await c_maestro.puente_tcp(lector_cli, escritor_cli)

        srv_maestro = await asyncio.start_server(atiende_cliente, "127.0.0.1", 0)
        puerto_maestro = srv_maestro.sockets[0].getsockname()[1]

        # Cliente envía 1 MB y lee la respuesta
        lector_cli, escritor_cli = await asyncio.open_connection("127.0.0.1", puerto_maestro)
        carga = os.urandom(1024 * 1024)
        escritor_cli.write(carga)
        await escritor_cli.drain()

        vuelta = await asyncio.wait_for(lector_cli.readexactly(len(carga)), timeout=10.0)
        assert vuelta == carga

        escritor_cli.close()
        srv_maestro.close()
        srv_eco.close()
        tarea_trab.cancel()
        await c_maestro.cerrar()
        await c_trab.cerrar()

    asyncio.run(caso())


def test_tubo_hibrido_fallback_a_rele():
    """Si la conexión directa falla o se fuerza el relé, cae automáticamente al relé WebSocket."""
    CLAVE = "clave-prueba-hibrida"

    async def eco(lector, escritor):
        while datos := await lector.read(65536):
            escritor.write(datos)
            await escritor.drain()
        escritor.close()

    async def caso():
        srv_eco = await asyncio.start_server(eco, "127.0.0.1", 0)
        puerto_eco = srv_eco.sockets[0].getsockname()[1]

        rele = Rele(CLAVE)
        async with serve(rele.atender, "127.0.0.1", 0, process_request=rele.salud) as srv_rele:
            puerto_rele = srv_rele.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{puerto_rele}"

            # TuboHibrido con fallo directo forzado (forzar_rele=True)
            tubo = TuboHibrido(url, CLAVE, "trabajador-hibrido", puerto_eco, forzar_rele=True)

            # Simular intento de conexión directa
            directo_ok = await tubo.conectar_directo(lambda _: None, lambda: asyncio.sleep(0.1))
            assert directo_ok is False

            # Activar fallback al relé
            await tubo.arrancar_fallback_rele()
            assert tubo.modo == "rele"
            assert tubo.puerto_remoto is not None

            # Probar transferencia de 1 MB a través del relé
            lector, escritor = await asyncio.open_connection("127.0.0.1", tubo.puerto_remoto)
            carga = os.urandom(1024 * 1024)
            escritor.write(carga)
            await escritor.drain()

            vuelta = await asyncio.wait_for(lector.readexactly(len(carga)), timeout=15.0)
            assert vuelta == carga

            escritor.close()
            await tubo.cerrar()
            srv_eco.close()

    asyncio.run(caso())


def test_canal_directo_timeout_espera():
    """Si no hay respuesta de señalización, esperar_listo eleva TimeoutError."""
    async def caso():
        c = CanalWebRTC()
        c.iniciar_como_oferente("timeout-test")
        with pytest.raises(TimeoutError):
            await c.esperar_listo(timeout_s=0.2)
        await c.cerrar()

    asyncio.run(caso())
