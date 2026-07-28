"""Arnes de simulacion: un mundo con verdad conocida para puntuar decisiones.

No es una capacidad del agente. Nada de aca se registra en scm_agent/tools.py.
"""

from src.simworld.lens import apply_dirt, export_history, history_frame
from src.simworld.runners import Policy, apply_policy, naive_policy, oracle_orders  # noqa: E402
from src.simworld.world import (
    Sku,
    Supplier,
    WeekOutcome,
    World,
    WorldState,
    advance,
    generate_world,
    initial_state,
)

__all__ = [
    "Sku", "Supplier", "WeekOutcome", "World", "WorldState",
    "advance", "generate_world", "initial_state",
    "apply_dirt", "export_history", "history_frame",
    "Policy", "apply_policy", "naive_policy", "oracle_orders",
]
