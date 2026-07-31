"""Arnes de simulacion: un mundo con verdad conocida para puntuar decisiones.

No es una capacidad del agente. Nada de aca se registra en scm_agent/tools.py.
"""

from src.simworld.lens import apply_dirt, export_history, history_frame
from src.simworld.runners import (  # noqa: E402
    Policy,
    apply_policy,
    kern_policy,
    naive_policy,
    oracle_orders,
)
from src.simworld.scorer import Kpis, capture, score_run, weekly_cost  # noqa: E402
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
    "Policy", "apply_policy", "kern_policy", "naive_policy", "oracle_orders",
    "Kpis", "capture", "score_run", "weekly_cost",
]
