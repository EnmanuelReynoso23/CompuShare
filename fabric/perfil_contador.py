"""Instrumentación desactivada por defecto para medir rendimiento del tubo y relé.
Se activa únicamente si la variable de entorno FABRIC_PERFIL=1 está presente.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import time
from typing import Any

CANALES_ACTIVOS: list[ContadorCanal] = []


RPC_NOMBRES = {
    0: "ALLOC_BUFFER",
    1: "GET_ALIGNMENT",
    2: "GET_MAX_SIZE",
    3: "BUFFER_GET_BASE",
    4: "FREE_BUFFER",
    5: "BUFFER_CLEAR",
    6: "SET_TENSOR",
    7: "SET_TENSOR_HASH",
    8: "GET_TENSOR",
    9: "COPY_TENSOR",
    10: "GRAPH_COMPUTE",
    11: "GET_DEVICE_MEMORY",
    12: "INIT_TENSOR",
    13: "GET_ALLOC_SIZE",
    14: "HELLO",
    15: "DEVICE_COUNT",
    16: "GRAPH_RECOMPUTE",
    17: "MEMSET_TENSOR",
    18: "SET_TENSOR_2D",
    19: "GET_TENSOR_2D",
    20: "COMM_INIT",
    21: "COMM_ALLREDUCE",
    22: "COMM_FREE",
}


def inspeccionar_socket(sock: Any) -> dict[str, Any]:
    if sock is None or not hasattr(sock, "getsockopt"):
        return {"error": "no_socket"}
    try:
        nodelay = sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY)
        rcvbuf = sock.getsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF)
        sndbuf = sock.getsockopt(socket.SOL_SOCKET, socket.SO_SNDBUF)
        return {
            "tcp_nodelay": nodelay,
            "so_rcvbuf": rcvbuf,
            "so_sndbuf": sndbuf,
        }
    except Exception as e:
        return {"error": str(e)}


class SnapshotFase:
    def __init__(self, c: ContadorCanal) -> None:
        self.tiempo_s = time.perf_counter()
        self.tcp_reads = c.tcp_reads
        self.tcp_read_bytes = c.tcp_read_bytes
        self.tcp_read_wait_s = c.tcp_read_wait_s
        self.ws_sends = c.ws_sends
        self.ws_send_wait_s = c.ws_send_wait_s
        self.ws_recvs = c.ws_recvs
        self.ws_recv_bytes = c.ws_recv_bytes
        self.ws_recv_wait_s = c.ws_recv_wait_s
        self.tcp_writes = c.tcp_writes
        self.tcp_drains = c.tcp_drains
        self.tcp_drain_wait_s = c.tcp_drain_wait_s
        self.tcp_read_sizes_len = len(c.tcp_read_sizes)
        self.ws_recv_sizes_len = len(c.ws_recv_sizes)
        self.rpc_comandos = dict(c.rpc_comandos)


class ContadorCanal:
    def __init__(self, etiqueta: str) -> None:
        self.etiqueta = etiqueta
        self.inicio = time.perf_counter()
        self.fin = 0.0

        # TCP -> WS
        self.tcp_reads = 0
        self.tcp_read_bytes = 0
        self.tcp_read_sizes: list[int] = []
        self.tcp_read_wait_s = 0.0
        self.ws_sends = 0
        self.ws_send_wait_s = 0.0

        # WS -> TCP
        self.ws_recvs = 0
        self.ws_recv_bytes = 0
        self.ws_recv_sizes: list[int] = []
        self.ws_recv_wait_s = 0.0
        self.tcp_writes = 0
        self.tcp_drains = 0
        self.tcp_drain_wait_s = 0.0

        # Tipos de datos
        self.mensajes_texto = 0
        self.mensajes_bytes = 0
        self.rpc_comandos: dict[str, int] = {}
        self._esperando_cmd = True
        self._bytes_restantes_cmd = 0

        # Sockets
        self.socket_tcp_info: dict[str, Any] = {}
        self.socket_ws_info: dict[str, Any] = {}

        # Fases
        self.fases_inicio: dict[str, SnapshotFase] = {}
        self.fases_delta: dict[str, dict[str, Any]] = {}

        CANALES_ACTIVOS.append(self)

    def iniciar_fase(self, nombre: str) -> None:
        self.fases_inicio[nombre] = SnapshotFase(self)

    def terminar_fase(self, nombre: str) -> dict[str, Any] | None:
        ini = self.fases_inicio.get(nombre)
        if ini is None:
            return None
        t_ahora = time.perf_counter()
        dur = t_ahora - ini.tiempo_s

        d_tcp_reads = self.tcp_reads - ini.tcp_reads
        d_tcp_read_bytes = self.tcp_read_bytes - ini.tcp_read_bytes
        d_tcp_read_wait_s = self.tcp_read_wait_s - ini.tcp_read_wait_s
        d_ws_sends = self.ws_sends - ini.ws_sends
        d_ws_send_wait_s = self.ws_send_wait_s - ini.ws_send_wait_s

        d_ws_recvs = self.ws_recvs - ini.ws_recvs
        d_ws_recv_bytes = self.ws_recv_bytes - ini.ws_recv_bytes
        d_ws_recv_wait_s = self.ws_recv_wait_s - ini.ws_recv_wait_s
        d_tcp_writes = self.tcp_writes - ini.tcp_writes
        d_tcp_drains = self.tcp_drains - ini.tcp_drains
        d_tcp_drain_wait_s = self.tcp_drain_wait_s - ini.tcp_drain_wait_s

        tcp_sizes = self.tcp_read_sizes[ini.tcp_read_sizes_len :]
        ws_sizes = self.ws_recv_sizes[ini.ws_recv_sizes_len :]
        
        d_rpc_cmds = {}
        for k, v in self.rpc_comandos.items():
            diff = v - ini.rpc_comandos.get(k, 0)
            if diff > 0:
                d_rpc_cmds[k] = diff

        def stats_lista(nums: list[int]) -> dict[str, Any]:
            if not nums:
                return {"count": 0, "sum": 0, "min": 0, "max": 0, "avg": 0, "p50": 0, "p95": 0}
            s = sorted(nums)
            n = len(s)
            return {
                "count": n,
                "sum": sum(s),
                "min": s[0],
                "max": s[-1],
                "avg": round(sum(s) / n, 1),
                "p50": s[n // 2],
                "p95": s[int(n * 0.95)],
            }

        res = {
            "nombre": nombre,
            "duracion_s": round(dur, 4),
            "tcp_hacia_ws": {
                "lecturas_tcp": d_tcp_reads,
                "bytes_leidos": d_tcp_read_bytes,
                "tiempo_espera_lectura_s": round(d_tcp_read_wait_s, 4),
                "envios_ws": d_ws_sends,
                "tiempo_espera_envio_ws_s": round(d_ws_send_wait_s, 4),
                "distribucion_tamanos": stats_lista(tcp_sizes),
            },
            "ws_hacia_tcp": {
                "recepciones_ws": d_ws_recvs,
                "bytes_recibidos": d_ws_recv_bytes,
                "tiempo_espera_recepcion_ws_s": round(d_ws_recv_wait_s, 4),
                "escrituras_tcp": d_tcp_writes,
                "drains_tcp": d_tcp_drains,
                "tiempo_espera_drain_s": round(d_tcp_drain_wait_s, 4),
                "distribucion_tamanos": stats_lista(ws_sizes),
            },
            "rpc_comandos": d_rpc_cmds,
        }
        self.fases_delta[nombre] = res
        return res

    def resumen(self) -> dict[str, Any]:
        dur = (self.fin or time.perf_counter()) - self.inicio

        def stats_lista(nums: list[int]) -> dict[str, Any]:
            if not nums:
                return {"count": 0, "sum": 0, "min": 0, "max": 0, "avg": 0, "p50": 0, "p95": 0}
            s = sorted(nums)
            n = len(s)
            return {
                "count": n,
                "sum": sum(s),
                "min": s[0],
                "max": s[-1],
                "avg": round(sum(s) / n, 1),
                "p50": s[n // 2],
                "p95": s[int(n * 0.95)],
            }

        return {
            "etiqueta": self.etiqueta,
            "duracion_total_s": round(dur, 4),
            "sockets": {
                "tcp": self.socket_tcp_info,
                "ws": self.socket_ws_info,
            },
            "fases": self.fases_delta,
            "tcp_hacia_ws": {
                "lecturas_tcp": self.tcp_reads,
                "bytes_leidos": self.tcp_read_bytes,
                "tiempo_espera_lectura_s": round(self.tcp_read_wait_s, 4),
                "envios_ws": self.ws_sends,
                "tiempo_espera_envio_ws_s": round(self.ws_send_wait_s, 4),
                "distribucion_tamanos": stats_lista(self.tcp_read_sizes),
            },
            "ws_hacia_tcp": {
                "recepciones_ws": self.ws_recvs,
                "bytes_recibidos": self.ws_recv_bytes,
                "tiempo_espera_recepcion_ws_s": round(self.ws_recv_wait_s, 4),
                "escrituras_tcp": self.tcp_writes,
                "drains_tcp": self.tcp_drains,
                "tiempo_espera_drain_s": round(self.tcp_drain_wait_s, 4),
                "distribucion_tamanos": stats_lista(self.ws_recv_sizes),
            },
            "formato": {
                "mensajes_bytes": self.mensajes_bytes,
                "mensajes_texto": self.mensajes_texto,
            },
        }

    def guardar(self, ruta: str | None = None) -> None:
        self.fin = time.perf_counter()
        if not ruta:
            ruta = f"/tmp/fabric_perfil_{self.etiqueta}_{os.getpid()}_{int(time.time()*1000)}.json"
        try:
            with open(ruta, "w", encoding="utf-8") as f:
                json.dump(self.resumen(), f, indent=2, ensure_ascii=False)
        except Exception:
            pass


def iniciar_fase_global(nombre: str) -> None:
    for c in CANALES_ACTIVOS:
        c.iniciar_fase(nombre)


def terminar_fase_global(nombre: str) -> list[dict[str, Any]]:
    res = []
    for c in CANALES_ACTIVOS:
        r = c.terminar_fase(nombre)
        if r:
            res.append(dict(r, etiqueta=c.etiqueta))
    return res


async def canalizar_instrumentado(
    ws: Any, lector: asyncio.StreamReader, escritor: asyncio.StreamWriter,
    tam_trozo: int, etiqueta: str, cuenta: Any = None
) -> None:
    contador = ContadorCanal(etiqueta)

    sock_tcp = escritor.get_extra_info("socket")
    contador.socket_tcp_info = inspeccionar_socket(sock_tcp)
    if hasattr(ws, "transport"):
        sock_ws = ws.transport.get_extra_info("socket")
        contador.socket_ws_info = inspeccionar_socket(sock_ws)

    async def hacia_ws() -> None:
        while True:
            t0 = time.perf_counter()
            trozo = await lector.read(tam_trozo)
            contador.tcp_read_wait_s += time.perf_counter() - t0
            if not trozo:
                break
            n = len(trozo)
            contador.tcp_reads += 1
            contador.tcp_read_bytes += n
            contador.tcp_read_sizes.append(n)
            if cuenta is not None:
                if hasattr(cuenta, "bytes"):
                    cuenta.bytes += n
                elif hasattr(cuenta, "tubo_bytes"):
                    cuenta.tubo_bytes += n

            # Decodificación de comandos RPC en la subida (llama-server -> rele)
            if contador._esperando_cmd and len(trozo) >= 9:
                cmd_id = trozo[0]
                if cmd_id in RPC_NOMBRES:
                    cname = RPC_NOMBRES[cmd_id]
                    contador.rpc_comandos[cname] = contador.rpc_comandos.get(cname, 0) + 1
                    try:
                        import struct
                        (tam_req,) = struct.unpack("<Q", trozo[1:9])
                        tot = 9 + tam_req
                        contador._bytes_restantes_cmd = max(0, tot - len(trozo))
                        contador._esperando_cmd = (contador._bytes_restantes_cmd == 0)
                    except Exception:
                        contador._esperando_cmd = True
            elif not contador._esperando_cmd:
                contador._bytes_restantes_cmd = max(0, contador._bytes_restantes_cmd - len(trozo))
                if contador._bytes_restantes_cmd == 0:
                    contador._esperando_cmd = True

            t1 = time.perf_counter()
            await ws.send(trozo)
            contador.ws_send_wait_s += time.perf_counter() - t1
            contador.ws_sends += 1

    async def hacia_tcp() -> None:
        iterador = ws.__aiter__()
        while True:
            try:
                t0 = time.perf_counter()
                trozo = await iterador.__anext__()
                contador.ws_recv_wait_s += time.perf_counter() - t0
            except StopAsyncIteration:
                break
            except Exception:
                break

            if isinstance(trozo, bytes):
                contador.mensajes_bytes += 1
                n = len(trozo)
                contador.ws_recvs += 1
                contador.ws_recv_bytes += n
                contador.ws_recv_sizes.append(n)
                if cuenta is not None:
                    if hasattr(cuenta, "bytes"):
                        cuenta.bytes += n
                    elif hasattr(cuenta, "tubo_bytes"):
                        cuenta.tubo_bytes += n

                escritor.write(trozo)
                contador.tcp_writes += 1

                t1 = time.perf_counter()
                await escritor.drain()
                contador.tcp_drain_wait_s += time.perf_counter() - t1
                contador.tcp_drains += 1
            elif isinstance(trozo, str):
                contador.mensajes_texto += 1

    tareas = [asyncio.create_task(hacia_ws()), asyncio.create_task(hacia_tcp())]
    try:
        await asyncio.wait(tareas, return_when=asyncio.FIRST_COMPLETED)
    finally:
        for t in tareas:
            t.cancel()
        await asyncio.gather(*tareas, return_exceptions=True)
        contador.guardar()
