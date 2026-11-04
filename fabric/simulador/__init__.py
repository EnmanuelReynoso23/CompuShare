"""Simulador de enlaces de red de CompuShare."""

from __future__ import annotations

from typing import Any

__all__ = [
    "CubetaFichas",
    "Enlace",
    "Escenario",
    "EstadisticasEnlace",
    "EstadisticasSentido",
    "MetricasEnlace",
    "PERFILES",
    "Perfil",
    "ResultadoEscenario",
    "obtener_perfil",
]


def __getattr__(name: str) -> Any:
    if name in ("Escenario", "ResultadoEscenario", "MetricasEnlace"):
        import fabric.simulador.escenario as _escenario

        return getattr(_escenario, name)
    if name in __all__:
        import fabric.simulador.red as _red

        return getattr(_red, name)
    raise AttributeError(f"modulo {__name__!r} no tiene el atributo {name!r}")
