import math

import pytest

from fabric.calculo import (
    BYTES_POR_PALABRA_MEDIDO, QWEN35_9B_Q8, T4_GBS, Equipo, Modelo, bytes_activacion_unidireccional,
    calibrar_mtp, con_especulativa, disponibilidad_cadena, elegir_optimo, memoria_necesaria_gb,
    palabras_por_pasada, prob_detectar_tramposo, repartir_capas, tiempo_pipelining, tiempo_por_palabra,
)


def test_reproduce_la_corrida_de_kaggle_dentro_de_15_por_ciento():
    # 20261031-dividido: 147-150 ms por palabra sin MTP
    r = tiempo_por_palabra(QWEN35_9B_Q8, Equipo("m", 30, T4_GBS),
                           [Equipo("t", 30, T4_GBS, 101, 200)], BYTES_POR_PALABRA_MEDIDO)
    assert 148 * 0.85 <= r.ms_por_pasada <= 148 * 1.15


def test_especulativa_formula_y_calibracion():
    assert palabras_por_pasada(0.0, 4) == 1
    assert palabras_por_pasada(1.0, 4) == 5
    assert palabras_por_pasada(0.79, 4) == pytest.approx(3.30, abs=0.01)  # local medido: 3,25
    g = calibrar_mtp(148, 1000 / 10.5, 0.79, 4)
    r = tiempo_por_palabra(QWEN35_9B_Q8, Equipo("m", 30, T4_GBS), [Equipo("t", 30, T4_GBS, 101, 200)],
                           BYTES_POR_PALABRA_MEDIDO)
    e = con_especulativa(r, 0.79, 4, g)
    # la calibración conserva la ganancia medida (148 -> 95 ms por palabra)
    assert r.ms_por_pasada / (e.ms_por_pasada / e.palabras_por_pasada) == pytest.approx(148 / (1000 / 10.5), rel=1e-6)


def test_capas_suman_y_no_cabe():
    m = Modelo("x", 10, 40, 1000)
    eq = [Equipo("a", 6, 100), Equipo("b", 6, 100)]
    assert sum(repartir_capas(m, eq, 11)) == 40
    with pytest.raises(ValueError):
        repartir_capas(m, eq, 13)


def test_memoria_cuenta_kv_por_flujo():
    m = Modelo("x", 10, 40, 1000, kv_kb_por_token=100)
    assert memoria_necesaria_gb(m, 4096, 4) > memoria_necesaria_gb(m, 4096, 1)


def test_elegir_optimo_prefiere_un_equipo_grande_a_dos_cercanos():
    m = Modelo("x", 10, 40, 4096)
    maestro = Equipo("m", 4, 300)
    cerca1, cerca2 = Equipo("c1", 4, 300, 30, 100), Equipo("c2", 4, 300, 30, 100)
    grande = Equipo("g", 12, 300, 45, 100)
    r = elegir_optimo(m, maestro, [cerca1, cerca2, grande])
    assert r.equipos == ["m", "g"]  # 45 ms < 30 + 30 ms


def test_si_cabe_en_el_maestro_no_usa_a_nadie():
    m = Modelo("x", 5, 40, 4096)
    r = elegir_optimo(m, Equipo("m", 24, 300), [Equipo("t", 24, 300, 10, 100)])
    assert r.equipos == ["m"] and r.ms_red == 0


def test_disponibilidad_y_tramposo():
    eq = [Equipo(str(i), 1, 1, disponibilidad=0.9) for i in range(4)]
    assert disponibilidad_cadena(eq) == pytest.approx(0.9 ** 4)
    assert disponibilidad_cadena(eq, 2) == pytest.approx(0.99 ** 4)
    n = math.ceil(math.log(0.01) / math.log(1 - 0.002))
    assert prob_detectar_tramposo(0.02, 0.1, n) >= 0.99


def test_pipelining_un_flujo_igual_a_latencia():
    m = Modelo("m", 10.0, 32, 2048)
    e1 = Equipo("e1", 10, 200)
    e2 = Equipo("e2", 10, 200, rtt_ms=20, subida_mbps=100)
    r1 = tiempo_pipelining(m, e1, [e2], flujos=1)
    assert r1.ms_por_pasada == pytest.approx(r1.detalle["ms_latencia"])
    assert r1.palabras_s_total == pytest.approx(1000 / r1.detalle["ms_latencia"])


def test_pipelining_escala_hasta_cuello_de_botella():
    m = Modelo("m", 10.0, 32, 2048)
    e1 = Equipo("e1", 10, 200)
    e2 = Equipo("e2", 10, 200, rtt_ms=20, subida_mbps=100)
    r1 = tiempo_pipelining(m, e1, [e2], flujos=1)
    r2 = tiempo_pipelining(m, e1, [e2], flujos=2)
    r8 = tiempo_pipelining(m, e1, [e2], flujos=8)

    cuello = r1.detalle["ms_cuello"]
    # Con 2 flujos en etapas simétricas, el rendimiento casi se duplica
    assert r2.palabras_s_total > r1.palabras_s_total * 1.8
    # Con 8 flujos, el rendimiento total satura en el cuello de botella: 1000 / cuello
    assert r8.palabras_s_total == pytest.approx(1000 / cuello, rel=1e-3)
    # La latencia por flujo aumenta proporcional al encolamiento
    assert r8.ms_por_pasada == pytest.approx(8 * cuello, rel=1e-3)


def test_pipelining_supera_a_estrella_con_varios_flujos():
    m = Modelo("m", 20.0, 40, 4096)
    maestro = Equipo("m", 10, 300)
    trabajadores = [
        Equipo("t1", 10, 300, rtt_ms=100, subida_mbps=50),
        Equipo("t2", 10, 300, rtt_ms=100, subida_mbps=50),
        Equipo("t3", 10, 300, rtt_ms=100, subida_mbps=50),
    ]
    r_estrella = tiempo_por_palabra(m, maestro, trabajadores, flujos=4)
    r_pipe = tiempo_pipelining(m, maestro, trabajadores, flujos=4)
    # En pipelining las etapas se solapan en la red y la activación es unidireccional: supera la estrella
    assert r_pipe.palabras_s_total > r_estrella.palabras_s_total
    assert bytes_activacion_unidireccional(m) == m.d_modelo * 4


# ── longitud del borrador γ* (predicción) ─────────────────────────────────────────────────────

import math  # noqa: E402

from fabric.calculo import (  # noqa: E402
    BYTES_VALOR_Q8, bytes_grafo_sin_codec, ronda_especulativa, ventana_optima,
)

D9B = QWEN35_9B_Q8.d_modelo  # 4096


def _gamma(alfa=0.75, bajada=100.0, subida=100.0, rtt=40.0, verif=40.0, **kw):
    return ventana_optima(alfa=alfa, rtt_ms=rtt, bajada_mbps=bajada, subida_mbps=subida,
                          ms_verificar=verif, d_modelo=D9B, **kw)


def test_gamma_baja_cuando_baja_el_ancho_de_banda():
    anchos = [1000.0, 100.0, 15.0, 5.0]
    gammas = [_gamma(bajada=b, subida=b).gamma for b in anchos]
    assert gammas == sorted(gammas, reverse=True)  # no sube al bajar el enlace
    assert gammas[0] > gammas[-1]  # y en un enlace de 5 Mb/s conviene menos borrador


def test_gamma_sube_cuando_sube_el_acierto():
    bajos = [_gamma(alfa=a, subida=15.0, bajada=15.0).gamma for a in (0.3, 0.6, 0.75, 0.9)]
    assert bajos == sorted(bajos)
    assert bajos[-1] > bajos[0]


def test_alfa_cero_no_conviene_borrar():
    v = _gamma(alfa=0.0, bajada=15.0, subida=15.0)
    assert v.gamma == 0
    # con alfa 0 cada ronda da exactamente 1 palabra, sea cual sea gamma
    assert all(abs(f["esperados"] - 1) < 1e-12 for f in v.tabla)


def test_ancho_infinito_y_sin_costo_de_borrador_llega_al_maximo():
    v = _gamma(bajada=math.inf, subida=math.inf, rtt=0.0, verif=20.0, gamma_max=12)
    assert v.gamma == 12
    assert v.tok_s == pytest.approx(1000 * palabras_por_pasada(0.75, 12) / 20.0)


def test_ancho_infinito_con_alfa_cero_empata_y_gana_el_menor():
    v = _gamma(alfa=0.0, bajada=math.inf, subida=math.inf, rtt=0.0, verif=20.0)
    assert v.gamma == 0


def test_gamma_cero_coincide_con_tiempo_por_palabra_sin_grafo():
    # mismo enlace simétrico y solo activación: la fila gamma=0 debe ser una pasada normal
    m = QWEN35_9B_Q8
    maestro, trabajador = Equipo("m", 30, T4_GBS), Equipo("t", 30, T4_GBS, 40, 15)
    r = tiempo_por_palabra(m, maestro, [trabajador])
    v = _gamma(rtt=40.0, bajada=15.0, subida=15.0, verif=r.ms_calculo)
    assert v.tabla[0]["ms_ronda"] == pytest.approx(r.ms_por_pasada, rel=1e-9)


def test_costo_de_borrador_reduce_gamma():
    sin_costo = _gamma(bajada=15.0, subida=15.0).gamma
    con_mtp = _gamma(bajada=15.0, subida=15.0, costo_borrador=0.28).gamma
    assert con_mtp <= sin_costo


def test_grafo_por_ronda_se_reparte_entre_mas_tokens():
    # El grafo se reenvía una vez por ronda, no por token: un gamma mayor lo amortiza.
    activacion = _gamma(bajada=15.0, subida=15.0).gamma
    reenviado = _gamma(bajada=15.0, subida=15.0, bytes_grafo=315e3).gamma
    assert reenviado >= activacion


def test_grafo_estimado_y_cuota_de_codec():
    assert bytes_grafo_sin_codec(32) == pytest.approx(315e3, rel=0.01)
    assert BYTES_VALOR_Q8 == pytest.approx(1.0625)


def test_ronda_con_q8_pesa_menos_que_f32():
    f32 = ronda_especulativa(4, alfa=0.7, rtt_ms=40, bajada_mbps=15, subida_mbps=15,
                             ms_verificar=40, d_modelo=D9B, bytes_por_valor=4)
    q8 = ronda_especulativa(4, alfa=0.7, rtt_ms=40, bajada_mbps=15, subida_mbps=15,
                            ms_verificar=40, d_modelo=D9B, bytes_por_valor=BYTES_VALOR_Q8)
    assert q8["bytes_ronda"] < f32["bytes_ronda"] / 3
    assert q8["tok_s"] > f32["tok_s"]


def test_valores_invalidos_se_rechazan():
    with pytest.raises(ValueError):
        _gamma(alfa=1.5)
    with pytest.raises(ValueError):
        _gamma(subida=0.0)


def test_tabla_tiene_una_fila_por_gamma_y_gana_la_de_mayor_tok_s():
    v = _gamma(bajada=15.0, subida=15.0, gamma_max=8)
    assert [f["gamma"] for f in v.tabla] == list(range(9))
    assert v.tok_s == pytest.approx(max(f["tok_s"] for f in v.tabla))
    assert v.tok_s_sin_especulativa == pytest.approx(v.tabla[0]["tok_s"])
