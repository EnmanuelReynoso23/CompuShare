"""llama-server de mentira para las pruebas: habla el mismo protocolo que usa fabric/nodo/motores.py
(/health, /apply-template y /completion en streaming SSE) sin cargar ningun modelo.

Se lanza como si fuera llama-server (`llama-server -m X --host H --port P ...`). Al arrancar escribe
sus argumentos en el archivo que indica la variable de entorno LLAMA_FALSO_ARGV (JSON), para que la
prueba compruebe qué le pidió el trabajador o el maestro. Cada respuesta de /completion incluye el
puerto en el que escucha, así la prueba sabe que el pedido llegó a este proceso.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def _argumentos(lista: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(add_help=False)
    p.add_argument("-m", "--model", default="")
    p.add_argument("--host", default="127.0.0.1")
    p.add_argument("--port", type=int, default=8080)
    conocidos, _ = p.parse_known_args(lista)
    return conocidos


class Manejador(BaseHTTPRequestHandler):
    puerto = 0

    def log_message(self, *_args) -> None:  # silencio en stderr
        pass

    def _json(self, estado: int, cuerpo: dict) -> None:
        datos = json.dumps(cuerpo).encode()
        self.send_response(estado)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def do_GET(self) -> None:
        if self.path.split("?")[0] == "/health":
            self._json(200, {"status": "ok"})
        else:
            self._json(404, {"error": "no existe"})

    def do_POST(self) -> None:
        largo = int(self.headers.get("Content-Length") or 0)
        pedido = json.loads(self.rfile.read(largo) or b"{}")
        ruta = self.path.split("?")[0]
        if ruta == "/apply-template":
            mensajes = pedido.get("messages") or []
            texto = " ".join(str(m.get("content", "")) for m in mensajes)
            self._json(200, {"prompt": texto})
        elif ruta == "/completion":
            respuesta = f"llama falso en {self.puerto}: {pedido.get('prompt', '')}"
            cuerpo = b""
            for trozo in (respuesta[: len(respuesta) // 2], respuesta[len(respuesta) // 2:]):
                cuerpo += b"data: " + json.dumps({"content": trozo, "stop": False}).encode() + b"\n\n"
            cuerpo += b"data: " + json.dumps({
                "content": "", "stop": True,
                "timings": {"predicted_n": 4, "predicted_per_second": 100.0, "prompt_n": 2},
            }).encode() + b"\n\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(cuerpo)))
            self.end_headers()
            self.wfile.write(cuerpo)
        else:
            self._json(404, {"error": "no existe"})


def main(lista: list[str]) -> None:
    args = _argumentos(lista)
    ruta_argv = os.environ.get("LLAMA_FALSO_ARGV")
    if ruta_argv:
        with open(ruta_argv, "w", encoding="utf-8") as f:
            json.dump(lista, f)
    Manejador.puerto = args.port
    servidor = ThreadingHTTPServer((args.host, args.port), Manejador)
    servidor.daemon_threads = True
    servidor.serve_forever()


if __name__ == "__main__":
    main(sys.argv[1:])
