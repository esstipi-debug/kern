"""Returns / reverse-logistics agent job: a returns CSV -> ranked disposition options.

The data-prep + deck + guided-options half of the returns tool. Reads returned lots (units,
reason, unit cost, resale value, sellable) with pandas directly (deliberately *not* via
jobs/intake.py, which the parallel loop owns), ranks each lot's disposition via
``src.reverse_logistics`` (restock / refurbish / liquidate / scrap), rolls up recovery rate +
value at risk + the reason Pareto, and emits a protected ``GuidedOutcome`` with **ranked,
executable portfolio strategies** (recovery-max / liquidate-all / restock-or-scrap) so the
tool offers *choices to act*, not just a number.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.decision_options import Objective, Scenario, decide
from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.guided import GuidedOutcome, recommend, verify_guided
from src.reverse_logistics import (
    DispositionRates,
    LineDisposition,
    ReturnLine,
    best_disposition,
    reason_pareto,
    recovered_value,
    recovery_rate,
    returns_value_at_cost,
)

_PRODUCT_COLS = ("product_id", "sku", "SKU", "item", "Product")
_UNITS_COLS = ("returned_units", "returns", "units", "qty", "quantity", "Returned Units")
_REASON_COLS = ("reason", "return_reason", "Reason", "category")
_COST_COLS = ("unit_cost", "cost", "Unit Cost", "price")
_RESALE_COLS = ("resale_value", "resale", "list_price", "sell_price", "Resale Value")
_SELLABLE_COLS = ("sellable", "resellable", "Sellable")


@dataclass(frozen=True)
class ReturnsReport:
    n_lines: int
    total_returned_units: float
    returns_value_at_cost: float
    recovered_value: float
    recovery_rate: float
    dispositions: tuple[LineDisposition, ...]    # ranked by recovery value desc
    reason_pareto: tuple[tuple[str, float], ...]
    top_reason: str
    outcome: GuidedOutcome                        # ranked, executable portfolio strategies
    recommended_strategy: str
    summary: str


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def _as_bool(value: object) -> bool:
    if isinstance(value, str):
        return value.strip().lower() not in ("false", "0", "no", "n", "")
    return bool(value)


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> list[ReturnLine]:
    """Sniff the returns columns and build one ReturnLine per returned lot."""
    params = params or {}
    product = _pick_column(df, params.get("product_col"), _PRODUCT_COLS)
    units = _pick_column(df, params.get("units_col"), _UNITS_COLS)
    cost = _pick_column(df, params.get("cost_col"), _COST_COLS)
    missing = [n for n, c in (("product_id", product), ("returned_units", units), ("unit_cost", cost)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})")

    reason = _pick_column(df, params.get("reason_col"), _REASON_COLS)
    resale = _pick_column(df, params.get("resale_col"), _RESALE_COLS)
    sellable = _pick_column(df, params.get("sellable_col"), _SELLABLE_COLS)

    lines: list[ReturnLine] = []
    for _, row in df.iterrows():
        unit_cost = float(row[cost])
        lines.append(ReturnLine(
            product_id=str(row[product]),
            returned_units=float(row[units]),
            reason=str(row[reason]) if reason and pd.notna(row[reason]) else "unspecified",
            unit_cost=unit_cost,
            resale_value=float(row[resale]) if resale and pd.notna(row[resale]) else unit_cost,
            sellable=_as_bool(row[sellable]) if sellable and pd.notna(row[sellable]) else True,
        ))
    return lines


def prepare(data_path: str, params: dict | None = None) -> list[ReturnLine]:
    """Read a returns CSV and build the ReturnLine records."""
    return prepare_records(pd.read_csv(data_path), params)


def _strategy_recovery(lines: list[ReturnLine], rates: DispositionRates) -> dict[str, float]:
    """Net recovery of each portfolio strategy over the same returns."""
    best = sum(best_disposition(ln, rates).recovery_value for ln in lines)
    liquidate = sum(rates.liquidation_recovery_pct * ln.unit_cost * ln.returned_units for ln in lines)
    restock_or_scrap = sum(
        ((ln.resale_value - rates.restock_handling_per_unit) if ln.sellable else -rates.scrap_cost_per_unit)
        * ln.returned_units
        for ln in lines
    )
    return {"recovery_max": best, "liquidate_all": liquidate, "restock_or_scrap": restock_or_scrap}


def run(
    lines: list[ReturnLine],
    *,
    restock_handling_per_unit: float = 0.0,
    refurbish_cost_per_unit: float = 0.0,
    refurbish_resale_factor: float = 0.6,
    liquidation_recovery_pct: float = 0.2,
    scrap_cost_per_unit: float = 0.0,
) -> ReturnsReport:
    """Rank each lot's disposition and present the portfolio as protected ranked options."""
    rates = DispositionRates(
        restock_handling_per_unit=restock_handling_per_unit,
        refurbish_cost_per_unit=refurbish_cost_per_unit,
        refurbish_resale_factor=refurbish_resale_factor,
        liquidation_recovery_pct=liquidation_recovery_pct,
        scrap_cost_per_unit=scrap_cost_per_unit,
    )
    dispositions = [best_disposition(ln, rates) for ln in lines]
    ranked = tuple(sorted(dispositions, key=lambda d: d.recovery_value, reverse=True))
    var = returns_value_at_cost(lines)
    rec_value = recovered_value(dispositions)
    rate = recovery_rate(dispositions, lines)
    pareto = tuple(reason_pareto(lines))
    top_reason = pareto[0][0] if pareto else "n/a"

    recovery = _strategy_recovery(lines, rates)
    summary = (
        f"{len(lines)} return line(s), {var:,.0f} at risk; best-route recovery "
        f"{rec_value:,.0f} ({rate * 100:.0f}%); top reason '{top_reason}'."
    )
    scenarios = [
        Scenario(
            "recovery_max", "Apply the best route per lot (restock / refurbish / liquidate as each lot's economics dictate)",
            {"net_recovery": recovery["recovery_max"], "handling": 8.0, "speed_days": 8.0},
            action="apply best-per-lot dispositions", tradeoffs="max cash recovered; mixed handling, slower (refurbish)",
        ),
        Scenario(
            "liquidate_all", "Send every return to a secondary-market liquidator",
            {"net_recovery": recovery["liquidate_all"], "handling": 2.0, "speed_days": 2.0},
            action="bulk-liquidate all returns", tradeoffs="fast and low effort; lowest recovery",
        ),
        Scenario(
            "restock_or_scrap", "Restock the sellable, scrap the rest (no refurbish capex)",
            {"net_recovery": recovery["restock_or_scrap"], "handling": 5.0, "speed_days": 4.0},
            action="restock sellable, scrap unsellable", tradeoffs="keeps inventory, no refurbish cost; forgoes repairable value",
        ),
    ]
    objectives = [
        Objective("net_recovery", weight=2.0, maximize=True),
        Objective("handling", weight=1.0),
        Objective("speed_days", weight=1.0),
    ]
    outcome = decide(summary, scenarios, objectives, confidence=0.8)
    recommended = recommend(outcome.options).label

    return ReturnsReport(
        n_lines=len(lines),
        total_returned_units=sum(ln.returned_units for ln in lines),
        returns_value_at_cost=var,
        recovered_value=rec_value,
        recovery_rate=rate,
        dispositions=ranked,
        reason_pareto=pareto,
        top_reason=top_reason,
        outcome=outcome,
        recommended_strategy=recommended,
        summary=summary,
    )


def verify(report: ReturnsReport) -> list[str]:
    """QA gate: the options outcome honours the never-unprotected contract + lots present."""
    issues = list(verify_guided(report.outcome))
    if report.n_lines <= 0:
        issues.append("no return lines to disposition")
    if not math.isfinite(report.recovery_rate):
        issues.append("recovery rate is not finite")
    return issues


def write_operational(report: ReturnsReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: per-lot recommended disposition + recovery."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "product_id": dp.line.product_id,
            "reason": dp.line.reason,
            "returned_units": round(dp.line.returned_units, 2),
            "recommended_action": dp.best.action,
            "net_recovery_per_unit": round(dp.best.net_recovery_per_unit, 2),
            "recovery_value": round(dp.recovery_value, 2),
        }
        for dp in report.dispositions
    ]
    return {"csv": write_summary_csv(rows, d / "returns_disposition.csv")}


def build_deck(
    report: ReturnsReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.8,
) -> Deliverable:
    """Compose the reverse-logistics study: recover the most, and the ranked ways to do it."""
    summary = (
        f"Reverse logistics across {report.n_lines} return line(s): {report.returns_value_at_cost:,.0f} "
        f"at risk, best-route recovery {report.recovered_value:,.0f} "
        f"({report.recovery_rate * 100:.0f}%); top reason '{report.top_reason}'."
    )

    # Surface the ranked, executable strategies as the action menu (recommended first).
    opts = report.outcome.options
    findings = [
        Finding(
            "Ranked recovery strategies (choose one)",
            "; ".join(
                f"{i + 1}. {o.label}{' [recommended]' if o.recommended else ''} - {o.tradeoffs}"
                for i, o in enumerate(opts)
            ),
            impact="pick a strategy; the recommended one maximizes net recovery on balance",
        ),
        Finding(
            "Top return driver",
            f"'{report.top_reason}' leads the reason Pareto - the highest-leverage prevention target.",
            impact="fixing the top reason cuts future returns at the source",
        ),
    ]
    if report.dispositions:
        worst = report.dispositions[0]
        findings.append(Finding(
            f"Highest-recovery lot: {worst.line.product_id}",
            f"{worst.line.returned_units:,.0f} units, best route '{worst.best.action}' "
            f"-> {worst.recovery_value:,.0f} recovered.",
            impact="action this lot first for the fastest cash back",
        ))

    kpis = (
        Kpi("Value at risk", f"{report.returns_value_at_cost:,.0f}", target="minimize",
            rationale="Original cost tied up in the returns"),
        Kpi("Recovered value", f"{report.recovered_value:,.0f}", target="maximize",
            rationale="Net value recovered under the best route per lot"),
        Kpi("Recovery rate", f"{report.recovery_rate * 100:.0f}%", target="maximize",
            rationale="Recovered value / value at risk"),
        Kpi("Recommended strategy", report.recommended_strategy,
            rationale="Best multi-objective balance (recovery vs handling vs speed)"),
        Kpi("Top return reason", report.top_reason, rationale="Largest driver in the reason Pareto"),
    )

    data_sources = (
        DataSource("Returned lots (units / reason / unit cost / resale value / sellable)", "RMA / returns system", "per return"),
        DataSource("Disposition rates (refurbish / liquidation / scrap)", "engagement parameters", "per run"),
    )

    recommendations = [
        f"Adopt the '{report.recommended_strategy}' strategy as the default disposition policy.",
        f"Run a prevention project on the top reason ('{report.top_reason}') to cut returns at the source.",
        "Re-run as rates and resale values change; the recommended strategy can flip.",
    ]

    return Deliverable(
        title="Returns & Reverse-Logistics Study",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        citations=tuple(citations),
        confidence=confidence,
        residual="Disposition rates and resale values are estimates - confirm liquidator terms "
                 "and refurbish costs before committing to a strategy.",
        prepared=prepared,
    )
