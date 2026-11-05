"""Reparto entre computadoras: menos equipos posibles, los de menor latencia primero; RTT con HELLO RPC."""

import socket
import struct
import threading

import pytest

from fabric import reparto
from fabric.reparto import Candidato


def test_si_cabe_en_el_maestro_no_usa_a_nadie():
    assert reparto.elegir([Candidato("a", 1, 30, 50)], modelo_gb=10, propio_gb=30) == []


def test_usa_los_menos_y_los_mas_cercanos():
    cs = [Candidato("lejos", 1, 30, 200), Candidato("cerca", 2, 30, 20), Candidato("medio", 3, 30, 80)]
    elegidos = reparto.elegir(cs, modelo_gb=40, propio_gb=30)  # necesita 50: con uno de 30 alcanza
    assert [c.nombre for c in elegidos] == ["cerca"]
    elegidos = reparto.elegir(cs, modelo_gb=60, propio_gb=30)  # necesita 75: hacen falta dos
    assert [c.nombre for c in elegidos] == ["cerca", "medio"]


def test_minimo_y_caidos():
    cs = [Candidato("caido", 1, 30), Candidato("vivo", 2, 30, 40)]
    assert [c.nombre for c in reparto.elegir(cs, 5, 30, minimo=1)] == ["vivo"]
    with pytest.raises(ValueError):
        reparto.elegir(cs, 5, 30, minimo=2)
    with pytest.raises(ValueError):
        reparto.elegir(cs, 100, 30)


def _recibir(con, n):
    datos = b""
    while len(datos) < n:
        trozo = con.recv(n - len(datos))
        if not trozo:
            raise ConnectionError
        datos += trozo
    return datos


def test_rtt_sobre_conexion_abierta():
    """HELLO al conectar y luego pings DEVICE_COUNT por la misma conexión; el servidor tarda 200 ms en
    aceptar (como abrir un tubo por el relé) y eso va a conexion_ms, no a rtt_ms."""
    import time

    servidor = socket.socket()
    servidor.bind(("127.0.0.1", 0))
    servidor.listen()
    puerto = servidor.getsockname()[1]
    vistos = []

    def atender():
        con, _ = servidor.accept()
        time.sleep(0.2)
        pedido = _recibir(con, 1 + 8 + reparto.HELLO_TAM)
        assert pedido[0] == reparto.RPC_CMD_HELLO
        assert struct.unpack("<Q", pedido[1:9])[0] == reparto.HELLO_TAM
        con.sendall(struct.pack("<Q", 28) + bytes(28))
        try:
            while True:
                cab = _recibir(con, 9)
                vistos.append(cab[0])
                con.sendall(struct.pack("<Q", 4) + struct.pack("<I", 1))
        except ConnectionError:
            con.close()

    threading.Thread(target=atender, daemon=True).start()
    m = reparto.medir_enlace(puerto, veces=3)
    assert m["conexion_ms"] >= 200 and m["rtt_ms"] < 100
    assert vistos == [reparto.RPC_CMD_DEVICE_COUNT] * 3
    servidor.close()
    assert reparto.medir_rtt(puerto, veces=1, espera_s=1) == float("inf")


def test_salida_en_el_maestro():
    from fabric.reparto import salida_en_el_maestro
    assert salida_en_el_maestro(0) == ["-ot", r"^(token_embd|output)\.weight$=CPU"]
    assert salida_en_el_maestro(2) == ["-ot", r"^(token_embd|output)\.weight$=CUDA1"]


def test_mtp_en_el_maestro():
    from fabric.reparto import mtp_en_el_maestro
    assert mtp_en_el_maestro(0) == ["-ot", r"blk\.24\..*=CPU"]
    assert mtp_en_el_maestro(1) == ["-ot", r"blk\.24\..*=CUDA0"]
    assert mtp_en_el_maestro(0, patron_capa=r"blk\.32\..*") == ["-ot", r"blk\.32\..*=CPU"]



def test_avance_en_salud_solo_cifras(tmp_path, monkeypatch):
    from fabric.rele import rele

    ruta = tmp_path / "avance.json"
    ruta.write_text('{"trozos_hechos": 3, "trozos_total": 93, "texto": "' + "x" * 200 + '", "lista": [1]}')
    monkeypatch.setenv("FABRIC_AVANCE", str(ruta))
    assert rele.leer_avance() == {"trozos_hechos": 3, "trozos_total": 93}
    monkeypatch.setenv("FABRIC_AVANCE", str(tmp_path / "no-existe.json"))
    assert rele.leer_avance() is None
