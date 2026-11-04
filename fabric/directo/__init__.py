"""Módulo de conexión directa P2P (WebRTC DataChannel / STUN) con fallback a relé."""

from fabric.directo.canal import CanalWebRTC
from fabric.directo.tubo_hibrido import TuboHibrido

__all__ = ["CanalWebRTC", "TuboHibrido"]
