"""Shelf-life aging report + markdown-vs-scrap disposition (offline)."""
from __future__ import annotations

from dataclasses import dataclass

from src.lots.fefo import AtRiskLot, Lot

# (label, inclusive upper bound on days_to_expiry); the last bucket catches the rest.
DEFAULT_THRESHOLDS: tuple[tuple[str, float], ...] = (
    ("expired", 0.0),
    ("expiring", 7.0),
    ("aging", 30.0),
    ("fresh", float("inf")),
)


@dataclass(frozen=True)
class ExpiryBucket:
    label: str
    n_lots: int
    quantity: float
    value: float        # quantity * unit_cost


@dataclass(frozen=True)
class DispositionPlan:
    at_risk_units: float
    at_risk_cost: float         # cost exposure of the at-risk units
    scrap_recovery: float       # scrap_value_pct * at_risk_cost
    markdown_recovery: float    # markdown_price_pct * potential revenue
    recommended: str            # "markdown" or "scrap"
    recovered_value: float


def _bucket_label(days: float, thresholds: tuple[tuple[str, float], ...]) -> str:
    for label, upper in thresholds:
        if days <= upper:
            return label
    return thresholds[-1][0]


def aging_report(
    lots: list[Lot],
    thresholds: tuple[tuple[str, float], ...] = DEFAULT_THRESHOLDS,
) -> list[ExpiryBucket]:
    """Bucket lots by days-to-expiry into shelf-life bands (threshold order preserved)."""
    agg: dict[str, dict[str, float]] = {label: {"n": 0.0, "qty": 0.0, "value": 0.0} for label, _ in thresholds}
    for lot in lots:
        label = _bucket_label(lot.days_to_expiry, thresholds)
        a = agg[label]
        a["n"] += 1
        a["qty"] += lot.quantity
        a["value"] += lot.quantity * lot.unit_cost
    return [
        ExpiryBucket(label=label, n_lots=int(agg[label]["n"]),
                     quantity=agg[label]["qty"], value=agg[label]["value"])
        for label, _ in thresholds
    ]


def markdown_vs_scrap(
    at_risk: list[AtRiskLot],
    *,
    scrap_value_pct: float = 0.0,
    markdown_price_pct: float = 0.5,
) -> DispositionPlan:
    """Compare clearing the at-risk units at a markdown vs scrapping them; recommend the better."""
    units = sum(lot.at_risk_quantity for lot in at_risk)
    cost = sum(lot.at_risk_value for lot in at_risk)
    revenue_potential = sum(lot.potential_revenue for lot in at_risk)
    scrap_recovery = scrap_value_pct * cost
    markdown_recovery = markdown_price_pct * revenue_potential
    recommended = "markdown" if markdown_recovery >= scrap_recovery else "scrap"
    return DispositionPlan(
        at_risk_units=units, at_risk_cost=cost,
        scrap_recovery=scrap_recovery, markdown_recovery=markdown_recovery,
        recommended=recommended, recovered_value=max(markdown_recovery, scrap_recovery),
    )
