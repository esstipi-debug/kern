"""Lean pull-loop sizing: kanban cards and takt time (Jacobs & Chase 15e, ch. 14).

The quantitative companions to the existing DDMRP / (s,Q) engines for a pull line.
"""

from __future__ import annotations

import math


def num_kanban_cards(
    demand_rate: float, lead_time: float, container_size: float, safety_factor: float = 0.0
) -> int:
    """Number of kanban card-sets (containers) for a pull loop: k = D*L*(1+S)/C, rounded up.

    D = demand rate, L = replenishment lead time, S = safety-stock fraction, C = container
    size. The WIP cap of the loop is k * C.
    """
    if demand_rate < 0 or lead_time < 0 or safety_factor < 0:
        raise ValueError("demand_rate, lead_time, safety_factor must be >= 0")
    if container_size <= 0:
        raise ValueError("container_size must be > 0")
    return math.ceil(demand_rate * lead_time * (1 + safety_factor) / container_size)


def takt_time(available_time: float, required_units: float) -> float:
    """Demand pace that syncs a line to demand: takt = available work time / required units.

    A unit must exit every takt interval to exactly meet demand; a station whose cycle time
    exceeds takt is a capacity shortfall.
    """
    if required_units <= 0:
        raise ValueError("required_units must be > 0")
    if available_time < 0:
        raise ValueError("available_time must be >= 0")
    return available_time / required_units
