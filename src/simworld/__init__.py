"""Arnes de simulacion: un mundo con verdad conocida para puntuar decisiones.

No es una capacidad del agente. Nada de aca se registra en scm_agent/tools.py.
"""

from src.simworld.world import Sku, Supplier, World, WorldState, generate_world, initial_state

__all__ = ["Sku", "Supplier", "World", "WorldState", "generate_world", "initial_state"]
