"""engine/red.py: SynRAG busca en local y un nodo de la red redacta (aqui un nodo falso)."""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path

from fabric.pruebas.test_red import CLAVE, red

import pytest  # noqa: E402

_ENGINE = Path(__file__).resolve().parents[2] / "engine"
if not (_ENGINE / "red.py").exists():
    pytest.skip("engine/red.py es de SynRAG; esta prueba corre en el repo de SynRAG", allow_module_level=True)
sys.path.insert(0, str(_ENGINE))
import red as red_fabric  # noqa: E402


class Redactor:
    tipo = "generar"
    concurrencia = 1

    def __init__(self) -> None:
        self.mensajes: list = []

    async def correr(self, datos, avance):
        self.mensajes = datos["mensajes"]
        return {"texto": "Se define en app.py:3-5", "modelo": "Qwen-falso", "tokens_por_segundo": 12.5}


def ubicacion(r: dict) -> str:
    return f"~/{r['rel_path']}:{r['line']}-{r['end_line']}"


def test_preguntar_red_usa_resultados_locales(monkeypatch):
    falso = types.ModuleType("indexer")
    falso.search_desktop = lambda q, limit, project, check_impact: [
        {"rel_path": "app.py", "line": 3, "end_line": 5, "content": "def crear_app():\n    return Flask()"}
    ]
    monkeypatch.setitem(sys.modules, "indexer", falso)
    redactor = Redactor()

    async def caso():
        async with red([redactor]) as (url, _c, _n):
            monkeypatch.setattr(red_fabric, "config", lambda: {"FABRIC_RELE": url, "FABRIC_CLAVE": CLAVE, "FABRIC_ENJAMBRE": "pruebas"})
            salida = await red_fabric.preguntar_red("donde se crea la app", "", 5, "falso", ubicacion)
            assert salida.startswith("Se define en app.py:3-5")
            assert "~/app.py:3-5" in salida and "Qwen-falso" in salida and "12.5 tok/s" in salida
            assert "def crear_app" in redactor.mensajes[1]["content"]
            assert "donde se crea la app" in redactor.mensajes[1]["content"]

    asyncio.run(caso())


def test_preguntar_red_sin_nodos_devuelve_lo_local(monkeypatch):
    falso = types.ModuleType("indexer")
    falso.search_desktop = lambda q, limit, project, check_impact: [{"rel_path": "a.py", "line": 1, "end_line": 2, "content": "x"}]
    monkeypatch.setitem(sys.modules, "indexer", falso)

    async def caso():
        async with red() as (url, _c, _n):
            monkeypatch.setattr(red_fabric, "config", lambda: {"FABRIC_RELE": url, "FABRIC_CLAVE": CLAVE, "FABRIC_ENJAMBRE": "pruebas"})
            salida = await red_fabric.preguntar_red("x", "", 5, "", ubicacion)
            assert "no hay ningun nodo" in salida and "~/a.py:1-2" in salida

    asyncio.run(caso())
