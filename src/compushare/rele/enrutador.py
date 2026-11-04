"""Enrutador y balanceador de carga del enjambre CompuShare.
Administra el estado de los nodos trabajadores y selecciona el mejor candidato para cada tarea.
"""

import logging
import time
from typing import Dict, List, Optional
from dataclasses import dataclass, field
from aiohttp import web

from compushare.protocolo import MensajeRegistro, MensajeHeartbeat

logger = logging.getLogger("compushare.enrutador")


@dataclass
class EstadoTrabajador:
    worker_id: str
    ws: web.WebSocketResponse
    info: MensajeRegistro
    last_heartbeat: float = field(default_factory=time.time)
    active_tasks: int = 0
    total_tasks_completed: int = 0
    total_tokens_generated: int = 0
    is_alive: bool = True

    @property
    def slots_disponibles(self) -> int:
        return max(0, self.info.max_slots - self.active_tasks)


class EnrutadorEnjambre:
    def __init__(self):
        self._trabajadores: Dict[str, EstadoTrabajador] = {}

    def registrar(self, ws: web.WebSocketResponse, info: MensajeRegistro) -> EstadoTrabajador:
        """Registra un nuevo trabajador en el enjambre o actualiza uno existente."""
        worker = EstadoTrabajador(
            worker_id=info.worker_id,
            ws=ws,
            info=info,
            last_heartbeat=time.time(),
        )
        self._trabajadores[info.worker_id] = worker
        logger.info(
            f"[+Worker] Registrado: {info.worker_id} | Modelo: {info.modelo} | "
            f"GPU: {info.gpu_info} ({info.vram_mb}MB) | Slots: {info.max_slots}"
        )
        return worker

    def desregistrar(self, worker_id: str) -> Optional[EstadoTrabajador]:
        """Elimina un trabajador desconectado de la lista activa."""
        worker = self._trabajadores.pop(worker_id, None)
        if worker:
            worker.is_alive = False
            logger.info(f"[-Worker] Desconectado: {worker_id}")
        return worker

    def actualizar_heartbeat(self, hb: MensajeHeartbeat):
        """Actualiza la telemetría periódica enviada por el trabajador."""
        worker = self._trabajadores.get(hb.worker_id)
        if worker:
            worker.last_heartbeat = time.time()

    def seleccionar_worker(self, modelo_solicitado: str) -> Optional[EstadoTrabajador]:
        """Selecciona el trabajador más óptimo según disponibilidad de slots, modelo y carga.
        
        Si no hay coincidencia exacta del modelo, busca trabajadores compatibles o el primer
        trabajador disponible si solo hay uno en el enjambre.
        """
        candidatos: List[EstadoTrabajador] = [
            w for w in self._trabajadores.values()
            if w.is_alive and not w.ws.closed and w.slots_disponibles > 0
        ]

        if not candidatos:
            return None

        # 1. Filtro por modelo exacto o coincidencia parcial
        m_lower = modelo_solicitado.lower().strip()
        exactos = [c for c in candidatos if c.info.modelo.lower() == m_lower]
        if exactos:
            pool = exactos
        else:
            parciales = [c for c in candidatos if m_lower in c.info.modelo.lower() or c.info.modelo.lower() in m_lower]
            if parciales:
                pool = parciales
            elif modelo_solicitado in ("*", "default", "auto") or len(candidatos) == 1:
                pool = candidatos
            else:
                return None

        # 2. Ordenar por menor carga activa y mayor velocidad (tokens/seg)
        pool.sort(key=lambda w: (w.active_tasks, -w.info.tokens_per_second))
        return pool[0]

    def listar_modelos(self) -> List[str]:
        """Retorna la lista única de nombres de modelos servidos por los workers activos."""
        modelos = set()
        for w in self._trabajadores.values():
            if w.is_alive and not w.ws.closed:
                modelos.add(w.info.modelo)
        return sorted(list(modelos))

    def resumen(self) -> dict:
        """Retorna estadísticas operativas del enjambre para monitorización y dashboard."""
        trabajadores_activos = [w for w in self._trabajadores.values() if w.is_alive and not w.ws.closed]
        total_slots = sum(w.info.max_slots for w in trabajadores_activos)
        slots_en_uso = sum(w.active_tasks for w in trabajadores_activos)
        total_tareas = sum(w.total_tasks_completed for w in self._trabajadores.values())
        total_tokens = sum(w.total_tokens_generated for w in self._trabajadores.values())

        return {
            "estado": "operativo" if trabajadores_activos else "sin_workers",
            "nodos_activos": len(trabajadores_activos),
            "nodos_totales_registrados": len(self._trabajadores),
            "slots_totales": total_slots,
            "slots_en_uso": slots_en_uso,
            "tareas_completadas": total_tareas,
            "tokens_generados": total_tokens,
            "modelos_disponibles": self.listar_modelos(),
            "workers": [
                {
                    "id": w.worker_id,
                    "modelo": w.info.modelo,
                    "backend": w.info.backend,
                    "gpu": w.info.gpu_info,
                    "vram_mb": w.info.vram_mb,
                    "tps": w.info.tokens_per_second,
                    "tareas_activas": w.active_tasks,
                    "tareas_completadas": w.total_tasks_completed,
                    "latencia_heartbeat_s": round(time.time() - w.last_heartbeat, 1)
                }
                for w in trabajadores_activos
            ]
        }
