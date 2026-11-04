"""Modelo matemático de CompuShare: cuánto tarda cada palabra, cuánta memoria hace falta y a quién usar.

Todo sale de cuatro ideas (ver docs/MATEMATICA.md):

1. Generar una palabra en un equipo cuesta leer sus pesos de memoria una vez:
       t_calc = bytes_de_pesos_del_equipo / ancho_de_banda_de_memoria
   (en la generación el cuello de botella es la memoria, no los cálculos).
2. llama.cpp RPC es una estrella: el maestro manda la activación a cada trabajador y espera la salida.
   Cada trabajador usado suma por palabra una ida y vuelta y lo que cruza el enlace:
       t_red = sum(rtt_i + bytes_por_palabra_i / subida_i)
3. Varias preguntas a la vez (flujos) comparten la misma lectura de pesos y la misma ida y vuelta.
4. La decodificación especulativa valida varias palabras por pasada; rinde E(alfa, k) palabras.

Las cifras por defecto que no salen de una medición están marcadas SUPUESTO.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field

GB = 1e9
KB = 1e3


@dataclass(frozen=True)
class Modelo:
    nombre: str
    pesos_gb: float  # tamaño del GGUF
    capas: int
    d_modelo: int  # ancho del estado que viaja entre equipos
    activos_frac: float = 1.0  # MoE: fracción de pesos que se leen por palabra (A3B de 35B = 3/35)
    kv_kb_por_token: float = 0.0  # caché KV por token de contexto, todas las capas


@dataclass(frozen=True)
class Equipo:
    nombre: str
    memoria_gb: float  # lo que se puede usar para el modelo
    banda_gbs: float  # ancho de banda de memoria efectivo (GB/s)
    rtt_ms: float = 0.0  # ida y vuelta con el maestro (0 para el maestro)
    subida_mbps: float = 1e6  # del enlace con el maestro
    disponibilidad: float = 1.0  # probabilidad de estar encendido cuando se le pide


# Calibración contra la corrida medida del 31-oct-2026 (fabric/kaggle/resultados/20261031-dividido):
# Qwen3.5-9B Q8_0 (9,53 GB) entre 2 cuadernos de Kaggle con 2 x T4 cada uno: 147-150 ms por palabra
# sin MTP. T4 = 320 GB/s de catálogo; EFICIENCIA_GPU es la fracción que se logra (SUPUESTO 0,7,
# habitual en llama.cpp). Con eso el cálculo da ~43 ms y la red ~105 ms, que cuadra con la ida y vuelta
# mediana de 101 ms medida al relé el 1-nov (20261101-2040). El HELLO de 237 ms fue una sola medición.
EFICIENCIA_GPU = 0.7
BYTES_POR_PALABRA_MEDIDO = 315 * KB  # 20261031-dividido, por el tubo


def bytes_activacion(m: Modelo, bytes_por_valor: int = 4) -> float:
    """Lo mínimo que cruza por trabajador y palabra: la activación de ida y la de vuelta (f32)."""
    return 2 * m.d_modelo * bytes_por_valor


def bytes_activacion_unidireccional(m: Modelo, bytes_por_valor: int = 4) -> float:
    """En pipeline o anillo la activación cruza cada enlace en un solo sentido hacia el siguiente nodo."""
    return m.d_modelo * bytes_por_valor


def memoria_necesaria_gb(m: Modelo, contexto: int = 4096, flujos: int = 1, extra: float = 0.10) -> float:
    """Pesos + caché KV de todos los flujos + un 10 % de búferes de cálculo."""
    kv = m.kv_kb_por_token * KB * contexto * flujos / GB
    return (m.pesos_gb + kv) * (1 + extra)


def repartir_capas(m: Modelo, equipos: list[Equipo], necesaria_gb: float) -> list[int]:
    """Capas por equipo, proporcional a la memoria (lo mismo que hace llama.cpp con --tensor-split)."""
    total = sum(e.memoria_gb for e in equipos)
    if total < necesaria_gb:
        raise ValueError(f"no cabe: hacen falta {necesaria_gb:.1f} GB y hay {total:.1f} GB")
    crudo = [m.capas * e.memoria_gb / total for e in equipos]
    capas = [math.floor(c) for c in crudo]
    for i in sorted(range(len(crudo)), key=lambda i: crudo[i] - capas[i], reverse=True)[: m.capas - sum(capas)]:
        capas[i] += 1
    return capas


@dataclass
class Resultado:
    equipos: list[str]
    capas: list[int]
    ms_calculo: float
    ms_red: float
    flujos: int = 1
    palabras_por_pasada: float = 1.0
    detalle: dict = field(default_factory=dict)

    @property
    def ms_por_pasada(self) -> float:
        return self.ms_calculo + self.ms_red

    @property
    def palabras_s_por_flujo(self) -> float:
        return 1000 * self.palabras_por_pasada / self.ms_por_pasada

    @property
    def palabras_s_total(self) -> float:
        return self.flujos * self.palabras_s_por_flujo


def tiempo_por_palabra(
    m: Modelo,
    maestro: Equipo,
    trabajadores: list[Equipo],
    bytes_por_palabra: float | None = None,
    flujos: int = 1,
    contexto: int = 4096,
    eficiencia: float = EFICIENCIA_GPU,
) -> Resultado:
    """Una pasada de generación (una palabra por flujo) con llama.cpp RPC en estrella.

    bytes_por_palabra: lo que cruza cada enlace por pasada con 1 flujo. None = solo la activación
    (grafo reutilizado con GRAPH_RECOMPUTE). Con varios flujos crece la parte de activación.
    """
    equipos = [maestro, *trabajadores]
    capas = repartir_capas(m, equipos, memoria_necesaria_gb(m, contexto, flujos))
    leidos_por_capa = m.pesos_gb * GB * m.activos_frac / m.capas
    ms_calc = sum(1000 * c * leidos_por_capa / (e.banda_gbs * GB * eficiencia) for e, c in zip(equipos, capas))
    act = bytes_activacion(m)
    fijo = (bytes_por_palabra - act) if bytes_por_palabra is not None else 0.0
    ms_red = 0.0
    for e, c in zip(trabajadores, capas[1:]):
        if c == 0:
            continue
        b = max(fijo, 0) + act * flujos
        ms_red += e.rtt_ms + 1000 * b * 8 / (e.subida_mbps * 1e6)
    return Resultado([e.nombre for e in equipos], capas, ms_calc, ms_red, flujos)


def tiempo_pipelining(
    m: Modelo,
    maestro: Equipo,
    trabajadores: list[Equipo],
    bytes_por_palabra: float | None = None,
    flujos: int = 1,
    contexto: int = 4096,
    eficiencia: float = EFICIENCIA_GPU,
) -> Resultado:
    """Pipelining real en cadena/anillo (Petals, prima.cpp PRP, BloomBee).

    Varios flujos en vuelo a la vez en distintas etapas del pipeline. Cada equipo procesa
    un flujo distinto en paralelo.

    Fórmula matemática:
      - Tiempo de servicio de la etapa i por palabra de un flujo:
            t_etapa_i = t_calc_i + t_red_i
      - Cuello de botella del pipeline (etapa más lenta):
            t_cuello = max(t_etapa_i)
      - Latencia de punta a punta de un token:
            t_latencia = sum(t_etapa_i)
      - Rendimiento total (palabras/s):
            R_total = min(flujos * 1000 / t_latencia, 1000 / t_cuello)
      - Latencia efectiva por palabra de cada flujo:
            t_por_palabra_flujo = max(t_latencia, flujos * t_cuello)

    En un pipeline unidireccional la activación cruza cada enlace una sola vez hacia el nodo siguiente.
    """
    equipos = [maestro, *trabajadores]
    capas = repartir_capas(m, equipos, memoria_necesaria_gb(m, contexto, flujos))
    leidos_por_capa = m.pesos_gb * GB * m.activos_frac / m.capas

    act_unidir = bytes_activacion_unidireccional(m)
    fijo = (bytes_por_palabra - act_unidir) if bytes_por_palabra is not None else 0.0
    bytes_enlace = max(fijo, 0.0) + act_unidir

    ms_calc_etapas: list[float] = []
    ms_red_etapas: list[float] = []
    ms_tot_etapas: list[float] = []

    n = len(equipos)
    for i, (e, c) in enumerate(zip(equipos, capas)):
        if c == 0:
            ms_calc_etapas.append(0.0)
            ms_red_etapas.append(0.0)
            ms_tot_etapas.append(0.0)
            continue
        c_calc = 1000 * c * leidos_por_capa / (e.banda_gbs * GB * eficiencia)
        if n == 1:
            c_red = 0.0
        else:
            latencia_salto = (e.rtt_ms / 2.0) if e.rtt_ms > 0 else (trabajadores[0].rtt_ms / 2.0 if trabajadores else 0.0)
            c_red = latencia_salto + 1000 * bytes_enlace * 8 / (e.subida_mbps * 1e6)

        ms_calc_etapas.append(c_calc)
        ms_red_etapas.append(c_red)
        ms_tot_etapas.append(c_calc + c_red)

    ms_latencia_calc = sum(ms_calc_etapas)
    ms_latencia_red = sum(ms_red_etapas)
    ms_latencia = ms_latencia_calc + ms_latencia_red
    ms_cuello = max(ms_tot_etapas) if ms_tot_etapas else 0.0

    ms_pasada_efectiva = max(ms_latencia, flujos * ms_cuello)
    factor = (ms_pasada_efectiva / ms_latencia) if ms_latencia > 0 else 1.0

    return Resultado(
        [e.nombre for e in equipos],
        capas,
        ms_latencia_calc * factor,
        ms_latencia_red * factor,
        flujos,
        palabras_por_pasada=1.0,
        detalle={
            "tipo": "pipelining",
            "ms_latencia": ms_latencia,
            "ms_cuello": ms_cuello,
            "ms_etapas": ms_tot_etapas,
        },
    )


def palabras_por_pasada(alfa: float, k: int) -> float:
    """Decodificación especulativa: k palabras adivinadas, cada una acertada con prob. alfa
    (independientes). Palabras que salen por pasada = 1 + alfa + ... + alfa^k."""
    if alfa >= 1:
        return k + 1
    return (1 - alfa ** (k + 1)) / (1 - alfa)


def con_especulativa(r: Resultado, alfa: float, k: int, costo_extra_por_adivinada: float) -> Resultado:
    """costo_extra_por_adivinada: fracción de la pasada que agrega cada palabra adivinada
    (adivinar + verificar más tokens). Se calibra con la corrida real: ver calibrar_mtp()."""
    factor = 1 + costo_extra_por_adivinada * k
    return Resultado(
        r.equipos, r.capas, r.ms_calculo * factor, r.ms_red * factor, r.flujos,
        palabras_por_pasada(alfa, k), {**r.detalle, "alfa": alfa, "k": k},
    )


def calibrar_mtp(ms_sin: float, ms_con: float, alfa: float, k: int) -> float:
    """Despeja costo_extra_por_adivinada de dos mediciones: ms por palabra sin y con especulativa."""
    e = palabras_por_pasada(alfa, k)
    return (e * ms_con / ms_sin - 1) / k


def ms_tensor_paralelo(m: Modelo, rtt_ms: float) -> float:
    """Repartir cada capa entre 2 equipos (COMM_ALLREDUCE de llama.cpp): 2 sumas por capa y palabra,
    cada una espera al menos media ida y vuelta entre los dos. Cota inferior, solo red."""
    return 2 * m.capas * rtt_ms / 2


def ms_expertos_repartidos(m: Modelo, rtt_ms: float) -> float:
    """MoE con los expertos de cada capa en equipos distintos: una ida y vuelta por capa y palabra."""
    return m.capas * rtt_ms


def disponibilidad_cadena(equipos: list[Equipo], replicas: int = 1) -> float:
    """Probabilidad de que la cadena funcione: cada tramo necesita al menos una de sus réplicas."""
    p = 1.0
    for e in equipos:
        p *= 1 - (1 - e.disponibilidad) ** replicas
    return p


def prob_detectar_tramposo(frac_revisada: float, frac_corrupta: float, palabras: int) -> float:
    """Se recalcula al azar en otro equipo una fracción de las pasadas. Un nodo que corrompe una
    fracción de sus respuestas se detecta tras n pasadas con esta probabilidad."""
    return 1 - (1 - frac_revisada * frac_corrupta) ** palabras


def elegir_optimo(
    m: Modelo,
    maestro: Equipo,
    candidatos: list[Equipo],
    bytes_por_palabra: float | None = None,
    flujos: int = 1,
    contexto: int = 4096,
    max_trabajadores: int = 8,
) -> Resultado:
    """Prueba todas las combinaciones de trabajadores (hasta max_trabajadores) y devuelve la más
    rápida que cabe. Con 20 candidatos son ~260 mil combinaciones: menos de un segundo."""
    mejor: Resultado | None = None
    for n in range(0, min(max_trabajadores, len(candidatos)) + 1):
        for combo in itertools.combinations(candidatos, n):
            try:
                r = tiempo_por_palabra(m, maestro, list(combo), bytes_por_palabra, flujos, contexto)
            except ValueError:
                continue
            if mejor is None or r.ms_por_pasada < mejor.ms_por_pasada:
                mejor = r
    if mejor is None:
        raise ValueError("no cabe ni con todos los candidatos")
    return mejor


# Modelos. Tamaños de GGUF medidos o de fabric/PRUEBA-10.md; capas y ancho de las fichas de los modelos.
QWEN35_9B_Q8 = Modelo("Qwen3.5-9B Q8_0", 9.53, capas=32, d_modelo=4096)  # 9,53 GB medido
QWEN38_27B_Q4 = Modelo("Qwen3.8-27B UD-Q4_K_M", 16.5, capas=64, d_modelo=5120)  # capas y ancho: SUPUESTO
QWEN36_35B_A3B_Q4 = Modelo("Qwen3.6-35B-A3B Q4_K_M", 21.0, capas=48, d_modelo=2048, activos_frac=3 / 35)  # SUPUESTO

T4_GBS = 320  # varias T4 en fila leen cada una su parte a 320 GB/s: el total equivale a una de 320 GB/s
