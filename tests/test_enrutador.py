import pytest
from unittest.mock import MagicMock
from compushare.protocolo import MensajeRegistro, MensajeHeartbeat
from compushare.rele.enrutador import EnrutadorEnjambre


def test_enrutador_registro_y_seleccion():
    enrutador = EnrutadorEnjambre()
    mock_ws1 = MagicMock()
    mock_ws1.closed = False
    mock_ws2 = MagicMock()
    mock_ws2.closed = False

    info1 = MensajeRegistro(
        worker_id="w1",
        modelo="qwen2.5-coder",
        tokens_per_second=30.0,
        max_slots=2,
    )
    info2 = MensajeRegistro(
        worker_id="w2",
        modelo="qwen2.5-coder",
        tokens_per_second=60.0,
        max_slots=2,
    )

    enrutador.registrar(mock_ws1, info1)
    enrutador.registrar(mock_ws2, info2)

    # Ambos tienen 0 tareas, debe seleccionar w2 por tener mayor TPS
    elegido = enrutador.seleccionar_worker("qwen2.5-coder")
    assert elegido is not None
    assert elegido.worker_id == "w2"

    # Si w2 tiene una tarea activa, debe preferir w1 que tiene 0 tareas activas
    elegido.active_tasks = 1
    siguiente = enrutador.seleccionar_worker("qwen2.5-coder")
    assert siguiente.worker_id == "w1"


def test_enrutador_sin_capacidad():
    enrutador = EnrutadorEnjambre()
    mock_ws = MagicMock()
    mock_ws.closed = False

    info = MensajeRegistro(
        worker_id="w1",
        modelo="llama3",
        max_slots=1,
    )
    enrutador.registrar(mock_ws, info)

    # Ocupar el único slot
    worker = enrutador.seleccionar_worker("llama3")
    assert worker is not None
    worker.active_tasks = 1

    # Ahora no debe haber trabajadores disponibles
    assert enrutador.seleccionar_worker("llama3") is None
