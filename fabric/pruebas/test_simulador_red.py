"""Pruebas del simulador de red TCP (fabric.simulador.red).

Verifica que:
1. La latencia de ida y vuelta queda dentro de +-15 % de 2x la latencia configurada.
2. El ancho de banda se respeta dentro de +-15 %.
3. Los bytes llegan completos y en orden exacto bajo perdida simulada (retransmision RTO).
4. El proxy cierra limpio sin fugas de tareas, sockets ni excepciones no controladas.
5. Los perfiles predefinidos coinciden con la tabla de fabric/PRUEBA-10.md.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import AsyncGenerator

import pytest

from fabric.simulador.red import (
    PERFILES,
    CubetaFichas,
    Enlace,
    obtener_perfil,
    parsear_direccion,
)


@asynccontextmanager
async def servidor_eco() -> AsyncGenerator[int, None]:
    """Servidor TCP local de eco en un puerto efimero."""
    tareas_activas: set[asyncio.Task] = set()

    async def atender_eco(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        t = asyncio.current_task()
        if t:
            tareas_activas.add(t)
        try:
            while True:
                datos = await reader.read(65536)
                if not datos:
                    break
                writer.write(datos)
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
            pass
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass
            if t:
                tareas_activas.discard(t)

    servidor = await asyncio.start_server(atender_eco, host="127.0.0.1", port=0)
    puerto = servidor.sockets[0].getsockname()[1]
    try:
        yield puerto
    finally:
        servidor.close()
        await servidor.wait_closed()
        for t in list(tareas_activas):
            t.cancel()
        if tareas_activas:
            await asyncio.gather(*tareas_activas, return_exceptions=True)


def test_perfiles_predefinidos_prueba_10():
    """Verifica que los perfiles predefinidos tienen los valores exactos de fabric/PRUEBA-10.md."""
    # Misma ciudad: 10 ms, 2 ms, 100 Mb/s
    p_ciudad = obtener_perfil("misma ciudad")
    assert p_ciudad.latencia_ms == 10.0
    assert p_ciudad.variacion_ms == 2.0
    assert p_ciudad.mbps == 100.0

    # Mismo pais: 40 ms, 10 ms, 50 Mb/s
    p_pais = obtener_perfil("mismo país")
    assert p_pais.latencia_ms == 40.0
    assert p_pais.variacion_ms == 10.0
    assert p_pais.mbps == 50.0

    # Otro continente: 120 ms, 20 ms, 20 Mb/s
    p_cont = obtener_perfil("otro continente")
    assert p_cont.latencia_ms == 120.0
    assert p_cont.variacion_ms == 20.0
    assert p_cont.mbps == 20.0

    # Cloudflare medido: 237 ms, 40 ms, mbps sin limite fijo (medir)
    p_cf = obtener_perfil("cloudflare medido")
    assert p_cf.latencia_ms == 237.0
    assert p_cf.variacion_ms == 40.0

    # Aliases
    assert obtener_perfil("misma_ciudad") == p_ciudad
    assert obtener_perfil("mismo_pais") == p_pais
    assert obtener_perfil("otro_continente") == p_cont
    assert obtener_perfil("cloudflare") == p_cf


def test_parsear_direccion():
    """Verifica el parseo de representaciones host:puerto."""
    assert parsear_direccion("127.0.0.1:8000") == ("127.0.0.1", 8000)
    assert parsear_direccion(9000) == ("127.0.0.1", 9000)
    assert parsear_direccion(("0.0.0.0", 5000)) == ("0.0.0.0", 5000)
    assert parsear_direccion(":8080") == ("127.0.0.1", 8080)


def test_latencia_ida_y_vuelta_mas_menos_15_por_ciento():
    """Mide con un eco TCP local que la ida y vuelta queda dentro de +-15 % de 2x latencia configurada."""

    async def caso():
        latencia_ida_ms = 40.0
        rtt_esperado_ms = 2.0 * latencia_ida_ms  # 80.0 ms
        margen = 0.15

        async with servidor_eco() as puerto_eco:
            async with Enlace(
                escuchar=("127.0.0.1", 0),
                destino=("127.0.0.1", puerto_eco),
                latencia_ms=latencia_ida_ms,
                variacion_ms=0.0,
            ) as enlace:
                reader, writer = await asyncio.open_connection("127.0.0.1", enlace.puerto)
                try:
                    rtts: list[float] = []
                    # 5 pings
                    for i in range(5):
                        mensaje = f"ping-{i}\n".encode()
                        t0 = time.perf_counter()
                        writer.write(mensaje)
                        await writer.drain()
                        resp = await reader.readline()
                        t1 = time.perf_counter()

                        assert resp == mensaje
                        rtts.append((t1 - t0) * 1000.0)

                    rtt_promedio = sum(rtts) / len(rtts)
                    limite_inferior = rtt_esperado_ms * (1.0 - margen)
                    limite_superior = rtt_esperado_ms * (1.0 + margen)

                    assert (
                        limite_inferior <= rtt_promedio <= limite_superior
                    ), f"RTT promedio {rtt_promedio:.2f} ms fuera de [{limite_inferior:.2f}, {limite_superior:.2f}] ms"

                    for rtt in rtts:
                        assert (
                            limite_inferior <= rtt <= limite_superior
                        ), f"RTT individual {rtt:.2f} ms fuera de [{limite_inferior:.2f}, {limite_superior:.2f}] ms"
                finally:
                    writer.close()
                    await writer.wait_closed()

    asyncio.run(caso())


def test_latencia_con_variacion_jitter():
    """Verifica que la distribucion normal recortada en 0 introduce variacion."""

    async def caso():
        latencia_ida_ms = 25.0
        variacion_ms = 5.0
        rtt_esperado_ms = 2.0 * latencia_ida_ms  # 50.0 ms

        async with servidor_eco() as puerto_eco:
            async with Enlace(
                escuchar=("127.0.0.1", 0),
                destino=("127.0.0.1", puerto_eco),
                latencia_ms=latencia_ida_ms,
                variacion_ms=variacion_ms,
            ) as enlace:
                reader, writer = await asyncio.open_connection("127.0.0.1", enlace.puerto)
                try:
                    rtts: list[float] = []
                    for i in range(8):
                        mensaje = f"ping-{i}\n".encode()
                        t0 = time.perf_counter()
                        writer.write(mensaje)
                        await writer.drain()
                        resp = await reader.readline()
                        t1 = time.perf_counter()
                        assert resp == mensaje
                        rtts.append((t1 - t0) * 1000.0)

                    rtt_promedio = sum(rtts) / len(rtts)
                    # El promedio debe quedar cerca de 2x latencia (+-20% con jitter)
                    assert (
                        rtt_esperado_ms * 0.80 <= rtt_promedio <= rtt_esperado_ms * 1.20
                    ), f"Promedio con jitter {rtt_promedio:.2f} ms alejado de {rtt_esperado_ms} ms"

                    # Debe haber dispersion entre muestras
                    assert max(rtts) - min(rtts) > 1.0, "No se observo variacion entre muestras de RTT"
                finally:
                    writer.close()
                    await writer.wait_closed()

    asyncio.run(caso())


def test_ancho_de_banda_mas_menos_15_por_ciento():
    """Mide con un eco TCP local que el ancho de banda se respeta dentro de +-15 %."""

    async def caso():
        mbps_configurado = 20.0
        margen = 0.15
        # 1.250.000 bytes = 10 Mb -> a 20 Mb/s tarda exactamente 0.50 segundos
        tamano_bytes = 1_250_000
        carga = b"K" * tamano_bytes

        async with servidor_eco() as puerto_eco:
            async with Enlace(
                escuchar=("127.0.0.1", 0),
                destino=("127.0.0.1", puerto_eco),
                latencia_ms=0.0,
                variacion_ms=0.0,
                mbps=mbps_configurado,
            ) as enlace:
                reader, writer = await asyncio.open_connection("127.0.0.1", enlace.puerto)
                try:

                    async def enviar():
                        writer.write(carga)
                        await writer.drain()

                    async def recibir() -> bytes:
                        recibidos = bytearray()
                        while len(recibidos) < tamano_bytes:
                            trozo = await reader.read(65536)
                            if not trozo:
                                break
                            recibidos.extend(trozo)
                        return bytes(recibidos)

                    t0 = time.perf_counter()
                    _, buffer_recibido = await asyncio.gather(enviar(), recibir())
                    t1 = time.perf_counter()

                    duracion = t1 - t0
                    assert len(buffer_recibido) == tamano_bytes
                    assert buffer_recibido == carga

                    mbps_medido = (tamano_bytes * 8.0 / 1_000_000.0) / duracion
                    limite_inferior = mbps_configurado * (1.0 - margen)
                    limite_superior = mbps_configurado * (1.0 + margen)

                    assert (
                        limite_inferior <= mbps_medido <= limite_superior
                    ), f"Ancho de banda {mbps_medido:.2f} Mb/s fuera de [{limite_inferior:.2f}, {limite_superior:.2f}] Mb/s"
                finally:
                    writer.close()
                    await writer.wait_closed()

    asyncio.run(caso())


def test_bytes_completos_y_en_orden_con_perdida():
    """Mide que los bytes llegan completos y en orden exacto bajo perdida simulada (RTO)."""

    async def caso():
        latencia_ms = 5.0
        variacion_ms = 1.0
        mbps = 50.0
        perdida_pct = 10.0  # 10% de perdida por paquete
        rto_ms = 20.0

        # Secuencia determinista con patron no repetitivo simple
        total_bytes = 60_000
        datos_originales = bytes((i * 17 + 3) % 256 for i in range(total_bytes))

        async with servidor_eco() as puerto_eco:
            async with Enlace(
                escuchar=("127.0.0.1", 0),
                destino=("127.0.0.1", puerto_eco),
                latencia_ms=latencia_ms,
                variacion_ms=variacion_ms,
                mbps=mbps,
                perdida=perdida_pct,
                rto_ms=rto_ms,
            ) as enlace:
                reader, writer = await asyncio.open_connection("127.0.0.1", enlace.puerto)
                try:

                    async def enviar():
                        for offset in range(0, total_bytes, 4096):
                            writer.write(datos_originales[offset : offset + 4096])
                            await writer.drain()
                        if writer.can_write_eof():
                            writer.write_eof()
                            await writer.drain()

                    async def recibir() -> bytes:
                        recibidos = bytearray()
                        while True:
                            trozo = await reader.read(65536)
                            if not trozo:
                                break
                            recibidos.extend(trozo)
                        return bytes(recibidos)

                    t0 = time.perf_counter()
                    _, datos_recibidos = await asyncio.gather(enviar(), recibir())
                    t1 = time.perf_counter()

                    # 1. Completos
                    assert len(datos_recibidos) == total_bytes, (
                        f"Longitud incorrecta: esperados {total_bytes}, recibidos {len(datos_recibidos)}"
                    )

                    # 2. En orden estricto
                    assert datos_recibidos == datos_originales, (
                        "Los bytes no coinciden exactamente con la secuencia original (error de orden o corrupcion)"
                    )

                    # 3. Se registraron paquetes perdidos (retransmitidos tras RTO)
                    st = enlace.estadisticas
                    assert st.paquetes_perdidos_totales > 0, (
                        "No se registraron paquetes perdidos durante la prueba con 10% de perdida"
                    )

                    duracion = t1 - t0
                    # La duracion debe ser mayor a la transmision ideal debido a los retrasos RTO
                    tiempo_ideal_sin_perdida = (total_bytes * 8.0 / (mbps * 1e6)) + (2 * latencia_ms / 1000.0)
                    assert duracion > tiempo_ideal_sin_perdida
                finally:
                    writer.close()
                    await writer.wait_closed()

    asyncio.run(caso())


def test_cierra_limpio():
    """Verifica que el proxy cierra limpio sin tareas pendientes, sockets huerfanos ni errores."""

    async def caso():
        async with servidor_eco() as puerto_eco:
            enlace = Enlace(
                escuchar=("127.0.0.1", 0),
                destino=("127.0.0.1", puerto_eco),
                latencia_ms=5.0,
                variacion_ms=1.0,
            )
            await enlace.iniciar()

            # Varias conexiones secuenciales cerradas limpiamente
            for i in range(3):
                r, w = await asyncio.open_connection("127.0.0.1", enlace.puerto)
                w.write(f"hola-{i}\n".encode())
                await w.drain()
                resp = await r.readline()
                assert resp == f"hola-{i}\n".encode()
                w.close()
                await w.wait_closed()

            # Desconexion abrupta del cliente
            r_abr, w_abr = await asyncio.open_connection("127.0.0.1", enlace.puerto)
            w_abr.write(b"datos-previos-a-desconexion")
            await w_abr.drain()
            w_abr.close()
            await w_abr.wait_closed()

            # Dar una decima de segundo para procesar el cierre
            await asyncio.sleep(0.1)

            # Detener el enlace por completo
            await enlace.detener()

            assert enlace._servidor is None
            assert len(enlace._tareas_conexiones) == 0
            assert enlace.estadisticas.conexiones_activas == 0
            assert enlace.estadisticas.conexiones_totales == 4

    asyncio.run(caso())


def test_enlace_con_perfil():
    """Verifica que Enlace inicializa sus atributos desde el perfil dado."""
    enlace = Enlace(escuchar=8001, destino=8002, perfil="mismo país")
    assert enlace.latencia_ms == 40.0
    assert enlace.variacion_ms == 10.0
    assert enlace.mbps == 50.0
    assert enlace.nombre_perfil == "mismo país"

    # Con override de mbps
    enlace2 = Enlace(escuchar=8001, destino=8002, perfil="mismo país", mbps=25.0)
    assert enlace2.latencia_ms == 40.0
    assert enlace2.mbps == 25.0
