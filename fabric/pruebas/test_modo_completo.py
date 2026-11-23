"""Modo completo (P4): un trabajador con el modelo entero corre llama-server y ofrece ese puerto por el tubo.

Se usa `fabric/pruebas/llama_falso.py` en lugar de llama.cpp: un ejecutable `llama-server` en un directorio
temporal (se pasa como `llama_bin`) que habla el protocolo HTTP y anota con qué argumentos lo lanzaron.
Los registros de procesos van a un directorio temporal para no ensuciar ~/.cache.
"""

from __future__ import annotations

import json
import os
import socket
import sys
from pathlib import Path

import pytest

from compushare.nodo import procesos
from compushare.nodo.trabajador import NodoTrabajador

RAIZ_PRUEBAS = Path(__file__).resolve().parent
LLAMA_FALSO = RAIZ_PRUEBAS / "llama_falso.py"


def puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(autouse=True)
def registros_aislados(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    carpeta = tmp_path / "registros"
    monkeypatch.setattr(procesos, "DIR_REGISTROS", carpeta)
    return carpeta


@pytest.fixture
def bin_falso(tmp_path: Path) -> Path:
    """Directorio con un `llama-server` ejecutable que es en realidad llama_falso.py con este Python."""
    carpeta = tmp_path / "bin"
    carpeta.mkdir()
    wrapper = carpeta / "llama-server"
    wrapper.write_text(f'#!/bin/sh\nexec "{sys.executable}" "{LLAMA_FALSO}" "$@"\n', encoding="utf-8")
    wrapper.chmod(0o755)
    return carpeta


@pytest.fixture
def gguf(tmp_path: Path) -> Path:
    ruta = tmp_path / "Qwen-test-Q8_0.gguf"
    with open(ruta, "wb") as f:
        f.truncate(12345)  # tamano falso sin ocupar disco
    return ruta


def test_trabajador_completo_lanza_llama_con_borrador_y_ofrece_info(bin_falso, gguf, tmp_path, monkeypatch):
    argv_archivo = tmp_path / "argv.json"
    monkeypatch.setenv("LLAMA_FALSO_ARGV", str(argv_archivo))
    puerto = puerto_libre()
    borrador = tmp_path / "draft.gguf"
    borrador.write_bytes(b"GGUF")

    nodo = NodoTrabajador(
        rele_url="ws://127.0.0.1:1", clave="c", nombre="completo-1", vram_gb=8.0, llama_bin=bin_falso,
        modelo=gguf, completo=True, borrador=borrador, especulativa="ngram-mod", puerto_api=puerto,
        ctx_size=2048, paralelo=2,
    )
    try:
        nodo._lanzar_ofrecido()
        args = json.loads(argv_archivo.read_text(encoding="utf-8"))
        assert args[args.index("--port") + 1] == str(puerto)
        assert args[args.index("--host") + 1] == "127.0.0.1"
        assert args[args.index("-m") + 1] == str(gguf)
        assert args[args.index("-md") + 1] == str(borrador)
        assert args[args.index("--spec-type") + 1] == "ngram-mod"
        assert args[args.index("-c") + 1] == str(2048 * 2)
        assert "--rpc" not in args

        info = nodo.info_tubo()
        assert info == {"modo": "completo", "modelo": gguf.name, "modelo_bytes": 12345, "vram_gb": 8.0}
        # El relé deja como mucho 10 claves de hasta 30 caracteres, con valores int/float/str.
        assert len(info) <= 10
        assert all(len(k) <= 30 and isinstance(v, (int, float, str)) for k, v in info.items())
    finally:
        nodo.detener()
    assert nodo.llama_proc.poll() is not None


def test_trabajador_completo_sin_borrador_no_pone_md(bin_falso, gguf, tmp_path, monkeypatch):
    argv_archivo = tmp_path / "argv.json"
    monkeypatch.setenv("LLAMA_FALSO_ARGV", str(argv_archivo))
    nodo = NodoTrabajador(
        rele_url="ws://127.0.0.1:1", clave="c", vram_gb=0.0, llama_bin=bin_falso,
        modelo=gguf, completo=True, puerto_api=puerto_libre(),
    )
    try:
        nodo._lanzar_ofrecido()
        args = json.loads(argv_archivo.read_text(encoding="utf-8"))
        assert "-md" not in args
        assert "--spec-type" not in args
    finally:
        nodo.detener()


def test_trabajador_completo_exige_el_archivo_del_modelo(tmp_path):
    with pytest.raises(FileNotFoundError):
        NodoTrabajador(rele_url="ws://127.0.0.1:1", clave="c", vram_gb=1.0, modelo=tmp_path / "no.gguf", completo=True)


def test_trabajador_sin_completo_info_es_solo_vram(tmp_path):
    nodo = NodoTrabajador(rele_url="ws://127.0.0.1:1", clave="c", vram_gb=4.5)
    assert nodo.info_tubo() == {"vram_gb": 4.5}
    nodo_sin_vram = NodoTrabajador(rele_url="ws://127.0.0.1:1", clave="c", vram_gb=0.0)
    assert "modo" not in nodo_sin_vram.info_tubo()


def test_cli_unirse_completo_valida_modelo(tmp_path):
    from typer.testing import CliRunner

    from compushare.cli import app

    runner = CliRunner()
    r = runner.invoke(app, ["unirse", "--clave", "c", "--completo"])
    assert r.exit_code == 1
    assert "--completo necesita --modelo" in r.output

    gguf = tmp_path / "m.gguf"
    gguf.write_bytes(b"GGUF")
    r = runner.invoke(app, ["unirse", "--clave", "c", "--modelo", str(gguf)])
    assert r.exit_code == 1
    assert "solo valen con --completo" in r.output
