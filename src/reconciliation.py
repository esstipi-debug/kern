"""Inventory reconciliation, record accuracy (IRA) and cycle-count plan (capability M6).

System-vs-physical comparison with a tolerance band, the IRA metric, the dollar
impact of variances, and an ABC-tiered cycle-count schedule (A items counted more
often). Pure arithmetic, auditable for the QA gate. Reference: Piasecki, *Inventory
Accuracy*; APICS/ASCM CPIM ECM v8.
"""

from __future__ import annotations

from dataclasses import dataclass

# Default ABC-tiered counts per year (control-group / Piasecki style).
_DEFAULT_COUNTS = {"A": 12, "B": 4, "C": 1}


@dataclass(frozen=True)
class CountResult:
    product_id: str
    system_qty: float
    physical_qty: float
    variance: float          # physical - system
    variance_pct: float      # variance / system
    unit_cost: float
    within_tolerance: bool


def reconcile(records: list[dict], *, tolerance_pct: float = 0.0, tolerance_units: float = 0.0):
    """Compare system vs physical counts. ``within_tolerance`` per the wider of the
    absolute and percentage bands."""
    out: list[CountResult] = []
    for r in records:
        system = float(r["system_qty"])
        physical = float(r["physical_qty"])
        unit_cost = float(r.get("unit_cost", 0.0))
        variance = physical - system
        variance_pct = (variance / system) if system != 0 else float("inf")
        band = max(tolerance_units, abs(system) * tolerance_pct)
        within = abs(variance) <= band
        out.append(CountResult(str(r["product_id"]), system, physical, variance,
                               variance_pct, unit_cost, within))
    return out


def inventory_record_accuracy(results: list[CountResult]) -> float:
    """IRA = fraction of counted records within tolerance."""
    if not results:
        return 0.0
    return sum(1 for r in results if r.within_tolerance) / len(results)


def total_variance_value(results: list[CountResult]) -> float:
    """Absolute dollar impact of all variances (sum |variance| * unit_cost)."""
    return sum(abs(r.variance) * r.unit_cost for r in results)


def cycle_count_plan(items: list[dict], *, counts_per_year: dict | None = None) -> list[dict]:
    """Assign a count frequency per SKU from its ABC class."""
    table = counts_per_year or _DEFAULT_COUNTS
    plan = []
    for it in items:
        abc = str(it.get("abc", "C")).upper()
        plan.append({
            "product_id": str(it["product_id"]),
            "abc": abc,
            "counts_per_year": table.get(abc, table.get("C", 1)),
        })
    return plan
