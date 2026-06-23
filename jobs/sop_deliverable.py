"""Compose a client-ready S&OP / IBP deck from a SopReview (capability gap #2).

Bridges the monthly cadence output (`src.sop.SopReview`) into the deliverable
composer (`src.deliverable`): the recommended supply plan, the demand-supply gap,
the cost / working-capital / service trade-offs, the alternatives weighed, and the
executive sign-off handoff. This is the "S&OP / IBP deck + monthly cadence"
deliverable SCM mode advertises. Additive - a caller opts in; nothing else changes.
"""
from __future__ import annotations

from collections.abc import Sequence

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.sop import PlanEvaluation, SopReview


def _gap_periods(ev: PlanEvaluation) -> list[str]:
    """Periods where the plan leaves demand unmet."""
    return [p.period for p in ev.periods if p.shortfall > 0]


def build(
    review: SopReview,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: Sequence[str] = (),
    data_source: str = "consensus demand plan (S&OP)",
) -> Deliverable:
    """Build the S&OP / IBP deck Deliverable from a completed cycle review."""
    rec = review.recommended
    others = [ev for name, ev in review.evaluations.items() if name != rec.name]

    summary = (
        f"Recommended supply plan: {rec.name} - {rec.fill_rate * 100:.0f}% fill, peak "
        f"inventory {rec.peak_inventory:,.0f} units, plan cost {rec.total_cost:,.0f}. "
        + review.summary
    )

    findings: list[Finding] = [
        Finding(
            f"Adopt the {rec.name} supply plan",
            "Best balance of cost, working capital and service across the three strategies "
            f"({rec.fill_rate * 100:.0f}% fill, {rec.capacity_changes:,.0f} units of capacity flex).",
            impact=f"plan cost {rec.total_cost:,.0f}",
        )
    ]
    gaps = _gap_periods(rec)
    if gaps:
        findings.append(Finding(
            f"Demand-supply gap in {len(gaps)} period(s): {', '.join(gaps)}",
            f"The recommended plan leaves {rec.total_shortfall:,.0f} units of demand unmet; "
            "close it with a pre-build, expedite, or demand-shaping move.",
            impact=f"{(1 - rec.fill_rate) * 100:.0f}% of demand short",
        ))
    findings.append(Finding(
        "Peak working capital",
        f"Inventory peaks at {rec.peak_inventory:,.0f} units (average {rec.average_inventory:,.0f}); "
        "this is the cash the plan ties up.",
        impact="size the cash / credit plan to this peak",
    ))
    alternatives = "; ".join(
        f"{ev.name} ({ev.fill_rate * 100:.0f}% fill, peak {ev.peak_inventory:,.0f}, "
        f"cost {ev.total_cost:,.0f})"
        for ev in others
    )
    findings.append(Finding(
        "Alternatives considered",
        f"Ranked against: {alternatives}.",
        impact="documented for the executive review",
    ))

    kpis = (
        Kpi("Recommended plan", rec.name,
            rationale="The strategy that best balances cost, working capital and service"),
        Kpi("Fill rate", f"{rec.fill_rate * 100:.0f}%", target="95%+",
            rationale="Share of demand served on time under the plan"),
        Kpi("Peak inventory", f"{rec.peak_inventory:,.0f} units",
            rationale="Working capital tied up at the inventory peak"),
        Kpi("Plan cost", f"{rec.total_cost:,.0f}",
            rationale="Holding + shortage + capacity-change cost over the horizon"),
        Kpi("Capacity flex", f"{rec.capacity_changes:,.0f} units", target="minimize",
            rationale="Overtime / hire-fire / expedite the plan implies"),
    )

    data_sources = (
        DataSource("Demand plan", data_source, "monthly"),
        DataSource("Cost rates (holding / shortage / capacity)", "engagement parameters", "per cycle"),
    )

    recommendations: list[str] = [
        f"Adopt the {rec.name} supply plan as the consensus operating plan."
    ]
    if gaps:
        recommendations.append(
            f"Close the demand-supply gap in {', '.join(gaps)} "
            "(pre-build, expedite, or demand-shaping)."
        )
    recommendations.append(
        "Take the plan to the executive S&OP review for sign-off before committing supply."
    )

    residual = ""
    if review.outcome.residuals:
        r = review.outcome.residuals[0]
        residual = f"{r.description}. Risk if skipped: {r.risk_if_skipped}."

    return Deliverable(
        title="S&OP / IBP Review",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        citations=tuple(citations),
        confidence=review.outcome.confidence,
        residual=residual,
        prepared=prepared,
    )
