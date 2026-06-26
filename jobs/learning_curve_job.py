"""Learning-curve agent job: a products CSV -> cost-down projection.

Reads products with a first-unit cost, learning rate and planned volume via pandas directly,
and projects the unit cost at volume and the total-order cost (and the saving vs. no learning)
via ``src.learning_curve``. For quoting and cost-down planning on ramping volumes.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.learning_curve import cumulative_time, unit_time

_PRODUCT_COLS = ("product", "part", "sku", "item", "id", "Product")
_FIRST_COLS = ("first_unit_cost", "first_unit", "first_cost", "unit_cost", "k", "First Unit Cost")
_RATE_COLS = ("learning_rate", "rate", "curve", "Learning Rate")
_VOLUME_COLS = ("planned_volume", "volume", "quantity", "units", "qty", "Planned Volume")


@dataclass(frozen=True)
class ProductCurve:
    product: str
    first_unit_cost: float
    learning_rate: float
    planned_volume: int
    projected_unit_cost: float    # cost of the last (volume-th) unit
    total_cost: float             # cumulative cost over the planned volume
    savings: float                # vs. no-learning baseline (first_unit_cost * volume)


@dataclass(frozen=True)
class LearningReport:
    products: tuple[ProductCurve, ...]    # sorted by savings desc
    n_products: int
    total_cost: float
    total_savings: float


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> list[dict]:
    """Sniff the product + first-unit-cost / learning-rate / volume columns."""
    params = params or {}
    product = _pick_column(df, params.get("product_col"), _PRODUCT_COLS)
    first = _pick_column(df, params.get("first_col"), _FIRST_COLS)
    rate = _pick_column(df, params.get("rate_col"), _RATE_COLS)
    volume = _pick_column(df, params.get("volume_col"), _VOLUME_COLS)
    missing = [n for n, c in (("product", product), ("first_unit_cost", first),
                              ("learning_rate", rate), ("planned_volume", volume)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})")
    return [
        {"product": str(row[product]), "first_unit_cost": float(row[first]),
         "learning_rate": float(row[rate]), "planned_volume": int(row[volume])}
        for _, row in df.iterrows()
    ]


def prepare(data_path: str, params: dict | None = None) -> list[dict]:
    """Read a products CSV and build the learning-curve records."""
    return prepare_records(pd.read_csv(data_path), params)


def run(records: list[dict]) -> LearningReport:
    """Project unit and total cost at the planned volume, and the saving vs. no learning."""
    curves: list[ProductCurve] = []
    for r in records:
        vol = max(1, r["planned_volume"])
        projected = unit_time(r["first_unit_cost"], vol, r["learning_rate"])
        total = cumulative_time(r["first_unit_cost"], vol, r["learning_rate"])
        baseline = r["first_unit_cost"] * vol
        curves.append(ProductCurve(
            product=r["product"], first_unit_cost=r["first_unit_cost"], learning_rate=r["learning_rate"],
            planned_volume=vol, projected_unit_cost=projected, total_cost=total, savings=baseline - total,
        ))
    curves.sort(key=lambda c: c.savings, reverse=True)
    return LearningReport(
        products=tuple(curves),
        n_products=len(curves),
        total_cost=sum(c.total_cost for c in curves),
        total_savings=sum(c.savings for c in curves),
    )


def verify(report: LearningReport) -> list[str]:
    """QA gate: products present."""
    return [] if report.n_products > 0 else ["no products to project"]


def write_operational(report: LearningReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: per-product cost-down projection."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {"product": c.product, "learning_rate": c.learning_rate, "planned_volume": c.planned_volume,
         "projected_unit_cost": round(c.projected_unit_cost, 2), "total_cost": round(c.total_cost, 2),
         "savings_vs_no_learning": round(c.savings, 2)}
        for c in report.products
    ]
    return {"csv": write_summary_csv(rows, d / "learning_curve.csv")}


def build_deck(
    report: LearningReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> Deliverable:
    """Compose the cost-down study: where volume buys the most unit-cost reduction."""
    summary = (
        f"Cost-down across {report.n_products} product(s): {report.total_cost:,.0f} total at the "
        f"planned volumes, {report.total_savings:,.0f} saved vs. no learning."
    )
    findings = [
        Finding(
            "Learning drives the unit cost down with volume",
            f"Total order cost {report.total_cost:,.0f} vs a flat {report.total_cost + report.total_savings:,.0f} - "
            f"{report.total_savings:,.0f} comes from the learning curve.",
            impact="commit volume where the curve is steepest to capture it",
        ),
    ]
    if report.products:
        t = report.products[0]
        findings.append(Finding(
            f"Biggest cost-down: {t.product}",
            f"Unit cost falls to {t.projected_unit_cost:,.2f} by unit {t.planned_volume} "
            f"(rate {t.learning_rate:.0%}); saves {t.savings:,.0f}.",
            impact="prioritize volume / negotiation here",
        ))
    kpis = (
        Kpi("Products", str(report.n_products), rationale="Products projected"),
        Kpi("Total cost", f"{report.total_cost:,.0f}", target="minimize",
            rationale="Cumulative cost over the planned volumes"),
        Kpi("Savings vs no learning", f"{report.total_savings:,.0f}", target="maximize",
            rationale="Cost-down captured by the curve"),
    )
    data_sources = (
        DataSource("Products (first-unit cost / learning rate / planned volume)", "cost engineering", "per run"),
    )
    recommendations = [
        "Lock in the volume commitments that capture the steepest cost-down.",
        "Quote using the projected unit cost at volume, not the first-unit cost.",
        "Negotiate a steeper learning rate (process improvement) on the high-savings products.",
    ]
    return Deliverable(
        title="Learning-Curve Cost-Down",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        citations=tuple(citations),
        confidence=confidence,
        residual="The learning rate is an estimate from history - confirm it holds at the new "
                 "volume before committing the cost-down to a quote.",
        prepared=prepared,
    )
