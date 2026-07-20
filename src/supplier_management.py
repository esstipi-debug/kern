"""Kraljic supplier segmentation + strategic SRM (CSCP coverage gap).

Places each supplier on the Kraljic (1983) purchasing-portfolio matrix: a
profit-impact axis (share of annual spend, cut by cumulative-spend Pareto) x a
supply-risk axis (a weighted composite of normalized risk drivers). The four
quadrants map to the standard SRM playbook - strategic -> partner/develop,
bottleneck -> secure supply/dual-source, leverage -> competitive tender,
non-critical -> simplify/automate.

Pure (stdlib + src.guided only): mirrors the weighted-composite -> banding
template of src/multi_criteria_classification.py and returns a never-unprotected
GuidedOutcome exactly like src/mcdm.py::award_outcome. Risk drivers arrive
already normalized to [0,1] (higher = riskier); normalization is the job layer's
job (jobs/supplier_management_job.py), keeping this module free of pandas/I/O.

Grounded in L3: knowledge::kraljic_matrix (Grant, Sustainable Logistics &
Supply Chain, Ch6 Sustainable Purchasing and Procurement).
"""

from __future__ import annotations

from dataclasses import dataclass

from src.guided import ExecutionOption, GuidedOutcome, as_options

# (impact_band, risk_band) -> Kraljic quadrant.
_QUADRANT = {
    ("high", "high"): "strategic",
    ("low", "high"): "bottleneck",
    ("high", "low"): "leverage",
    ("low", "low"): "non_critical",
}

# Quadrant -> the SRM strategy it prescribes (ASCII-only for cp1252 safety).
QUADRANT_STRATEGY = {
    "strategic": "partner / develop - joint roadmap, long-term contract, dual capacity",
    "bottleneck": "secure supply / dual-source - buffer stock, qualify alternates, hedge",
    "leverage": "competitive tender - exploit buying power, RFQ/spot, consolidate volume",
    "non_critical": "simplify / automate - catalog buy, P-card, cut transaction cost",
}


@dataclass(frozen=True)
class RiskDriver:
    """One supply-risk axis input and its weight in the composite."""

    name: str
    weight: float = 1.0


@dataclass(frozen=True)
class SupplierInput:
    """A supplier with its annual spend and normalized [0,1] risk driver scores."""

    supplier: str
    annual_value: float
    risk_scores: dict[str, float]


@dataclass(frozen=True)
class SupplierSegment:
    """A fully-placed supplier: both axes, its quadrant and prescribed strategy."""

    supplier: str
    annual_value: float
    spend_share: float          # annual_value / total spend, [0,1]
    profit_impact: float        # == spend_share (the profit-impact axis value)
    impact_band: str            # "high" | "low"
    supply_risk: float          # composite risk, [0,1]
    risk_band: str              # "high" | "low"
    quadrant: str               # strategic | bottleneck | leverage | non_critical
    strategy: str


def composite_risk(risk_scores: dict[str, float], drivers: list[RiskDriver]) -> float:
    """Weighted average of the (already-normalized) driver scores; 0.0 if no weight."""
    total_w = sum(d.weight for d in drivers)
    if total_w <= 0:
        return 0.0
    acc = sum(d.weight * float(risk_scores.get(d.name, 0.0)) for d in drivers)
    return acc / total_w


def segment_suppliers(
    suppliers: list[SupplierInput],
    drivers: list[RiskDriver],
    *,
    impact_pareto: float = 0.8,
    risk_threshold: float = 0.5,
) -> list[SupplierSegment]:
    """Place each supplier on the Kraljic matrix.

    Profit-impact: suppliers are ranked by spend descending; a supplier is "high"
    impact while the cumulative spend share *before* it is below ``impact_pareto``
    (the vital few carrying the top ~80% of spend), "low" otherwise.
    Supply-risk: composite driver score >= ``risk_threshold`` is "high".
    """
    if not suppliers:
        return []
    if not 0.0 < impact_pareto <= 1.0:
        raise ValueError("impact_pareto must be in (0, 1]")

    total = sum(s.annual_value for s in suppliers)
    if total <= 0:
        raise ValueError("total annual spend must be positive")

    ordered = sorted(suppliers, key=lambda s: s.annual_value, reverse=True)
    segments: list[SupplierSegment] = []
    cum_before = 0.0
    for s in ordered:
        share = s.annual_value / total
        impact_band = "high" if cum_before < impact_pareto - 1e-9 else "low"
        cum_before += share

        risk = composite_risk(s.risk_scores, drivers)
        risk_band = "high" if risk >= risk_threshold else "low"

        quadrant = _QUADRANT[(impact_band, risk_band)]
        segments.append(
            SupplierSegment(
                supplier=s.supplier,
                annual_value=s.annual_value,
                spend_share=share,
                profit_impact=share,
                impact_band=impact_band,
                supply_risk=risk,
                risk_band=risk_band,
                quadrant=quadrant,
                strategy=QUADRANT_STRATEGY[quadrant],
            )
        )
    return segments


def segment_outcome(
    segments: list[SupplierSegment],
    *,
    summary: str,
    action_prefix: str = "stage:srm:",
) -> GuidedOutcome:
    """Present a priority action list (highest spend x risk exposure first).

    Exposure = spend_share * supply_risk - the strategic/bottleneck suppliers
    carrying both weight and risk float to the top; the best is auto-recommended.
    """
    if not segments:
        raise ValueError("no segments to build an outcome from")
    ranked = sorted(segments, key=lambda s: s.spend_share * s.supply_risk, reverse=True)
    options = [
        ExecutionOption(
            label=s.supplier,
            summary=f"{s.quadrant}: {s.strategy}",
            score=s.spend_share * s.supply_risk,
            action=f"{action_prefix}{s.quadrant}:{s.supplier}",
            tradeoffs=f"spend share {s.spend_share * 100:.0f}%, supply risk {s.supply_risk:.2f}",
        )
        for s in ranked
    ]
    return as_options(summary, options)
