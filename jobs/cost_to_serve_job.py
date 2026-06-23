"""Cost-to-serve agent job: raw order lines -> segment activity -> CFO analysis.

The data-prep half of the cost-to-serve tool. Aggregates an order-line CSV into
per-segment activity using pandas directly (deliberately *not* via jobs/intake.py,
which the parallel loop owns), runs the cost-to-serve + working-capital analysis,
and QA-gates the result. The deliverable composition stays in
``jobs/cost_to_serve_deliverable`` (the premium deck) + ``write_operational`` (the CSV).

Pure-ish: ``aggregate_segments`` / ``run`` / ``verify`` are deterministic; ``prepare``
reads a file. Column names are sniffed from common headers and overridable via params.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.cost_to_serve import (
    CostToServePortfolio,
    SegmentActivity,
    ServiceCostRates,
    analyze_portfolio,
)
from src.export import write_summary_csv
from src.working_capital import CashReleasePlan, WorkingCapital, cash_release_plan, working_capital

# Default header candidates per field; first match wins. Overridable via params.
_SEGMENT_COLS = ("Customer Segment", "Segment", "segment", "Channel", "channel", "customer_segment")
_REVENUE_COLS = ("Sales", "sales", "Revenue", "revenue", "net_sales")
_QTY_COLS = ("Quantity", "quantity", "Units", "units", "qty")
_ORDER_COLS = ("Order ID", "order_id", "OrderID", "order id", "order")
_COGS_COLS = ("COGS", "cogs", "Cost", "cost", "cost_of_goods")
_RETURNS_COLS = ("returns_units", "Returns", "returns", "return_qty")
_FREIGHT_COLS = ("Shipping Cost", "freight", "Freight", "outbound_freight", "shipping")


@dataclass(frozen=True)
class CostToServeReport:
    """What the tool's run stage produces: the portfolio + optional cash lens + summary."""

    portfolio: CostToServePortfolio
    working_cap: WorkingCapital | None
    cash_release: CashReleasePlan | None
    summary: str


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def aggregate_segments(
    df: pd.DataFrame,
    *,
    segment_col: str,
    revenue_col: str,
    qty_col: str | None = None,
    order_col: str | None = None,
    cogs_col: str | None = None,
    returns_col: str | None = None,
    freight_col: str | None = None,
    cost_ratio: float = 0.6,
) -> list[SegmentActivity]:
    """Roll order lines up to one SegmentActivity per segment.

    COGS falls back to ``revenue * cost_ratio`` when no cost column exists; orders fall
    back to the line count when there is no order id.
    """
    out: list[SegmentActivity] = []
    for seg, g in df.groupby(segment_col):
        revenue = float(g[revenue_col].sum())
        out.append(SegmentActivity(
            segment=str(seg),
            revenue=revenue,
            units=float(g[qty_col].sum()) if qty_col else 0.0,
            orders=float(g[order_col].nunique()) if order_col else float(len(g)),
            cogs=float(g[cogs_col].sum()) if cogs_col else revenue * cost_ratio,
            returns_units=float(g[returns_col].sum()) if returns_col else 0.0,
            outbound_freight=float(g[freight_col].sum()) if freight_col else 0.0,
        ))
    return out


def prepare(data_path: str, params: dict | None = None) -> list[SegmentActivity]:
    """Read an order/sales CSV and aggregate it to per-segment activity.

    Raises ``FileNotFoundError`` if the file is missing and ``ValueError`` (naming the
    params to set) when the segment or revenue column cannot be located.
    """
    params = params or {}
    df = pd.read_csv(data_path)

    segment = _pick_column(df, params.get("segment_col"), _SEGMENT_COLS)
    revenue = _pick_column(df, params.get("revenue_col"), _REVENUE_COLS)
    missing = [name for name, col in (("segment_col", segment), ("revenue_col", revenue)) if col is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})")

    return aggregate_segments(
        df,
        segment_col=segment,
        revenue_col=revenue,
        qty_col=_pick_column(df, params.get("qty_col"), _QTY_COLS),
        order_col=_pick_column(df, params.get("order_col"), _ORDER_COLS),
        cogs_col=_pick_column(df, params.get("cogs_col"), _COGS_COLS),
        returns_col=_pick_column(df, params.get("returns_col"), _RETURNS_COLS),
        freight_col=_pick_column(df, params.get("freight_col"), _FREIGHT_COLS),
        cost_ratio=float(params.get("cost_ratio", 0.6)),
    )


def run(
    activities: list[SegmentActivity],
    *,
    rates: ServiceCostRates | None = None,
    dio: float | None = None,
    dso: float | None = None,
    dpo: float | None = None,
    dio_days: float = 0.0,
    dso_days: float = 0.0,
    dpo_days: float = 0.0,
) -> CostToServeReport:
    """Run the cost-to-serve analysis, adding the working-capital lens when DIO/DSO/DPO
    are supplied and a cash-release plan when any improvement days are given."""
    portfolio = analyze_portfolio(activities, rates or ServiceCostRates())

    wc: WorkingCapital | None = None
    cr: CashReleasePlan | None = None
    if None not in (dio, dso, dpo):
        total_cogs = sum(a.cogs for a in activities)
        wc = working_capital(revenue=portfolio.total_revenue, cogs=total_cogs, dio=dio, dso=dso, dpo=dpo)
        if dio_days or dso_days or dpo_days:
            cr = cash_release_plan(revenue=portfolio.total_revenue, cogs=total_cogs,
                                   dio_days=dio_days, dso_days=dso_days, dpo_days=dpo_days)

    summary = (
        f"Cost-to-serve across {len(portfolio.segments)} segments; net-to-serve margin "
        f"{portfolio.overall_net_margin * 100:.0f}%, {len(portfolio.loss_making)} loss-making."
    )
    return CostToServeReport(portfolio=portfolio, working_cap=wc, cash_release=cr, summary=summary)


def verify(report: CostToServeReport) -> list[str]:
    """QA gate: a usable cost-to-serve report has segments and finite numbers."""
    issues: list[str] = []
    if not report.portfolio.segments:
        issues.append("no segments to analyze")
    for s in report.portfolio.segments:
        if not math.isfinite(s.net_to_serve) or not math.isfinite(s.net_margin_pct):
            issues.append(f"non-finite cost-to-serve for segment {s.segment}")
    return issues


def write_operational(report: CostToServeReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: one row per segment with its full P&L."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "segment": s.segment,
            "revenue": round(s.revenue, 2),
            "product_cost": round(s.product_cost, 2),
            "fulfillment_cost": round(s.fulfillment_cost, 2),
            "returns_cost": round(s.returns_cost, 2),
            "overhead_cost": round(s.overhead_cost, 2),
            "total_cost_to_serve": round(s.total_cost_to_serve, 2),
            "net_to_serve": round(s.net_to_serve, 2),
            "net_margin_pct": round(s.net_margin_pct, 4),
        }
        for s in report.portfolio.segments
    ]
    return {"csv": write_summary_csv(rows, d / "cost_to_serve.csv")}
