"""Pruebas de interoperabilidad entre relé y tubo mezclando piezas en Python y Go."""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import time
from pathlib import Path

import pytest

CLAVE = "clave-interop-test"
RAIZ_WORKTREE = Path(__file__).resolve().parent.parent.parent
BIN_RELE_GO = RAIZ_WORKTREE / "go" / "bin" / "compushare-rele"
BIN_TUBO_GO = RAIZ_WORKTREE / "go" / "bin" / "compushare-tubo"


def puerto_libre(inicio: int = 52000, fin: int = 52999) -> int:
    for p in range(inicio, fin + 1):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                s.bind(("127.0.0.1", p))
                return p
            except OSError:
                continue
    raise RuntimeError(f"no hay puertos libres en el rango {inicio}-{fin}")


async def iniciar_servidor_eco(puerto: int):
    async def eco(lector, escritor):
        while datos := await lector.read(65536):
            escritor.write(datos)
            await escritor.drain()
        escritor.close()

    return await asyncio.start_server(eco, "127.0.0.1", puerto)


@pytest.mark.skipif(not BIN_RELE_GO.exists() or not BIN_TUBO_GO.exists(), reason="Binarios Go no compilados en go/bin")
def test_rele_python_tubo_go():
    """Relé Python + Tubo Go con eco TCP."""
    from fabric.rele import rele as modulo_rele

    async def caso():
        puerto_rele = puerto_libre(52100, 52199)
        puerto_eco = puerto_libre(52200, 52299)
        puerto_tubo = puerto_libre(52300, 52399)
        servidor_eco = await iniciar_servidor_eco(puerto_eco)

        orig_tubo_base = os.environ.get("TUBO_PUERTO_BASE")
        os.environ["TUBO_PUERTO_BASE"] = str(puerto_tubo)
        modulo_rele.TUBO_PUERTO_BASE = puerto_tubo

        listo_rele = asyncio.Event()
        tarea_rele = asyncio.create_task(modulo_rele.principal(CLAVE, puerto_rele, listo_rele))
        await asyncio.wait_for(listo_rele.wait(), 5)

        proc_tubo = subprocess.Popen(
            [
                str(BIN_TUBO_GO),
                "--rele", f"ws://127.0.0.1:{puerto_rele}",
                "--clave", CLAVE,
                "--nombre", "trabajador-go",
                "--puerto", str(puerto_eco),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            limite = time.monotonic() + 5.0
            conectado = False
            lector, escritor = None, None
            while time.monotonic() < limite:
                try:
                    lector, escritor = await asyncio.open_connection("127.0.0.1", puerto_tubo)
                    conectado = True
                    break
                except OSError:
                    await asyncio.sleep(0.1)

            assert conectado, f"no se pudo conectar al puerto {puerto_tubo} del relé ofrecido por el tubo Go"

            carga = os.urandom(1024 * 1024)
            escritor.write(carga)
            await escritor.drain()
            vuelta = await asyncio.wait_for(lector.readexactly(len(carga)), timeout=10)
            assert vuelta == carga
            escritor.close()
            await escritor.wait_closed()
        finally:
            proc_tubo.terminate()
            proc_tubo.wait(timeout=2)
            servidor_eco.close()
            tarea_rele.cancel()
            await asyncio.gather(tarea_rele, return_exceptions=True)
            if orig_tubo_base is not None:
                os.environ["TUBO_PUERTO_BASE"] = orig_tubo_base
            else:
                os.environ.pop("TUBO_PUERTO_BASE", None)

    asyncio.run(caso())


@pytest.mark.skipif(not BIN_RELE_GO.exists() or not BIN_TUBO_GO.exists(), reason="Binarios Go no compilados en go/bin")
def test_rele_go_tubo_python():
    """Relé Go + Tubo Python con eco TCP."""
    from fabric.tubo import Tubo

    async def caso():
        puerto_rele = puerto_libre(52100, 52199)
        puerto_eco = puerto_libre(52200, 52299)
        puerto_tubo = puerto_libre(52400, 52499)
        servidor_eco = await iniciar_servidor_eco(puerto_eco)

        proc_rele = subprocess.Popen(
            [
                str(BIN_RELE_GO),
                "--puerto", str(puerto_rele),
                "--clave", CLAVE,
                "--tubo-puerto-base", str(puerto_tubo),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        limite = time.monotonic() + 5.0
        import urllib.request
        arriba = False
        while time.monotonic() < limite:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{puerto_rele}/salud", timeout=1) as r:
                    if r.status == 200:
                        arriba = True
                        break
            except Exception:
                await asyncio.sleep(0.05)
        assert arriba, "el relé Go no respondió /salud"

        tubo_py = Tubo(f"ws://127.0.0.1:{puerto_rele}", CLAVE, "trabajador-py", puerto_eco)
        tarea_tubo = asyncio.create_task(tubo_py.vivir())

        try:
            await asyncio.wait_for(tubo_py.listo.wait(), timeout=5)
            assert tubo_py.puerto_remoto == puerto_tubo

            lector, escritor = await asyncio.open_connection("127.0.0.1", tubo_py.puerto_remoto)
            carga = os.urandom(1024 * 1024)
            escritor.write(carga)
            await escritor.drain()
            vuelta = await asyncio.wait_for(lector.readexactly(len(carga)), timeout=10)
            assert vuelta == carga
            escritor.close()
            await escritor.wait_closed()
        finally:
            tarea_tubo.cancel()
            proc_rele.terminate()
            proc_rele.wait(timeout=2)
            servidor_eco.close()
            await asyncio.gather(tarea_tubo, return_exceptions=True)

    asyncio.run(caso())


@pytest.mark.skipif(not BIN_RELE_GO.exists() or not BIN_TUBO_GO.exists(), reason="Binarios Go no compilados en go/bin")
def test_rele_go_tubo_go():
    """Relé Go + Tubo Go con eco TCP."""
    async def caso():
        puerto_rele = puerto_libre(52100, 52199)
        puerto_eco = puerto_libre(52200, 52299)
        puerto_tubo = puerto_libre(52500, 52599)
        servidor_eco = await iniciar_servidor_eco(puerto_eco)

        proc_rele = subprocess.Popen(
            [
                str(BIN_RELE_GO),
                "--puerto", str(puerto_rele),
                "--clave", CLAVE,
                "--tubo-puerto-base", str(puerto_tubo),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        limite = time.monotonic() + 5.0
        import urllib.request
        arriba = False
        while time.monotonic() < limite:
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{puerto_rele}/salud", timeout=1) as r:
                    if r.status == 200:
                        arriba = True
                        break
            except Exception:
                await asyncio.sleep(0.05)
        assert arriba, "el relé Go no respondió /salud"

        proc_tubo = subprocess.Popen(
            [
                str(BIN_TUBO_GO),
                "--rele", f"ws://127.0.0.1:{puerto_rele}",
                "--clave", CLAVE,
                "--nombre", "trabajador-go-2",
                "--puerto", str(puerto_eco),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

        try:
            limite = time.monotonic() + 5.0
            conectado = False
            lector, escritor = None, None
            while time.monotonic() < limite:
                try:
                    lector, escritor = await asyncio.open_connection("127.0.0.1", puerto_tubo)
                    conectado = True
                    break
                except OSError:
                    await asyncio.sleep(0.05)

            assert conectado, f"no se pudo conectar al puerto {puerto_tubo} del relé Go"

            carga = os.urandom(2 * 1024 * 1024)
            escritor.write(carga)
            await escritor.drain()
            vuelta = await asyncio.wait_for(lector.readexactly(len(carga)), timeout=10)
            assert vuelta == carga
            escritor.close()
            await escritor.wait_closed()
        finally:
            proc_tubo.terminate()
            proc_tubo.wait(timeout=2)
            proc_rele.terminate()
            proc_rele.wait(timeout=2)
            servidor_eco.close()

    asyncio.run(caso())
