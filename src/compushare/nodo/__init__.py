"""Módulo de Nodos (Maestro y Trabajador) de CompuShare."""

from compushare.nodo.maestro import NodoMaestro
from compushare.nodo.trabajador import NodoTrabajador, detectar_hardware

__all__ = ["NodoMaestro", "NodoTrabajador", "detectar_hardware"]
