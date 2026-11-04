"""Modulo de Nodos (Maestro y Trabajador) de CompuShare."""

from compushare.nodo.maestro import NodoMaestro
from compushare.nodo.trabajador import NodoTrabajador, buscar_binario, detectar_vram_gb

__all__ = ["NodoMaestro", "NodoTrabajador", "buscar_binario", "detectar_vram_gb"]
