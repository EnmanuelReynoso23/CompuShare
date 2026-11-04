"""Lo que sabe hacer un nodo. Cada motor recibe los `datos` de la tarea y devuelve
un dict; `generar` además va soltando texto con `avance(texto_nuevo)`.

Los motores pesados se importan solo si el nodo los ofrece: un nodo de prueba
con `eco` no necesita torch ni GPU.
"""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Awaitable, Callable

Avance = Callable[[str], Awaitable[None]]


class Eco:
    """Devuelve lo que recibe. Sirve para probar la red sin modelos."""

    tipo = "eco"
    concurrencia = 8

    async def correr(self, datos: Any, avance: Avance) -> dict[str, Any]:
        return {"eco": datos}


class Generar:
    """Texto con un servidor compatible con OpenAI (llama-server de llama.cpp, vLLM...)."""

    tipo = "generar"

    def __init__(self, url: str, modelo: str = "", concurrencia: int = 2, max_tokens: int = 1024) -> None:
        import httpx

        self.url = url.rstrip("/")
        self.modelo = modelo
        self.concurrencia = concurrencia
        self.max_tokens = max_tokens
        self.http = httpx.AsyncClient(timeout=httpx.Timeout(900, connect=10))

    async def correr(self, datos: Any, avance: Avance) -> dict[str, Any]:
        datos = datos or {}
        mensajes = datos.get("mensajes")
        if not mensajes and datos.get("prompt"):
            mensajes = [{"role": "user", "content": str(datos["prompt"])}]
        if not isinstance(mensajes, list) or not mensajes:
            raise ValueError("generar necesita `mensajes` o `prompt`")
        cuerpo = {
            "model": self.modelo or "local",
            "messages": mensajes,
            "max_tokens": min(int(datos.get("max_tokens") or self.max_tokens), 8192),
            "temperature": float(datos.get("temperatura", 0.3)),
            "stream": True,
            "stream_options": {"include_usage": True},
        }
        # Los Qwen recientes razonan antes de contestar; `pensar: false` lo apaga
        # (respuestas más cortas y rápidas para tareas mecánicas).
        if datos.get("pensar") is False:
            cuerpo["chat_template_kwargs"] = {"enable_thinking": False}
        inicio = time.monotonic()
        primero: float | None = None
        partes: list[str] = []
        pendiente = ""
        ultimo_envio = time.monotonic()
        uso: dict[str, Any] = {}
        async with self.http.stream("POST", f"{self.url}/v1/chat/completions", json=cuerpo) as r:
            r.raise_for_status()
            async for linea in r.aiter_lines():
                if not linea.startswith("data:"):
                    continue
                carga = linea[5:].strip()
                if carga == "[DONE]":
                    break
                trozo = json.loads(carga)
                if trozo.get("usage"):
                    uso = trozo["usage"]
                for opcion in trozo.get("choices") or []:
                    delta = (opcion.get("delta") or {}).get("content") or ""
                    if delta:
                        if primero is None:
                            primero = time.monotonic()
                        partes.append(delta)
                        pendiente += delta
                # Agrupar el avance: un mensaje cada ~0,25 s, no uno por token.
                if pendiente and time.monotonic() - ultimo_envio > 0.25:
                    await avance(pendiente)
                    pendiente = ""
                    ultimo_envio = time.monotonic()
        if pendiente:
            await avance(pendiente)
        total = time.monotonic() - inicio
        tokens = int(uso.get("completion_tokens") or 0)
        generando = total - ((primero or time.monotonic()) - inicio)
        return {
            "texto": "".join(partes),
            "modelo": self.modelo,
            "tokens_entrada": int(uso.get("prompt_tokens") or 0),
            "tokens_salida": tokens,
            "segundos": round(total, 2),
            "primer_token_s": round((primero or time.monotonic()) - inicio, 2),
            "tokens_por_segundo": round(tokens / generando, 1) if tokens and generando > 0 else None,
        }


class Embeber:
    """Vectores con sentence-transformers (en la GPU si hay)."""

    tipo = "embeber"
    concurrencia = 1

    def __init__(self, modelo: str, dispositivo: str | None = None) -> None:
        from sentence_transformers import SentenceTransformer

        self.nombre = modelo
        self.modelo = SentenceTransformer(modelo, device=dispositivo)
        # Qwen3-Embedding acepta 32k tokens: sin tope, un lote de trozos largos llena la GPU que
        # comparte con llama.cpp.
        self.modelo.max_seq_length = min(self.modelo.max_seq_length or 1024, 1024)

    async def correr(self, datos: Any, avance: Avance) -> dict[str, Any]:
        textos = (datos or {}).get("textos")
        if not isinstance(textos, list) or not textos or len(textos) > 512:
            raise ValueError("embeber necesita `textos` (1 a 512)")
        inicio = time.monotonic()
        es_consulta = bool((datos or {}).get("consulta"))

        def calcular() -> list[list[float]]:
            opciones: dict[str, Any] = {"normalize_embeddings": True, "batch_size": 8}
            if es_consulta and "query" in (getattr(self.modelo, "prompts", None) or {}):
                opciones["prompt_name"] = "query"
            v = self.modelo.encode([str(t) for t in textos], **opciones)
            return [[round(float(x), 5) for x in fila] for fila in v]

        vectores = await asyncio.to_thread(calcular)
        return {
            "vectores": vectores,
            "dimensiones": len(vectores[0]),
            "modelo": self.nombre,
            "segundos": round(time.monotonic() - inicio, 3),
        }


class Reordenar:
    """Puntaje de relevancia consulta-documento con un cross-encoder."""

    tipo = "reordenar"
    concurrencia = 1

    def __init__(self, modelo: str, dispositivo: str | None = None) -> None:
        from sentence_transformers import CrossEncoder

        self.nombre = modelo
        self.modelo = CrossEncoder(modelo, device=dispositivo, max_length=1024)

    async def correr(self, datos: Any, avance: Avance) -> dict[str, Any]:
        datos = datos or {}
        consulta = str(datos.get("consulta") or "")
        documentos = datos.get("documentos")
        if not consulta or not isinstance(documentos, list) or not documentos or len(documentos) > 200:
            raise ValueError("reordenar necesita `consulta` y `documentos` (1 a 200)")
        inicio = time.monotonic()

        def calcular() -> list[float]:
            puntajes = self.modelo.predict([(consulta, str(d)) for d in documentos], batch_size=16)
            return [round(float(p), 4) for p in puntajes]

        puntajes = await asyncio.to_thread(calcular)
        return {"puntajes": puntajes, "modelo": self.nombre, "segundos": round(time.monotonic() - inicio, 3)}
