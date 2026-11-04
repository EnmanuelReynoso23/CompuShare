import asyncio
import json
import pytest
import aiohttp
from websockets.asyncio.client import connect
from compushare.rele.servidor import ServidorRele
from compushare.protocolo import (
    TipoMensaje,
    MensajeRegistro,
    MensajeInferenciaReq,
    MensajeInferenciaChunk,
    MensajeInferenciaFin,
    serializar_mensaje,
    deserializar_mensaje,
)


class WorkerSimulado:
    def __init__(self, url: str, worker_id: str, modelo: str):
        self.url = url
        self.worker_id = worker_id
        self.modelo = modelo
        self._ws = None
        self._corriendo = False

    async def conectar(self):
        self._corriendo = True
        self._ws = await connect(self.url)
        # Registro
        reg = MensajeRegistro(
            worker_id=self.worker_id,
            modelo=self.modelo,
            max_slots=2,
            vram_mb=8192,
            tokens_per_second=30.0,
        )
        await self._ws.send(serializar_mensaje(reg))

        try:
            async for crudo in self._ws:
                if not self._corriendo:
                    break
                msg = deserializar_mensaje(crudo)
                if isinstance(msg, MensajeInferenciaReq):
                    # Enviar chunks simulados
                    texto = "CompuShare Swarm: Inferencia distribuida ejecutada con exito."
                    palabras = texto.split(" ")
                    for i, p in enumerate(palabras):
                        chunk = MensajeInferenciaChunk(
                            id_tarea=msg.id_tarea,
                            delta_texto=p + (" " if i < len(palabras) - 1 else ""),
                            indice=i,
                        )
                        await self._ws.send(serializar_mensaje(chunk))
                        await asyncio.sleep(0.01)
                    fin = MensajeInferenciaFin(
                        id_tarea=msg.id_tarea,
                        prompt_tokens=10,
                        completion_tokens=len(palabras),
                        tiempo_total_ms=50.0,
                    )
                    await self._ws.send(serializar_mensaje(fin))
        except Exception:
            pass

    async def detener(self):
        self._corriendo = False
        if self._ws:
            await self._ws.close()


@pytest.mark.asyncio
async def test_end_to_end_inferencia_distribuida():
    puerto_test = 8899
    servidor = ServidorRele(host="127.0.0.1", puerto=puerto_test)
    await servidor.iniciar()

    trabajador = WorkerSimulado(
        url=f"ws://127.0.0.1:{puerto_test}/ws/worker",
        worker_id="test-worker-e2e",
        modelo="qwen2.5-coder-test",
    )
    task_worker = asyncio.create_task(trabajador.conectar())

    # Dar un instante para que el worker conecte y registre
    await asyncio.sleep(0.5)

    base_url = f"http://127.0.0.1:{puerto_test}"

    async with aiohttp.ClientSession() as session:
        # 1. Probar /v1/models
        async with session.get(f"{base_url}/v1/models") as resp:
            assert resp.status == 200
            data_models = await resp.json()
            ids = [m["id"] for m in data_models.get("data", [])]
            assert "qwen2.5-coder-test" in ids

        # 2. Probar inferencia sin streaming (JSON directo)
        payload_sync = {
            "model": "qwen2.5-coder-test",
            "messages": [{"role": "user", "content": "Hola mundo"}],
            "stream": False,
        }
        async with session.post(f"{base_url}/v1/chat/completions", json=payload_sync) as resp:
            assert resp.status == 200
            data_sync = await resp.json()
            assert data_sync["object"] == "chat.completion"
            assert "choices" in data_sync
            assert len(data_sync["choices"]) > 0
            texto = data_sync["choices"][0]["message"]["content"]
            assert len(texto) > 10
            assert "CompuShare Swarm" in texto

        # 3. Probar inferencia con streaming SSE
        payload_stream = {
            "model": "qwen2.5-coder-test",
            "messages": [{"role": "user", "content": "Genera una funcion"}],
            "stream": True,
        }
        async with session.post(f"{base_url}/v1/chat/completions", json=payload_stream) as resp:
            assert resp.status == 200
            assert "text/event-stream" in resp.headers.get("Content-Type", "")

            chunks_recibidos = []
            async for line in resp.content:
                line_str = line.decode("utf-8").strip()
                if line_str.startswith("data: "):
                    data_part = line_str[6:].strip()
                    if data_part == "[DONE]":
                        break
                    parsed = json.loads(data_part)
                    delta = parsed["choices"][0]["delta"].get("content", "")
                    if delta:
                        chunks_recibidos.append(delta)

            assert len(chunks_recibidos) > 3
            texto_completo = "".join(chunks_recibidos)
            assert "CompuShare Swarm" in texto_completo

        # 4. Probar /api/status
        async with session.get(f"{base_url}/api/status") as resp:
            assert resp.status == 200
            status_data = await resp.json()
            assert status_data["nodos_activos"] == 1
            assert status_data["tareas_completadas"] >= 2

    # Limpieza
    await trabajador.detener()
    task_worker.cancel()
    await servidor.detener()
