"""Freight cost-to-serve by lane (offline).

Pure aggregation: roll per-shipment freight up to the lane (origin->destination / customer
/ route) and expose the operating ratios a logistics analyst acts on - freight per unit and
freight as a share of order value - ranked worst-first. Complements ``cost_to_serve`` (whole
service cost) by isolating the transport leg.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FreightLine:
    lane: str
    freight_cost: float
    units: float = 0.0
    order_value: float = 0.0
    weight_kg: float = 0.0


@dataclass(frozen=True)
class LaneCost:
    lane: str
    shipments: int
    total_freight: float
    total_units: float
    total_value: float
    total_weight: float
    freight_per_unit: float
    freight_pct_of_value: float     # total_freight / total_value (0 when value unknown)


def lane_cost_to_serve(lines: list[FreightLine]) -> list[LaneCost]:
    """Aggregate freight by lane; rank by total freight desc."""
    agg: dict[str, dict[str, float]] = {}
    for line in lines:
        a = agg.setdefault(line.lane, {"freight": 0.0, "units": 0.0, "value": 0.0, "weight": 0.0, "n": 0.0})
        a["freight"] += line.freight_cost
        a["units"] += line.units
        a["value"] += line.order_value
        a["weight"] += line.weight_kg
        a["n"] += 1

    lanes = [
        LaneCost(
            lane=lane,
            shipments=int(a["n"]),
            total_freight=a["freight"],
            total_units=a["units"],
            total_value=a["value"],
            total_weight=a["weight"],
            freight_per_unit=(a["freight"] / a["units"]) if a["units"] > 0 else 0.0,
            freight_pct_of_value=(a["freight"] / a["value"]) if a["value"] > 0 else 0.0,
        )
        for lane, a in agg.items()
    ]
    lanes.sort(key=lambda lc: lc.total_freight, reverse=True)
    return lanes
