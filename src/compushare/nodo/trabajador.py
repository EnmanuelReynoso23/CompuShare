"""Modulo del Nodo Trabajador (Worker) de CompuShare.
Gestiona la conexion con el rele y la ejecucion de inferencia local.
"""

class NodoTrabajador:
    def __init__(self, rele_url: str, modelo: str):
        self.rele_url = rele_url
        self.modelo = modelo
        self.activo = False

    async def conectar(self):
        """Establece conexion WebSocket con el rele maestro."""
        raise NotImplementedError("Implementacion en curso")

    async def ejecutar_tarea(self, prompt: str):
        """Ejecuta inferencia mediante el backend local (llama.cpp / vLLM)."""
        raise NotImplementedError("Implementacion en curso")
