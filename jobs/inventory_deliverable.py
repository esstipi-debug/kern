"""Compose a client-ready deliverable from an inventory-optimization JobReport.

Bridges the flagship engine output (`jobs.inventory_optimization.JobReport`) into
the deliverable composer (`src.deliverable`): turns SKU recommendations and
portfolio numbers into quantified findings, a KPI table with rationale, a
data-source map, and action recommendations. Additive — does not modify the
existing tool/deliverables path; a caller opts in.
"""
from __future__ import annotations

from collections.abc import Sequence

from src.deliverable import DataSource, Deliverable, Finding, Kpi

from .inventory_optimization import JobReport


def build(
    report: JobReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: Sequence[str] = (),
    residual: str = "",
    confidence: float | None = None,
    data_source: str = "demand history (intake)",
) -> Deliverable:
    """Build a Deliverable from an inventory-optimization report."""
    sl = report.params.get("service_level", 0.95)
    summary = (
        f"Analyzed {report.n_skus} SKUs; recommended ${report.final_investment:,.0f} "
        f"inventory investment at {sl * 100:.0f}% cycle service level."
    )

    findings: list[Finding] = []
    if report.n_at_risk:
        findings.append(Finding(
            f"{report.n_at_risk} SKUs with high forecast bias",
            "Forecast persistently diverges from actuals, so the sized policy may be off.",
            impact="review before ordering",
        ))
    if report.n_intermittent:
        findings.append(Finding(
            f"{report.n_intermittent} intermittent-demand SKUs",
            "Sporadic demand; placed on periodic (R, S) review instead of (s, Q).",
            impact="avoids over-stocking slow movers",
        ))
    if report.budget is not None and report.safety_stock_scale < 1.0:
        findings.append(Finding(
            "Budget-constrained safety stock",
            f"Safety stock scaled to {report.safety_stock_scale * 100:.0f}% to fit the "
            f"${report.budget:,.0f} budget.",
            impact="service drops below target on the tail",
        ))
    top = sorted(report.recommendations, key=lambda r: r.investment, reverse=True)[:3]
    if top:
        names = ", ".join(f"{r.product_id} (${r.investment:,.0f})" for r in top)
        findings.append(Finding(
            "Inventory concentration",
            f"Largest investments: {names}.",
            impact="focus cycle counts and review cadence here",
        ))

    kpis = (
        Kpi("SKUs analyzed", str(report.n_skus), rationale="Catalog coverage of the analysis"),
        Kpi("Recommended investment", f"${report.final_investment:,.0f}",
            target=(f"<= ${report.budget:,.0f}" if report.budget is not None else ""),
            rationale="Capital tied up in cycle + safety stock"),
        Kpi("Cycle service level", f"{sl * 100:.0f}%", target="95%+",
            rationale="Probability of not stocking out within a replenishment cycle"),
        Kpi("High-bias SKUs", str(report.n_at_risk), target="0",
            rationale="Forecast-quality risk that distorts policy sizing"),
        Kpi("Intermittent SKUs", str(report.n_intermittent),
            rationale="Slow / sporadic movers managed on periodic review"),
    )

    data_sources = (
        DataSource("Demand history & unit cost", data_source, "per run"),
        DataSource("Service level / holding rate / order cost", "engagement parameters", "per run"),
    )

    recs: list[str] = []
    if report.n_at_risk:
        recs.append("Review the high-bias SKUs' forecasts before issuing their POs.")
    recs.append("Issue replenishment POs to the computed reorder points and order quantities.")
    if report.budget is not None and not report.feasible:
        recs.append(
            f"Budget ${report.budget:,.0f} is below the cycle-stock floor "
            f"(${report.cycle_floor:,.0f}) - raise the budget or trim SKUs."
        )

    return Deliverable(
        title="Inventory Optimization",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recs),
        citations=tuple(citations),
        confidence=confidence,
        residual=residual,
        prepared=prepared,
    )
