"""Pruebas de compushare.transcribir contra un llama-server de mentira (sin modelo)."""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from compushare import transcribir as tr


def test_trozos_no_cortan_parrafos_y_limpian_html():
    texto = tr.limpiar("=== Capitulo Uno ===\n<div>uno uno</div>\n\ndos dos\n\ncenter\ntres tres\n")
    partes = tr.trozos(texto, chars=10)
    assert partes == ["uno uno", "dos dos", "tres tres"]
    assert tr.trozos("a\n\nb\n\nc", chars=100) == ["a\n\nb\n\nc"]


def test_repara_texto_utf8_leido_como_latin1():
    roto = "se despertó una mañana después".encode("utf-8").decode("latin-1")
    assert roto != "se despertó una mañana después"
    assert tr.reparar_codificacion(roto) == "se despertó una mañana después"
    assert tr.limpiar(roto + "\nya bien: acción") == "se despertó una mañana después\nya bien: acción"
    assert tr.reparar_codificacion("texto normal con ñ") == "texto normal con ñ"


class _FalsoLlama(BaseHTTPRequestHandler):
    """Copia el texto del usuario; si contiene 'MAL', se equivoca a propósito."""

    def log_message(self, *a):  # silencio
        pass

    def do_POST(self):
        cuerpo = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.path == "/apply-template":
            assert cuerpo["chat_template_kwargs"] == {"enable_thinking": False}
            resp = {"prompt": "<<" + cuerpo["messages"][-1]["content"] + ">>"}
        else:
            texto = cuerpo["prompt"][2:-2]
            salida = texto.replace("MAL", "BIEN")
            resp = {"content": salida, "stop_type": "eos",
                    "timings": {"prompt_n": 10, "predicted_n": 5, "predicted_per_second": 50.0,
                                "draft_n": 4, "draft_n_accepted": 3}}
        datos = json.dumps(resp).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)


def test_transcribir_compara_y_guarda(tmp_path):
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FalsoLlama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        url = f"http://127.0.0.1:{srv.server_address[1]}"
        vistos = []
        res = tr.transcribir(url, ["hola mundo", "esto sale MAL", "adios"], flujos=2, salida_dir=tmp_path,
                             al_terminar_trozo=lambda i, f: vistos.append(i))
    finally:
        srv.shutdown()
    assert sorted(vistos) == [0, 1, 2]
    assert res["trozos_hechos"] == 3 and res["trozos_exactos"] == 2 and res["trozos_con_error"] == 0
    assert res["gen_tokens"] == 15 and res["aciertos_especulativa"] == 0.75
    guardado = json.loads((tmp_path / "transcripcion.json").read_text(encoding="utf-8"))
    assert [t["exacto"] for t in sorted(guardado["trozos"], key=lambda t: t["i"])] == [True, False, True]
    assert (tmp_path / "libro_transcrito.txt").read_text(encoding="utf-8") == "hola mundo\n\nesto sale BIEN\n\nadios"


def test_transcribir_registra_errores_y_sigue(tmp_path):
    res = tr.transcribir("http://127.0.0.1:9", ["a", "b"], flujos=1, salida_dir=tmp_path)
    assert res["trozos_hechos"] == 2 and res["trozos_con_error"] == 2 and res["trozos_exactos"] == 0


def _servidor_falso():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _FalsoLlama)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_address[1]}"


def test_si_se_cae_el_servidor_recupera_y_repite_el_trozo(tmp_path, monkeypatch):
    srv, url = _servidor_falso()
    original = tr.transcribir_trozo
    estado = {"caido": False, "llamadas": 0}

    def trozo(u, texto, espera=3600):
        estado["llamadas"] += 1
        if texto == "b" and estado["llamadas"] == 2:  # el servidor se cae justo en el trozo "b"
            estado["caido"] = True
        if estado["caido"]:
            raise ConnectionRefusedError("sin llama-server")
        return original(u, texto, espera)

    def recuperar():
        estado["caido"] = False  # el maestro lo relanzó
        return True

    monkeypatch.setattr(tr, "transcribir_trozo", trozo)
    try:
        res = tr.transcribir(url, ["a", "b", "c"], salida_dir=tmp_path, recuperar=recuperar)
    finally:
        srv.shutdown()
    assert res["trozos_hechos"] == 3 and res["trozos_con_error"] == 0 and res["trozos_exactos"] == 3
    assert res["recuperaciones"] == 1 and not res["detenido_sin_servidor"]


def test_si_no_se_recupera_para_y_no_cuenta_los_que_faltan(tmp_path):
    vistos = []
    res = tr.transcribir("http://127.0.0.1:9", ["a", "b", "c", "d"], salida_dir=tmp_path,
                         recuperar=lambda: vistos.append(1) or False)
    assert vistos == [1]  # una sola recuperación intentada, no una por trozo
    assert res["detenido_sin_servidor"] and res["trozos_hechos"] == 0 and res["trozos_con_error"] == 0


def test_retomar_salta_lo_ya_hecho(tmp_path):
    srv, url = _servidor_falso()
    try:
        tr.transcribir(url, ["uno", "dos"], salida_dir=tmp_path)
        pedidos = []
        original = tr.transcribir_trozo
        tr.transcribir_trozo = lambda u, t, espera=3600: pedidos.append(t) or original(u, t, espera)
        try:
            res = tr.transcribir(url, ["uno", "dos", "tres"], salida_dir=tmp_path, retomar=True)
        finally:
            tr.transcribir_trozo = original
    finally:
        srv.shutdown()
    assert pedidos == ["tres"] and res["retomados"] == 2 and res["trozos_hechos"] == 3
    assert (tmp_path / "libro_transcrito.txt").read_text(encoding="utf-8") == "uno\n\ndos\n\ntres"
