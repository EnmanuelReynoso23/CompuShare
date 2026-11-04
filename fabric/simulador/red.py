"""Proxy TCP en espacio de usuario (asyncio) para simular enlaces de red.

Simula latencia de ida, variacion (jitter con distribucion normal recortada en 0),
ancho de banda (con cubeta de fichas por sentido) y perdida de paquetes
(modelada como retraso de retransmision de 1 RTO, preservando el orden de bytes).
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import random
import signal
import sys
from dataclasses import dataclass, field
from typing import Any

log = logging.getLogger("simulador.red")


@dataclass(frozen=True)
class Perfil:
    """Perfil predefinido de enlace de red segun PRUEBA-10.md."""

    nombre: str
    latencia_ms: float
    variacion_ms: float
    mbps: float | None
    perdida: float = 0.0
    rto_ms: float | None = None


# Perfiles listos con los valores de la tabla de fabric/PRUEBA-10.md
PERFILES: dict[str, Perfil] = {
    "misma ciudad": Perfil(
        nombre="misma ciudad",
        latencia_ms=10.0,
        variacion_ms=2.0,
        mbps=100.0,
        perdida=0.0,
    ),
    "mismo país": Perfil(
        nombre="mismo país",
        latencia_ms=40.0,
        variacion_ms=10.0,
        mbps=50.0,
        perdida=0.0,
    ),
    "otro continente": Perfil(
        nombre="otro continente",
        latencia_ms=120.0,
        variacion_ms=20.0,
        mbps=20.0,
        perdida=0.0,
    ),
    "cloudflare medido": Perfil(
        nombre="cloudflare medido",
        latencia_ms=237.0,
        variacion_ms=40.0,
        mbps=None,
        perdida=0.0,
    ),
}

_ALIAS_PERFILES: dict[str, str] = {
    "misma_ciudad": "misma ciudad",
    "ciudad": "misma ciudad",
    "mismo_pais": "mismo país",
    "pais": "mismo país",
    "otro_continente": "otro continente",
    "continente": "otro continente",
    "cloudflare": "cloudflare medido",
    "cloudflare_medido": "cloudflare medido",
}


def _quitar_tildes(texto: str) -> str:
    reemplazos = {"á": "a", "é": "e", "í": "i", "ó": "o", "ú": "u"}
    for origen, dest in reemplazos.items():
        texto = texto.replace(origen, dest)
    return texto


def obtener_perfil(nombre: str) -> Perfil:
    """Obtiene un perfil por nombre o alias."""
    clave_original = nombre.strip().lower().replace("_", " ").replace("-", " ")
    clave_limpia = _quitar_tildes(clave_original)

    if clave_original in PERFILES:
        return PERFILES[clave_original]

    for k, p in PERFILES.items():
        if clave_limpia == _quitar_tildes(k):
            return p

    if clave_original in _ALIAS_PERFILES:
        return PERFILES[_ALIAS_PERFILES[clave_original]]

    if clave_limpia in _ALIAS_PERFILES:
        return PERFILES[_ALIAS_PERFILES[clave_limpia]]

    for alias, oficial in _ALIAS_PERFILES.items():
        if clave_limpia == _quitar_tildes(alias):
            return PERFILES[oficial]

    raise KeyError(
        f"Perfil '{nombre}' desconocido. Perfiles disponibles: {list(PERFILES.keys())}"
    )


def parsear_direccion(dir_val: str | tuple[str, int] | int, host_defecto: str = "127.0.0.1") -> tuple[str, int]:
    """Parsea representaciones host:puerto o puerto a tuple[str, int]."""
    if isinstance(dir_val, tuple):
        return str(dir_val[0]), int(dir_val[1])
    if isinstance(dir_val, int):
        return host_defecto, dir_val
    s = str(dir_val).strip()
    if ":" in s:
        partes = s.rsplit(":", 1)
        h = partes[0].strip() or host_defecto
        return h, int(partes[1])
    return host_defecto, int(s)


class CubetaFichas:
    """Cubeta de fichas (token bucket / pacing) para limitar el ancho de banda por sentido."""

    def __init__(self, mbps: float | None, max_rafaga_ms: float = 0.0) -> None:
        self.mbps = mbps
        if mbps is not None and mbps > 0:
            # 1 Mb/s = 1_000_000 bits/s = 125_000 bytes/s
            self.bytes_por_segundo = (float(mbps) * 1_000_000.0) / 8.0
            self.max_rafaga_segundos = max_rafaga_ms / 1000.0
        else:
            self.bytes_por_segundo = 0.0
            self.max_rafaga_segundos = 0.0
        self._tiempo_disponible: float = 0.0

    def registrar_envio(self, tamano_bytes: int, ahora: float) -> float:
        """Devuelve el instante de tiempo en el que termina de transmitirse el paquete."""
        if self.bytes_por_segundo <= 0:
            return ahora
        inicio = max(self._tiempo_disponible, ahora - self.max_rafaga_segundos)
        duracion = tamano_bytes / self.bytes_por_segundo
        fin = inicio + duracion
        self._tiempo_disponible = fin
        return max(ahora, fin)


@dataclass
class EstadisticasSentido:
    """Metricas de transmision en un sentido del enlace."""

    bytes_totales: int = 0
    paquetes_totales: int = 0
    paquetes_perdidos: int = 0


@dataclass
class EstadisticasEnlace:
    """Metricas globales del enlace proxy TCP."""

    conexiones_activas: int = 0
    conexiones_totales: int = 0
    a_b: EstadisticasSentido = field(default_factory=EstadisticasSentido)
    b_a: EstadisticasSentido = field(default_factory=EstadisticasSentido)

    @property
    def bytes_totales(self) -> int:
        return self.a_b.bytes_totales + self.b_a.bytes_totales

    @property
    def paquetes_totales(self) -> int:
        return self.a_b.paquetes_totales + self.b_a.paquetes_totales

    @property
    def paquetes_perdidos_totales(self) -> int:
        return self.a_b.paquetes_perdidos + self.b_a.paquetes_perdidos


class Enlace:
    """Proxy TCP bidireccional en espacio de usuario con simulacion de red."""

    def __init__(
        self,
        escuchar: str | tuple[str, int] | int,
        destino: str | tuple[str, int] | int,
        latencia_ms: float | None = None,
        variacion_ms: float | None = None,
        mbps: float | None = None,
        perdida: float | None = None,
        rto_ms: float | None = None,
        perfil: str | Perfil | None = None,
        mss: int = 1460,
    ) -> None:
        self.host, self.puerto_solicitado = parsear_direccion(escuchar)
        self.puerto: int = self.puerto_solicitado
        self.destino_host, self.destino_puerto = parsear_direccion(destino)
        self.mss = max(1, int(mss))

        # Cargar perfil si se indico
        p: Perfil | None = None
        if isinstance(perfil, str):
            p = obtener_perfil(perfil)
        elif isinstance(perfil, Perfil):
            p = perfil

        if p is not None:
            self.latencia_ms = float(p.latencia_ms if latencia_ms is None else latencia_ms)
            self.variacion_ms = float(p.variacion_ms if variacion_ms is None else variacion_ms)
            self.mbps = p.mbps if mbps is None else (float(mbps) if mbps > 0 else None)
            self.perdida = float(p.perdida if perdida is None else perdida)
            self.rto_ms = p.rto_ms if rto_ms is None else float(rto_ms)
            self.nombre_perfil: str | None = p.nombre
        else:
            self.latencia_ms = float(latencia_ms if latencia_ms is not None else 0.0)
            self.variacion_ms = float(variacion_ms if variacion_ms is not None else 0.0)
            self.mbps = float(mbps) if (mbps is not None and mbps > 0) else None
            self.perdida = float(perdida if perdida is not None else 0.0)
            self.rto_ms = float(rto_ms) if rto_ms is not None else None
            self.nombre_perfil = None

        # RTO por defecto segun latencia de ida (RTT = 2 * latencia)
        if self.rto_ms is None:
            if self.latencia_ms > 0:
                self.rto_ms = max(200.0, 2.0 * self.latencia_ms + 4.0 * self.variacion_ms)
            else:
                self.rto_ms = 200.0

        # Cubetas de fichas independientes por sentido
        self.cubeta_a_b = CubetaFichas(self.mbps)
        self.cubeta_b_a = CubetaFichas(self.mbps)

        # Estadisticas
        self.estadisticas = EstadisticasEnlace()

        # Servidor y control
        self._servidor: asyncio.Server | None = None
        self._tareas_conexiones: set[asyncio.Task[Any]] = set()
        self._activo = False

    @property
    def direccion(self) -> tuple[str, int]:
        return self.host, self.puerto

    def _calcular_retardo(self) -> tuple[float, bool]:
        """Calcula retardo (segundos) y si hubo perdida modelada como 1 RTO."""
        if self.variacion_ms > 0:
            d_ms = max(0.0, random.gauss(self.latencia_ms, self.variacion_ms))
        else:
            d_ms = max(0.0, self.latencia_ms)

        perdido = False
        if self.perdida > 0.0 and random.random() < (self.perdida / 100.0):
            d_ms += self.rto_ms
            perdido = True

        return d_ms / 1000.0, perdido

    async def _canal_directo(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        stats: EstadisticasSentido,
    ) -> None:
        """Ruta rapida sin retrasos para medir costo propio minimo."""
        try:
            while True:
                datos = await reader.read(65536)
                if not datos:
                    break
                stats.bytes_totales += len(datos)
                stats.paquetes_totales += 1
                writer.write(datos)
                await writer.drain()
        except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
            pass
        finally:
            try:
                if writer.can_write_eof():
                    writer.write_eof()
                    await writer.drain()
                else:
                    writer.close()
                    await writer.wait_closed()
            except (OSError, RuntimeError):
                pass

    async def _canal(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        cubeta: CubetaFichas,
        stats: EstadisticasSentido,
    ) -> None:
        """Canal con simulacion de ancho de banda, latencia, jitter y perdida en orden."""
        sin_retrasos = (
            self.latencia_ms == 0.0
            and self.variacion_ms == 0.0
            and (self.mbps is None or self.mbps <= 0.0)
            and self.perdida == 0.0
        )
        if sin_retrasos:
            await self._canal_directo(reader, writer, stats)
            return

        cola: asyncio.Queue[tuple[float, bytes] | None] = asyncio.Queue(maxsize=1024)
        evento_error = asyncio.Event()

        async def consumidor() -> None:
            loop = asyncio.get_running_loop()
            try:
                while True:
                    item = await cola.get()
                    if item is None:
                        cola.task_done()
                        break
                    t_entrega, trozo = item
                    ahora = loop.time()
                    espera = t_entrega - ahora
                    if espera > 0:
                        await asyncio.sleep(espera)
                    writer.write(trozo)
                    if cola.empty():
                        await writer.drain()
                    cola.task_done()
                await writer.drain()
            except (ConnectionResetError, BrokenPipeError, asyncio.CancelledError):
                evento_error.set()
            except Exception:
                evento_error.set()
                raise

        tarea_consumidor = asyncio.create_task(consumidor())

        try:
            loop = asyncio.get_running_loop()
            ultimo_t_entrega = 0.0

            while True:
                if evento_error.is_set():
                    break
                try:
                    datos = await reader.read(65536)
                except (ConnectionResetError, BrokenPipeError):
                    break
                if not datos:
                    break

                mss = self.mss
                for i in range(0, len(datos), mss):
                    if evento_error.is_set():
                        break
                    trozo = datos[i : i + mss]
                    ahora = loop.time()

                    t_tx = cubeta.registrar_envio(len(trozo), ahora)
                    retardo_s, perdido = self._calcular_retardo()

                    if perdido:
                        stats.paquetes_perdidos += 1
                    stats.paquetes_totales += 1
                    stats.bytes_totales += len(trozo)

                    t_llegada = t_tx + retardo_s
                    # Respetar el orden de bytes: ningun byte puede entregarse antes que los previos
                    t_entrega = max(t_llegada, ultimo_t_entrega)
                    ultimo_t_entrega = t_entrega

                    await cola.put((t_entrega, trozo))

            if not evento_error.is_set():
                await cola.put(None)
                await tarea_consumidor
        except (asyncio.CancelledError, ConnectionResetError, BrokenPipeError):
            pass
        finally:
            if not tarea_consumidor.done():
                tarea_consumidor.cancel()
                await asyncio.gather(tarea_consumidor, return_exceptions=True)
            try:
                if writer.can_write_eof():
                    writer.write_eof()
                    await writer.drain()
                else:
                    writer.close()
                    await writer.wait_closed()
            except (OSError, RuntimeError):
                pass

    async def _manejar_conexion(
        self, cliente_reader: asyncio.StreamReader, cliente_writer: asyncio.StreamWriter
    ) -> None:
        tarea_actual = asyncio.current_task()
        if tarea_actual:
            self._tareas_conexiones.add(tarea_actual)
        self.estadisticas.conexiones_activas += 1
        self.estadisticas.conexiones_totales += 1

        dest_writer: asyncio.StreamWriter | None = None
        try:
            try:
                dest_reader, dest_writer = await asyncio.open_connection(
                    self.destino_host, self.destino_puerto
                )
            except Exception as e:
                log.warning(
                    "No se pudo conectar al destino %s:%d: %s",
                    self.destino_host,
                    self.destino_puerto,
                    e,
                )
                cliente_writer.close()
                await cliente_writer.wait_closed()
                return

            tarea_a_b = asyncio.create_task(
                self._canal(
                    cliente_reader, dest_writer, self.cubeta_a_b, self.estadisticas.a_b
                )
            )
            tarea_b_a = asyncio.create_task(
                self._canal(
                    dest_reader, cliente_writer, self.cubeta_b_a, self.estadisticas.b_a
                )
            )

            await asyncio.gather(tarea_a_b, tarea_b_a, return_exceptions=True)
        finally:
            for w in (cliente_writer, dest_writer):
                if w is not None:
                    try:
                        w.close()
                        await w.wait_closed()
                    except Exception:
                        pass
            self.estadisticas.conexiones_activas -= 1
            if tarea_actual:
                self._tareas_conexiones.discard(tarea_actual)

    async def iniciar(self) -> Enlace:
        """Inicia el servidor proxy TCP."""
        self._servidor = await asyncio.start_server(
            self._manejar_conexion, host=self.host, port=self.puerto_solicitado
        )
        sock = self._servidor.sockets[0]
        self.host, self.puerto = sock.getsockname()[:2]
        self._activo = True
        return self

    async def detener(self) -> None:
        """Detiene el servidor y cierra todas las conexiones activas limpiamente."""
        self._activo = False
        if self._servidor is not None:
            self._servidor.close()
            await self._servidor.wait_closed()
            self._servidor = None

        tareas = list(self._tareas_conexiones)
        for t in tareas:
            t.cancel()
        if tareas:
            await asyncio.gather(*tareas, return_exceptions=True)
        self._tareas_conexiones.clear()

    async def esperar_cierre(self) -> None:
        """Espera a que el servidor termine de servir."""
        if self._servidor is not None:
            await self._servidor.serve_forever()

    async def __aenter__(self) -> Enlace:
        await self.iniciar()
        return self

    async def __aexit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        await self.detener()


def _construir_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fabric.simulador.red",
        description="Simulador de enlace de red TCP en espacio de usuario (asyncio).",
    )
    parser.add_argument(
        "--escuchar",
        required=True,
        help="Direccion local de escucha (host:puerto o puerto, ej: 127.0.0.1:8000)",
    )
    parser.add_argument(
        "--destino",
        required=True,
        help="Direccion destino (host:puerto o puerto, ej: 127.0.0.1:9000)",
    )
    parser.add_argument(
        "--perfil",
        default=None,
        help="Perfil predefinido (misma ciudad, mismo país, otro continente, cloudflare medido)",
    )
    parser.add_argument(
        "--latencia-ms",
        type=float,
        default=None,
        help="Latencia de ida en milisegundos (ej: 40)",
    )
    parser.add_argument(
        "--variacion-ms",
        type=float,
        default=None,
        help="Variacion / jitter en milisegundos (ej: 10)",
    )
    parser.add_argument(
        "--mbps",
        type=float,
        default=None,
        help="Ancho de banda en Mb/s (ej: 50)",
    )
    parser.add_argument(
        "--perdida",
        type=float,
        default=None,
        help="Porcentaje de perdida de paquetes (ej: 0.5 para 0.5%%)",
    )
    parser.add_argument(
        "--rto-ms",
        type=float,
        default=None,
        help="Retransmission TimeOut en ms para paquetes perdidos",
    )
    parser.add_argument(
        "--mss",
        type=int,
        default=1460,
        help="Tamano de segmento TCP (MSS) en bytes (por defecto 1460)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Activar registro detallado",
    )
    return parser


async def _ejecutar_cli(args: argparse.Namespace) -> None:
    enlace = Enlace(
        escuchar=args.escuchar,
        destino=args.destino,
        latencia_ms=args.latencia_ms,
        variacion_ms=args.variacion_ms,
        mbps=args.mbps,
        perdida=args.perdida,
        rto_ms=args.rto_ms,
        perfil=args.perfil,
        mss=args.mss,
    )

    await enlace.iniciar()

    perfil_txt = f" [{enlace.nombre_perfil}]" if enlace.nombre_perfil else ""
    mbps_txt = f"{enlace.mbps} Mb/s" if enlace.mbps is not None else "sin limite"
    print(
        f"Enlace activo{perfil_txt}: {enlace.host}:{enlace.puerto} -> "
        f"{enlace.destino_host}:{enlace.destino_puerto}"
    )
    print(
        f"Parametros: latencia={enlace.latencia_ms} ms, variacion={enlace.variacion_ms} ms, "
        f"ancho={mbps_txt}, perdida={enlace.perdida} %, RTO={enlace.rto_ms} ms"
    )

    loop = asyncio.get_running_loop()
    parar = asyncio.Event()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, parar.set)
        except NotImplementedError:
            pass

    try:
        await parar.wait()
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        print("\nCerrando enlace...")
        await enlace.detener()
        st = enlace.estadisticas
        print(
            f"Estadisticas finales: conexiones={st.conexiones_totales}, "
            f"bytes={st.bytes_totales}, paquetes={st.paquetes_totales}, "
            f"perdidos={st.paquetes_perdidos_totales}"
        )


def main() -> None:
    parser = _construir_parser()
    args = parser.parse_args()

    nivel = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=nivel, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

    try:
        asyncio.run(_ejecutar_cli(args))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
