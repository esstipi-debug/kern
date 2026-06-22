"""Warehouse space (m3) and COI slotting (capability M7).

Turns an inventory plan into a space plan: cube per SKU, warehouse utilization, and
class-based slotting by the Cube-per-Order Index (Kallina & Lynn 1976) — lowest COI
(most picks per unit of space) goes to the most accessible zone. Pure numpy/python;
3D bin packing (cartonization) is a separate optional dep (py3dbp).
"""

from __future__ import annotations

from dataclasses import dataclass


def sku_volume(length: float, width: float, height: float) -> float:
    """Unit volume from dimensions (consistent units in -> volume out)."""
    return length * width * height


def required_space(unit_volume: float, target_units: float) -> float:
    """Storage volume needed to hold the target quantity of a SKU."""
    return unit_volume * target_units


def cube_per_order_index(required_space: float, pick_frequency: float) -> float:
    """COI = storage cube / pick frequency. Lower COI deserves a closer slot."""
    if pick_frequency <= 0:
        return float("inf")
    return required_space / pick_frequency


def warehouse_utilization(used_volume: float, available_volume: float) -> float:
    """Fraction of usable cube occupied (net of aisles/clearance in available)."""
    if available_volume <= 0:
        return float("inf")
    return used_volume / available_volume


@dataclass(frozen=True)
class SkuSlot:
    product_id: str
    required_space: float
    pick_frequency: float
    coi: float
    zone: str


def slot_skus(skus: list[dict], *, zone_cuts: tuple[float, float] = (0.2, 0.5)) -> list[SkuSlot]:
    """Assign SKUs to zones A/B/C by ascending COI (A = most accessible).

    Each SKU dict has ``product_id``, ``required_space`` and ``pick_frequency``.
    ``zone_cuts`` are cumulative *count* fractions for the A and B boundaries.
    """
    if not skus:
        return []
    a_cut, b_cut = zone_cuts
    ranked = sorted(
        (
            {
                "product_id": str(s["product_id"]),
                "required_space": float(s["required_space"]),
                "pick_frequency": float(s["pick_frequency"]),
                "coi": cube_per_order_index(float(s["required_space"]), float(s["pick_frequency"])),
            }
            for s in skus
        ),
        key=lambda r: r["coi"],
    )
    n = len(ranked)
    out: list[SkuSlot] = []
    for i, r in enumerate(ranked):
        frac = (i + 1) / n  # cumulative count fraction after this SKU
        if frac <= a_cut:
            zone = "A"
        elif frac <= b_cut:
            zone = "B"
        else:
            zone = "C"
        out.append(SkuSlot(r["product_id"], r["required_space"], r["pick_frequency"], r["coi"], zone))
    return out
