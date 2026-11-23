"""Maestro en modo auto / completo / capas (P4), y punta a punta con relé real, trabajador completo y el
llama-server falso de `llama_falso.py`. Los fixtures (llama-server falso, GGUF falso, registros aislados)
viven en test_modo_completo.py.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from typer.testing import CliRunner

from compushare.nodo.maestro import NodoMaestro
from compushare.nodo.reenvio import Reenvio
from compushare.nodo.trabajador import NodoTrabajador
from fabric import reparto
from fabric.nodo import motores
from fabric.pruebas.test_modo_completo import (  # noqa: F401  (fixtures y helpers compartidos)
    bin_falso,
    gguf,
    puerto_libre,
    registros_aislados,
)

CLAVE = "clave-modo-completo"


def tubo_completo(puerto: int, modelo: str, tamano: int) -> dict:
    return {"puerto": puerto, "abiertos": 0,
            "info": {"modo": "completo", "modelo": modelo, "modelo_bytes": tamano, "vram_gb": 8.0}}


def tubo_rpc(puerto: int, vram: float = 4.0) -> dict:
    return {"puerto": puerto, "abiertos": 0, "info": {"vram_gb": vram}}


def test_maestro_validaciones_de_modo(tmp_path: Path, gguf: Path):
    with pytest.raises(ValueError):
        NodoMaestro(modelo=gguf, modo="raro", gpus_locales=0)
    # Sin archivo solo el modo completo puede arrancar; el nombre y el tamano bastan.
    with pytest.raises(FileNotFoundError):
        NodoMaestro(modelo=tmp_path / "no.gguf", modo="auto", gpus_locales=0)
    with pytest.raises(FileNotFoundError):
        NodoMaestro(modelo=tmp_path / "no.gguf", modo="capas", gpus_locales=0)
    m = NodoMaestro(modelo="Qwen-test-Q8_0.gguf", modo="completo", modelo_bytes=2_000_000_000, gpus_locales=0)
    assert m.modelo_bytes == 2_000_000_000 and m.modelo.name == "Qwen-test-Q8_0.gguf"
    m_con_archivo = NodoMaestro(modelo=gguf, modo="completo", modelo_bytes=7, gpus_locales=0)
    assert m_con_archivo.modelo_bytes == gguf.stat().st_size  # el archivo manda sobre --modelo-bytes


def test_candidatos_completos_exigen_mismo_modelo_y_tamano(gguf: Path):
    m = NodoMaestro(modelo=gguf, modo="auto", gpus_locales=0)
    tamano = gguf.stat().st_size
    tubos = {
        "bien": tubo_completo(50100, gguf.name, tamano),
        "otro_modelo": tubo_completo(50101, "Otro-Q4.gguf", tamano),
        "otro_tamano": tubo_completo(50102, gguf.name, tamano + 1),
        "rpc": tubo_rpc(50103),
    }
    assert m._candidatos_completos(tubos) == [("bien", 50100)]
    # Sin tamano conocido (modo completo sin archivo) basta el nombre.
    m_nombre = NodoMaestro(modelo=gguf.name, modo="completo", gpus_locales=0)
    assert sorted(p for _, p in m_nombre._candidatos_completos(tubos)) == [50100, 50102]


def test_auto_elige_el_nodo_completo_de_menor_ida_y_vuelta(gguf: Path):
    m = NodoMaestro(modelo=gguf, modo="auto", gpus_locales=0)
    tamano = gguf.stat().st_size
    tubos = {
        "lejos": tubo_completo(50100, gguf.name, tamano),
        "cerca": tubo_completo(50101, gguf.name, tamano),
        "rpc": tubo_rpc(50102),
    }
    medidas = {50100: 80.0, 50101: 12.0}

    async def fake_medir(puerto, veces=3):
        return medidas.get(puerto)

    llamadas: list[tuple[str, int]] = []

    async def fake_arrancar_completo(nombre, puerto):
        llamadas.append((nombre, puerto))

    async def caso():
        with patch.object(m, "_medir_salud", fake_medir), \
             patch.object(m, "_arrancar_completo", fake_arrancar_completo), \
             patch.object(m, "_esperar_tubos", AsyncMock(return_value=tubos)):
            await m._arrancar()

    asyncio.run(caso())
    assert llamadas == [("cerca", 50101)]


def test_auto_sin_completo_que_responda_cae_a_capas(gguf: Path):
    m = NodoMaestro(modelo=gguf, modo="auto", gpus_locales=0)
    tamano = gguf.stat().st_size
    tubos = {"caido": tubo_completo(50100, gguf.name, tamano), "rpc": tubo_rpc(50101)}
    capas = AsyncMock()
    with patch.object(m, "_medir_salud", AsyncMock(return_value=None)), \
         patch.object(m, "_esperar_tubos", AsyncMock(return_value=tubos)), \
         patch.object(m, "seleccionar_trabajadores", AsyncMock(return_value=[])) as sel, \
         patch.object(m, "_arrancar_servidor", capas):
        asyncio.run(m._arrancar())
    assert m.modo_activo == "capas"
    sel.assert_awaited_once_with(tubos)
    capas.assert_awaited_once_with([])


def test_modo_completo_exige_nodo_completo(gguf: Path):
    m = NodoMaestro(modelo=gguf, modo="completo", espera_trabajadores=0.2, gpus_locales=0)
    with patch.object(m, "obtener_tubos_registrados", AsyncMock(return_value={"rpc": tubo_rpc(50100)})):
        with pytest.raises(RuntimeError, match="ningun nodo completo"):
            asyncio.run(m._arrancar())


def test_seleccion_por_capas_nunca_mide_ni_manda_a_rpc_un_completo(gguf: Path):
    m = NodoMaestro(modelo=gguf, modo="capas", gpus_locales=0)
    tamano = gguf.stat().st_size
    tubos = {"completo": tubo_completo(50100, gguf.name, tamano), "rpc": tubo_rpc(50101)}
    medidos: list[int] = []

    def medir(puerto, *_a, **_k):
        medidos.append(puerto)
        return {"rtt_ms": 1.0, "conexion_ms": 1.0}

    with patch.object(reparto, "medir_enlace", medir):
        rpc = asyncio.run(m.seleccionar_trabajadores(tubos))
    assert 50100 not in medidos
    assert "127.0.0.1:50100" not in rpc
    assert medidos == [50101]


def test_reenvio_copia_en_ambos_sentidos():
    async def caso():
        async def eco(lector, escritor):
            while trozo := await lector.read(4096):
                escritor.write(trozo.upper())
                await escritor.drain()
            escritor.close()

        servidor_eco = await asyncio.start_server(eco, "127.0.0.1", 0)
        puerto_eco = servidor_eco.sockets[0].getsockname()[1]
        reenvio = Reenvio(puerto_libre(), puerto_eco)
        await reenvio.iniciar()
        try:
            lector, escritor = await asyncio.open_connection("127.0.0.1", reenvio.puerto_local)
            escritor.write(b"hola reenvio")
            await escritor.drain()
            recibido = await asyncio.wait_for(lector.read(100), 5)
            escritor.close()
            return recibido
        finally:
            reenvio.detener()
            servidor_eco.close()

    assert asyncio.run(caso()) == b"HOLA REENVIO"


def test_cli_servir_modos(monkeypatch: pytest.MonkeyPatch):
    from compushare.cli import app

    monkeypatch.setenv("FABRIC_CLAVE", "clave_prueba")
    runner = CliRunner()
    with patch.object(NodoMaestro, "iniciar", new_callable=AsyncMock) as iniciar, \
         patch.object(NodoMaestro, "detener"):
        r = runner.invoke(app, ["servir", "--modelo", "Qwen-test-Q8_0.gguf", "--modo", "completo",
                                "--modelo-bytes", "123", "--duracion", "0.01"])
        assert r.exit_code == 0, r.output
        assert iniciar.called
    r = runner.invoke(app, ["servir", "--modelo", "Qwen-test-Q8_0.gguf", "--modo", "auto"])
    assert r.exit_code == 1 and "no existe" in r.output
    r = runner.invoke(app, ["servir", "--modelo", "Qwen-test-Q8_0.gguf", "--modo", "raro"])
    assert r.exit_code == 1 and "auto, completo o capas" in r.output


# ── Punta a punta: relé real, trabajador completo y llama-server falso ──────────────────────────────────

CLAVE_E2E = "clave-e2e-completo"


async def _esperar(condicion, limite: float = 40.0) -> None:
    fin = time.monotonic() + limite
    while time.monotonic() < fin:
        if condicion():
            return
        await asyncio.sleep(0.2)
    raise AssertionError("se acabo el tiempo esperando")


async def _esperar_async(condicion, limite: float = 40.0) -> None:
    fin = time.monotonic() + limite
    while time.monotonic() < fin:
        if await condicion():
            return
        await asyncio.sleep(0.2)
    raise AssertionError("se acabo el tiempo esperando (async)")


async def _salud_en(puerto: int) -> bool:
    import httpx

    try:
        async with httpx.AsyncClient() as c:
            return (await c.get(f"http://127.0.0.1:{puerto}/health", timeout=1.0)).status_code == 200
    except Exception:
        return False


async def _pedir(puerto_api: int, prompt: str) -> str:
    motor = motores.Generar(f"http://127.0.0.1:{puerto_api}", max_tokens=32)

    async def avance(_texto: str) -> None:
        pass

    try:
        return (await motor.correr({"prompt": prompt, "pensar": False}, avance))["texto"]
    finally:
        await motor.http.aclose()


async def _detener_tareas(*tareas) -> None:
    for t in tareas:
        t.cancel()
    await asyncio.gather(*tareas, return_exceptions=True)


def test_auto_usa_nodo_completo_de_punta_a_punta(bin_falso, gguf, tmp_path, monkeypatch):
    """El maestro en auto no lanza llama-server: reenvía su puerto de API al del trabajador completo."""
    monkeypatch.setenv("LLAMA_FALSO_ARGV", str(tmp_path / "argv_trabajador.json"))

    async def caso():
        puerto_rele = puerto_libre()
        puerto_llama_trabajador = puerto_libre()
        puerto_api = puerto_libre()
        trabajador = NodoTrabajador(
            rele_url=f"ws://127.0.0.1:{puerto_rele}", clave=CLAVE_E2E, nombre="completo-1", vram_gb=8.0,
            llama_bin=bin_falso, modelo=gguf, completo=True, puerto_api=puerto_llama_trabajador,
        )
        maestro = NodoMaestro(
            modelo=gguf, rele_url=f"ws://127.0.0.1:{puerto_rele}", clave=CLAVE_E2E, puerto_api=puerto_api,
            puerto_rele=puerto_rele, llama_bin=bin_falso, espera_trabajadores=10, espera_carga=20,
            gpus_locales=0, tubo_puerto_base=puerto_libre(), modo="auto",
        )
        t_maestro = asyncio.create_task(maestro.iniciar())
        await _esperar(lambda: maestro._rele_task is not None and not maestro._rele_task.done())
        t_trabajador = asyncio.create_task(trabajador.conectar())
        try:
            await _esperar(lambda: maestro.modo_activo == "completo" or t_maestro.done(), 60)
            assert maestro.modo_activo == "completo", "el maestro no eligio el nodo completo"
            assert maestro.server_proc is None, "en modo completo el maestro no lanza llama-server"
            texto = await _pedir(puerto_api, "hola completo")
            assert f"llama falso en {puerto_llama_trabajador}: hola completo" in texto
        finally:
            maestro.detener()
            trabajador.detener()
            await _detener_tareas(t_maestro, t_trabajador)

    asyncio.run(caso())


def test_auto_sin_nodo_completo_arranca_llama_server_local(bin_falso, gguf):
    """Sin nodo completo ni trabajadores RPC, auto sirve el llama-server del maestro (capas, sin RPC)."""

    async def caso():
        puerto_rele = puerto_libre()
        puerto_api = puerto_libre()
        maestro = NodoMaestro(
            modelo=gguf, clave=CLAVE_E2E, puerto_api=puerto_api, puerto_rele=puerto_rele, llama_bin=bin_falso,
            espera_trabajadores=0.5, espera_carga=20, gpus_locales=0, tubo_puerto_base=puerto_libre(), modo="auto",
        )
        t_maestro = asyncio.create_task(maestro.iniciar())
        try:
            await _esperar(lambda: maestro.modo_activo == "capas" or t_maestro.done(), 60)
            assert maestro.modo_activo == "capas"
            await _esperar_async(lambda: _salud_en(puerto_api), 60)
            texto = await _pedir(puerto_api, "hola capas")
            assert f"llama falso en {puerto_api}: hola capas" in texto
        finally:
            maestro.detener()
            await _detener_tareas(t_maestro)

    asyncio.run(caso())


def test_nodo_completo_caido_se_relanza_y_si_desaparece_cae_a_capas(bin_falso, gguf, tmp_path, monkeypatch):
    """Si el llama-server del trabajador completo muere, el trabajador lo relanza y el maestro vuelve a elegirlo."""
    monkeypatch.setenv("LLAMA_FALSO_ARGV", str(tmp_path / "argv_trabajador.json"))

    async def caso():
        puerto_rele = puerto_libre()
        puerto_llama_trabajador = puerto_libre()
        puerto_api = puerto_libre()
        trabajador = NodoTrabajador(
            rele_url=f"ws://127.0.0.1:{puerto_rele}", clave=CLAVE_E2E, nombre="completo-2", vram_gb=8.0,
            llama_bin=bin_falso, modelo=gguf, completo=True, puerto_api=puerto_llama_trabajador,
        )
        maestro = NodoMaestro(
            modelo=gguf, rele_url=f"ws://127.0.0.1:{puerto_rele}", clave=CLAVE_E2E, puerto_api=puerto_api,
            puerto_rele=puerto_rele, llama_bin=bin_falso, espera_trabajadores=10, espera_carga=30,
            gpus_locales=0, tubo_puerto_base=puerto_libre(), modo="auto", max_reinicios=3,
        )
        t_maestro = asyncio.create_task(maestro.iniciar())
        await _esperar(lambda: maestro._rele_task is not None and not maestro._rele_task.done())
        t_trabajador = asyncio.create_task(trabajador.conectar())
        try:
            await _esperar(lambda: maestro.modo_activo == "completo", 60)
            # modo_activo cambia antes de que el nodo responda: primero se confirma que atiende de verdad.
            assert "llama falso" in await _pedir(puerto_api, "antes de la caida")

            # 1) Se cae solo su llama-server: el trabajador lo relanza en el mismo puerto y el maestro sigue
            #    con el mismo nodo (no hace falta reelegir).
            trabajador.llama_proc.kill()
            for _ in range(120):
                if await maestro._completo_vivo():
                    break
                await asyncio.sleep(0.5)
            assert f"llama falso en {puerto_llama_trabajador}: tras la caida" in await _pedir(puerto_api, "tras la caida")
            assert maestro.modo_activo == "completo"

            # 2) Desaparece el trabajador entero: el maestro lo da por caido, vuelve a elegir y, sin otro nodo
            #    completo, cae al reparto por capas (aqui, su propio llama-server).
            trabajador.detener()
            t_trabajador.cancel()
            await _esperar(lambda: maestro.modo_activo == "capas", 120)
            # modo_activo cambia antes de que llama-server local escuche: se espera su /health.
            for _ in range(240):
                if await _salud_en(puerto_api):
                    break
                await asyncio.sleep(0.5)
            assert maestro.reinicios >= 1
            assert f"llama falso en {puerto_api}: tras perder el nodo" in await _pedir(puerto_api, "tras perder el nodo")
        finally:
            maestro.detener()
            trabajador.detener()
            await _detener_tareas(t_maestro, t_trabajador)

    asyncio.run(caso())
