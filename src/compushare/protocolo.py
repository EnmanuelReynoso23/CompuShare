"""Protocolo de comunicación y estructuras de datos para CompuShare.
Define los mensajes serializables entre Nodos Trabajadores y el Relé Maestro.
"""

from enum import Enum
from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field
import time
import uuid


class TipoMensaje(str, Enum):
    REGISTRO = "registro"
    REGISTRO_ACK = "registro_ack"
    HEARTBEAT = "heartbeat"
    HEARTBEAT_ACK = "heartbeat_ack"
    INFERENCIA_REQ = "inferencia_req"
    INFERENCIA_CHUNK = "inferencia_chunk"
    INFERENCIA_FIN = "inferencia_fin"
    INFERENCIA_ERROR = "inferencia_error"
    ESTADO_UPDATE = "estado_update"


class RolMensaje(str, Enum):
    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"


class MensajeChat(BaseModel):
    role: str = "user"
    content: str


class ParametrosInferencia(BaseModel):
    messages: List[MensajeChat]
    temperature: float = 0.7
    max_tokens: Optional[int] = 2048
    top_p: float = 0.95
    top_k: int = 40
    stream: bool = True
    stop: Optional[List[str]] = None


# --- Mensajes de Registro ---

class MensajeRegistro(BaseModel):
    tipo: TipoMensaje = TipoMensaje.REGISTRO
    worker_id: str = Field(default_factory=lambda: f"worker-{uuid.uuid4().hex[:8]}")
    modelo: str
    backend: str = "llama.cpp"  # "llama.cpp", "ollama", "simulado", "openai_compatible"
    gpu_info: str = "CPU"
    vram_mb: int = 0
    tokens_per_second: float = 0.0
    max_slots: int = 1
    timestamp: float = Field(default_factory=time.time)


class MensajeRegistroAck(BaseModel):
    tipo: TipoMensaje = TipoMensaje.REGISTRO_ACK
    worker_id: str
    status: str = "ok"  # "ok" o "error"
    mensaje: str = "Registro exitoso en el Relé Maestro"
    timestamp: float = Field(default_factory=time.time)


# --- Heartbeats ---

class MensajeHeartbeat(BaseModel):
    tipo: TipoMensaje = TipoMensaje.HEARTBEAT
    worker_id: str
    cpu_pct: float = 0.0
    ram_pct: float = 0.0
    gpu_pct: float = 0.0
    slots_ocupados: int = 0
    timestamp: float = Field(default_factory=time.time)


class MensajeHeartbeatAck(BaseModel):
    tipo: TipoMensaje = TipoMensaje.HEARTBEAT_ACK
    timestamp: float = Field(default_factory=time.time)


# --- Inferencia ---

class MensajeInferenciaReq(BaseModel):
    tipo: TipoMensaje = TipoMensaje.INFERENCIA_REQ
    id_tarea: str = Field(default_factory=lambda: f"task-{uuid.uuid4().hex[:12]}")
    modelo: str
    params: ParametrosInferencia
    timestamp: float = Field(default_factory=time.time)


class MensajeInferenciaChunk(BaseModel):
    tipo: TipoMensaje = TipoMensaje.INFERENCIA_CHUNK
    id_tarea: str
    delta_texto: str
    indice: int = 0
    finish_reason: Optional[str] = None


class MensajeInferenciaFin(BaseModel):
    tipo: TipoMensaje = TipoMensaje.INFERENCIA_FIN
    id_tarea: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    tiempo_total_ms: float = 0.0
    finish_reason: str = "stop"


class MensajeInferenciaError(BaseModel):
    tipo: TipoMensaje = TipoMensaje.INFERENCIA_ERROR
    id_tarea: str
    error: str
    reintentable: bool = True


# --- Unión de Tipos para deserialización flexible ---
MensajeRed = Union[
    MensajeRegistro,
    MensajeRegistroAck,
    MensajeHeartbeat,
    MensajeHeartbeatAck,
    MensajeInferenciaReq,
    MensajeInferenciaChunk,
    MensajeInferenciaFin,
    MensajeInferenciaError,
]


def serializar_mensaje(msg: BaseModel) -> str:
    """Convierte un modelo Pydantic a cadena JSON."""
    return msg.model_dump_json()


def deserializar_mensaje(data: Union[str, bytes, dict]) -> BaseModel:
    """Parsea una carga JSON al modelo de mensaje correspondiente según 'tipo'."""
    if isinstance(data, (str, bytes)):
        import json
        obj = json.loads(data)
    else:
        obj = data

    tipo = obj.get("tipo")
    mapeo = {
        TipoMensaje.REGISTRO.value: MensajeRegistro,
        TipoMensaje.REGISTRO_ACK.value: MensajeRegistroAck,
        TipoMensaje.HEARTBEAT.value: MensajeHeartbeat,
        TipoMensaje.HEARTBEAT_ACK.value: MensajeHeartbeatAck,
        TipoMensaje.INFERENCIA_REQ.value: MensajeInferenciaReq,
        TipoMensaje.INFERENCIA_CHUNK.value: MensajeInferenciaChunk,
        TipoMensaje.INFERENCIA_FIN.value: MensajeInferenciaFin,
        TipoMensaje.INFERENCIA_ERROR.value: MensajeInferenciaError,
    }

    cls = mapeo.get(tipo)
    if not cls:
        raise ValueError(f"Tipo de mensaje desconocido en protocolo: '{tipo}'")
    return cls.model_validate(obj)
