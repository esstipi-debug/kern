"""Capacity cushion / utilization sizing (Jacobs & Chase 15e, ch. 5).

A concrete decision rule for sizing safety-capacity headroom against a demand forecast,
complementing the aggregate-planning (S&OP) layer.
"""

from __future__ import annotations


def capacity_cushion(capacity: float, expected_demand: float) -> float:
    """Excess-capacity fraction = (capacity - expected demand) / expected demand.

    Negative => a structural capacity shortfall.
    """
    if expected_demand <= 0:
        raise ValueError("expected_demand must be > 0")
    return (capacity - expected_demand) / expected_demand


def utilization_from_cushion(cushion: float) -> float:
    """Utilization implied by a cushion: u = 1 / (1 + cushion) (e.g. 20% cushion -> 83%)."""
    if cushion <= -1:
        raise ValueError("cushion must be > -1")
    return 1.0 / (1.0 + cushion)


def required_capacity(demand: float, target_cushion: float) -> float:
    """Capacity needed to hold a target cushion over a demand forecast: demand * (1 + cushion)."""
    if target_cushion <= -1:
        raise ValueError("target_cushion must be > -1")
    return demand * (1.0 + target_cushion)
