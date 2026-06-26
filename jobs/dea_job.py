"""DEA benchmarking agent job: a units CSV -> relative-efficiency frontier.

The data-prep + deck half of the DEA tool. Reads comparable units (suppliers, warehouses,
DCs, stores) with their input_* and output_* columns via pandas directly (not jobs/intake.py),
scores each unit's relative efficiency against the data-driven frontier (`src.dea`, input-
oriented CCR), and ranks the laggards. Unlike the supplier scorecard / TOPSIS (fixed weights),
DEA derives the frontier from the data with no preset weights.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.dea import dea_efficiency
from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv

_NAME_COLS = ("unit", "dmu", "name", "supplier", "warehouse", "store", "branch", "id")


@dataclass(frozen=True)
class UnitEfficiency:
    name: str
    efficiency: float
    is_efficient: bool


@dataclass(frozen=True)
class DeaReport:
    units: tuple[UnitEfficiency, ...]      # sorted by efficiency ascending (worst first)
    n_units: int
    n_efficient: int
    mean_efficiency: float
    worst_unit: str
    input_cols: tuple[str, ...]
    output_cols: tuple[str, ...]


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> dict:
    """Sniff the name + input_*/output_* columns and build the DEA matrices."""
    params = params or {}
    name = _pick_column(df, params.get("name_col"), _NAME_COLS)
    input_cols = list(params.get("input_cols") or [c for c in df.columns if str(c).lower().startswith(("input_", "in_"))])
    output_cols = list(params.get("output_cols") or [c for c in df.columns if str(c).lower().startswith(("output_", "out_"))])
    if not input_cols or not output_cols:
        cols = list(df.columns)[:10]
        raise ValueError(f"need input_* and output_* columns (or pass input_cols/output_cols); columns seen: {cols}")
    names = [str(v) for v in df[name].tolist()] if name else [f"unit_{i + 1}" for i in range(len(df))]
    return {
        "names": names,
        "inputs": df[input_cols].astype(float).values.tolist(),
        "outputs": df[output_cols].astype(float).values.tolist(),
        "input_cols": input_cols,
        "output_cols": output_cols,
    }


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Read a units CSV and build the DEA payload."""
    return prepare_records(pd.read_csv(data_path), params)


def run(payload: dict) -> DeaReport:
    """Score each unit's relative efficiency and rank the laggards."""
    effs = dea_efficiency(payload["inputs"], payload["outputs"])
    units = [UnitEfficiency(n, float(e), e >= 1.0 - 1e-6) for n, e in zip(payload["names"], effs)]
    units.sort(key=lambda u: u.efficiency)
    return DeaReport(
        units=tuple(units),
        n_units=len(units),
        n_efficient=sum(1 for u in units if u.is_efficient),
        mean_efficiency=sum(u.efficiency for u in units) / len(units) if units else 0.0,
        worst_unit=units[0].name if units else "n/a",
        input_cols=tuple(payload["input_cols"]),
        output_cols=tuple(payload["output_cols"]),
    )


def verify(report: DeaReport) -> list[str]:
    """QA gate: units scored and every efficiency is a valid fraction."""
    issues: list[str] = []
    if report.n_units <= 0:
        issues.append("no units to benchmark")
    for u in report.units:
        if not 0.0 < u.efficiency <= 1.0 + 1e-6:
            issues.append(f"efficiency out of (0,1] for {u.name}")
    return issues


def write_operational(report: DeaReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: per-unit efficiency, worst first."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {"unit": u.name, "efficiency": round(u.efficiency, 4), "on_frontier": u.is_efficient}
        for u in report.units
    ]
    return {"csv": write_summary_csv(rows, d / "dea_efficiency.csv")}


def build_deck(
    report: DeaReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> Deliverable:
    """Compose the efficiency study: who is on the frontier and who is leaking value."""
    summary = (
        f"Benchmarked {report.n_units} unit(s): {report.n_efficient} on the efficiency frontier, "
        f"mean efficiency {report.mean_efficiency * 100:.0f}%; weakest is '{report.worst_unit}'."
    )
    findings = [
        Finding(
            f"Least efficient: {report.worst_unit}",
            f"Efficiency {report.units[0].efficiency * 100:.0f}% vs the data-driven frontier - "
            "the biggest improvement opportunity." if report.units else "no units",
            impact="bring this unit toward the frontier first",
        ),
        Finding(
            "Frontier vs laggards",
            f"{report.n_efficient}/{report.n_units} unit(s) are efficient (score 1.0); the rest "
            "use more input for their output than the best peers achieve.",
            impact="the frontier units are the playbook to replicate",
        ),
    ]
    kpis = (
        Kpi("Units benchmarked", str(report.n_units), rationale="Comparable peer units scored"),
        Kpi("On the frontier", str(report.n_efficient), rationale="Efficient units (score 1.0)"),
        Kpi("Mean efficiency", f"{report.mean_efficiency * 100:.0f}%", target="maximize",
            rationale="Average relative efficiency across the set"),
        Kpi("Weakest unit", report.worst_unit, rationale="Lowest efficiency - first to fix"),
    )
    data_sources = (
        DataSource(f"Units with inputs ({', '.join(report.input_cols)}) and outputs ({', '.join(report.output_cols)})",
                   "operational records", "per run"),
        DataSource("DEA model (input-oriented CCR)", "src.dea", "deterministic"),
    )
    recommendations = [
        f"Diagnose and improve '{report.worst_unit}' first; it is furthest from the frontier.",
        "Study the frontier units as the operating playbook for the laggards.",
        "Re-run as inputs/outputs change to track convergence to the frontier.",
    ]
    return Deliverable(
        title="Efficiency Benchmarking (DEA)",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        citations=tuple(citations),
        confidence=confidence,
        residual="DEA efficiency is relative to this peer set and these input/output choices - "
                 "confirm the chosen factors with the operator before ranking units externally.",
        prepared=prepared,
    )
