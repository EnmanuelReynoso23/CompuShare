"""Longitud del borrador en el maestro: flags de llama-server (nombres verificados en llama.cpp b11434),
γ* con el enlace medido por la sonda y la ruta completa de seleccionar_trabajadores con un tubo simulado."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from compushare.nodo import maestro as maestro_mod
from compushare.nodo.maestro import NodoMaestro
from fabric import calculo, reparto


def _modelo_9b(tmp_path: Path) -> Path:
    """Archivo disperso con nombre de 9B: el maestro lee el tamaño y las capas del nombre."""
    ruta = tmp_path / "Qwen3.5-9B-Q8_0.gguf"
    with open(ruta, "wb") as f:
        f.truncate(9_530_000_000)
    return ruta


def _nodo(tmp_path: Path, **kw) -> NodoMaestro:
    return NodoMaestro(modelo=_modelo_9b(tmp_path), clave="c", gpus_locales=1, **kw)


def _cmd(nodo: NodoMaestro) -> list[str]:
    with patch.object(maestro_mod, "buscar_binario", return_value=Path("/bin/llama-server")):
        return nodo.construir_comando_llama_server(["127.0.0.1:59100"])


def _valor(cmd: list[str], flag: str) -> str:
    return cmd[cmd.index(flag) + 1]


def test_ventana_fija_con_mtp_y_ngram_usa_los_dos_nombres(tmp_path):
    cmd = _cmd(_nodo(tmp_path, especulativa="ngram-mod,draft-mtp", ventana=3))
    assert _valor(cmd, "--spec-type") == "ngram-mod,draft-mtp"
    assert _valor(cmd, "--spec-draft-n-max") == "3"
    assert _valor(cmd, "--spec-ngram-mod-n-max") == "3"
    assert _valor(cmd, "--spec-ngram-mod-n-min") == "3"  # con 3 < 48 no se exige una coincidencia completa
    assert "--draft-max" not in cmd and "--draft-n" not in cmd  # esas opciones ya no existen en b11434


def test_ventana_fija_grande_no_baja_el_minimo_de_ngram(tmp_path):
    cmd = _cmd(_nodo(tmp_path, especulativa="ngram-mod", ventana=64))
    assert _valor(cmd, "--spec-ngram-mod-n-max") == "64"
    assert _valor(cmd, "--spec-ngram-mod-n-min") == "48"  # el valor por defecto de llama.cpp
    assert "--spec-draft-n-max" not in cmd  # ngram-mod no usa el draft del modelo


def test_draft_mtp_solo_pasa_draft_n_max(tmp_path):
    cmd = _cmd(_nodo(tmp_path, especulativa="draft-mtp", ventana=5))
    assert _valor(cmd, "--spec-draft-n-max") == "5"
    assert "--spec-ngram-mod-n-max" not in cmd


def test_ventana_cero_no_toca_los_flags(tmp_path):
    cmd = _cmd(_nodo(tmp_path, especulativa="ngram-mod,draft-mtp", ventana=0))
    assert _valor(cmd, "--spec-type") == "ngram-mod,draft-mtp"
    assert not any(a.startswith("--spec-draft-n-max") or a.startswith("--spec-ngram-mod-n-max") for a in cmd)


def test_sin_especulativa_no_hay_flags_de_ventana(tmp_path):
    cmd = _cmd(_nodo(tmp_path, ventana=8))
    assert "--spec-type" not in cmd and "--spec-draft-n-max" not in cmd


def test_auto_sin_prediccion_usa_el_valor_por_defecto_documentado(tmp_path):
    cmd = _cmd(_nodo(tmp_path, especulativa="draft-mtp"))
    assert _valor(cmd, "--spec-draft-n-max") == str(maestro_mod.GAMMA_SIN_PREDECIR)


def test_auto_usa_el_gamma_calculado(tmp_path):
    nodo = _nodo(tmp_path, especulativa="draft-mtp")
    nodo.gamma_optimo = 7
    assert _valor(_cmd(nodo), "--spec-draft-n-max") == "7"


def test_validaciones(tmp_path):
    with pytest.raises(ValueError):
        _nodo(tmp_path, ventana=-1)
    with pytest.raises(ValueError):
        _nodo(tmp_path, ventana="abc")
    with pytest.raises(ValueError):
        _nodo(tmp_path, acierto=1.5)
    assert _nodo(tmp_path, ventana="12").ventana == 12


def test_predecir_ventana_crece_con_el_enlace(tmp_path):
    nodo = _nodo(tmp_path, especulativa="draft-mtp")
    maestro = calculo.Equipo("m", 6, calculo.T4_GBS)
    trabajador = calculo.Equipo("t", 8, 320.0, rtt_ms=40.0, subida_mbps=15.0)
    modelo = maestro_mod.extraer_modelo_info(nodo.modelo)

    def gamma(ancho: float) -> int:
        enlaces = {"t": {"rtt_ms": 40.0, "bajada_mbps": ancho, "subida_mbps": ancho}}
        return nodo.predecir_ventana(modelo, maestro, [trabajador], enlaces).gamma

    assert gamma(500.0) >= gamma(15.0) >= gamma(5.0)


def _tubo(nombre: str, info: dict) -> dict:
    return {"puerto": 59100, "info": {"vram_gb": 8.0, **info}}


def test_seleccion_usa_la_sonda_medida_y_no_el_100_fijo(tmp_path, caplog):
    """Ruta completa: el relé ya tiene la sonda del tubo; el maestro la usa para γ* y el log lo dice."""
    caplog.set_level(logging.INFO, logger="compushare.maestro")

    async def caso(subida: float) -> NodoMaestro:
        nodo = _nodo(tmp_path, especulativa="draft-mtp", espera_trabajadores=0.5)
        tubos = {"trabajador": _tubo("trabajador", {"bajada_mbps": subida, "subida_mbps": subida, "sonda": "ok"})}
        with patch.object(nodo, "obtener_tubos_registrados", AsyncMock(return_value=tubos)), \
             patch.object(maestro_mod, "detectar_memoria_maestro_gb", return_value=6.0), \
             patch.object(reparto, "medir_enlace", return_value={"rtt_ms": 40.0, "conexion_ms": 90.0}):
            rpc = await nodo.seleccionar_trabajadores()
        assert rpc == ["127.0.0.1:59100"]
        return nodo

    lento = asyncio.run(caso(5.0))
    rapido = asyncio.run(caso(500.0))
    assert lento.gamma_optimo is not None and rapido.gamma_optimo is not None
    assert rapido.gamma_optimo >= lento.gamma_optimo
    assert lento.prediccion is not None
    assert lento.gamma_optimo == max(1, lento.prediccion.gamma)
    assert "gamma*=" in caplog.text and "medido" in caplog.text


def test_seleccion_sin_sonda_usa_el_supuesto_documentado(tmp_path, caplog, monkeypatch):
    monkeypatch.setattr(maestro_mod, "ESPERA_SONDA_S", 0.5)
    caplog.set_level(logging.INFO, logger="compushare.maestro")

    async def caso() -> NodoMaestro:
        nodo = _nodo(tmp_path, especulativa="draft-mtp", espera_trabajadores=0.5)
        tubos = {"sin-sonda": _tubo("sin-sonda", {})}
        with patch.object(nodo, "obtener_tubos_registrados", AsyncMock(return_value=tubos)), \
             patch.object(maestro_mod, "detectar_memoria_maestro_gb", return_value=6.0), \
             patch.object(reparto, "medir_enlace", return_value={"rtt_ms": 40.0, "conexion_ms": 90.0}):
            await nodo.seleccionar_trabajadores()
        return nodo

    nodo = asyncio.run(caso())
    assert nodo.gamma_optimo is not None
    assert "supuesto, sin sonda" in caplog.text
    assert nodo.prediccion is not None
    assert nodo.prediccion.tabla[0]["gamma"] == 0
