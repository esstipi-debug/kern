"""Supplier scorecards — OTIF / DIFOT and quality (capability M8).

OTIF (on-time in-full) requires both conditions on a delivery; on-time and in-full
rates are reported separately, with average lead time and defect PPM. Pure arithmetic.
Reference: ASCM/CIPS supplier-performance practice.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SupplierScore:
    supplier: str
    deliveries: int
    on_time_rate: float
    in_full_rate: float
    otif: float
    avg_lead_time: float
    ppm: float


def score_supplier(supplier: str, deliveries: list[dict]) -> SupplierScore:
    """Aggregate a supplier's delivery records into a scorecard."""
    n = len(deliveries)
    if n == 0:
        return SupplierScore(supplier, 0, 0.0, 0.0, 0.0, 0.0, 0.0)

    on_time = sum(1 for d in deliveries if d.get("on_time"))
    in_full = sum(1 for d in deliveries if d.get("in_full"))
    otif = sum(1 for d in deliveries if d.get("on_time") and d.get("in_full"))
    lead_total = sum(float(d.get("lead_time_days", 0.0)) for d in deliveries)
    units = sum(float(d.get("units", 0.0)) for d in deliveries)
    defects = sum(float(d.get("defects", 0.0)) for d in deliveries)
    ppm = (defects / units * 1_000_000) if units > 0 else 0.0

    return SupplierScore(
        supplier=supplier,
        deliveries=n,
        on_time_rate=on_time / n,
        in_full_rate=in_full / n,
        otif=otif / n,
        avg_lead_time=lead_total / n,
        ppm=ppm,
    )
