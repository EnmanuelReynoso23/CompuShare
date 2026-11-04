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


def test_rtt_con_hello_rpc():
    servidor = socket.socket()
    servidor.bind(("127.0.0.1", 0))
    servidor.listen()
    puerto = servidor.getsockname()[1]

    def atender():
        for _ in range(2):
            con, _ = servidor.accept()
            pedido = con.recv(1 + 8 + reparto.HELLO_TAM)
            assert pedido[0] == reparto.RPC_CMD_HELLO
            assert struct.unpack("<Q", pedido[1:9])[0] == reparto.HELLO_TAM
            con.sendall(struct.pack("<Q", 28) + bytes(28))
            con.close()

    threading.Thread(target=atender, daemon=True).start()
    assert reparto.medir_rtt(puerto, veces=2) < 1000
    servidor.close()
    assert reparto.medir_rtt(puerto, veces=1, espera_s=1) == float("inf")
