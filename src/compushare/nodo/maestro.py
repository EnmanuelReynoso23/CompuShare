"""Módulo del Nodo Maestro / Coordinador de CompuShare.
Gestiona el registro de trabajadores, balanceo de carga y API OpenAI.
"""

import asyncio
from typing import Optional
from compushare.rele.servidor import ServidorRele


class NodoMaestro:
    def __init__(self, host: str = "0.0.0.0", puerto: int = 8000, auth_token: Optional[str] = None):
        self.host = host
        self.puerto = puerto
        self.auth_token = auth_token
        self.servidor = ServidorRele(host=host, puerto=puerto, auth_token=auth_token)

    async def iniciar(self):
        """Inicia el servidor de orquestación y espera indefinidamente."""
        await self.servidor.iniciar()
        try:
            while True:
                await asyncio.sleep(3600)
        except asyncio.CancelledError:
            await self.servidor.detener()

    async def detener(self):
        """Detiene el servidor."""
        await self.servidor.detener()

    def resumen(self) -> dict:
        """Retorna el estado actual del enjambre."""
        return self.servidor.enrutador.resumen()
