"""What-if agent job: a drivers CSV -> sensitivity sweep of the inventory policy cost.

The data-prep + deck half of the what-if tool. Reads a drivers table (driver, base, low,
high) with pandas directly (deliberately *not* via jobs/intake.py, which the parallel loop
owns), sweeps each driver over its band against the EOQ + safety-stock policy-cost model
(``src.whatif`` over ``src.eoq`` + ``src.safety_stock``), and composes the study deck inline:
which assumption threatens cost the most (tornado), the optimistic/pessimistic corners, and
where cost breaks the budget (break-even). Unlisted model inputs fall back to sane defaults.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.eoq import compute_eoq
from src.export import write_summary_csv
from src.safety_stock import safety_stock
from src.whatif import (
    Driver,
    break_even,
    optimistic_case,
    pessimistic_case,
    tornado,
)

# The model the what-if sweep runs over: the (Q*, Ss) policy and its annual cost.
MODEL_INPUTS = (
    "annual_demand", "holding_cost", "fixed_order_cost", "demand_std", "service_level", "lead_time",
)
_DEFAULTS: dict[str, float] = {
    "annual_demand": 12_000.0,
    "holding_cost": 3.0,
    "fixed_order_cost": 75.0,
    "demand_std": 40.0,
    "service_level": 0.95,
    "lead_time": 2.0,
}

_DRIVER_COLS = ("driver", "parameter", "name", "assumption", "Driver", "Parameter")
_BASE_COLS = ("base", "baseline", "expected", "Base", "value", "Value")
_LOW_COLS = ("low", "min", "lower", "Low", "Min")
_HIGH_COLS = ("high", "max", "upper", "High", "Max")
_UNIT_COLS = ("unit", "units", "Unit")


def policy_model(inp: dict) -> dict:
    """EOQ cycle cost + safety-stock holding -> annual policy cost and its drivers."""
    eoq = compute_eoq(inp["annual_demand"], inp["holding_cost"], inp["fixed_order_cost"])
    ss = safety_stock(inp["demand_std"], inp["service_level"], inp["lead_time"])
    annual_cost = eoq.optimal_total_cost + inp["holding_cost"] * ss.safety_stock
    return {
        "annual_cost": annual_cost,
        "order_quantity": eoq.order_quantity,
        "safety_stock": ss.safety_stock,
        "orders_per_year": eoq.orders_per_year,
    }


@dataclass(frozen=True)
class DriverSensitivity:
    """One driver's band and how it moves the target metric (a tornado bar)."""

    driver: str
    unit: str
    low_in: float
    base_in: float
    high_in: float
    low_output: float
    base_output: float
    high_output: float
    swing: float


@dataclass(frozen=True)
class WhatIfReport:
    metric: str
    base_value: float
    rows: tuple[DriverSensitivity, ...]      # sorted by swing desc
    optimistic_value: float
    pessimistic_value: float
    optimistic_inputs: dict
    pessimistic_inputs: dict
    top_driver: str
    breakeven_value: float | None
    breakeven_found: bool
    breakeven_target: float
    n_drivers: int


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> dict:
    """Sniff the drivers table and build the Driver band list + the base-case inputs."""
    params = params or {}
    driver_col = _pick_column(df, params.get("driver_col"), _DRIVER_COLS)
    low_col = _pick_column(df, params.get("low_col"), _LOW_COLS)
    high_col = _pick_column(df, params.get("high_col"), _HIGH_COLS)
    missing = [n for n, c in (("driver", driver_col), ("low", low_col), ("high", high_col)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(
            f"missing the {', '.join(missing)} band column(s); pass them in params "
            f"(columns seen: {cols})"
        )
    base_col = _pick_column(df, params.get("base_col"), _BASE_COLS)
    unit_col = _pick_column(df, params.get("unit_col"), _UNIT_COLS)

    drivers: list[Driver] = []
    unknown: list[str] = []
    base_inputs = dict(_DEFAULTS)
    for _, row in df.iterrows():
        name = str(row[driver_col]).strip()
        if name not in MODEL_INPUTS:
            unknown.append(name)
            continue
        low = float(row[low_col])
        high = float(row[high_col])
        base = float(row[base_col]) if base_col and pd.notna(row[base_col]) else _DEFAULTS[name]
        unit = str(row[unit_col]) if unit_col and pd.notna(row[unit_col]) else ""
        drivers.append(Driver(name, base=base, low=low, high=high, unit=unit))
        base_inputs[name] = base

    if unknown:
        raise ValueError(
            f"unknown driver(s) {unknown}; valid model inputs are {list(MODEL_INPUTS)}"
        )
    if not drivers:
        raise ValueError("no drivers to sweep")
    return {"drivers": drivers, "base_inputs": base_inputs}


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Read a drivers CSV and build the sweep payload."""
    return prepare_records(pd.read_csv(data_path), params)


def run(
    payload: dict,
    *,
    metric: str = "annual_cost",
    budget_pct: float = 0.10,
    maximize: bool = False,
) -> WhatIfReport:
    """Sweep every driver, rank by swing, find the corners and the budget break-even."""
    drivers: list[Driver] = payload["drivers"]
    base_inputs: dict = payload["base_inputs"]
    by_name = {d.name: d for d in drivers}

    base_value = float(policy_model(base_inputs)[metric])
    sweeps = tornado(policy_model, base_inputs, drivers, metric)
    rows = tuple(
        DriverSensitivity(
            driver=s.driver,
            unit=by_name[s.driver].unit,
            low_in=by_name[s.driver].low,
            base_in=by_name[s.driver].base,
            high_in=by_name[s.driver].high,
            low_output=s.low_output,
            base_output=s.base_output,
            high_output=s.high_output,
            swing=s.swing,
        )
        for s in sweeps
    )

    opt = optimistic_case(policy_model, base_inputs, drivers, metric, maximize=maximize)
    pes = pessimistic_case(policy_model, base_inputs, drivers, metric, maximize=maximize)

    top = rows[0]
    target = base_value * (1.0 - budget_pct if maximize else 1.0 + budget_pct)
    be = break_even(policy_model, base_inputs, by_name[top.driver], metric, target)

    return WhatIfReport(
        metric=metric,
        base_value=base_value,
        rows=rows,
        optimistic_value=float(opt.outputs[metric]),
        pessimistic_value=float(pes.outputs[metric]),
        optimistic_inputs=opt.inputs,
        pessimistic_inputs=pes.inputs,
        top_driver=top.driver,
        breakeven_value=be.value,
        breakeven_found=be.found,
        breakeven_target=target,
        n_drivers=len(drivers),
    )


def verify(report: WhatIfReport) -> list[str]:
    """QA gate: drivers swept, base finite, and the tornado is actually ranked."""
    issues: list[str] = []
    if report.n_drivers <= 0 or not report.rows:
        issues.append("no drivers to sweep")
    if not math.isfinite(report.base_value):
        issues.append("base value is not finite")
    swings = [r.swing for r in report.rows]
    if swings != sorted(swings, reverse=True):
        issues.append("tornado rows are not ranked by swing")
    return issues


def write_operational(report: WhatIfReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: one row per driver with its band and swing."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "driver": r.driver,
            "unit": r.unit,
            "low_in": round(r.low_in, 4),
            "base_in": round(r.base_in, 4),
            "high_in": round(r.high_in, 4),
            f"{report.metric}_low": round(r.low_output, 2),
            f"{report.metric}_base": round(r.base_output, 2),
            f"{report.metric}_high": round(r.high_output, 2),
            "swing": round(r.swing, 2),
        }
        for r in report.rows
    ]
    return {"csv": write_summary_csv(rows, d / "whatif_sensitivity.csv")}


def build_deck(
    report: WhatIfReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.8,
) -> Deliverable:
    """Compose the sensitivity study: what threatens the plan and at what point it breaks."""
    top = report.rows[0]
    spread = report.pessimistic_value - report.optimistic_value
    summary = (
        f"Sensitivity of {report.metric} (base {report.base_value:,.0f}) across "
        f"{report.n_drivers} assumption(s): '{top.driver}' is the biggest lever "
        f"(swing {top.swing:,.0f}); the realistic range is "
        f"{report.optimistic_value:,.0f} to {report.pessimistic_value:,.0f}."
    )

    findings = [
        Finding(
            f"Most sensitive assumption: {top.driver}",
            f"Over its {top.low_in:,.2f}-{top.high_in:,.2f} {top.unit} band, {report.metric} "
            f"moves {top.swing:,.0f} (from {top.low_output:,.0f} to {top.high_output:,.0f}).",
            impact="monitor and hedge this driver first; it dominates the outcome",
        ),
        Finding(
            "Optimistic vs pessimistic corner",
            f"Best realistic case {report.optimistic_value:,.0f}, worst {report.pessimistic_value:,.0f} "
            f"- a {spread:,.0f} spread the plan must absorb.",
            impact="size buffers/contingency to the pessimistic corner, not the base",
        ),
    ]
    if report.breakeven_found:
        findings.append(Finding(
            f"Budget break-even on {report.top_driver}",
            f"{report.metric} reaches the {report.breakeven_target:,.0f} budget when "
            f"{report.top_driver} hits {report.breakeven_value:,.2f} {top.unit}.",
            impact="set this as the trip-wire that triggers a re-plan",
        ))
    else:
        findings.append(Finding(
            f"Budget {report.breakeven_target:,.0f} not breached in band",
            f"Within the {report.top_driver} band, {report.metric} never crosses the budget - "
            "the plan is robust to this driver alone.",
            impact="no trip-wire needed for this driver at the current band",
        ))

    kpis = (
        Kpi("Base annual cost", f"{report.base_value:,.0f}", rationale="Cost at the expected assumptions"),
        Kpi("Top driver", f"{top.driver}", rationale="Assumption with the widest cost swing"),
        Kpi("Top-driver swing", f"{top.swing:,.0f}", target="minimize",
            rationale="Cost spread from this driver alone"),
        Kpi("Optimistic", f"{report.optimistic_value:,.0f}", rationale="Best realistic corner"),
        Kpi("Pessimistic", f"{report.pessimistic_value:,.0f}", target="minimize",
            rationale="Worst realistic corner - size contingency here"),
    )

    data_sources = (
        DataSource("Driver bands (base / low / high per assumption)", "planner judgement + history", "per run"),
        DataSource("Policy cost model (EOQ + safety stock)", "src.eoq + src.safety_stock", "deterministic"),
    )

    recommendations = [
        f"Track '{top.driver}' as the primary risk driver and review it most often.",
        "Plan contingency to the pessimistic corner, not the base case.",
        "Use the break-even value as the trigger to re-run the policy.",
    ]

    return Deliverable(
        title="What-If / Sensitivity Study",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        citations=tuple(citations),
        confidence=confidence,
        residual="Bands and the policy cost model are approximations - confirm the driver "
                 "ranges and cost parameters with the planner before acting on the trip-wires.",
        prepared=prepared,
    )
