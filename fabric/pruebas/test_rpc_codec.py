"""Pruebas del códec `rpc1` (fabric/rpc_codec.py) con flujos sintéticos de ggml-rpc, y de extremo a extremo
con el relé y el tubo reales de Python contra un servidor TCP falso que guarda lo recibido."""

from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import random
import socket
import struct

import pytest

from fabric import rpc_codec
from fabric.pruebas import flujo_rpc_sintetico as sint
from fabric.rele import rele as modulo_rele
from fabric.tubo import Tubo

CLAVE = "clave-codec"
VECTORES = pathlib.Path(__file__).resolve().parent / "vectores_codec"


def puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def codificar_en_trozos(flujo: bytes, rng: random.Random, q8: bool = False) -> list[bytes]:
    """Parte `flujo` en trozos de tamaño aleatorio (cortes arbitrarios) y devuelve los mensajes WS."""
    cod = rpc_codec.Codificador(q8=q8)
    mensajes, pos = [], 0
    while pos < len(flujo):
        k = rng.randint(1, 70000)
        salida = cod.codificar(flujo[pos:pos + k])
        if salida:
            mensajes.append(salida)
        pos += k
    return mensajes


def tipos_registro(mensaje: bytes) -> list[int]:
    """Tipos de registro de un mensaje WS (recorre las cabeceras de registro)."""
    tipos, pos = [], 0
    while pos < len(mensaje):
        t = mensaje[pos]
        tipos.append(t)
        pos += 1
        if t == 0x00:
            pos += 4 + struct.unpack_from("<I", mensaje, pos)[0]
        elif t == 0x01:
            pos += 8 + struct.unpack_from("<I", mensaje, pos + 4)[0]
        elif t == 0x02:
            pos += 16
        elif t == 0x03:
            pos += 16 + 8 + struct.unpack_from("<I", mensaje, pos + 20)[0]
        elif t == 0x04:
            hlen = struct.unpack_from("<I", mensaje, pos)[0]
            n = struct.unpack_from("<Q", mensaje, pos + 4 + hlen)[0]
            pos += 4 + hlen + 8 + (-(-n // 32)) * 34
        else:
            raise AssertionError(f"tipo desconocido {t}")
    return tipos


def eventos_pedidos(flujo: bytes) -> list[tuple[int, int, bytes]]:
    """(cmd, tamaño, cuerpo) de cada pedido, con el parser del códec."""
    salida: list[tuple[int, int, bytes]] = []
    cuerpo = bytearray()
    cur = None
    for tipo, cmd, tam, datos in rpc_codec.ParserPedidos().alimentar(flujo):
        if tipo == "cabecera":
            cur = (cmd, tam)
            cuerpo = bytearray()
        elif tipo == "datos":
            cuerpo += datos
        else:
            salida.append((cur[0], cur[1], bytes(cuerpo)))
    return salida


# ── parser y codificador/decodificador ─────────────────────────────────────────────────────────────────────


def test_parser_tolera_cortes_arbitrarios():
    flujo = sint.flujo(pasos=6, megas_pesos=1)
    rng = random.Random(7)
    parser = rpc_codec.ParserPedidos()
    reconstruido = bytearray()
    pos = 0
    cmds = []
    while pos < len(flujo):
        k = rng.randint(1, 9000)
        for tipo, cmd, tam, datos in parser.alimentar(flujo[pos:pos + k]):
            if tipo == "cabecera":
                cmds.append((cmd, tam))
                reconstruido += bytes([cmd]) + struct.pack("<Q", tam)
            elif tipo == "datos":
                reconstruido += datos
        pos += k
    assert bytes(reconstruido) == flujo
    assert len(cmds) == len(eventos_pedidos(flujo))


@pytest.mark.parametrize("semilla", [1, 2, 3])
def test_ida_y_vuelta_con_cortes_aleatorios(semilla):
    flujo = sint.flujo(semilla=semilla, pasos=24, megas_pesos=2)
    mensajes = codificar_en_trozos(flujo, random.Random(semilla))
    dec = rpc_codec.Decodificador()
    assert b"".join(dec.decodificar(m) for m in mensajes) == flujo


def test_ida_y_vuelta_byte_a_byte():
    flujo = sint.flujo(semilla=4, pasos=4, megas_pesos=0, tensores=60)
    cod = rpc_codec.Codificador()
    dec = rpc_codec.Decodificador()
    reconstruido = bytearray()
    for i in range(len(flujo)):
        salida = cod.codificar(flujo[i:i + 1])
        if salida:
            reconstruido += dec.decodificar(salida)
    assert bytes(reconstruido) == flujo


def test_ahorro_mayor_que_90_por_ciento_en_grafos_repetidos():
    flujo = sint.grafos(pasos=30, tensores=400)
    cod = rpc_codec.Codificador()
    enviado = len(cod.codificar(flujo))
    assert enviado < 0.10 * len(flujo), (enviado, len(flujo))
    # Y el decodificador lo reconstruye exacto.
    assert rpc_codec.Decodificador().decodificar(rpc_codec.Codificador().codificar(flujo)) == flujo


def test_registros_de_grafo_usan_ref_y_delta():
    flujo = sint.grafos(pasos=12, tensores=200)
    tipos = tipos_registro(rpc_codec.Codificador().codificar(flujo))
    assert tipos[0] == 0x01                      # primer grafo: completo
    assert 0x03 in tipos or 0x02 in tipos        # las formas siguientes salen por delta o por referencia
    assert 0x02 in tipos                         # la forma repetida sale por referencia


def test_hash_desconocido_es_error():
    dec = rpc_codec.Decodificador()
    with pytest.raises(rpc_codec.ErrorCodec):
        dec.decodificar(bytes([0x02]) + bytes(range(16)))


def test_delta_sin_base_es_error():
    dec = rpc_codec.Decodificador()
    registro = bytes([0x03]) + bytes(16) + struct.pack("<II", 10, 4) + b"\x00" * 4
    with pytest.raises(rpc_codec.ErrorCodec):
        dec.decodificar(registro)


def test_tipo_desconocido_es_error():
    with pytest.raises(rpc_codec.ErrorCodec):
        rpc_codec.Decodificador().decodificar(b"\x09\x00\x00")


def test_registro_truncado_es_error():
    with pytest.raises(rpc_codec.ErrorCodec):
        rpc_codec.Decodificador().decodificar(b"\x00\x10\x00\x00\x00abc")


def test_cmd_mayor_o_igual_a_64_pasa_crudo_para_siempre():
    previo = sint.grafos(pasos=3, tensores=100)
    extrano = sint.comando(70, b"x" * 5000)
    despues = sint.grafos(semilla=9, pasos=3, tensores=100)
    cod = rpc_codec.Codificador()
    cod.codificar(previo + extrano)
    tras = cod.codificar(despues)
    assert tipos_registro(tras) and set(tipos_registro(tras)) == {0x00}
    assert rpc_codec.Decodificador().decodificar(tras) == despues


def test_grafo_incompresible_sale_crudo():
    rng = random.Random(5)
    cuerpo = rng.randbytes(20000)
    flujo = sint.comando(10, cuerpo)
    cod = rpc_codec.Codificador()
    mensaje = cod.codificar(flujo)
    assert tipos_registro(mensaje) == [0x00]
    assert rpc_codec.Decodificador().decodificar(mensaje) == flujo


def test_estadisticas_suman_lo_que_entra_y_sale():
    flujo = sint.flujo(semilla=11, pasos=9, megas_pesos=1, tensores=150)
    cod = rpc_codec.Codificador()
    res = cod.codificar(flujo)
    st = cod.estad
    assert st.crudo == len(flujo)
    assert st.enviado == len(res)
    assert sum(v[1] for v in st.comandos.values()) == len(flujo)
    assert st.comandos["GRAPH_COMPUTE"][0] == 9
    assert "GRAPH_FULL" in st.registros and "GRAPH_REF" in st.registros
    assert st.resumen()["ahorro"] > 0.0


# ── Q8_0 ───────────────────────────────────────────────────────────────────────────────────────────────────


def _finitos_f32(cuerpo: bytes) -> bool:
    import math
    n = (len(cuerpo) - rpc_codec.CABECERA_SET) // 4
    vals = struct.unpack(f"<{n}f", cuerpo[rpc_codec.CABECERA_SET:])
    return all(math.isfinite(v) for v in vals)


def _sets(flujo: bytes) -> list[bytes]:
    """Cuerpos de los SET_TENSOR del flujo."""
    return [cuerpo for cmd, _, cuerpo in eventos_pedidos(flujo) if cmd == 6]


def test_q8_error_acotado_y_mascara_intacta():
    flujo = sint.flujo(semilla=21, pasos=6, megas_pesos=1, tensores=80)
    mensaje = b"".join(codificar_en_trozos(flujo, random.Random(1), q8=True))
    salida = rpc_codec.Decodificador().decodificar(mensaje)
    originales, reconstruidos = _sets(flujo), _sets(salida)
    assert len(originales) == len(reconstruidos)
    n_q8 = n_crudos = 0
    for a, b in zip(originales, reconstruidos):
        es_f32_grande = (len(a) - rpc_codec.CABECERA_SET >= 4 * rpc_codec.Q8_MIN_FLOATS
                         and a[8:12] == b"\0\0\0\0")
        if es_f32_grande and _finitos_f32(a):
            n_q8 += 1
            assert b[:rpc_codec.CABECERA_SET] == a[:rpc_codec.CABECERA_SET]
            xs = struct.unpack(f"<{(len(a) - rpc_codec.CABECERA_SET) // 4}f", a[rpc_codec.CABECERA_SET:])
            ys = struct.unpack(f"<{len(xs)}f", b[rpc_codec.CABECERA_SET:])
            for i in range(0, len(xs), 32):
                amax = max(abs(v) for v in xs[i:i + 32])
                for x, y in zip(xs[i:i + 32], ys[i:i + 32]):
                    assert abs(x - y) <= amax / 127.0 + 1e-6
        else:
            n_crudos += 1
            assert a == b  # máscara con -inf, pesos y lo demás: idénticos
    assert n_q8 > 0 and n_crudos > 0


def test_q8_numpy_y_python_puro_dan_los_mismos_bytes(monkeypatch):
    np = pytest.importorskip("numpy")
    rng = random.Random(8)
    for n in (1024, 1500, 4096):
        datos = struct.pack(f"<{n}f", *[rng.gauss(0, 3.0) for _ in range(n)])
        monkeypatch.setattr(rpc_codec, "_NP", np)
        con_numpy = rpc_codec._q8_codificar(datos, n)
        dec_numpy = rpc_codec._q8_a_f32(con_numpy, n)
        monkeypatch.setattr(rpc_codec, "_NP", None)
        sin_numpy = rpc_codec._q8_codificar(datos, n)
        dec_puro = rpc_codec._q8_a_f32(con_numpy, n)
        assert con_numpy == sin_numpy
        assert dec_numpy == dec_puro


def test_q8_redondeo_medio_lejos_de_cero(monkeypatch):
    # amax = 127 da escala 1.0; 0.5 -> 1, -2.5 -> -3, 2.4 -> 2 (medio redondeo se aleja de cero).
    bloque = [127.0, 0.5, -2.5, 2.4, 1.5] + [0.0] * 27
    datos = struct.pack("<32f", *bloque)
    monkeypatch.setattr(rpc_codec, "_NP", None)
    bloques = rpc_codec._q8_codificar(datos + bytes(4 * 992), 1024)
    q = struct.unpack_from("<5b", bloques, 2)
    assert q == (127, 1, -3, 2, 2)


def test_q8_no_se_usa_sin_grafo_previo():
    rng = random.Random(2)
    flujo = sint.set_activacion(rng, 1) + sint.grafo(0, 0, rng, tensores=20)
    cod = rpc_codec.Codificador(q8=True)
    mensaje = cod.codificar(flujo)
    assert tipos_registro(mensaje)[0] == 0x00
    assert rpc_codec.Decodificador().decodificar(mensaje) == flujo


# ── extremo a extremo: relé y tubo reales, servidor TCP falso ──────────────────────────────────────────────


def _espiar(monkeypatch) -> dict:
    cuenta = {"codificar": 0, "salida": 0, "decodificar": 0}
    cod_original = rpc_codec.Codificador.codificar
    dec_original = rpc_codec.Decodificador.decodificar

    def codificar(self, trozo):
        salida = cod_original(self, trozo)
        cuenta["codificar"] += 1
        cuenta["salida"] += len(salida)
        return salida

    def decodificar(self, mensaje):
        cuenta["decodificar"] += 1
        return dec_original(self, mensaje)

    monkeypatch.setattr(rpc_codec.Codificador, "codificar", codificar)
    monkeypatch.setattr(rpc_codec.Decodificador, "decodificar", decodificar)
    return cuenta


async def _extremo(flujo: bytes, monkeypatch, *, tubo_codec: bool = True) -> bytes:
    recibido = bytearray()
    fin = asyncio.Event()

    async def falso(lector, escritor):
        while trozo := await lector.read(65536):
            recibido.extend(trozo)
        fin.set()
        escritor.close()

    puerto_falso = puerto_libre()
    servidor_falso = await asyncio.start_server(falso, "127.0.0.1", puerto_falso)
    monkeypatch.setattr(modulo_rele, "TUBO_PUERTO_BASE", puerto_libre())
    puerto_rele = puerto_libre()
    listo = asyncio.Event()
    rele = asyncio.create_task(modulo_rele.principal(CLAVE, puerto_rele, listo))
    await asyncio.wait_for(listo.wait(), 5)
    tubo = Tubo(f"ws://127.0.0.1:{puerto_rele}", CLAVE, "trabajador-codec", puerto_falso, codec=tubo_codec)
    vida = asyncio.create_task(tubo.vivir())
    try:
        await asyncio.wait_for(tubo.listo.wait(), 5)
        lector, escritor = await asyncio.open_connection("127.0.0.1", tubo.puerto_remoto)
        rng = random.Random(12)
        pos = 0
        while pos < len(flujo):
            k = rng.randint(1, 30000)
            escritor.write(flujo[pos:pos + k])
            await escritor.drain()
            pos += k
        escritor.close()
        await asyncio.wait_for(fin.wait(), 20)
    finally:
        vida.cancel()
        rele.cancel()
        servidor_falso.close()
        await asyncio.gather(vida, rele, return_exceptions=True)
    return bytes(recibido)


@pytest.fixture
def entorno_codec(monkeypatch):
    monkeypatch.delenv("FABRIC_CODEC", raising=False)
    monkeypatch.delenv("FABRIC_CODEC_Q8", raising=False)
    monkeypatch.delenv("FABRIC_PERFIL", raising=False)
    return monkeypatch


def test_extremo_a_extremo_con_codec(entorno_codec):
    flujo = sint.flujo(semilla=31, pasos=12, megas_pesos=1, tensores=200)
    cuenta = _espiar(entorno_codec)
    recibido = asyncio.run(_extremo(flujo, entorno_codec))
    assert recibido == flujo
    assert cuenta["codificar"] > 0 and cuenta["decodificar"] > 0
    assert cuenta["salida"] < len(flujo)


def test_extremo_a_extremo_sin_codec_por_variable(entorno_codec):
    flujo = sint.flujo(semilla=32, pasos=6, megas_pesos=1, tensores=120)
    entorno_codec.setenv("FABRIC_CODEC", "0")
    cuenta = _espiar(entorno_codec)
    assert asyncio.run(_extremo(flujo, entorno_codec)) == flujo
    assert cuenta["codificar"] == 0 and cuenta["decodificar"] == 0


def test_extremo_a_extremo_tubo_sin_codecs(entorno_codec):
    flujo = sint.flujo(semilla=33, pasos=6, megas_pesos=1, tensores=120)
    cuenta = _espiar(entorno_codec)
    assert asyncio.run(_extremo(flujo, entorno_codec, tubo_codec=False)) == flujo
    assert cuenta["codificar"] == 0


def test_extremo_a_extremo_con_perfil_desactiva_codec(entorno_codec):
    flujo = sint.flujo(semilla=34, pasos=4, megas_pesos=1, tensores=100)
    entorno_codec.setenv("FABRIC_PERFIL", "1")
    cuenta = _espiar(entorno_codec)
    assert asyncio.run(_extremo(flujo, entorno_codec)) == flujo
    assert cuenta["codificar"] == 0


def test_extremo_a_extremo_q8_conserva_comandos_y_grafos(entorno_codec):
    flujo = sint.flujo(semilla=35, pasos=6, megas_pesos=1, tensores=120)
    entorno_codec.setenv("FABRIC_CODEC_Q8", "1")
    recibido = asyncio.run(_extremo(flujo, entorno_codec))
    original, llegado = eventos_pedidos(flujo), eventos_pedidos(recibido)
    assert [(c, t) for c, t, _ in original] == [(c, t) for c, t, _ in llegado]
    cuantizadas = 0
    for (cmd, _, a), (_, _, b) in zip(original, llegado):
        if cmd == 10 or (cmd == 6 and not _finitos_cuerpo(a)):
            assert a == b  # grafos y todo lo no finito (máscara) llegan idénticos
        elif cmd == 6:
            cuantizadas += a != b  # la activación finita llegó cuantizada (Q8 de verdad)
    assert cuantizadas > 0


def _finitos_cuerpo(cuerpo: bytes) -> bool:
    """True si el SET_TENSOR F32 grande de este cuerpo tiene solo valores finitos (candidato a Q8)."""
    if len(cuerpo) - rpc_codec.CABECERA_SET < 4 * rpc_codec.Q8_MIN_FLOATS or cuerpo[8:12] != b"\0\0\0\0":
        return False
    return _finitos_f32(cuerpo)


# ── vectores compartidos con Go ─────────────────────────────────────────────────────────────────────────────


def _generador_de_vectores():
    spec = importlib.util.spec_from_file_location("generar_vectores", VECTORES / "generar.py")
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_vectores_decodifican_y_se_regeneran_igual(tmp_path, monkeypatch):
    datos = (VECTORES / "mensajes.bin").read_bytes()
    mensajes, pos = [], 0
    while pos < len(datos):
        n = struct.unpack_from("<I", datos, pos)[0]
        mensajes.append(datos[pos + 4:pos + 4 + n])
        pos += 4 + n
    dec = rpc_codec.Decodificador()
    assert b"".join(dec.decodificar(m) for m in mensajes) == (VECTORES / "flujo_crudo.bin").read_bytes()
    q8 = (VECTORES / "q8.bin").read_bytes()
    assert rpc_codec.Decodificador().decodificar(q8) == (VECTORES / "q8_esperado.bin").read_bytes()

    generador = _generador_de_vectores()
    for nombre, contenido in generador.generar(tmp_path).items():
        assert (VECTORES / nombre).read_bytes() == contenido, nombre
    # Sin numpy, Q8 tiene que dar exactamente los mismos vectores.
    monkeypatch.setattr(rpc_codec, "_NP", None)
    for nombre, contenido in generador.generar(tmp_path / "puro").items():
        assert (VECTORES / nombre).read_bytes() == contenido, nombre
