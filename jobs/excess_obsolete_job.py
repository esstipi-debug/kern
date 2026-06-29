"""Excess & Obsolete (E&O) agent job: a stock CSV -> dead / excess classification + cash at risk.

The data-prep + deck half of the E&O tool. Reads on-hand stock (quantity, daily demand,
optional unit cost + days since last sale) with pandas directly (deliberately *not* via
jobs/intake.py, which the parallel loop owns) and classifies each SKU healthy / excess / dead
via ``src.excess_obsolete``, sizing the cash tied up in slow and non-moving stock.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.excess_obsolete import DEAD, EXCESS, SkuEO, SkuStock, classify_excess_obsolete
from src.export import write_summary_csv

_PRODUCT_COLS = ("product_id", "sku", "SKU", "item", "Product", "product")
_ONHAND_COLS = ("on_hand", "quantity", "qty", "stock", "units", "On Hand")
_DEMAND_COLS = ("daily_demand", "demand", "demand_rate", "daily_sales", "run_rate")
_COST_COLS = ("unit_cost", "cost", "Unit Cost", "price")
_LASTSALE_COLS = ("days_since_last_sale", "last_sale_days", "days_no_sale", "since_last_sale", "idle_days")

_DEFAULT_TARGET_COVER_DAYS = 90.0
_DEFAULT_DEAD_THRESHOLD_DAYS = 180.0


@dataclass(frozen=True)
class EOReport:
    n_skus: int
    n_dead: int
    n_excess: int
    n_healthy: int
    total_on_hand_value: float
    dead_value: float
    excess_value: float
    eo_value: float                 # dead_value + excess_value (cash at risk)
    eo_pct_of_value: float          # eo_value / total_on_hand_value
    lines: tuple[SkuEO, ...]        # ranked by at-risk value desc
    target_cover_days: float
    dead_threshold_days: float
    summary: str


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> dict:
    """Sniff the stock columns and build the per-SKU stock records."""
    params = params or {}
    product = _pick_column(df, params.get("product_col"), _PRODUCT_COLS)
    on_hand = _pick_column(df, params.get("on_hand_col"), _ONHAND_COLS)
    demand = _pick_column(df, params.get("demand_col"), _DEMAND_COLS)
    missing = [n for n, c in (("product_id", product), ("on_hand", on_hand), ("daily_demand", demand)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})")

    cost = _pick_column(df, params.get("cost_col"), _COST_COLS)
    last_sale = _pick_column(df, params.get("last_sale_col"), _LASTSALE_COLS)
    stocks = [
        SkuStock(
            product_id=str(row[product]),
            on_hand=float(row[on_hand]),
            daily_demand=float(row[demand]) if pd.notna(row[demand]) else 0.0,
            unit_cost=float(row[cost]) if cost and pd.notna(row[cost]) else 0.0,
            days_since_last_sale=float(row[last_sale]) if last_sale and pd.notna(row[last_sale]) else 0.0,
        )
        for _, row in df.iterrows()
    ]
    return {
        "stocks": stocks,
        "target_cover_days": float(params.get("target_cover_days", _DEFAULT_TARGET_COVER_DAYS)),
        "dead_threshold_days": float(params.get("dead_threshold_days", _DEFAULT_DEAD_THRESHOLD_DAYS)),
    }


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Read a stock CSV and build the E&O payload."""
    return prepare_records(pd.read_csv(data_path), params)


def run(payload: dict) -> EOReport:
    """Classify each SKU and roll up the cash tied up in dead + excess stock."""
    lines = classify_excess_obsolete(
        payload["stocks"], target_cover_days=payload["target_cover_days"],
        dead_threshold_days=payload["dead_threshold_days"],
    )
    dead_value = sum(e.excess_value for e in lines if e.classification == DEAD)
    excess_value = sum(e.excess_value for e in lines if e.classification == EXCESS)
    total_value = sum(e.on_hand_value for e in lines)
    eo_value = dead_value + excess_value
    n_dead = sum(1 for e in lines if e.classification == DEAD)
    n_excess = sum(1 for e in lines if e.classification == EXCESS)
    summary = (
        f"E&O over {len(lines)} SKU(s): {n_dead} dead + {n_excess} excess hold "
        f"{eo_value:,.0f} ({(eo_value / total_value * 100) if total_value > 0 else 0:.0f}% of "
        f"{total_value:,.0f} on-hand value) at risk."
    )
    return EOReport(
        n_skus=len(lines), n_dead=n_dead, n_excess=n_excess,
        n_healthy=len(lines) - n_dead - n_excess,
        total_on_hand_value=total_value, dead_value=dead_value, excess_value=excess_value,
        eo_value=eo_value, eo_pct_of_value=(eo_value / total_value) if total_value > 0 else 0.0,
        lines=tuple(lines), target_cover_days=payload["target_cover_days"],
        dead_threshold_days=payload["dead_threshold_days"], summary=summary,
    )


def verify(report: EOReport) -> list[str]:
    """QA gate: SKUs present, finite non-negative values, E&O share a valid fraction."""
    import math

    issues: list[str] = []
    if report.n_skus <= 0:
        issues.append("no SKUs to assess")
    if not math.isfinite(report.eo_value) or report.eo_value < 0:
        issues.append("E&O value is negative or non-finite")
    if not 0.0 <= report.eo_pct_of_value <= 1.0:
        issues.append(f"E&O share out of [0,1]: {report.eo_pct_of_value}")
    return issues


def write_operational(report: EOReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: the at-risk SKUs (dead/excess) by cash exposure."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "product_id": e.product_id,
            "classification": e.classification,
            "on_hand": round(e.on_hand, 1),
            "days_of_cover": ("inf" if e.days_of_cover == float("inf") else round(e.days_of_cover, 1)),
            "excess_units": round(e.excess_units, 1),
            "at_risk_value": round(e.excess_value, 2),
            "recommended_action": e.recommended_action,
        }
        for e in report.lines
    ]
    return {"csv": write_summary_csv(rows, d / "excess_obsolete.csv")}


def build_deck(
    report: EOReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> Deliverable:
    """Compose the E&O study: how much cash is stuck in dead and excess stock, and what to do."""
    summary = (
        f"Excess & obsolete over {report.n_skus} SKU(s): {report.n_dead} dead "
        f"({report.dead_value:,.0f}) + {report.n_excess} excess ({report.excess_value:,.0f}) = "
        f"{report.eo_value:,.0f} at risk, {report.eo_pct_of_value * 100:.0f}% of the "
        f"{report.total_on_hand_value:,.0f} on-hand value."
    )

    worst = report.lines[0] if report.lines else None
    findings = [
        Finding(
            "Dead stock (non-moving)",
            f"{report.n_dead} SKU(s) with no sale in {report.dead_threshold_days:.0f}+ days hold "
            f"{report.dead_value:,.0f}.",
            impact="cash and space locked up - liquidate, return to vendor, or write off",
        ),
        Finding(
            "Excess stock (over cover)",
            f"{report.n_excess} SKU(s) above the {report.target_cover_days:.0f}-day cover target hold "
            f"{report.excess_value:,.0f} of excess.",
            impact="stop buying and draw down before it ages into dead stock",
        ),
    ]
    if worst is not None and worst.excess_value > 0:
        findings.append(Finding(
            f"Largest single exposure: {worst.product_id}",
            f"{worst.classification}, {worst.excess_value:,.0f} at risk "
            f"({worst.on_hand:,.0f} on hand).",
            impact="act on this SKU first",
        ))

    kpis = (
        Kpi("SKUs", f"{report.n_skus}", rationale="Stock lines assessed"),
        Kpi("Dead SKUs", f"{report.n_dead}", target="minimize", rationale="No movement past the threshold"),
        Kpi("Excess SKUs", f"{report.n_excess}", target="minimize", rationale="Above the cover target"),
        Kpi("Cash at risk (E&O)", f"{report.eo_value:,.0f}", target="minimize",
            rationale="Dead value + excess value"),
        Kpi("E&O share of value", f"{report.eo_pct_of_value * 100:.0f}%", target="minimize",
            rationale="At-risk cash as a share of on-hand value"),
    )

    data_sources = (
        DataSource("On-hand stock, daily demand, days since last sale", "WMS / sales history", "weekly"),
        DataSource("Unit cost", "Cost master", "per cost review"),
    )

    recommendations = (
        "Liquidate / return / write off the dead stock to release the locked cash and space.",
        "Stop buying the excess SKUs and redistribute or promote them before they age to dead.",
        "Set a days-of-cover ceiling per ABC class to keep excess from rebuilding.",
    )

    return Deliverable(
        title="Excess & Obsolete (E&O) Stock",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=recommendations,
        citations=tuple(citations),
        confidence=confidence,
        residual="Disposition is a human / commercial decision: the agent delivers the ranked at-risk "
                 "list and recommended actions to approve - it does not liquidate, return or write off "
                 "stock. Confirm the cover target, the dead threshold and salvage channels before acting.",
        prepared=prepared,
    )
