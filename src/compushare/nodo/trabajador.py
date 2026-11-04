"""Módulo del Nodo Trabajador (Worker) de CompuShare.
Gestiona la conexión WebSocket saliente con el Relé Maestro,
recibe solicitudes de inferencia y transmite streaming de tokens
usando motores locales (llama.cpp, Ollama, vLLM o simulado).
"""

import asyncio
import json
import logging
import platform
import subprocess
import time
from typing import Optional
import aiohttp
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from compushare.protocolo import (
    TipoMensaje,
    MensajeRegistro,
    MensajeRegistroAck,
    MensajeHeartbeat,
    MensajeHeartbeatAck,
    MensajeInferenciaReq,
    MensajeInferenciaChunk,
    MensajeInferenciaFin,
    MensajeInferenciaError,
    serializar_mensaje,
    deserializar_mensaje,
)

logger = logging.getLogger("compushare.trabajador")


def detectar_hardware() -> tuple[str, int]:
    """Detecta GPU y VRAM disponible en el sistema (NVIDIA o CPU)."""
    try:
        resultado = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=3,
        )
        if resultado.returncode == 0 and resultado.stdout.strip():
            lineas = resultado.stdout.strip().split("\n")
            nombres = []
            vram_total = 0
            for linea in lineas:
                partes = linea.split(",")
                if len(partes) >= 2:
                    nombres.append(partes[0].strip())
                    vram_total += int(partes[1].strip())
            gpu_str = " + ".join(nombres) if nombres else "NVIDIA GPU"
            return gpu_str, vram_total
    except Exception:
        pass

    # Fallback a CPU del sistema
    cpu = platform.processor() or "CPU Genérica"
    return f"CPU: {cpu}", 0


class NodoTrabajador:
    def __init__(
        self,
        rele_url: str = "ws://127.0.0.1:8000/ws/worker",
        modelo: str = "qwen2.5-coder",
        backend: str = "simulado",
        backend_url: Optional[str] = None,
        worker_id: Optional[str] = None,
        max_slots: int = 2,
        tokens_per_second: float = 30.0,
    ):
        # Normalizar URL del relé
        if rele_url.startswith("http://"):
            rele_url = "ws://" + rele_url[7:]
        elif rele_url.startswith("https://"):
            rele_url = "wss://" + rele_url[8:]
        if not rele_url.endswith("/ws/worker"):
            rele_url = rele_url.rstrip("/") + "/ws/worker"

        self.rele_url = rele_url
        self.modelo = modelo
        self.backend = backend.lower()
        self.backend_url = backend_url or self._url_por_defecto_backend(self.backend)
        self.worker_id = worker_id or f"worker-{platform.node().lower()}-{int(time.time()) % 10000}"
        self.max_slots = max_slots
        self.tokens_per_second = tokens_per_second

        gpu_desc, vram = detectar_hardware()
        self.gpu_info = gpu_desc
        self.vram_mb = vram

        self._activo = False
        self._tareas_en_curso = 0

    def _url_por_defecto_backend(self, backend: str) -> str:
        if backend in ("llama.cpp", "llamacpp", "vllm"):
            return "http://127.0.0.1:8080"
        elif backend == "ollama":
            return "http://127.0.0.1:11434"
        return "http://127.0.0.1:8080"

    async def conectar(self):
        """Inicia el bucle de conexión y auto-reconexión continua con el Relé Maestro."""
        self._activo = True
        intento = 0

        while self._activo:
            try:
                logger.info(f"Conectando al Relé Maestro en {self.rele_url}...")
                async with connect(
                    self.rele_url,
                    ping_interval=20,
                    ping_timeout=20,
                    max_size=16 * 1024 * 1024,
                ) as ws:
                    intento = 0
                    logger.info("¡Conexión establecida! Enviando saludo de registro...")

                    # 1. Enviar Registro
                    registro = MensajeRegistro(
                        worker_id=self.worker_id,
                        modelo=self.modelo,
                        backend=self.backend,
                        gpu_info=self.gpu_info,
                        vram_mb=self.vram_mb,
                        tokens_per_second=self.tokens_per_second,
                        max_slots=self.max_slots,
                    )
                    await ws.send(serializar_mensaje(registro))

                    # 2. Esperar Ack
                    resp = await ws.recv()
                    ack = deserializar_mensaje(resp)
                    if not isinstance(ack, MensajeRegistroAck) or ack.status != "ok":
                        logger.error(f"Fallo en el registro: {ack}")
                        await asyncio.sleep(5)
                        continue

                    logger.info(f"Registrado exitosamente: {ack.mensaje}")

                    # 3. Iniciar tareas en paralelo: Heartbeat y Escucha de Inferencia
                    heartbeat_task = asyncio.create_task(self._bucle_heartbeat(ws))
                    try:
                        async for raw_msg in ws:
                            try:
                                msg = deserializar_mensaje(raw_msg)
                            except Exception as e:
                                logger.warning(f"Error deserializando mensaje: {e}")
                                continue

                            if isinstance(msg, MensajeInferenciaReq):
                                asyncio.create_task(self._ejecutar_tarea(msg, ws))
                            elif isinstance(msg, MensajeHeartbeatAck):
                                pass
                    finally:
                        heartbeat_task.cancel()

            except (ConnectionClosed, ConnectionRefusedError, OSError) as e:
                if not self._activo:
                    break
                intento += 1
                espera = min(30, 2 ** min(intento, 5))
                logger.warning(f"Desconectado del relé ({e}). Reintentando en {espera}s (intento {intento})...")
                await asyncio.sleep(espera)
            except Exception as e:
                logger.error(f"Error inesperado en worker: {e}", exc_info=True)
                await asyncio.sleep(5)

    async def detener(self):
        """Detiene el worker."""
        self._activo = False

    async def _bucle_heartbeat(self, ws):
        """Envía telemetría periódica al Relé."""
        while self._activo:
            try:
                await asyncio.sleep(15)
                hb = MensajeHeartbeat(
                    worker_id=self.worker_id,
                    slots_ocupados=self._tareas_en_curso,
                )
                await ws.send(serializar_mensaje(hb))
            except Exception:
                break

    async def _ejecutar_tarea(self, req: MensajeInferenciaReq, ws):
        """Ejecuta una solicitud de inferencia según el backend configurado."""
        self._tareas_en_curso += 1
        t_inicio = time.time()
        logger.info(f"[Tarea {req.id_tarea}] Iniciando inferencia para modelo '{req.modelo}' (backend: {self.backend})")

        try:
            if self.backend == "simulado":
                await self._inferir_simulado(req, ws, t_inicio)
            else:
                await self._inferir_openai_compatible(req, ws, t_inicio)
        except Exception as e:
            logger.error(f"[Tarea {req.id_tarea}] Error ejecutando inferencia: {e}", exc_info=True)
            error_msg = MensajeInferenciaError(
                id_tarea=req.id_tarea,
                error=f"Error en worker {self.worker_id}: {str(e)}",
                reintentable=True,
            )
            try:
                await ws.send(serializar_mensaje(error_msg))
            except Exception:
                pass
        finally:
            self._tareas_en_curso = max(0, self._tareas_en_curso - 1)

    async def _inferir_simulado(self, req: MensajeInferenciaReq, ws, t_inicio: float):
        """Generador simulado para pruebas y benchmarking sin necesidad de GPU."""
        ultimo_mensaje = req.params.messages[-1].content if req.params.messages else "Hola"
        
        texto_simulado = (
            f"[CompuShare Swarm: Nodo {self.worker_id} ({self.gpu_info})]\n\n"
            f"He procesado tu mensaje: \"{ultimo_mensaje}\".\n\n"
            f"Esta respuesta fue distribuida a través de la red descentralizada de cómputo CompuShare. "
            f"Los tokens fueron transmitidos en streaming en tiempo real."
        )

        palabras = texto_simulado.split(" ")
        tokens_generados = 0

        for i, palabra in enumerate(palabras):
            delta = palabra + (" " if i < len(palabras) - 1 else "")
            chunk = MensajeInferenciaChunk(
                id_tarea=req.id_tarea,
                delta_texto=delta,
                indice=i,
                finish_reason=None if i < len(palabras) - 1 else "stop",
            )
            await ws.send(serializar_mensaje(chunk))
            tokens_generados += 1
            await asyncio.sleep(0.04)  # ~25 tokens/s

        duracion_ms = (time.time() - t_inicio) * 1000
        fin = MensajeInferenciaFin(
            id_tarea=req.id_tarea,
            prompt_tokens=len(ultimo_mensaje.split()),
            completion_tokens=tokens_generados,
            tiempo_total_ms=duracion_ms,
            finish_reason="stop",
        )
        await ws.send(serializar_mensaje(fin))
        logger.info(f"[Tarea {req.id_tarea}] Completada: {tokens_generados} tokens en {duracion_ms:.1f}ms")

    async def _inferir_openai_compatible(self, req: MensajeInferenciaReq, ws, t_inicio: float):
        """Se conecta a un backend local OpenAI-compatible (llama-server, vLLM o Ollama)."""
        url = f"{self.backend_url.rstrip('/')}/v1/chat/completions"
        payload = {
            "model": req.modelo,
            "messages": [{"role": m.role, "content": m.content} for m in req.params.messages],
            "temperature": req.params.temperature,
            "max_tokens": req.params.max_tokens,
            "stream": True,
        }
        if req.params.stop:
            payload["stop"] = req.params.stop

        async with aiohttp.ClientSession() as session:
            async with session.post(url, json=payload, timeout=aiohttp.ClientTimeout(total=300)) as resp:
                if resp.status != 200:
                    cuerpo_error = await resp.text()
                    raise RuntimeError(f"Backend HTTP {resp.status}: {cuerpo_error}")

                tokens_generados = 0
                idx = 0
                async for linea in resp.content:
                    linea_str = linea.decode("utf-8").strip()
                    if not linea_str:
                        continue
                    if linea_str.startswith("data: "):
                        data_part = linea_str[6:].strip()
                        if data_part == "[DONE]":
                            break
                        try:
                            chunk_json = json.loads(data_part)
                            choices = chunk_json.get("choices", [])
                            if choices:
                                delta = choices[0].get("delta", {}).get("content", "")
                                finish_reason = choices[0].get("finish_reason")
                                if delta or finish_reason:
                                    chunk_msg = MensajeInferenciaChunk(
                                        id_tarea=req.id_tarea,
                                        delta_texto=delta,
                                        indice=idx,
                                        finish_reason=finish_reason,
                                    )
                                    await ws.send(serializar_mensaje(chunk_msg))
                                    idx += 1
                                    if delta:
                                        tokens_generados += 1
                        except Exception as e:
                            logger.warning(f"Error procesando chunk de backend: {e}")

                duracion_ms = (time.time() - t_inicio) * 1000
                fin = MensajeInferenciaFin(
                    id_tarea=req.id_tarea,
                    prompt_tokens=sum(len(m.content.split()) for m in req.params.messages),
                    completion_tokens=tokens_generados,
                    tiempo_total_ms=duracion_ms,
                    finish_reason="stop",
                )
                await ws.send(serializar_mensaje(fin))
                logger.info(f"[Tarea {req.id_tarea}] Completada: {tokens_generados} tokens en {duracion_ms:.1f}ms")
