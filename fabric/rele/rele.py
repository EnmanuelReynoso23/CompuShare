"""Relé de la red: junta nodos (los que tienen GPU) y clientes (los que piden).

No hay base de datos. Todo vive en memoria y se pierde al reiniciar, a propósito:
la lista de nodos se rehace sola en cuanto vuelven a conectarse, y una tarea a
medias se da por perdida (el cliente la reintenta).

Protocolo: mensajes JSON por WebSocket, uno por frame, con el campo `t`.

  cliente/nodo -> relé
    hola       {clave, enjambre, rol: "nodo"|"cliente", id?, nombre?, capacidades?, info?}
    latido     {carga?}
    tarea      {id, tipo, datos, para?}         pedir trabajo a un nodo
    parcial    {id, datos}                      (nodo) avance de una tarea
    resultado  {id, datos}                      (nodo) tarea terminada
    error      {id, mensaje}                    (nodo) tarea fallida
    cancelar   {id}                             (cliente) ya no hace falta
    mensaje    {texto}                          sala de chat del enjambre
    responder  {mensaje_id, respuesta, estado, por, ms?}

  relé -> cliente/nodo
    bienvenido {id, nodos, mensajes}
    nodos      {nodos}                          cada vez que cambia la lista
    tarea      {id, tipo, datos, de}            (a un nodo)
    parcial / resultado / error {id, ...}       (al que pidió, con SU id)
    cancelar   {id}                             (a un nodo)
    mensaje    {mensaje}                        alta o cambio en la sala
    rechazo    {motivo}                         y se cierra la conexión

GET /salud responde un resumen en JSON (para Caddy y para vigilarlo).
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
import re
import secrets
import time
from collections import deque
from dataclasses import dataclass, field
from http import HTTPStatus
from typing import Any

from websockets.asyncio.server import ServerConnection, serve
from websockets.datastructures import Headers
from websockets.exceptions import ConnectionClosed
from websockets.http11 import Request, Response
from websockets.protocol import State

CLAVE = os.environ.get("FABRIC_CLAVE", "")
PUERTO = int(os.environ.get("PUERTO", "8095"))
MAX_CONEXIONES = int(os.environ.get("MAX_CONEXIONES", "300"))
MAX_BYTES = int(os.environ.get("MAX_BYTES", str(4 * 1024 * 1024)))
# Cubeta de fichas por conexión: ráfaga y ritmo sostenido (mensajes por segundo).
RAFAGA = float(os.environ.get("RAFAGA", "120"))
RITMO = float(os.environ.get("RITMO", "40"))
TAREA_VENCE_S = float(os.environ.get("TAREA_VENCE_S", "900"))
LATIDO_VENCE_S = float(os.environ.get("LATIDO_VENCE_S", "60"))
MENSAJES_POR_SALA = 50
TEXTO_MAX = 8000

TIPOS_DE_TAREA = {"eco", "generar", "embeber", "reordenar"}
ENJAMBRE_VALIDO = re.compile(r"^[a-z0-9-]{1,40}$")
ID_VALIDO = re.compile(r"^[A-Za-z0-9_-]{4,48}$")
TUBO_PUERTO_BASE = int(os.environ.get("TUBO_PUERTO_BASE", "50100"))
TUBO_TROZO = 256 * 1024

log = logging.getLogger("rele")


@dataclass
class Par:
    id: str
    ws: ServerConnection
    enjambre: str
    rol: str
    nombre: str = ""
    capacidades: set[str] = field(default_factory=set)
    info: dict[str, Any] = field(default_factory=dict)
    carga: int = 0
    completadas: int = 0
    visto: float = field(default_factory=time.monotonic)
    fichas: float = RAFAGA
    rellenado: float = field(default_factory=time.monotonic)

    def gastar_ficha(self) -> bool:
        ahora = time.monotonic()
        self.fichas = min(RAFAGA, self.fichas + (ahora - self.rellenado) * RITMO)
        self.rellenado = ahora
        if self.fichas < 1:
            return False
        self.fichas -= 1
        return True

    def publico(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "nombre": self.nombre,
            "capacidades": sorted(self.capacidades),
            "info": self.info,
            "carga": self.carga,
            "completadas": self.completadas,
        }


@dataclass
class Oferta:
    nombre: str
    control: Any
    puerto: int
    servidor: Any = None
    abiertos: int = 0
    esperando: dict[str, asyncio.Future] = field(default_factory=dict)
    info: dict = field(default_factory=dict)  # lo que el trabajador dice prestar (p. ej. vram_gb)


@dataclass
class Tarea:
    id: str
    id_cliente: str
    solicitante: str
    nodo: str
    creada: float = field(default_factory=time.monotonic)


class Rele:
    def __init__(self, clave: str) -> None:
        if not clave:
            raise SystemExit("Falta FABRIC_CLAVE: el relé no arranca abierto.")
        self.clave = clave
        self.pares: dict[str, Par] = {}
        self.tareas: dict[str, Tarea] = {}
        self.salas: dict[str, deque[dict[str, Any]]] = {}
        self.inicio = time.time()
        self.tareas_atendidas = 0
        self.ofertas: dict[str, Oferta] = {}
        self.tubo_bytes = 0

    # ── utilidades ──────────────────────────────────────────────────────────

    def nodos(self, enjambre: str) -> list[dict[str, Any]]:
        return [p.publico() for p in self.pares.values() if p.enjambre == enjambre and p.rol == "nodo"]

    async def enviar(self, par: Par, mensaje: dict[str, Any]) -> None:
        try:
            await par.ws.send(json.dumps(mensaje, ensure_ascii=False, separators=(",", ":")))
        except ConnectionClosed:
            pass

    async def difundir(self, enjambre: str, mensaje: dict[str, Any]) -> None:
        texto = json.dumps(mensaje, ensure_ascii=False, separators=(",", ":"))
        destinos = [p.ws for p in self.pares.values() if p.enjambre == enjambre]
        for ws in destinos:
            try:
                await ws.send(texto)
            except ConnectionClosed:
                pass

    async def avisar_nodos(self, enjambre: str) -> None:
        await self.difundir(enjambre, {"t": "nodos", "nodos": self.nodos(enjambre)})

    def elegir_nodo(self, enjambre: str, tipo: str, excluir: str, para: str | None) -> Par | None:
        if para:
            p = self.pares.get(para)
            if p and p.enjambre == enjambre and p.rol == "nodo" and tipo in p.capacidades:
                return p
            return None
        candidatos = [
            p
            for p in self.pares.values()
            if p.enjambre == enjambre and p.rol == "nodo" and tipo in p.capacidades and p.id != excluir
        ]
        if not candidatos:
            return None
        # Menos carga primero; a igual carga, el que menos ha trabajado.
        return min(candidatos, key=lambda p: (p.carga, p.completadas))

    def resumen(self) -> dict[str, Any]:
        enjambres: dict[str, dict[str, int]] = {}
        for p in self.pares.values():
            e = enjambres.setdefault(p.enjambre, {"nodos": 0, "clientes": 0})
            e["nodos" if p.rol == "nodo" else "clientes"] += 1
        return {
            "ok": True,
            "segundos_arriba": int(time.time() - self.inicio),
            "conexiones": len(self.pares),
            "tareas_en_curso": len(self.tareas),
            "tareas_atendidas": self.tareas_atendidas,
            "enjambres": enjambres,
            "tubos": {o.nombre: {"puerto": o.puerto, "abiertos": o.abiertos, "info": o.info}
                      for o in self.ofertas.values()},
            "tubo_bytes": self.tubo_bytes,
        }

    # ── conexión ────────────────────────────────────────────────────────────

    async def atender(self, ws: ServerConnection) -> None:
        if len(self.pares) >= MAX_CONEXIONES:
            await ws.close(1013, "relé lleno")
            return
        try:
            primero = json.loads(await asyncio.wait_for(ws.recv(), timeout=10))
        except (asyncio.TimeoutError, ConnectionClosed, ValueError, TypeError):
            await ws.close(1008, "falta hola")
            return
        if ws.request is not None and ws.request.path.split("?")[0] == "/tubo":
            await self.atender_tubo(ws, primero)
            return
        par = await self.presentar(ws, primero)
        if par is None:
            return
        try:
            async for crudo in ws:
                if not par.gastar_ficha():
                    await self.enviar(par, {"t": "rechazo", "motivo": "demasiados mensajes"})
                    await ws.close(1008, "ritmo")
                    break
                try:
                    mensaje = json.loads(crudo)
                except ValueError:
                    continue
                if isinstance(mensaje, dict):
                    par.visto = time.monotonic()
                    await self.despachar(par, mensaje)
        except ConnectionClosed:
            pass
        finally:
            await self.despedir(par)

    async def presentar(self, ws: ServerConnection, m: Any) -> Par | None:
        async def rechazar(motivo: str) -> None:
            try:
                await ws.send(json.dumps({"t": "rechazo", "motivo": motivo}))
            except ConnectionClosed:
                pass
            await ws.close(1008, motivo)

        if not isinstance(m, dict) or m.get("t") != "hola":
            await rechazar("falta hola")
            return None
        if not hmac.compare_digest(str(m.get("clave", "")).encode(), self.clave.encode()):
            await rechazar("clave incorrecta")
            return None
        enjambre = str(m.get("enjambre", "")).lower()
        if not ENJAMBRE_VALIDO.match(enjambre):
            await rechazar("enjambre inválido")
            return None
        rol = m.get("rol")
        if rol not in ("nodo", "cliente"):
            await rechazar("rol inválido")
            return None
        pedido = str(m.get("id") or "")
        viejo = self.pares.get(pedido)
        # Quien se reconecta (p. ej. el navegador que pasa a donar) conserva su id si la
        # conexión anterior ya se está cerrando; si sigue abierta, el id es de otro.
        reusar = bool(ID_VALIDO.match(pedido)) and (viejo is None or viejo.ws.state is not State.OPEN)
        if reusar and viejo is not None:
            await self.despedir(viejo)
        ident = pedido if reusar else secrets.token_hex(8)
        capacidades = {c for c in (m.get("capacidades") or []) if c in TIPOS_DE_TAREA} if rol == "nodo" else set()
        info = m.get("info") if isinstance(m.get("info"), dict) else {}
        info = {k: v for k, v in list(info.items())[:12] if isinstance(v, (str, int, float, bool))}
        par = Par(
            id=ident,
            ws=ws,
            enjambre=enjambre,
            rol=rol,
            nombre=str(m.get("nombre") or "")[:40],
            capacidades=capacidades,
            info=info,
        )
        self.pares[ident] = par
        log.info("entra %s %s (%s) en %s %s", rol, ident, par.nombre, enjambre, sorted(capacidades))
        await self.enviar(
            par,
            {
                "t": "bienvenido",
                "id": ident,
                "nodos": self.nodos(enjambre),
                "mensajes": list(self.salas.get(enjambre, ())),
            },
        )
        if rol == "nodo":
            await self.avisar_nodos(enjambre)
        return par

    async def despedir(self, par: Par) -> None:
        if self.pares.get(par.id) is not par:
            return
        del self.pares[par.id]
        log.info("sale %s %s", par.rol, par.id)
        for tarea in list(self.tareas.values()):
            if tarea.nodo == par.id:
                self.tareas.pop(tarea.id, None)
                pidio = self.pares.get(tarea.solicitante)
                if pidio:
                    await self.enviar(pidio, {"t": "error", "id": tarea.id_cliente, "mensaje": "nodo_desconectado"})
            elif tarea.solicitante == par.id:
                self.tareas.pop(tarea.id, None)
                nodo = self.pares.get(tarea.nodo)
                if nodo:
                    nodo.carga = max(0, nodo.carga - 1)
                    await self.enviar(nodo, {"t": "cancelar", "id": tarea.id})
        if par.rol == "nodo":
            await self.avisar_nodos(par.enjambre)

    # ── mensajes ────────────────────────────────────────────────────────────

    async def despachar(self, par: Par, m: dict[str, Any]) -> None:
        t = m.get("t")
        if t == "latido":
            carga = m.get("carga")
            if par.rol == "nodo" and isinstance(carga, int) and carga != par.carga:
                par.carga = max(0, carga)
                await self.avisar_nodos(par.enjambre)
        elif t == "tarea":
            await self.pedir_tarea(par, m)
        elif t in ("parcial", "resultado", "error") and par.rol == "nodo":
            await self.devolver(par, t, m)
        elif t == "cancelar":
            tarea = self.tareas.pop(f"{par.id}:{m.get('id')}", None)
            if tarea:
                nodo = self.pares.get(tarea.nodo)
                if nodo:
                    nodo.carga = max(0, nodo.carga - 1)
                    await self.enviar(nodo, {"t": "cancelar", "id": tarea.id})
        elif t == "mensaje":
            await self.nuevo_mensaje(par, m)
        elif t == "responder":
            await self.responder_mensaje(par, m)
        elif t in ("senalar", "p2p_oferta", "p2p_respuesta", "p2p_candidato", "sdp_oferta", "sdp_respuesta"):
            await self.senalar(par, m)

    async def pedir_tarea(self, par: Par, m: dict[str, Any]) -> None:
        id_cliente = str(m.get("id") or "")[:48]
        tipo = m.get("tipo")
        if not id_cliente or tipo not in TIPOS_DE_TAREA:
            await self.enviar(par, {"t": "error", "id": id_cliente, "mensaje": "tarea_invalida"})
            return
        nodo = self.elegir_nodo(par.enjambre, tipo, par.id, m.get("para"))
        if nodo is None:
            await self.enviar(par, {"t": "error", "id": id_cliente, "mensaje": "sin_nodos"})
            return
        tarea = Tarea(id=f"{par.id}:{id_cliente}", id_cliente=id_cliente, solicitante=par.id, nodo=nodo.id)
        self.tareas[tarea.id] = tarea
        nodo.carga += 1
        await self.enviar(nodo, {"t": "tarea", "id": tarea.id, "tipo": tipo, "datos": m.get("datos"), "de": par.id})
        await self.avisar_nodos(par.enjambre)

    async def devolver(self, nodo: Par, t: str, m: dict[str, Any]) -> None:
        tarea = self.tareas.get(str(m.get("id")))
        if tarea is None or tarea.nodo != nodo.id:
            return
        pidio = self.pares.get(tarea.solicitante)
        salida: dict[str, Any] = {"t": t, "id": tarea.id_cliente, "nodo": nodo.id}
        if t == "error":
            salida["mensaje"] = str(m.get("mensaje", ""))[:500]
        else:
            salida["datos"] = m.get("datos")
        if t != "parcial":
            self.tareas.pop(tarea.id, None)
            nodo.carga = max(0, nodo.carga - 1)
            if t == "resultado":
                nodo.completadas += 1
                self.tareas_atendidas += 1
            await self.avisar_nodos(nodo.enjambre)
        if pidio:
            await self.enviar(pidio, salida)

    async def nuevo_mensaje(self, par: Par, m: dict[str, Any]) -> None:
        texto = str(m.get("texto") or "").strip()[:TEXTO_MAX]
        if not texto:
            return
        mensaje = {
            "id": secrets.token_hex(6),
            "creado": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "texto": texto,
            "de": par.id,
            "estado": "pendiente",
        }
        sala = self.salas.setdefault(par.enjambre, deque(maxlen=MENSAJES_POR_SALA))
        sala.append(mensaje)
        await self.difundir(par.enjambre, {"t": "mensaje", "mensaje": mensaje})

    async def responder_mensaje(self, par: Par, m: dict[str, Any]) -> None:
        sala = self.salas.get(par.enjambre, ())
        mensaje = next((x for x in sala if x["id"] == m.get("mensaje_id")), None)
        # Solo quien preguntó cierra su mensaje (es quien recibió la respuesta del nodo).
        if mensaje is None or mensaje["de"] != par.id:
            return
        mensaje["respuesta"] = str(m.get("respuesta") or "")[:TEXTO_MAX]
        mensaje["estado"] = "respondido" if m.get("estado") != "error" else "error"
        mensaje["por"] = str(m.get("por") or "")[:48]
        if isinstance(m.get("ms"), (int, float)):
            mensaje["ms"] = int(m["ms"])
        await self.difundir(par.enjambre, {"t": "mensaje", "mensaje": mensaje})

    async def senalar(self, par: Par, m: dict[str, Any]) -> None:
        para_id = str(m.get("para") or "")
        destino = self.pares.get(para_id)
        if destino is None or destino.enjambre != par.enjambre:
            await self.enviar(par, {"t": "error", "mensaje": "destino_no_encontrado", "para": para_id})
            return
        carga = {k: v for k, v in m.items() if k != "para"}
        carga["de"] = par.id
        await self.enviar(destino, carga)

    # ── limpieza ────────────────────────────────────────────────────────────

    # ── tubos TCP ───────────────────────────────────────────────────────────
    # Para dividir un modelo entre dos cuadernos con llama.cpp RPC: el trabajador "ofrece" su
    # ggml-rpc-server (que solo escucha en su 127.0.0.1) y el relé abre un puerto en el 127.0.0.1 de
    # esta máquina; cada conexión TCP que llega ahí viaja por un WebSocket propio hasta el trabajador.
    # Nada queda abierto a internet: solo entra quien tiene la clave, y el puerto es local.

    async def atender_tubo(self, ws: ServerConnection, m: Any) -> None:
        if not isinstance(m, dict) or not hmac.compare_digest(str(m.get("clave", "")).encode(), self.clave.encode()):
            await ws.close(1008, "clave incorrecta")
            return
        if m.get("t") == "ofrezco":
            info = m.get("info") if isinstance(m.get("info"), dict) else {}
            info = {str(k)[:30]: v for k, v in list(info.items())[:10] if isinstance(v, (int, float, str))}
            await self.ofrecer(ws, str(m.get("nombre") or ""), info)
        elif m.get("t") == "datos":
            esperando = next((o.esperando.pop(str(m.get("id")), None) for o in self.ofertas.values()
                              if str(m.get("id")) in o.esperando), None)
            if esperando is None or esperando.done():
                await ws.close(1008, "tubo desconocido")
                return
            terminado = asyncio.Event()
            esperando.set_result((ws, terminado))
            await terminado.wait()
        else:
            await ws.close(1008, "tubo inválido")

    async def ofrecer(self, ws: ServerConnection, nombre: str, info: dict | None = None) -> None:
        if not ENJAMBRE_VALIDO.match(nombre):
            await ws.close(1008, "nombre inválido")
            return
        vieja = self.ofertas.pop(nombre, None)
        if vieja:
            vieja.servidor.close()
        usados = {o.puerto for o in self.ofertas.values()}
        puerto = next(p for p in range(TUBO_PUERTO_BASE, TUBO_PUERTO_BASE + 100) if p not in usados)
        oferta = Oferta(nombre=nombre, control=ws, puerto=puerto, info=info or {})
        oferta.servidor = await asyncio.start_server(
            lambda lector, escritor: self.abrir_tubo(oferta, lector, escritor), "127.0.0.1", puerto
        )
        self.ofertas[nombre] = oferta
        log.info("tubo %s ofrecido en 127.0.0.1:%s", nombre, puerto)
        try:
            await ws.send(json.dumps({"t": "listo", "puerto": puerto}))
            async for _ in ws:  # solo latidos; la conexión de control vive mientras se ofrezca
                pass
        except ConnectionClosed:
            pass
        finally:
            oferta.servidor.close()
            for futuro in oferta.esperando.values():
                futuro.cancel()
            if self.ofertas.get(nombre) is oferta:
                del self.ofertas[nombre]
            log.info("tubo %s retirado", nombre)

    async def abrir_tubo(self, oferta: Oferta, lector: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        ident = secrets.token_hex(8)
        futuro: asyncio.Future = asyncio.get_running_loop().create_future()
        oferta.esperando[ident] = futuro
        try:
            await oferta.control.send(json.dumps({"t": "abre", "id": ident}))
            ws, terminado = await asyncio.wait_for(futuro, timeout=30)
        except (asyncio.TimeoutError, asyncio.CancelledError, ConnectionClosed):
            oferta.esperando.pop(ident, None)
            escritor.close()
            return
        oferta.abiertos += 1
        try:
            await self.canalizar(ws, lector, escritor)
        finally:
            oferta.abiertos -= 1
            terminado.set()

    async def canalizar(self, ws: Any, lector: asyncio.StreamReader, escritor: asyncio.StreamWriter) -> None:
        if os.environ.get("FABRIC_PERFIL") == "1":
            from fabric.perfil_contador import canalizar_instrumentado
            await canalizar_instrumentado(ws, lector, escritor, TUBO_TROZO, "rele", self)
            return

        async def hacia_ws() -> None:
            while trozo := await lector.read(TUBO_TROZO):
                self.tubo_bytes += len(trozo)
                await ws.send(trozo)

        async def hacia_tcp() -> None:
            async for trozo in ws:
                if isinstance(trozo, bytes):
                    self.tubo_bytes += len(trozo)
                    escritor.write(trozo)
                    await escritor.drain()

        tareas = [asyncio.create_task(hacia_ws()), asyncio.create_task(hacia_tcp())]
        try:
            await asyncio.wait(tareas, return_when=asyncio.FIRST_COMPLETED)
        finally:
            for t in tareas:
                t.cancel()
            await asyncio.gather(*tareas, return_exceptions=True)
            escritor.close()
            await ws.close()

    async def barrer(self) -> None:
        while True:
            await asyncio.sleep(15)
            ahora = time.monotonic()
            for tarea in list(self.tareas.values()):
                if ahora - tarea.creada > TAREA_VENCE_S:
                    self.tareas.pop(tarea.id, None)
                    nodo = self.pares.get(tarea.nodo)
                    if nodo:
                        nodo.carga = max(0, nodo.carga - 1)
                        await self.enviar(nodo, {"t": "cancelar", "id": tarea.id})
                    pidio = self.pares.get(tarea.solicitante)
                    if pidio:
                        await self.enviar(pidio, {"t": "error", "id": tarea.id_cliente, "mensaje": "vencida"})
            for par in list(self.pares.values()):
                if ahora - par.visto > LATIDO_VENCE_S:
                    await par.ws.close(1001, "sin latido")

    def salud(self, conexion: ServerConnection, pedido: Request) -> Response | None:
        ruta = pedido.path.split("?")[0]
        if os.environ.get("FABRIC_PERFIL") == "1" and ruta.startswith("/perfil/"):
            from fabric import perfil_contador
            partes = ruta.strip("/").split("/")
            if len(partes) >= 3 and partes[1] == "fase_iniciar":
                perfil_contador.iniciar_fase_global(partes[2])
                for o in self.ofertas.values():
                    asyncio.create_task(o.control.send(json.dumps({"t": "perfil_fase", "accion": "iniciar", "nombre": partes[2]})))
                cuerpo = json.dumps({"ok": True, "fase_iniciada": partes[2]}).encode()
            elif len(partes) >= 3 and partes[1] == "fase_terminar":
                res = perfil_contador.terminar_fase_global(partes[2])
                for o in self.ofertas.values():
                    asyncio.create_task(o.control.send(json.dumps({"t": "perfil_fase", "accion": "terminar", "nombre": partes[2]})))
                cuerpo = json.dumps({"ok": True, "fase_terminada": partes[2], "deltas": res}).encode()
            else:
                cuerpo = b'{"ok": false, "error": "comando perfil desconocido"}'
            cabeceras = Headers([("Content-Type", "application/json"), ("Content-Length", str(len(cuerpo))),
                                 ("Access-Control-Allow-Origin", "*")])
            return Response(HTTPStatus.OK.value, "OK", cabeceras, cuerpo)

        if ruta != "/salud":
            return None
        cuerpo = json.dumps(self.resumen()).encode()
        # Abierto a cualquier origen: la web (Vercel) lo consulta para saber si este relé sigue vivo.
        cabeceras = Headers([("Content-Type", "application/json"), ("Content-Length", str(len(cuerpo))),
                             ("Access-Control-Allow-Origin", "*")])
        return Response(HTTPStatus.OK.value, "OK", cabeceras, cuerpo)


async def principal(clave: str = CLAVE, puerto: int = PUERTO, listo: asyncio.Event | None = None) -> None:
    rele = Rele(clave)
    async with serve(
        rele.atender,
        "0.0.0.0",
        puerto,
        max_size=MAX_BYTES,
        process_request=rele.salud,
        ping_interval=20,
        ping_timeout=20,
    ):
        log.info("relé escuchando en :%s", puerto)
        if listo:
            listo.set()
        await rele.barrer()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    asyncio.run(principal())
