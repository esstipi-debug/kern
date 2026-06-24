"""Inventory-reconciliation / IRA agent job: a count CSV -> record-accuracy study.

The data-prep + deck half of the reconciliation tool. Reads count lines (system vs physical
qty + unit cost) with pandas directly (deliberately *not* via jobs/intake.py, which the
parallel loop owns), reconciles them against a tolerance band via ``src.reconciliation``, and
reports inventory record accuracy (IRA), the dollar impact of variances, and the worst
discrepancies. ``unit_cost`` defaults to 0 when absent (IRA still computes; $ impact is 0).
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.reconciliation import (
    CountResult,
    inventory_record_accuracy,
    reconcile,
    total_variance_value,
)

_PRODUCT_COLS = ("product_id", "sku", "SKU", "item", "Product")
_SYSTEM_COLS = ("system_qty", "system", "book_qty", "book", "on_hand_system", "System")
_PHYSICAL_COLS = ("physical_qty", "physical", "counted", "count", "actual", "Physical")
_UNIT_COST_COLS = ("unit_cost", "cost", "Unit Cost", "price")

# Inventory-record-accuracy is judged against a class-leading target.
_IRA_TARGET = 0.97


@dataclass(frozen=True)
class IRAReport:
    n_counted: int
    n_within: int
    ira: float
    total_variance_value: float
    tolerance_pct: float
    tolerance_units: float
    worst: tuple[CountResult, ...]   # out-of-tolerance lines, ranked by $ impact desc


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> list[dict]:
    """Sniff the count columns and build one reconcile record per line."""
    params = params or {}
    product = _pick_column(df, params.get("product_col"), _PRODUCT_COLS)
    system = _pick_column(df, params.get("system_col"), _SYSTEM_COLS)
    physical = _pick_column(df, params.get("physical_col"), _PHYSICAL_COLS)
    missing = [n for n, c in (("product_id", product), ("system_qty", system), ("physical_qty", physical)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})")

    unit_cost = _pick_column(df, params.get("unit_cost_col"), _UNIT_COST_COLS)
    return [
        {
            "product_id": str(row[product]),
            "system_qty": float(row[system]),
            "physical_qty": float(row[physical]),
            "unit_cost": float(row[unit_cost]) if unit_cost and pd.notna(row[unit_cost]) else 0.0,
        }
        for _, row in df.iterrows()
    ]


def prepare(data_path: str, params: dict | None = None) -> list[dict]:
    """Read a count CSV and build the reconcile records."""
    return prepare_records(pd.read_csv(data_path), params)


def run(records: list[dict], *, tolerance_pct: float = 0.0, tolerance_units: float = 0.0) -> IRAReport:
    """Reconcile system vs physical, compute IRA + variance value, rank the worst lines."""
    results = reconcile(records, tolerance_pct=tolerance_pct, tolerance_units=tolerance_units)
    out_of_tol = [r for r in results if not r.within_tolerance]
    worst = tuple(sorted(out_of_tol, key=lambda r: abs(r.variance) * r.unit_cost, reverse=True))
    return IRAReport(
        n_counted=len(results),
        n_within=sum(1 for r in results if r.within_tolerance),
        ira=inventory_record_accuracy(results),
        total_variance_value=total_variance_value(results),
        tolerance_pct=tolerance_pct,
        tolerance_units=tolerance_units,
        worst=worst,
    )


def verify(report: IRAReport) -> list[str]:
    """QA gate: lines counted and IRA is a valid fraction."""
    issues: list[str] = []
    if report.n_counted <= 0:
        issues.append("no count lines to reconcile")
    if not 0.0 <= report.ira <= 1.0:
        issues.append(f"IRA out of [0,1]: {report.ira}")
    return issues


def write_operational(report: IRAReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: the out-of-tolerance lines by dollar impact."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "product_id": r.product_id,
            "system_qty": round(r.system_qty, 2),
            "physical_qty": round(r.physical_qty, 2),
            "variance": round(r.variance, 2),
            "variance_value": round(abs(r.variance) * r.unit_cost, 2),
        }
        for r in report.worst
    ]
    return {"csv": write_summary_csv(rows, d / "reconciliation.csv")}


def build_deck(
    report: IRAReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> Deliverable:
    """Compose the IRA study: how trustworthy the records are and where they break."""
    n_out = report.n_counted - report.n_within
    summary = (
        f"Inventory record accuracy {report.ira * 100:.0f}% across {report.n_counted} counted "
        f"line(s); {n_out} out of tolerance, {report.total_variance_value:,.0f} in variance value."
    )

    findings = [
        Finding(
            "Inventory record accuracy vs target",
            f"IRA {report.ira * 100:.0f}% against a {_IRA_TARGET * 100:.0f}% target; "
            f"{report.n_within}/{report.n_counted} lines within tolerance.",
            impact="below target means the system stock can't be trusted for planning",
        ),
        Finding(
            "Dollar impact of the discrepancies",
            f"{report.total_variance_value:,.0f} total absolute variance value across "
            f"{n_out} mismatched line(s).",
            impact="prioritize the high-value variances for root-cause",
        ),
    ]
    if report.worst:
        w = report.worst[0]
        findings.append(Finding(
            f"Biggest discrepancy: {w.product_id}",
            f"System {w.system_qty:,.0f} vs physical {w.physical_qty:,.0f} "
            f"(variance {w.variance:+,.0f}, {abs(w.variance) * w.unit_cost:,.0f} value).",
            impact="root-cause this SKU first (receiving, picking, or shrink)",
        ))

    kpis = (
        Kpi("Inventory record accuracy", f"{report.ira * 100:.0f}%", target="maximize",
            rationale="Share of counted lines within tolerance (target ~97%+)"),
        Kpi("Lines counted", f"{report.n_counted}", rationale="Size of the count sample"),
        Kpi("Out of tolerance", f"{n_out}", target="minimize",
            rationale="Lines whose variance exceeds the band"),
        Kpi("Variance value", f"{report.total_variance_value:,.0f}", target="minimize",
            rationale="Absolute dollar impact of all variances"),
    )

    data_sources = (
        DataSource("Count lines (system vs physical qty + unit cost)", "WMS / cycle-count sheets", "per count"),
    )

    recommendations = [
        "Root-cause the highest-value variances first (receiving, picking, shrink).",
        "Raise cycle-count frequency on A-items until IRA holds above target.",
        "Freeze and re-count the worst SKUs before trusting the system stock for planning.",
    ]

    return Deliverable(
        title="Inventory Record Accuracy (IRA) Study",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        citations=tuple(citations),
        confidence=confidence,
        residual="Counts are a sample at a point in time - confirm the count method and "
                 "tolerance band with operations before acting on the IRA figure.",
        prepared=prepared,
    )
