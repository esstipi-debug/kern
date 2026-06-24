"""ABC-XYZ inventory classification (capability M4).

Two transparent axes that select a policy + service level per SKU:

- **ABC** (importance): rank by annual usage value (unit_cost x annual demand),
  walk the cumulative-value Pareto curve, cut at configurable thresholds
  (default A<=80%, B<=95%, C=rest). The SKU that crosses a threshold stays in the
  more-important class (cut on the *running* cumulative before the SKU).
- **XYZ** (predictability): coefficient of variation CV = sigma/mean of demand;
  X (CV<0.5) stable, Y (0.5<=CV<1.0) variable, Z (CV>=1.0 or zero-mean) erratic.

The 9-cell matrix maps each SKU to a default review policy, cycle-service-level
target and buffer distribution, which the existing safety_stock / fill_rate /
cost_optimization modules then size. Pure numpy/pandas — auditable for the QA gate.

Reference: Silver, Pyke & Thomas, *Inventory and Production Management in Supply
Chains* (4th ed., 2017), Pareto/ABC; ABC-XYZ matrix as the action layer.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# Importance -> cycle-service-level target.
_SERVICE_LEVEL = {"A": 0.98, "B": 0.95, "C": 0.90}
# Predictability -> review policy and buffer distribution.
_POLICY_BY_XYZ = {"X": "(R, S)", "Y": "(s, Q)", "Z": "(s, Q)"}
_BUFFER_BY_XYZ = {"X": "normal", "Y": "normal", "Z": "gamma"}


@dataclass(frozen=True)
class SkuClassification:
    product_id: str
    annual_value: float
    cumulative_share: float
    abc: str
    mean_demand: float
    cv: float
    xyz: str
    cell: str
    service_level: float
    policy: str
    buffer_distribution: str


def _xyz_class(cv: float, x_cut: float, y_cut: float) -> str:
    if cv < x_cut:
        return "X"
    if cv < y_cut:
        return "Y"
    return "Z"


def classify_portfolio(
    items: list[dict],
    *,
    abc_thresholds: tuple[float, float] = (0.80, 0.95),
    cv_cuts: tuple[float, float] = (0.5, 1.0),
) -> list[SkuClassification]:
    """Classify a portfolio of SKUs into the ABC-XYZ matrix.

    Each item is a dict with ``product_id``, ``unit_cost`` and ``demand`` (a list of
    per-period demand). Returns one SkuClassification per SKU, ordered by descending
    annual value (the ABC ranking).
    """
    if not items:
        return []

    a_cut, b_cut = abc_thresholds
    x_cut, y_cut = cv_cuts

    enriched = []
    for it in items:
        demand = np.asarray(it["demand"], dtype=float)
        annual_demand = float(demand.sum())
        mean = float(demand.mean()) if demand.size else 0.0
        std = float(demand.std(ddof=1)) if demand.size > 1 else 0.0
        cv = std / mean if mean > 0 else float("inf")
        enriched.append(
            {
                "product_id": it["product_id"],
                "annual_value": float(it["unit_cost"]) * annual_demand,
                "mean": mean,
                "cv": cv,
            }
        )

    enriched.sort(key=lambda r: r["annual_value"], reverse=True)
    total = sum(r["annual_value"] for r in enriched) or 1.0

    out: list[SkuClassification] = []
    running = 0.0  # cumulative share BEFORE the current SKU
    for r in enriched:
        if running < a_cut:
            abc = "A"
        elif running < b_cut:
            abc = "B"
        else:
            abc = "C"
        share = r["annual_value"] / total
        running += share

        xyz = _xyz_class(r["cv"], x_cut, y_cut)
        cell = abc + xyz
        policy = "make-to-order / review for discontinuation" if cell == "CZ" else _POLICY_BY_XYZ[xyz]
        out.append(
            SkuClassification(
                product_id=r["product_id"],
                annual_value=r["annual_value"],
                cumulative_share=running,
                abc=abc,
                mean_demand=r["mean"],
                cv=r["cv"],
                xyz=xyz,
                cell=cell,
                service_level=_SERVICE_LEVEL[abc],
                policy=policy,
                buffer_distribution=_BUFFER_BY_XYZ[xyz],
            )
        )
    return out


def portfolio_summary(classifications: list[SkuClassification]) -> dict:
    """Counts and value share per ABC-XYZ cell — the portfolio view for the dashboard."""
    summary: dict[str, dict] = {}
    total_value = sum(c.annual_value for c in classifications) or 1.0
    for c in classifications:
        bucket = summary.setdefault(c.cell, {"count": 0, "value": 0.0})
        bucket["count"] += 1
        bucket["value"] += c.annual_value
    for bucket in summary.values():
        bucket["value_share"] = bucket["value"] / total_value
    return summary


def service_levels(classifications: list[SkuClassification]) -> dict[str, float]:
    """Per-SKU cycle-service-level target from the ABC class (A highest, C lowest).

    The bridge that lets inventory size a *differentiated* safety stock: feed this map to
    ``jobs.inventory_optimization.run(service_levels=...)`` so each SKU's buffer reflects
    its importance instead of one global target.
    """
    return {c.product_id: c.service_level for c in classifications}
