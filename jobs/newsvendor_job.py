"""Single-period (newsvendor) agent job: a per-SKU CSV -> one-shot order plan.

The data-prep + deck half of the newsvendor tool. Reads per-SKU single-period demand
(mean + std) with price/cost (and optional salvage / goodwill) using pandas directly
(deliberately *not* via jobs/intake.py, which the parallel loop owns), and sizes the
profit-maximizing one-shot order quantity per SKU via the critical-ratio rule over a
normal-approximation demand PMF (``src.newsvendor`` + ``src.discrete_demand``). Built for
perishable / seasonal / fashion / spare-part demand that does NOT repeat.

``salvage_value`` defaults to 0, ``goodwill`` (extra stock-out penalty) to 0. A SKU that
sells at or below cost (no underage incentive) is left un-stocked (Q = 0).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.discrete_demand import DiscretePMF
from src.export import write_summary_csv
from src.newsvendor import optimal_newsvendor_discrete

_PRODUCT_COLS = ("product_id", "sku", "SKU", "item", "Product", "product")
_MEAN_COLS = ("mean_demand", "mean", "avg_demand", "demand_mean", "expected_demand", "forecast")
_STD_COLS = ("std_demand", "std", "sigma", "demand_std", "stdev", "demand_sigma")
_PRICE_COLS = ("price", "unit_price", "sell_price", "selling_price", "Price")
_UNIT_COST_COLS = ("unit_cost", "cost", "Unit Cost", "buy_cost", "Cost")
_SALVAGE_COLS = ("salvage_value", "salvage", "Salvage")
_GOODWILL_COLS = ("goodwill", "shortage_penalty", "stockout_penalty")

# Demand is approximated by a normal over +/- this many sigma when building the PMF.
_N_SIGMA = 4


@dataclass(frozen=True)
class NewsvendorLine:
    product_id: str
    optimal_quantity: float
    critical_ratio: float
    expected_profit: float
    mean_demand: float
    std_demand: float
    price: float
    unit_cost: float


@dataclass(frozen=True)
class NewsvendorReport:
    n_skus: int
    lines: tuple[NewsvendorLine, ...]      # ranked by expected profit desc
    total_order_qty: float
    total_expected_profit: float
    scarcest_product: str                  # highest critical ratio (protect availability)
    thinnest_product: str                  # lowest critical ratio (overage risk dominates)
    avg_critical_ratio: float
    summary: str


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> list[dict]:
    """Sniff the per-SKU single-period columns and build one record per line."""
    params = params or {}
    product = _pick_column(df, params.get("product_col"), _PRODUCT_COLS)
    mean = _pick_column(df, params.get("mean_col"), _MEAN_COLS)
    price = _pick_column(df, params.get("price_col"), _PRICE_COLS)
    unit_cost = _pick_column(df, params.get("unit_cost_col"), _UNIT_COST_COLS)
    missing = [n for n, c in (("product_id", product), ("mean_demand", mean),
                              ("price", price), ("unit_cost", unit_cost)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})")

    std = _pick_column(df, params.get("std_col"), _STD_COLS)
    salvage = _pick_column(df, params.get("salvage_col"), _SALVAGE_COLS)
    goodwill = _pick_column(df, params.get("goodwill_col"), _GOODWILL_COLS)
    return [
        {
            "product_id": str(row[product]),
            "mean_demand": float(row[mean]),
            "std_demand": float(row[std]) if std and pd.notna(row[std]) else 0.0,
            "price": float(row[price]),
            "unit_cost": float(row[unit_cost]),
            "salvage_value": float(row[salvage]) if salvage and pd.notna(row[salvage]) else 0.0,
            "goodwill": float(row[goodwill]) if goodwill and pd.notna(row[goodwill]) else 0.0,
        }
        for _, row in df.iterrows()
    ]


def prepare(data_path: str, params: dict | None = None) -> list[dict]:
    """Read a single-period CSV and build the per-SKU records."""
    return prepare_records(pd.read_csv(data_path), params)


def _normal_pmf(mean: float, std: float) -> DiscretePMF:
    """Discrete integer PMF approximating Normal(mean, std), clipped at zero demand."""
    if std <= 0:
        v = max(0, int(round(mean)))
        return DiscretePMF(values=np.array([v], dtype=int), probabilities=np.array([1.0]))
    lo = max(0, int(np.floor(mean - _N_SIGMA * std)))
    hi = int(np.ceil(mean + _N_SIGMA * std))
    grid = np.arange(lo, hi + 1, dtype=int)
    weights = norm.pdf(grid, loc=mean, scale=std)
    total = float(weights.sum())
    probs = weights / total if total > 0 else np.full(len(grid), 1.0 / len(grid))
    return DiscretePMF(values=grid, probabilities=probs)


def _solve_line(rec: dict) -> NewsvendorLine:
    mean = rec["mean_demand"]
    std = rec["std_demand"]
    price = rec["price"]
    cost = rec["unit_cost"]
    # Keep overage cost non-negative: a salvage above cost would invert the model.
    salvage = min(rec["salvage_value"], cost)
    goodwill = rec["goodwill"]
    underage = price - cost + goodwill          # cu: lost margin (+ goodwill) per short unit
    if underage <= 0 or mean <= 0:
        # No incentive to stock (sells at/below cost) or no demand -> order nothing.
        return NewsvendorLine(rec["product_id"], 0.0, 0.0, 0.0, mean, std, price, cost)
    result = optimal_newsvendor_discrete(_normal_pmf(mean, std), price, cost, salvage, goodwill)
    return NewsvendorLine(
        product_id=rec["product_id"],
        optimal_quantity=result.optimal_quantity,
        critical_ratio=result.critical_ratio,
        expected_profit=result.expected_profit,
        mean_demand=mean, std_demand=std, price=price, unit_cost=cost,
    )


def run(records: list[dict]) -> NewsvendorReport:
    """Size the one-shot order per SKU and roll up the order/profit totals."""
    lines = sorted((_solve_line(r) for r in records),
                   key=lambda ln: ln.expected_profit, reverse=True)
    total_qty = sum(ln.optimal_quantity for ln in lines)
    total_profit = sum(ln.expected_profit for ln in lines)
    stocked = [ln for ln in lines if ln.optimal_quantity > 0]
    avg_cr = sum(ln.critical_ratio for ln in stocked) / len(stocked) if stocked else 0.0
    scarcest = max(lines, key=lambda ln: ln.critical_ratio).product_id if lines else "n/a"
    thinnest = min(stocked, key=lambda ln: ln.critical_ratio).product_id if stocked else "n/a"
    summary = (
        f"Single-period order for {len(lines)} SKU(s): {total_qty:,.0f} unit(s), "
        f"{total_profit:,.0f} expected profit."
    )
    return NewsvendorReport(
        n_skus=len(lines), lines=tuple(lines), total_order_qty=total_qty,
        total_expected_profit=total_profit, scarcest_product=scarcest,
        thinnest_product=thinnest, avg_critical_ratio=avg_cr, summary=summary,
    )


def verify(report: NewsvendorReport) -> list[str]:
    """QA gate: SKUs present, critical ratios are valid fractions, quantities non-negative."""
    issues: list[str] = []
    if report.n_skus <= 0:
        issues.append("no SKUs to size")
    for ln in report.lines:
        if not 0.0 <= ln.critical_ratio <= 1.0:
            issues.append(f"{ln.product_id}: critical ratio out of [0,1]: {ln.critical_ratio}")
        if ln.optimal_quantity < 0:
            issues.append(f"{ln.product_id}: negative order quantity")
        if not np.isfinite(ln.expected_profit):
            issues.append(f"{ln.product_id}: non-finite expected profit")
    return issues


def write_operational(report: NewsvendorReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: the order book (qty + implied service per SKU)."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "product_id": ln.product_id,
            "order_quantity": round(ln.optimal_quantity),
            "in_stock_target_pct": round(ln.critical_ratio * 100, 1),
            "expected_profit": round(ln.expected_profit, 2),
        }
        for ln in report.lines
    ]
    return {"csv": write_summary_csv(rows, d / "newsvendor.csv")}


def build_deck(
    report: NewsvendorReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> Deliverable:
    """Compose the single-period order study: what to buy for a one-shot window and why."""
    summary = (
        f"Single-period (newsvendor) order across {report.n_skus} SKU(s): "
        f"{report.total_order_qty:,.0f} unit(s) for {report.total_expected_profit:,.0f} expected profit, "
        f"at an average {report.avg_critical_ratio * 100:.0f}% in-stock target."
    )

    findings = [
        Finding(
            "Recommended one-shot order",
            f"Order {report.total_order_qty:,.0f} unit(s) across {report.n_skus} SKU(s); "
            f"expected profit {report.total_expected_profit:,.0f}.",
            impact="unsold units only recover salvage - commit before the selling window",
        ),
        Finding(
            f"Highest in-stock priority: {report.scarcest_product}",
            "Critical ratio is highest here - a stock-out costs more than overstock.",
            impact="protect availability on this SKU first",
        ),
        Finding(
            f"Thinnest margin of safety: {report.thinnest_product}",
            "Overage risk dominates - order conservatively where salvage is low.",
            impact="trim the order to limit write-offs",
        ),
    ]

    kpis = (
        Kpi("SKUs", f"{report.n_skus}", rationale="Single-period items sized"),
        Kpi("Total order units", f"{report.total_order_qty:,.0f}", target="optimize",
            rationale="Sum of the critical-ratio-optimal order quantities"),
        Kpi("Expected profit", f"{report.total_expected_profit:,.0f}", target="maximize",
            rationale="Expected profit at the recommended quantities"),
        Kpi("Avg in-stock target", f"{report.avg_critical_ratio * 100:.0f}%", target="balance",
            rationale="Critical ratio = implied service level for one-shot demand"),
    )

    data_sources = (
        DataSource("Per-SKU demand mean/std", "Demand history / S&OP forecast", "per selling window"),
        DataSource("Price / unit cost / salvage", "Cost master + commercial terms", "per buy"),
    )

    recommendations = (
        "Commit the recommended quantities before the order deadline - single-period demand does not repeat.",
        "Open salvage / markdown channels to lower overage cost on the thin-margin SKUs.",
        "Tighten the demand-std estimate on the high-value SKUs - it drives the order quantity most.",
    )

    return Deliverable(
        title="Single-Period (Newsvendor) Order Plan",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=recommendations,
        citations=tuple(citations),
        confidence=confidence,
        residual="Single-period decision: confirm the demand distribution, price, cost and "
                 "salvage with the buyer, and that demand truly does not repeat "
                 "(perishable / seasonal / one-shot), before committing the order.",
        prepared=prepared,
    )
