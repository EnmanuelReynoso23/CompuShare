"""Un nodo de la red: se conecta al relé, dice qué sabe hacer y atiende tareas.

    python -m fabric.nodo.nodo --rele wss://... --clave ... --enjambre pruebas \\
        --eco --llm-url http://127.0.0.1:8080 --llm-modelo Qwen3.8-27B \\
        --embeber Qwen/Qwen3-Embedding-0.6B --reordenar BAAI/bge-reranker-v2-m3

Si se cae la conexión vuelve a entrar solo (espera creciente, hasta 30 s).
Con --duracion sale limpio después de N segundos (lo usan los cuadernos de Kaggle).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import platform
import time
from typing import Any

from websockets.asyncio.client import ClientConnection, connect
from websockets.exceptions import ConnectionClosed

from fabric.nodo import motores

log = logging.getLogger("nodo")


def info_del_equipo() -> dict[str, Any]:
    info: dict[str, Any] = {"nucleos": os.cpu_count() or 0, "sistema": platform.system()}
    try:
        import torch

        if torch.cuda.is_available():
            n = torch.cuda.device_count()
            info["gpus"] = n
            info["gpu"] = torch.cuda.get_device_name(0)
            info["vram_gb"] = round(sum(torch.cuda.get_device_properties(i).total_memory for i in range(n)) / 1e9, 1)
    except Exception:
        pass
    return info


class Nodo:
    def __init__(self, rele: str, clave: str, enjambre: str, nombre: str, lista: list[Any], info: dict[str, Any]) -> None:
        self.rele = rele
        self.clave = clave
        self.enjambre = enjambre
        self.nombre = nombre
        self.motores = {m.tipo: m for m in lista}
        self.cupos = {m.tipo: asyncio.Semaphore(getattr(m, "concurrencia", 1)) for m in lista}
        self.info = info
        self.id = ""
        self.ws: ClientConnection | None = None
        self.en_curso: dict[str, asyncio.Task] = {}
        self.atendidas = 0
        self.fallidas = 0

    async def enviar(self, mensaje: dict[str, Any]) -> None:
        if self.ws is None:
            return
        try:
            await self.ws.send(json.dumps(mensaje, ensure_ascii=False, separators=(",", ":")))
        except ConnectionClosed:
            pass

    async def latir(self) -> None:
        while True:
            await asyncio.sleep(10)
            await self.enviar({"t": "latido", "carga": len(self.en_curso)})

    async def atender(self, m: dict[str, Any]) -> None:
        ident = m["id"]
        motor = self.motores.get(m.get("tipo"))
        try:
            if motor is None:
                raise ValueError(f"este nodo no hace {m.get('tipo')}")
            async with self.cupos[motor.tipo]:

                async def avance(texto: str) -> None:
                    await self.enviar({"t": "parcial", "id": ident, "datos": {"texto": texto}})

                datos = await motor.correr(m.get("datos"), avance)
            await self.enviar({"t": "resultado", "id": ident, "datos": datos})
            self.atendidas += 1
            log.info("%s %s listo (%s)", m.get("tipo"), ident, _resumen(datos))
        except asyncio.CancelledError:
            log.info("%s cancelada", ident)
        except Exception as e:
            self.fallidas += 1
            log.warning("%s %s falló: %s", m.get("tipo"), ident, e)
            await self.enviar({"t": "error", "id": ident, "mensaje": f"{type(e).__name__}: {e}"[:500]})
        finally:
            self.en_curso.pop(ident, None)

    async def una_sesion(self) -> None:
        async with connect(self.rele, max_size=8 * 1024 * 1024, open_timeout=20) as ws:
            self.ws = ws
            await ws.send(
                json.dumps(
                    {
                        "t": "hola",
                        "clave": self.clave,
                        "enjambre": self.enjambre,
                        "rol": "nodo",
                        "id": self.id or None,
                        "nombre": self.nombre,
                        "capacidades": sorted(self.motores),
                        "info": self.info,
                    }
                )
            )
            hola = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
            if hola.get("t") != "bienvenido":
                raise PermissionError(hola.get("motivo") or "rechazado")
            self.id = hola["id"]
            log.info("dentro del enjambre %s como %s con %s", self.enjambre, self.id, sorted(self.motores))
            latido = asyncio.create_task(self.latir())
            try:
                async for crudo in ws:
                    m = json.loads(crudo)
                    if m.get("t") == "tarea":
                        self.en_curso[m["id"]] = asyncio.create_task(self.atender(m))
                    elif m.get("t") == "cancelar":
                        tarea = self.en_curso.pop(str(m.get("id")), None)
                        if tarea:
                            tarea.cancel()
            finally:
                latido.cancel()
                for tarea in self.en_curso.values():
                    tarea.cancel()
                self.en_curso.clear()
                self.ws = None

    async def vivir(self) -> None:
        espera = 1.0
        while True:
            inicio = time.monotonic()
            try:
                await self.una_sesion()
            except PermissionError as e:
                log.error("el relé no deja entrar: %s", e)
                raise
            except (OSError, ConnectionClosed, asyncio.TimeoutError) as e:
                log.warning("conexión perdida (%s); reintento en %.0f s", e, espera)
            if time.monotonic() - inicio > 60:
                espera = 1.0
            await asyncio.sleep(espera)
            espera = min(espera * 2, 30)


def _resumen(datos: Any) -> str:
    if not isinstance(datos, dict):
        return ""
    claves = ("tokens_salida", "tokens_por_segundo", "dimensiones", "segundos")
    return ", ".join(f"{k}={datos[k]}" for k in claves if k in datos)


def armar_motores(a: argparse.Namespace) -> list[Any]:
    lista: list[Any] = []
    if a.eco:
        lista.append(motores.Eco())
    if a.llm_url:
        lista.append(motores.Generar(a.llm_url, a.llm_modelo, concurrencia=a.llm_concurrencia))
    if a.embeber:
        lista.append(motores.Embeber(a.embeber, a.dispositivo))
    if a.reordenar:
        lista.append(motores.Reordenar(a.reordenar, a.dispositivo))
    if not lista:
        raise SystemExit("El nodo no ofrece nada: usa --eco, --llm-url, --embeber o --reordenar.")
    return lista


async def correr(a: argparse.Namespace) -> Nodo:
    info = info_del_equipo()
    if a.llm_modelo:
        info["modelo"] = a.llm_modelo
    nodo = Nodo(a.rele, a.clave, a.enjambre, a.nombre, armar_motores(a), info)
    vida = asyncio.create_task(nodo.vivir())
    try:
        if a.duracion:
            await asyncio.wait_for(asyncio.shield(vida), timeout=a.duracion)
        else:
            await vida
    except asyncio.TimeoutError:
        log.info("fin de la duración pedida (%s s)", a.duracion)
    finally:
        vida.cancel()
    return nodo


def argumentos(lista: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Nodo de la red de SynRAG")
    p.add_argument("--rele", default=os.environ.get("FABRIC_RELE", "ws://127.0.0.1:8095"))
    p.add_argument("--clave", default=os.environ.get("FABRIC_CLAVE", ""))
    p.add_argument("--enjambre", default=os.environ.get("FABRIC_ENJAMBRE", "pruebas"))
    p.add_argument("--nombre", default=platform.node()[:40])
    p.add_argument("--eco", action="store_true")
    p.add_argument("--llm-url", default="")
    p.add_argument("--llm-modelo", default="")
    p.add_argument("--llm-concurrencia", type=int, default=2)
    p.add_argument("--embeber", default="")
    p.add_argument("--reordenar", default="")
    p.add_argument("--dispositivo", default=None)
    p.add_argument("--duracion", type=float, default=0)
    return p.parse_args(lista)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(correr(argumentos()))
