"""Simulation agent job: a per-SKU CSV -> Monte-Carlo-optimized (R,S) policy.

The data-prep + deck half of the simulation tool. Reads per-SKU demand + cost parameters with
pandas directly (deliberately *not* via jobs/intake.py, which the parallel loop owns) and, per
SKU, finds the safety stock / order-up-to level that minimizes simulated total cost (holding +
ordering + backorder) via ``src.simulation_opt`` - starting from the analytical (R,S) optimum
and refining by simulation. Reports the cost vs the analytical policy, so the value-add of the
Monte-Carlo search is explicit. Compute-heavy: each SKU runs many full simulations.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.simulation_opt import find_best_safety_stock_smart_start, simulate_rs_cost

_PRODUCT_COLS = ("product_id", "sku", "SKU", "item", "Product", "product")
_MEAN_COLS = ("mean_demand", "mean", "demand_mean", "avg_demand", "demand")
_STD_COLS = ("std_demand", "std", "sigma", "demand_std", "stdev")
_LEAD_COLS = ("lead_time", "lead_time_periods", "lead", "lt")
_HOLDING_COLS = ("holding_cost", "holding_cost_per_period", "holding", "h")
_ORDER_COLS = ("order_cost", "fixed_order_cost", "ordering_cost", "setup_cost")
_BACKORDER_COLS = ("backorder_cost", "shortage_cost", "stockout_cost", "penalty")
_REVIEW_COLS = ("review_period", "review", "R")

_DEFAULT_HOLDING = 1.0
_DEFAULT_ORDER = 100.0
_DEFAULT_BACKORDER = 5.0
_DEFAULT_REVIEW = 1
_DEFAULT_PERIODS = 3_000


@dataclass(frozen=True)
class SkuSimResult:
    product_id: str
    recommended_safety_stock: float
    order_up_to_level: float
    total_cost: float
    fill_rate: float
    holding_cost: float
    ordering_cost: float
    backorder_cost: float
    analytical_safety_stock: float
    analytical_cost: float
    cost_saving: float            # analytical_cost - total_cost


@dataclass(frozen=True)
class SimulationReport:
    n_skus: int
    periods: int
    lines: tuple[SkuSimResult, ...]
    total_optimized_cost: float
    total_analytical_cost: float
    total_saving: float
    mean_fill_rate: float
    summary: str


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> dict:
    """Sniff the per-SKU demand + cost columns; costs/review fall back to params."""
    params = params or {}
    product = _pick_column(df, params.get("product_col"), _PRODUCT_COLS)
    mean = _pick_column(df, params.get("mean_col"), _MEAN_COLS)
    std = _pick_column(df, params.get("std_col"), _STD_COLS)
    lead = _pick_column(df, params.get("lead_col"), _LEAD_COLS)
    missing = [n for n, c in (("product_id", product), ("mean_demand", mean),
                              ("std_demand", std), ("lead_time", lead)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})")

    holding = _pick_column(df, params.get("holding_col"), _HOLDING_COLS)
    order = _pick_column(df, params.get("order_col"), _ORDER_COLS)
    backorder = _pick_column(df, params.get("backorder_col"), _BACKORDER_COLS)
    review = _pick_column(df, params.get("review_col"), _REVIEW_COLS)

    def _val(row, col, default):
        return float(row[col]) if col and pd.notna(row[col]) else float(default)

    records = [
        {
            "product_id": str(row[product]),
            "mean_demand": float(row[mean]),
            "std_demand": float(row[std]),
            "lead_time_periods": max(1, int(round(float(row[lead])))),
            "holding_cost_per_period": _val(row, holding, params.get("holding_cost", _DEFAULT_HOLDING)),
            "fixed_order_cost": _val(row, order, params.get("order_cost", _DEFAULT_ORDER)),
            "backorder_cost": _val(row, backorder, params.get("backorder_cost", _DEFAULT_BACKORDER)),
            "review_period": max(1, int(round(_val(row, review, params.get("review_period", _DEFAULT_REVIEW))))),
        }
        for _, row in df.iterrows()
    ]
    return {"records": records, "periods": int(params.get("periods", _DEFAULT_PERIODS))}


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Read a per-SKU CSV and build the simulation payload."""
    return prepare_records(pd.read_csv(data_path), params)


def _optimize_sku(rec: dict, periods: int) -> SkuSimResult:
    sim_best, start_ss = find_best_safety_stock_smart_start(
        rec["mean_demand"], rec["std_demand"], rec["lead_time_periods"], rec["review_period"],
        rec["holding_cost_per_period"], rec["fixed_order_cost"], rec["backorder_cost"],
        periods=periods,
    )
    mu_cycle = rec["mean_demand"] * (rec["review_period"] + rec["lead_time_periods"])
    analytical = simulate_rs_cost(
        mu_cycle + start_ss, rec["lead_time_periods"], rec["review_period"],
        mean_demand=rec["mean_demand"], std_demand=rec["std_demand"],
        holding_cost_per_period=rec["holding_cost_per_period"],
        fixed_order_cost=rec["fixed_order_cost"], backorder_cost=rec["backorder_cost"],
        periods=periods,
    )
    return SkuSimResult(
        product_id=rec["product_id"], recommended_safety_stock=sim_best.safety_stock,
        order_up_to_level=sim_best.order_up_to_level, total_cost=sim_best.total_cost,
        fill_rate=sim_best.fill_rate, holding_cost=sim_best.holding_cost,
        ordering_cost=sim_best.ordering_cost, backorder_cost=sim_best.backorder_cost,
        analytical_safety_stock=start_ss, analytical_cost=analytical.total_cost,
        cost_saving=analytical.total_cost - sim_best.total_cost,
    )


def run(payload: dict) -> SimulationReport:
    """Simulation-optimize each SKU's (R,S) policy and roll up the cost vs the analytical policy."""
    periods = payload["periods"]
    lines = [_optimize_sku(rec, periods) for rec in payload["records"]]
    total_opt = sum(ln.total_cost for ln in lines)
    total_ana = sum(ln.analytical_cost for ln in lines)
    mean_fill = sum(ln.fill_rate for ln in lines) / len(lines) if lines else 0.0
    summary = (
        f"Simulation-optimized (R,S) for {len(lines)} SKU(s) over {periods:,} periods: "
        f"{total_opt:,.0f} cost vs {total_ana:,.0f} analytical "
        f"({total_ana - total_opt:,.0f} saved), {mean_fill * 100:.0f}% mean fill."
    )
    return SimulationReport(
        n_skus=len(lines), periods=periods, lines=tuple(lines),
        total_optimized_cost=total_opt, total_analytical_cost=total_ana,
        total_saving=total_ana - total_opt, mean_fill_rate=mean_fill, summary=summary,
    )


def verify(report: SimulationReport) -> list[str]:
    """QA gate: SKUs present, finite costs, fill rates are valid fractions."""
    import math

    issues: list[str] = []
    if report.n_skus <= 0:
        issues.append("no SKUs to simulate")
    for ln in report.lines:
        if not math.isfinite(ln.total_cost) or ln.total_cost < 0:
            issues.append(f"{ln.product_id}: invalid total cost")
        if not 0.0 <= ln.fill_rate <= 1.0:
            issues.append(f"{ln.product_id}: fill rate out of [0,1]: {ln.fill_rate}")
    return issues


def write_operational(report: SimulationReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: the simulation-optimized policy per SKU."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "product_id": ln.product_id,
            "safety_stock": round(ln.recommended_safety_stock, 1),
            "order_up_to_level": round(ln.order_up_to_level, 1),
            "total_cost": round(ln.total_cost, 2),
            "fill_rate": round(ln.fill_rate, 4),
            "analytical_cost": round(ln.analytical_cost, 2),
            "cost_saving": round(ln.cost_saving, 2),
        }
        for ln in report.lines
    ]
    return {"csv": write_summary_csv(rows, d / "simulation.csv")}


def build_deck(
    report: SimulationReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> Deliverable:
    """Compose the simulation study: the Monte-Carlo-optimized policy and its cost edge."""
    summary = (
        f"Simulation-optimized (R,S) policy over {report.n_skus} SKU(s) ({report.periods:,} periods): "
        f"{report.total_optimized_cost:,.0f} total cost vs {report.total_analytical_cost:,.0f} "
        f"analytical - {report.total_saving:,.0f} saved at {report.mean_fill_rate * 100:.0f}% mean fill."
    )

    findings = [
        Finding(
            "Simulation vs analytical policy",
            f"Monte-Carlo search lands at {report.total_optimized_cost:,.0f} total cost vs the "
            f"analytical {report.total_analytical_cost:,.0f}.",
            impact=f"{report.total_saving:,.0f} cost saved by validating the policy under simulated demand",
        ),
        Finding(
            "Service achieved",
            f"Mean fill rate {report.mean_fill_rate * 100:.0f}% at the recommended order-up-to levels.",
            impact="confirms the cost-optimal policy still meets service under variability",
        ),
    ]

    kpis = (
        Kpi("SKUs", f"{report.n_skus}", rationale="SKUs simulation-optimized"),
        Kpi("Simulation periods", f"{report.periods:,}", rationale="Length of each Monte-Carlo run"),
        Kpi("Optimized cost", f"{report.total_optimized_cost:,.0f}", target="minimize",
            rationale="Total simulated cost at the optimized policy"),
        Kpi("Saving vs analytical", f"{report.total_saving:,.0f}", target="maximize",
            rationale="Cost edge of the simulation search over the analytical optimum"),
        Kpi("Mean fill rate", f"{report.mean_fill_rate * 100:.0f}%", target="maximize",
            rationale="Service achieved at the recommended policy"),
    )

    data_sources = (
        DataSource("Per-SKU demand (mean/std) + lead time", "Demand history / forecast", "per planning cycle"),
        DataSource("Holding / order / backorder costs", "Finance + ops cost model", "per cost review"),
    )

    recommendations = (
        "Set each SKU's order-up-to level to the simulation-optimized value in the plan CSV.",
        "Re-run with more periods / alternate seeds on the high-value SKUs before committing.",
        "Adopt the change where the cost saving is material; keep the analytical policy elsewhere.",
    )

    return Deliverable(
        title="Simulation-Optimized (R,S) Policy",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=recommendations,
        citations=tuple(citations),
        confidence=confidence,
        residual="Simulation results depend on the demand and cost assumptions: confirm the "
                 "demand distribution, lead time and the holding/order/backorder costs, and re-run "
                 "with more periods on the high-value SKUs before committing the policy.",
        prepared=prepared,
    )
