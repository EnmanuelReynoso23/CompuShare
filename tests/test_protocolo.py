import pytest
from compushare.protocolo import (
    TipoMensaje,
    MensajeRegistro,
    MensajeRegistroAck,
    MensajeHeartbeat,
    MensajeInferenciaReq,
    MensajeInferenciaChunk,
    MensajeInferenciaFin,
    MensajeInferenciaError,
    ParametrosInferencia,
    MensajeChat,
    serializar_mensaje,
    deserializar_mensaje,
)


def test_serializar_deserializar_registro():
    reg = MensajeRegistro(
        worker_id="worker-test-01",
        modelo="qwen2.5-coder-32b",
        backend="llama.cpp",
        gpu_info="NVIDIA RTX 4090",
        vram_mb=24576,
        tokens_per_second=42.5,
        max_slots=4,
    )
    raw = serializar_mensaje(reg)
    obj = deserializar_mensaje(raw)
    assert isinstance(obj, MensajeRegistro)
    assert obj.worker_id == "worker-test-01"
    assert obj.modelo == "qwen2.5-coder-32b"
    assert obj.vram_mb == 24576


def test_serializar_deserializar_inferencia_req():
    req = MensajeInferenciaReq(
        id_tarea="task-abc-123",
        modelo="deepseek-coder",
        params=ParametrosInferencia(
            messages=[MensajeChat(role="user", content="Hola CompuShare")],
            temperature=0.8,
            stream=True,
        ),
    )
    raw = serializar_mensaje(req)
    obj = deserializar_mensaje(raw)
    assert isinstance(obj, MensajeInferenciaReq)
    assert obj.id_tarea == "task-abc-123"
    assert obj.params.messages[0].content == "Hola CompuShare"
    assert obj.params.stream is True


def test_serializar_deserializar_chunks():
    chunk = MensajeInferenciaChunk(
        id_tarea="task-1",
        delta_texto="hola mundo",
        indice=0,
    )
    raw = serializar_mensaje(chunk)
    obj = deserializar_mensaje(raw)
    assert isinstance(obj, MensajeInferenciaChunk)
    assert obj.delta_texto == "hola mundo"

    fin = MensajeInferenciaFin(
        id_tarea="task-1",
        prompt_tokens=5,
        completion_tokens=20,
        tiempo_total_ms=450.2,
        finish_reason="stop",
    )
    raw_fin = serializar_mensaje(fin)
    obj_fin = deserializar_mensaje(raw_fin)
    assert isinstance(obj_fin, MensajeInferenciaFin)
    assert obj_fin.completion_tokens == 20
