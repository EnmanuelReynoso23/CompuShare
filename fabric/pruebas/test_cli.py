"""Pruebas unitarias de la CLI compushare usando typer.testing.CliRunner y mocks de subprocess."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from typer.testing import CliRunner

from compushare.cli import app, resolver_clave
from compushare.nodo.maestro import NodoMaestro, extraer_modelo_info
from compushare.nodo.trabajador import NodoTrabajador

runner = CliRunner()



def _archivo_disperso(ruta: Path, tamano: int) -> None:
    """Archivo del tamano pedido sin ocupar RAM ni disco (antes se armaban 16,5 GB en memoria y el sistema mataba el proceso)."""
    with open(ruta, "wb") as f:
        f.truncate(tamano)

def test_cli_ayuda():
    """Verifica que la CLI responda a --help y muestre los comandos principales."""
    resultado = runner.invoke(app, ["--help"])
    assert resultado.exit_code == 0
    assert "info" in resultado.output
    assert "unirse" in resultado.output
    assert "servir" in resultado.output
    assert "calcular" in resultado.output


def test_cli_info_sin_medir_banda():
    """Verifica el comando info en modo rapido sin medir ancho de banda."""
    resultado = runner.invoke(app, ["info", "--no-medir-banda"])
    assert resultado.exit_code == 0
    assert "Hardware Local" in resultado.output
    assert "Compatibilidad de Modelos" in resultado.output
    assert "Qwen3.5-0.8B" in resultado.output
    assert "Qwen3.8-27B" in resultado.output


def test_cli_info_con_mock_gpu_y_banda():
    """Verifica que info muestre GPU y ancho de banda medido con llama-bench."""
    mock_gpus = [{"nombre": "NVIDIA GeForce RTX 4060", "total_gb": 8.0, "libre_gb": 7.5}]
    mock_banda = {"banda_gbs": 38.5, "tok_s": 42.1, "modelo": "Qwen3.5-0.8B-Q8_0.gguf"}

    with patch("compushare.cli.obtener_info_gpus", return_value=mock_gpus), \
         patch("compushare.cli.medir_banda_memoria", return_value=mock_banda):
        resultado = runner.invoke(app, ["info"])
        assert resultado.exit_code == 0
        assert "RTX 4060" in resultado.output
        assert "38.5 GB/s" in resultado.output
        assert "42.1 tok/s" in resultado.output


def test_cli_calcular_defecto():
    """Verifica el comando calcular con valores por defecto."""
    resultado = runner.invoke(app, ["calcular"])
    assert resultado.exit_code == 0
    assert "Calibración" in resultado.output or "Calibracion" in resultado.output
    assert "Qwen3.8-27B" in resultado.output
    assert "palabras/s" in resultado.output


def test_cli_calcular_personalizado():
    """Verifica calcular con parametros especificos de red."""
    resultado = runner.invoke(app, ["calcular", "--rtt-ms", "20", "--subida-mbps", "100", "--modelo", "27b"])
    assert resultado.exit_code == 0
    assert "RTT: 20" in resultado.output or "RTT 20" in resultado.output
    assert "100" in resultado.output
    assert "Qwen3.8-27B" in resultado.output


def test_resolver_clave(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verifica la resolucion de la clave desde parametro, entorno o archivo sin imprimirla."""
    monkeypatch.delenv("FABRIC_CLAVE", raising=False)

    # Parametro directo
    assert resolver_clave("clave_directa") == "clave_directa"

    # Variable de entorno
    monkeypatch.setenv("FABRIC_CLAVE", "clave_en_env")
    assert resolver_clave() == "clave_en_env"

    # Archivo dedicado
    archivo = tmp_path / "clave.txt"
    archivo.write_text("clave_en_archivo\n", encoding="utf-8")
    monkeypatch.delenv("FABRIC_CLAVE", raising=False)
    assert resolver_clave(clave_archivo=archivo) == "clave_en_archivo"


def test_cli_unirse_sin_clave(monkeypatch: pytest.MonkeyPatch):
    """Verifica que unirse falle limpiamente si falta la clave."""
    monkeypatch.delenv("FABRIC_CLAVE", raising=False)
    resultado = runner.invoke(app, ["unirse"])
    assert resultado.exit_code == 1
    assert "Error: No se especifico la clave" in resultado.output


def test_cli_unirse_mock_no_imprime_clave(monkeypatch: pytest.MonkeyPatch):
    """Verifica que compushare unirse configure el trabajador y NUNCA imprima la clave secreta."""
    monkeypatch.delenv("FABRIC_CLAVE", raising=False)
    clave_secreta = "supersecreto_no_filtrar_12345"

    with patch.object(NodoTrabajador, "conectar", new_callable=AsyncMock) as mock_conectar, \
         patch.object(NodoTrabajador, "detener") as mock_detener:
        resultado = runner.invoke(
            app,
            [
                "unirse",
                "--rele", "ws://127.0.0.1:59095",
                "--clave", clave_secreta,
                "--puerto-rpc", "59052",
                "--threads", "4",
                "--duracion", "0.01",
            ],
        )
        assert resultado.exit_code == 0
        assert mock_conectar.called
        assert mock_detener.called
        # La clave NUNCA debe estar en la salida de consola
        assert clave_secreta not in resultado.output
        assert "127.0.0.1:59052" in resultado.output


def test_cli_servir_sin_modelo(monkeypatch: pytest.MonkeyPatch):
    """Verifica que compushare servir falle si el archivo de modelo no existe."""
    monkeypatch.setenv("FABRIC_CLAVE", "clave_prueba")
    resultado = runner.invoke(app, ["servir", "--modelo", "/ruta/fantasma/modelo.gguf"])
    assert resultado.exit_code == 1
    assert "El archivo de modelo no existe" in resultado.output


def test_cli_servir_mock_no_imprime_clave(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Verifica que compushare servir inicie el maestro y NUNCA imprima la clave secreta."""
    modelo_dummy = tmp_path / "test-model.gguf"
    modelo_dummy.write_bytes(b"GGUF" + b"\x00" * 1024)

    clave_secreta = "clave_privada_maestro_98765"
    monkeypatch.delenv("FABRIC_CLAVE", raising=False)

    with patch.object(NodoMaestro, "iniciar", new_callable=AsyncMock) as mock_iniciar, \
         patch.object(NodoMaestro, "detener") as mock_detener:
        resultado = runner.invoke(
            app,
            [
                "servir",
                "--modelo", str(modelo_dummy),
                "--clave", clave_secreta,
                "--puerto", "59000",
                "--duracion", "0.01",
            ],
        )
        assert resultado.exit_code == 0
        assert mock_iniciar.called
        assert mock_detener.called
        # La clave NUNCA debe estar en la salida de consola
        assert clave_secreta not in resultado.output
        assert "127.0.0.1:59000" in resultado.output


def test_nodo_trabajador_proceso_y_detener():
    """Prueba unitaria de NodoTrabajador comprobando ejecucion y terminacion estricta por PID."""
    mock_proc = MagicMock()
    mock_proc.pid = 99991
    mock_proc.poll.return_value = None

    with patch("compushare.nodo.trabajador.buscar_binario", return_value=Path("/bin/ggml-rpc-server")), \
         patch("subprocess.Popen", return_value=mock_proc) as mock_popen, \
         patch("compushare.nodo.trabajador.esperar_puerto_tcp", return_value=True), \
         patch("compushare.nodo.trabajador.detectar_vram_gb", return_value=0.0):
        trabajador = NodoTrabajador(
            rele_url="ws://127.0.0.1:59095",
            clave="clave_test",
            puerto_rpc=59052,
            threads=8,  # Debe limitarse a 6
        )
        assert trabajador.threads == 6  # Verificacion de regla: max 6 hilos

        proc = trabajador.iniciar_rpc_server()
        assert proc.pid == 99991
        # Comprobar llamada a Popen con host 127.0.0.1
        mock_popen.assert_called_once()
        args = mock_popen.call_args[0][0]
        assert args[1:5] == ["-H", "127.0.0.1", "-p", "59052"]
        assert args[5:7] == ["-t", "6"]

        trabajador.detener()
        mock_proc.terminate.assert_called_once()


def test_nodo_maestro_construir_comando(tmp_path: Path):
    """Verifica la construccion de argumentos de llama-server con y sin RPC."""
    modelo_dummy = tmp_path / "modelo.gguf"
    modelo_dummy.write_bytes(b"GGUF" + b"\x00" * 1024)

    with patch("compushare.nodo.maestro.buscar_binario", return_value=Path("/bin/llama-server")):
        maestro = NodoMaestro(
            modelo=modelo_dummy,
            clave="clave_test",
            puerto_openai=59000,
            threads=10,  # Debe limitarse a 6
            gpus_locales=0,
        )
        assert maestro.threads == 6

        # Sin trabajadores RPC
        cmd_solo = maestro.construir_comando_llama_server([])
        assert "--rpc" not in cmd_solo
        assert "--port" in cmd_solo
        assert "59000" in cmd_solo
        assert "-t" in cmd_solo
        assert "6" in cmd_solo

        # Con trabajadores RPC: debe incluir --rpc y salida_en_el_maestro (-ot CPU para 0 GPU)
        cmd_rpc = maestro.construir_comando_llama_server(["127.0.0.1:59100"])
        assert "--rpc" in cmd_rpc
        assert "127.0.0.1:59100" in cmd_rpc
        assert "-ot" in cmd_rpc
        assert any("token_embd|output" in arg for arg in cmd_rpc)


def test_extraer_modelo_info(tmp_path: Path):
    """Verifica la extraccion de metadatos de modelos conocidos y genericos."""
    m_08b = tmp_path / "Qwen3.5-0.8B-Q8_0.gguf"
    _archivo_disperso(m_08b, 800_000_000)
    info_08b = extraer_modelo_info(m_08b)
    assert info_08b.capas == 24
    assert info_08b.d_modelo == 1024
    assert round(info_08b.pesos_gb, 1) == 0.8

    m_27b = tmp_path / "Qwen3.8-27B-UD-Q4.gguf"
    _archivo_disperso(m_27b, 16_500_000_000)
    info_27b = extraer_modelo_info(m_27b)
    assert info_27b.capas == 64
    assert info_27b.d_modelo == 5120
    assert round(info_27b.pesos_gb, 1) == 16.5


def test_contar_gpus_locales_usa_lo_que_ve_llama_cpp():
    """nvidia-smi puede ver una GPU que llama.cpp (compilado solo CPU) no puede usar: manda --list-devices."""
    from compushare.nodo import maestro as m

    sin_gpu = MagicMock(stdout="Available devices:\n  (none)\n", stderr="")
    con_dos = MagicMock(stdout="Available devices:\n  CUDA0: Tesla T4 (15095 MiB, 14992 MiB free)\n  CUDA1: Tesla T4 (15095 MiB, 14992 MiB free)\n", stderr="")
    with patch.object(m, "buscar_binario", return_value=Path("/bin/llama-server")):
        with patch("subprocess.run", return_value=sin_gpu):
            assert m.contar_gpus_locales() == 0
        with patch("subprocess.run", return_value=con_dos):
            assert m.contar_gpus_locales() == 2
