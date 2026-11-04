"""Servidor HTTP y WebSocket del Relé Maestro de CompuShare.
Expone la API compatible con OpenAI (/v1/chat/completions, /v1/models)
y orquesta los workers conectados vía WebSockets.
"""

import asyncio
import json
import logging
import time
import uuid
from typing import Dict, Optional
from aiohttp import web, WSMsgType

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
    ParametrosInferencia,
    MensajeChat,
    serializar_mensaje,
    deserializar_mensaje,
)
from compushare.rele.enrutador import EnrutadorEnjambre, EstadoTrabajador

logger = logging.getLogger("compushare.rele")


class ServidorRele:
    def __init__(self, host: str = "0.0.0.0", puerto: int = 8000, auth_token: Optional[str] = None):
        self.host = host
        self.puerto = puerto
        self.auth_token = auth_token
        self.enrutador = EnrutadorEnjambre()
        self.app = web.Application()
        self._colas_tareas: Dict[str, asyncio.Queue] = {}
        self._runner: Optional[web.AppRunner] = None
        self._configurar_rutas()

    def _configurar_rutas(self):
        self.app.router.add_get("/", self._handle_dashboard)
        self.app.router.add_get("/health", self._handle_health)
        self.app.router.add_get("/api/status", self._handle_status)
        self.app.router.add_get("/v1/models", self._handle_v1_models)
        self.app.router.add_post("/v1/chat/completions", self._handle_v1_chat_completions)
        self.app.router.add_get("/ws/worker", self._handle_ws_worker)

    async def _handle_dashboard(self, request: web.Request) -> web.Response:
        """Dashboard HTML ligero para monitorizar el estado del enjambre."""
        resumen = self.enrutador.resumen()
        html = f"""<!DOCTYPE html>
<html lang="es">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>CompuShare - La Computadora Compartida</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; background: #0f172a; color: #f8fafc; margin: 0; padding: 2rem; }}
        .container {{ max-width: 900px; margin: 0 auto; }}
        h1 {{ color: #38bdf8; margin-bottom: 0.2rem; }}
        p.subtitle {{ color: #94a3b8; font-style: italic; margin-top: 0; }}
        .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 1rem; margin: 2rem 0; }}
        .card {{ background: #1e293b; border-radius: 8px; padding: 1.5rem; border: 1px solid #334155; }}
        .card .num {{ font-size: 2.2rem; font-weight: bold; color: #38bdf8; }}
        .card .lbl {{ color: #94a3b8; font-size: 0.85rem; text-transform: uppercase; letter-spacing: 0.05em; }}
        table {{ width: 100%; border-collapse: collapse; margin-top: 1rem; background: #1e293b; border-radius: 8px; overflow: hidden; }}
        th, td {{ padding: 12px; text-align: left; border-bottom: 1px solid #334155; }}
        th {{ background: #0f172a; color: #38bdf8; font-size: 0.85rem; text-transform: uppercase; }}
        .tag {{ background: #0284c7; color: white; padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; }}
        .status-dot {{ display: inline-block; width: 10px; height: 10px; border-radius: 50%; background: #22c55e; margin-right: 6px; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>⚡ CompuShare</h1>
        <p class="subtitle">La Computadora Compartida - Red Distribuida de Cómputo e Inferencia de IA</p>
        
        <div class="grid">
            <div class="card">
                <div class="num"><span class="status-dot"></span>{resumen['nodos_activos']}</div>
                <div class="lbl">Nodos Activos</div>
            </div>
            <div class="card">
                <div class="num">{resumen['slots_totales']}</div>
                <div class="lbl">Slots de Cómputo</div>
            </div>
            <div class="card">
                <div class="num">{resumen['tareas_completadas']}</div>
                <div class="lbl">Tareas Completadas</div>
            </div>
            <div class="card">
                <div class="num">{resumen['tokens_generados']}</div>
                <div class="lbl">Tokens Generados</div>
            </div>
        </div>

        <h2>Malla de Nodos Trabajadores (Swarm)</h2>
        <table>
            <thead>
                <tr>
                    <th>Worker ID</th>
                    <th>Modelo</th>
                    <th>Backend</th>
                    <th>GPU / VRAM</th>
                    <th>TPS</th>
                    <th>Activas</th>
                </tr>
            </thead>
            <tbody>
                {''.join(f"<tr><td><b>{w['id']}</b></td><td><span class='tag'>{w['modelo']}</span></td><td>{w['backend']}</td><td>{w['gpu']} ({w['vram_mb']}MB)</td><td>{w['tps']} t/s</td><td>{w['tareas_activas']}</td></tr>" for w in resumen['workers']) if resumen['workers'] else "<tr><td colspan='6' style='text-align: center; color: #94a3b8;'>No hay workers conectados actualmente. Inicia uno con <code>compushare unirse</code>.</td></tr>"}
            </tbody>
        </table>

        <p style="margin-top: 2rem; color: #64748b; font-size: 0.85rem;">Endpoint OpenAI: <code>http://{self.host}:{self.puerto}/v1/chat/completions</code></p>
    </div>
</body>
</html>"""
        return web.Response(text=html, content_type="text/html")

    async def _handle_health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "healthy", "service": "compushare-relay"})

    async def _handle_status(self, request: web.Request) -> web.Response:
        return web.json_response(self.enrutador.resumen())

    async def _handle_v1_models(self, request: web.Request) -> web.Response:
        """Endpoint estándar OpenAI: /v1/models"""
        modelos = self.enrutador.listar_modelos()
        ahora = int(time.time())
        data = [
            {
                "id": m,
                "object": "model",
                "created": ahora,
                "owned_by": "compushare-swarm",
                "permission": [],
                "root": m,
                "parent": None,
            }
            for m in modelos
        ]
        return web.json_response({"object": "list", "data": data})

    async def _handle_v1_chat_completions(self, request: web.Request) -> web.StreamResponse:
        """Endpoint estándar OpenAI: /v1/chat/completions con soporte de Streaming SSE y llamada estándar."""
        try:
            body = await request.json()
        except Exception:
            return web.json_response({"error": {"message": "JSON inválido en el cuerpo de la solicitud"}}, status=400)

        modelo_solicitado = body.get("model", "default")
        mensajes_raw = body.get("messages", [])
        if not mensajes_raw:
            return web.json_response({"error": {"message": "El campo 'messages' es obligatorio"}}, status=400)

        stream = bool(body.get("stream", False))
        temperature = float(body.get("temperature", 0.7))
        max_tokens = body.get("max_tokens", 2048)
        stop = body.get("stop")

        mensajes = [MensajeChat(role=m.get("role", "user"), content=m.get("content", "")) for m in mensajes_raw]
        params = ParametrosInferencia(
            messages=mensajes,
            temperature=temperature,
            max_tokens=max_tokens,
            stream=stream,
            stop=stop if isinstance(stop, list) else ([stop] if stop else None),
        )

        # Seleccionar trabajador óptimo
        worker = self.enrutador.seleccionar_worker(modelo_solicitado)
        if not worker:
            return web.json_response(
                {
                    "error": {
                        "message": f"No hay trabajadores disponibles en el enjambre CompuShare para el modelo '{modelo_solicitado}'",
                        "type": "compushare_unavailable_error",
                        "code": 503,
                    }
                },
                status=503,
            )

        task_id = f"chatcmpl-{uuid.uuid4().hex[:16]}"
        cola_tarea = asyncio.Queue()
        self._colas_tareas[task_id] = cola_tarea
        worker.active_tasks += 1

        req_msg = MensajeInferenciaReq(
            id_tarea=task_id,
            modelo=worker.info.modelo,
            params=params,
        )

        try:
            await worker.ws.send_str(serializar_mensaje(req_msg))
        except Exception as e:
            worker.active_tasks = max(0, worker.active_tasks - 1)
            self._colas_tareas.pop(task_id, None)
            return web.json_response(
                {"error": {"message": f"Fallo al enviar tarea al trabajador {worker.worker_id}: {str(e)}"}},
                status=502,
            )

        creado = int(time.time())

        # Modo Streaming SSE
        if stream:
            response = web.StreamResponse(
                status=200,
                reason="OK",
                headers={
                    "Content-Type": "text/event-stream",
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                    "Access-Control-Allow-Origin": "*",
                },
            )
            await response.prepare(request)

            try:
                while True:
                    msg = await cola_tarea.get()

                    if isinstance(msg, MensajeInferenciaChunk):
                        chunk_payload = {
                            "id": task_id,
                            "object": "chat.completion.chunk",
                            "created": creado,
                            "model": worker.info.modelo,
                            "choices": [
                                {
                                    "index": msg.indice,
                                    "delta": {"content": msg.delta_texto} if msg.delta_texto else {},
                                    "finish_reason": msg.finish_reason,
                                }
                            ],
                        }
                        await response.write(f"data: {json.dumps(chunk_payload, ensure_ascii=False)}\n\n".encode("utf-8"))

                    elif isinstance(msg, MensajeInferenciaFin):
                        final_chunk = {
                            "id": task_id,
                            "object": "chat.completion.chunk",
                            "created": creado,
                            "model": worker.info.modelo,
                            "choices": [
                                {
                                    "index": 0,
                                    "delta": {},
                                    "finish_reason": msg.finish_reason or "stop",
                                }
                            ],
                            "usage": {
                                "prompt_tokens": msg.prompt_tokens,
                                "completion_tokens": msg.completion_tokens,
                                "total_tokens": msg.prompt_tokens + msg.completion_tokens,
                            },
                        }
                        await response.write(f"data: {json.dumps(final_chunk, ensure_ascii=False)}\n\n".encode("utf-8"))
                        await response.write(b"data: [DONE]\n\n")
                        worker.total_tasks_completed += 1
                        worker.total_tokens_generated += msg.completion_tokens
                        break

                    elif isinstance(msg, MensajeInferenciaError):
                        err_chunk = {"error": {"message": msg.error, "type": "worker_error"}}
                        await response.write(f"data: {json.dumps(err_chunk)}\n\n".encode("utf-8"))
                        await response.write(b"data: [DONE]\n\n")
                        break

            finally:
                worker.active_tasks = max(0, worker.active_tasks - 1)
                self._colas_tareas.pop(task_id, None)

            return response

        # Modo Síncrono / JSON Completo
        else:
            buffer_texto = []
            resultado_fin: Optional[MensajeInferenciaFin] = None
            try:
                while True:
                    msg = await cola_tarea.get()
                    if isinstance(msg, MensajeInferenciaChunk):
                        buffer_texto.append(msg.delta_texto)
                    elif isinstance(msg, MensajeInferenciaFin):
                        resultado_fin = msg
                        worker.total_tasks_completed += 1
                        worker.total_tokens_generated += msg.completion_tokens
                        break
                    elif isinstance(msg, MensajeInferenciaError):
                        return web.json_response(
                            {"error": {"message": msg.error, "type": "worker_error"}},
                            status=500,
                        )
            finally:
                worker.active_tasks = max(0, worker.active_tasks - 1)
                self._colas_tareas.pop(task_id, None)

            contenido_total = "".join(buffer_texto)
            p_tokens = resultado_fin.prompt_tokens if resultado_fin else 0
            c_tokens = resultado_fin.completion_tokens if resultado_fin else len(contenido_total.split())

            return web.json_response(
                {
                    "id": task_id,
                    "object": "chat.completion",
                    "created": creado,
                    "model": worker.info.modelo,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": contenido_total},
                            "finish_reason": resultado_fin.finish_reason if resultado_fin else "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": p_tokens,
                        "completion_tokens": c_tokens,
                        "total_tokens": p_tokens + c_tokens,
                    },
                }
            )

    async def _handle_ws_worker(self, request: web.Request) -> web.WebSocketResponse:
        """Endpoint de conexión WebSocket para Nodos Trabajadores."""
        ws = web.WebSocketResponse(heartbeat=30.0, max_msg_size=16 * 1024 * 1024)
        await ws.prepare(request)

        worker_actual: Optional[EstadoTrabajador] = None

        try:
            async for msg in ws:
                if msg.type == WSMsgType.TEXT:
                    try:
                        objeto = deserializar_mensaje(msg.data)
                    except Exception as e:
                        logger.warning(f"Error parseando mensaje de worker: {e}")
                        continue

                    # Handshake de Registro
                    if isinstance(objeto, MensajeRegistro):
                        worker_actual = self.enrutador.registrar(ws, objeto)
                        ack = MensajeRegistroAck(
                            worker_id=objeto.worker_id,
                            status="ok",
                            mensaje=f"Bienvenido a CompuShare. Registrado como worker para '{objeto.modelo}'",
                        )
                        await ws.send_str(serializar_mensaje(ack))

                    # Heartbeat
                    elif isinstance(objeto, MensajeHeartbeat):
                        self.enrutador.actualizar_heartbeat(objeto)
                        ack = MensajeHeartbeatAck()
                        await ws.send_str(serializar_mensaje(ack))

                    # Chunks de Inferencia
                    elif isinstance(objeto, (MensajeInferenciaChunk, MensajeInferenciaFin, MensajeInferenciaError)):
                        cola = self._colas_tareas.get(objeto.id_tarea)
                        if cola:
                            await cola.put(objeto)

                elif msg.type in (WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR):
                    break

        finally:
            if worker_actual:
                self.enrutador.desregistrar(worker_actual.worker_id)
                # Notificar error a cualquier tarea que estuviese pendiente en este worker
                for tid, cola in list(self._colas_tareas.items()):
                    await cola.put(
                        MensajeInferenciaError(
                            id_tarea=tid,
                            error=f"El trabajador {worker_actual.worker_id} se desconectó inesperadamente.",
                            reintentable=True,
                        )
                    )

        return ws

    async def iniciar(self):
        """Arranca el servidor HTTP y WebSocket de forma asíncrona."""
        self._runner = web.AppRunner(self.app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, self.host, self.puerto)
        await site.start()
        logger.info(f"Relé Maestro CompuShare iniciado en http://{self.host}:{self.puerto}")

    async def detener(self):
        """Detiene el servidor y limpia conexiones."""
        if self._runner:
            await self._runner.cleanup()
            logger.info("Relé Maestro CompuShare detenido correctamente.")
