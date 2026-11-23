"""Motor Go (P4): `compushare unirse --motor go` lanza el binario compushare-tubo y `servir --motor-rele go`
lanza compushare-rele. Los binarios se compilan en un directorio temporal; si no hay `go` la prueba se omite.

Lo que se verifica:
- el relé Go responde /salud con `tubos` en el mismo formato que lee el maestro, y respeta TUBO_PUERTO_BASE;
- punta a punta: relé Go + tubo Go + trabajador completo + maestro (auto), con llama-server falso;
- la clave va por el entorno del proceso, no por la línea de comandos;
- si falta el binario, el mensaje dice cómo compilarlo.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from compushare.nodo import procesos
from compushare.nodo.maestro import NodoMaestro
from compushare.nodo.trabajador import NodoTrabajador
from fabric.pruebas.test_modo_completo import bin_falso, gguf, puerto_libre, registros_aislados  # noqa: F401
from fabric.pruebas.test_modo_maestro import _detener_tareas, _esperar, _pedir

RAIZ_REPO = Path(__file__).resolve().parents[2]
CLAVE = "clave-motor-go"


def _go() -> str | None:
    return shutil.which("go") or ("/usr/local/go/bin/go" if Path("/usr/local/go/bin/go").exists() else None)


@pytest.fixture(scope="module")
def bins_go(tmp_path_factory) -> Path:
    """compushare-tubo y compushare-rele compilados desde go/ en un directorio temporal."""
    go = _go()
    if go is None:
        pytest.skip("no hay `go` instalado")
    salida = tmp_path_factory.mktemp("bin_go")
    env = {**os.environ, "PATH": f"{Path(go).parent}{os.pathsep}{os.environ.get('PATH', '')}"}
    subprocess.run([go, "build", "-o", str(salida) + os.sep, "./cmd/compushare-tubo", "./cmd/compushare-rele"],
                   cwd=RAIZ_REPO / "go", env=env, check=True, timeout=300)
    return salida


def _arrancar_rele_go(bin_dir: Path, puerto: int, base: int) -> subprocess.Popen:
    cmd = [str(bin_dir / "compushare-rele"), "--puerto", str(puerto), "--tubo-puerto-base", str(base)]
    proc, _ = procesos.lanzar(cmd, f"rele-go-test-{puerto}", {**os.environ, "FABRIC_CLAVE": CLAVE})
    return proc


async def _esperar_salud(puerto: int, limite: float = 10.0) -> dict:
    import httpx

    fin = asyncio.get_running_loop().time() + limite
    async with httpx.AsyncClient() as c:
        while asyncio.get_running_loop().time() < fin:
            try:
                r = await c.get(f"http://127.0.0.1:{puerto}/salud", timeout=1.0)
                if r.status_code == 200:
                    return r.json()
            except Exception:
                pass
            await asyncio.sleep(0.2)
    raise AssertionError("el relé Go no respondio /salud")


def test_salud_del_rele_go_tiene_tubos_como_los_lee_el_maestro(bins_go: Path):
    puerto_rele = puerto_libre()
    base = puerto_libre()
    rele = _arrancar_rele_go(bins_go, puerto_rele, base)

    async def caso():
        try:
            salud = await _esperar_salud(puerto_rele)
            assert "tubos" in salud and isinstance(salud["tubos"], dict)
            tubo = subprocess.Popen(
                [str(bins_go / "compushare-tubo"), "--rele", f"ws://127.0.0.1:{puerto_rele}", "--nombre", "go-1",
                 "--puerto", "1", "--vram-gb", "6.5",
                 "--info-json", json.dumps({"modo": "completo", "modelo": "m.gguf", "modelo_bytes": 12345})],
                env={**os.environ, "FABRIC_CLAVE": CLAVE}, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
            try:
                maestro = NodoMaestro(modelo=Path("m.gguf"), modo="completo", gpus_locales=0,
                                      puerto_rele=puerto_rele, tubo_puerto_base=base)
                fin = asyncio.get_running_loop().time() + 10
                tubos = {}
                while asyncio.get_running_loop().time() < fin:
                    tubos = await maestro.obtener_tubos_registrados()
                    if "go-1" in tubos:
                        break
                    await asyncio.sleep(0.2)
                assert "go-1" in tubos, f"el tubo Go no aparece en /salud: {tubos}"
                det = tubos["go-1"]
                assert base <= int(det["puerto"]) < base + 100, "no respeta TUBO_PUERTO_BASE"
                assert det["info"]["modo"] == "completo"
                assert det["info"]["modelo_bytes"] == 12345
                assert det["info"]["vram_gb"] == 6.5
                assert maestro._candidatos_completos(tubos) == [("go-1", int(det["puerto"]))]
            finally:
                tubo.terminate()
                tubo.wait(timeout=5)
        finally:
            rele.terminate()
            rele.wait(timeout=5)

    asyncio.run(caso())


def test_motor_go_punta_a_punta_con_nodo_completo(bins_go: Path, bin_falso, gguf, tmp_path, monkeypatch):
    """Relé Go + tubo Go + trabajador completo + maestro en auto: el pedido llega al llama-server falso."""
    monkeypatch.setenv("LLAMA_FALSO_ARGV", str(tmp_path / "argv.json"))

    async def caso():
        puerto_rele = puerto_libre()
        base = puerto_libre()
        puerto_llama = puerto_libre()
        puerto_api = puerto_libre()
        trabajador = NodoTrabajador(
            rele_url=f"ws://127.0.0.1:{puerto_rele}", clave=CLAVE, nombre="go-completo", vram_gb=8.0,
            llama_bin=bin_falso, modelo=gguf, completo=True, puerto_api=puerto_llama,
            motor="go", go_bin=bins_go,
        )
        maestro = NodoMaestro(
            modelo=gguf, rele_url=f"ws://127.0.0.1:{puerto_rele}", clave=CLAVE, puerto_api=puerto_api,
            puerto_rele=puerto_rele, llama_bin=bin_falso, espera_trabajadores=15, espera_carga=30,
            gpus_locales=0, tubo_puerto_base=base, modo="auto", motor_rele="go", go_bin=bins_go,
        )
        t_maestro = asyncio.create_task(maestro.iniciar())
        t_trabajador = None
        try:
            await _esperar(lambda: maestro._rele_proc is not None, 20)
            t_trabajador = asyncio.create_task(trabajador.conectar())
            await _esperar(lambda: maestro.modo_activo == "completo" or t_maestro.done(), 90)
            assert maestro.modo_activo == "completo", "el maestro no eligio el nodo completo con el relé Go"
            assert maestro._rele_task is None  # el relé corre como proceso Go, no como tarea de este proceso
            assert f"llama falso en {puerto_llama}: hola go" in await _pedir(puerto_api, "hola go")
        finally:
            maestro.detener()
            trabajador.detener()
            tareas = [t_maestro] + ([t_trabajador] if t_trabajador else [])
            await _detener_tareas(*tareas)

    asyncio.run(caso())


def test_clave_no_va_en_la_linea_de_comandos(bins_go: Path, bin_falso, gguf, monkeypatch):
    """El tubo Go recibe la clave por FABRIC_CLAVE: ps no debe poder leerla."""
    trabajador = NodoTrabajador(
        rele_url="ws://127.0.0.1:1", clave=CLAVE, nombre="sin-clave-en-argv", vram_gb=3.0,
        llama_bin=bin_falso, motor="go", go_bin=bins_go,
    )
    llamadas = []

    def falso_lanzar(cmd, nombre, env=None):
        llamadas.append((cmd, env))
        proc = MagicMock()
        proc.poll.return_value = None
        proc.pid = 1
        return proc, Path("/dev/null")

    async def caso():
        with patch.object(procesos, "lanzar", falso_lanzar):
            try:
                await asyncio.wait_for(trabajador._mantener_tubo_go(50052, {"vram_gb": 3.0}), 0.6)
            except asyncio.TimeoutError:
                pass

    asyncio.run(caso())
    cmd, env = llamadas[0]
    assert CLAVE not in " ".join(cmd)
    assert env["FABRIC_CLAVE"] == CLAVE
    assert "--vram-gb" in cmd and "--nombre" in cmd


def test_falta_el_binario_go_dice_como_compilarlo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(procesos, "RAIZ_REPO", tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path))
    with pytest.raises(FileNotFoundError, match="go build"):
        NodoTrabajador(rele_url="ws://127.0.0.1:1", clave="c", motor="go")


def test_cli_motores_validan_y_avisan(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from compushare.cli import app

    runner = CliRunner()
    monkeypatch.setenv("FABRIC_CLAVE", "clave_prueba")
    r = runner.invoke(app, ["unirse", "--motor", "raro"])
    assert r.exit_code == 1 and "python o go" in r.output

    monkeypatch.setattr(procesos, "RAIZ_REPO", tmp_path)
    monkeypatch.setenv("PATH", str(tmp_path))
    r = runner.invoke(app, ["unirse", "--motor", "go"])
    assert r.exit_code == 1 and "go build" in r.output
    modelo = tmp_path / "m.gguf"
    modelo.write_bytes(b"GGUF")
    r = runner.invoke(app, ["servir", "--modelo", str(modelo), "--motor-rele", "go"])
    assert r.exit_code == 1 and "compushare-rele" in r.output
