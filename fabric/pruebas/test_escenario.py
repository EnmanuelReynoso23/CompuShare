"""Pruebas del simulador de escenarios distribuidos de CompuShare (fabric/simulador/escenario.py)."""

from pathlib import Path
import pytest

from fabric.simulador.escenario import (
    Escenario,
    MetricasEnlace,
    ResultadoEscenario,
    medir_banda_memoria_llama_bench,
    puerto_libre,
    RPC_SERVER_BIN,
    LLAMA_SERVER_BIN,
    MODELO_DEFECTO,
)


def test_puerto_libre_en_rango_a2():
    """El asignador de puertos debe devolver siempre puertos dentro del rango asignado a A2 (51000-51999)."""
    puertos = [puerto_libre() for _ in range(20)]
    for p in puertos:
        assert 51000 <= p <= 51999
    # Todos deben ser unicos en la tanda
    assert len(set(puertos)) == len(puertos)


def test_escenario_inicializacion():
    """Verifica que el objeto Escenario configure adecuadamente sus parametros e hilos."""
    esc_2 = Escenario(n=2, perfil="sin retraso")
    assert esc_2.n == 2
    assert esc_2.hilos_rpc == 2
    assert 51000 <= esc_2.puerto_servidor <= 51999

    esc_6 = Escenario(n=6, perfil="mismo país")
    assert esc_6.n == 6
    assert esc_6.hilos_rpc == 1
    assert 51000 <= esc_6.puerto_servidor <= 51999


def test_metricas_enlace_dataclass():
    """Verifica la estructura y tipos de MetricasEnlace."""
    m = MetricasEnlace(
        indice=1,
        puerto_rpc=51010,
        puerto_proxy=51011,
        bytes_ida=1000,
        bytes_vuelta=5000,
        bytes_ida_por_token=100.0,
        bytes_vuelta_por_token=500.0,
        bytes_totales_por_token=600.0,
    )
    assert m.indice == 1
    assert m.puerto_rpc == 51010
    assert m.bytes_totales_por_token == 600.0


def test_escenario_ejecucion_corta():
    """Prueba corta de integracion con N=2 nodos en 'sin retraso' (se salta si no hay binarios o modelo)."""
    if not (Path(RPC_SERVER_BIN).exists() and Path(LLAMA_SERVER_BIN).exists() and Path(MODELO_DEFECTO).exists()):
        pytest.skip("llama.cpp o modelo Qwen3.5-0.8B no disponibles en esta maquina")

    async def caso():
        esc = Escenario(
            n=2,
            perfil="sin retraso",
            modelo_path=MODELO_DEFECTO,
            rpc_server_bin=RPC_SERVER_BIN,
            llama_server_bin=LLAMA_SERVER_BIN,
            hilos_rpc=1,
            hilos_servidor=2,
        )

        try:
            await esc.iniciar(timeout_listo=60.0)
            res = await esc.medir_escenario(
                prompt="Hola",
                n_predict=4,
                repeticiones=1,
                banda_gbs_medida=24.0,
            )
            assert res.n_nodos == 2
            assert res.perfil == "sin retraso"
            assert res.tokens_generados == 4
            assert res.ms_por_palabra_mediana > 0.0
            assert len(res.enlaces) == 2
            assert res.bytes_total_por_token > 0.0
            assert res.calculo_ms_total > 0.0
        finally:
            await esc.detener()

    import asyncio
    asyncio.run(caso())
