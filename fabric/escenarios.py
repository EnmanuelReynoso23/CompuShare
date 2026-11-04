"""Escenarios de CompuShare entre amigos, calculados con fabric/calculo.py.

Uso: python -m fabric.escenarios   (imprime las tablas de docs/MATEMATICA.md)

Los equipos de los amigos son SUPUESTOS típicos; cambiarlos por los medidos de cada uno.
"""

from __future__ import annotations

import math

from fabric.calculo import (
    BYTES_POR_PALABRA_MEDIDO,
    QWEN35_9B_Q8,
    T4_GBS,
    Equipo,
    Modelo,
    calibrar_mtp,
    con_especulativa,
    disponibilidad_cadena,
    elegir_optimo,
    ms_expertos_repartidos,
    ms_tensor_paralelo,
    palabras_por_pasada,
    prob_detectar_tramposo,
    tiempo_por_palabra,
)

# SUPUESTOS: memoria usable para el modelo y ancho de banda de memoria de catálogo.
RTT_PAIS_MS = 40  # fabric/PRUEBA-10.md, "mismo país"
SUBIDA_CASA_MBPS = 10  # subida típica de un plan de casa: medir la de cada amigo

def amigos(rtt=RTT_PAIS_MS, subida=SUBIDA_CASA_MBPS) -> tuple[Equipo, list[Equipo]]:
    maestro = Equipo("tu PC (RTX 3060 12 GB)", 11, 360)
    return maestro, [
        Equipo("amigo 1 (RTX 4060 8 GB)", 7, 272, rtt, subida, 0.9),
        Equipo("amigo 2 (RTX 3060 12 GB)", 11, 360, rtt, subida, 0.9),
        Equipo("amigo 3 (laptop, 16 GB RAM, CPU)", 10, 40, rtt, subida, 0.9),
        Equipo("amigo 4 (Mac M1 16 GB)", 10, 68, rtt, subida, 0.9),
    ]


QWEN38_27B = Modelo("Qwen3.8-27B Q4", 16.5, capas=64, d_modelo=5120, kv_kb_por_token=128)
MOE_35B_A3B = Modelo("Qwen3.6-35B-A3B Q4", 21.0, capas=48, d_modelo=2048, activos_frac=3 / 35, kv_kb_por_token=64)


def fila(r, extra=""):
    usados = [f"{n.split(' (')[0]}:{c}" for n, c in zip(r.equipos, r.capas)]
    return (f"| {', '.join(usados)} | {r.ms_calculo:.0f} | {r.ms_red:.0f} | {r.ms_por_pasada:.0f} | "
            f"{r.palabras_s_por_flujo:.1f} | {r.palabras_s_total:.1f} |{extra}")


def main() -> None:
    print("## Calibración (Qwen3.5-9B Q8_0, 2 cuadernos Kaggle, 31-oct)\n")
    r = tiempo_por_palabra(QWEN35_9B_Q8, Equipo("maestro", 30, T4_GBS),
                           [Equipo("trabajador", 30, T4_GBS, 101, 200)], BYTES_POR_PALABRA_MEDIDO)
    print(f"Calculado {r.ms_por_pasada:.0f} ms por palabra (cálculo {r.ms_calculo:.0f} + red {r.ms_red:.0f}); "
          "medido 147-150 ms.\n")
    gamma = calibrar_mtp(148, 1000 / 10.5, 0.79, 4)
    print(f"MTP: con alfa 0,79 y k 4 (3,3 palabras por pasada, medido en local), 10,5 tok/s medidos -> "
          f"cada palabra adivinada encarece la pasada un {gamma * 100:.0f} %.\n")

    hdr = "| Equipos:capas | ms cálculo | ms red | ms por pasada | palabras/s por pregunta | palabras/s total |"
    sep = "|---|---|---|---|---|---|"
    maestro, cands = amigos()
    for m in (QWEN38_27B, MOE_35B_A3B):
        print(f"## {m.nombre} entre amigos (ida y vuelta {RTT_PAIS_MS} ms, subida {SUBIDA_CASA_MBPS} Mb/s)\n")
        print(hdr + " Caso |")
        print(sep + "---|")
        for etiqueta, bpp in (("grafo reenviado (315 KB, medido)", BYTES_POR_PALABRA_MEDIDO),
                              ("solo activación (grafo reutilizado)", None)):
            for flujos in (1, 4, 8):
                try:
                    r = elegir_optimo(m, maestro, cands, bpp, flujos)
                except ValueError:
                    print(f"| no cabe | | | | | | {etiqueta}, {flujos} flujos |")
                    continue
                print(fila(r, f" {etiqueta}, {flujos} flujos |"))
        r = elegir_optimo(m, maestro, cands, None, 1)
        for g, nombre in ((gamma, "MTP como hoy"), (0.05, "MTP sin viajes extra")):
            mejor = max(range(1, 5), key=lambda k: palabras_por_pasada(0.79, k) / (1 + g * k))
            e = con_especulativa(r, 0.79, mejor, g)
            print(fila(e, f" solo activación, 1 flujo, {nombre} (k={mejor}) |"))
        print()

    print("## Formas de repartir que NO sirven por internet (solo red, por palabra)\n")
    print("| Forma | ms por palabra a 40 ms | a 5 ms | a 0,5 ms (misma casa) |")
    print("|---|---|---|---|")
    for nombre, f, m in (("Capas partidas entre 2 (tensor paralelo)", ms_tensor_paralelo, QWEN38_27B),
                         ("Expertos de MoE en equipos distintos", ms_expertos_repartidos, MOE_35B_A3B)):
        print(f"| {nombre} ({m.nombre}) | {f(m, 40):.0f} | {f(m, 5):.0f} | {f(m, 0.5):.0f} |")
    print()

    print("## Amigos que apagan la PC (cada uno encendido el 90 % de las veces)\n")
    print("| Amigos en la cadena | Funciona | Con un suplente por tramo |")
    print("|---|---|---|")
    for n in range(1, 5):
        c = cands[:n]
        print(f"| {n} | {disponibilidad_cadena(c) * 100:.0f} % | {disponibilidad_cadena(c, 2) * 100:.1f} % |")
    print()

    print("## Detectar a un tramposo\n")
    n = math.ceil(math.log(0.01) / math.log(1 - 0.02 * 0.10))
    print(f"Revisando al azar el 2 % de las pasadas, un nodo que corrompe el 10 % de sus respuestas se detecta "
          f"con 99 % de seguridad en {n} pasadas ({prob_detectar_tramposo(0.02, 0.10, n) * 100:.1f} %).")


if __name__ == "__main__":
    main()
