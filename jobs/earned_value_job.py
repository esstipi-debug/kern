"""Earned-value agent job: a tasks CSV -> project cost/schedule control pack.

Reads work packages (planned / earned / actual cost) with pandas directly, rolls up the
portfolio EVM and ranks the worst-performing tasks via ``src.earned_value``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.earned_value import EarnedValue, earned_value
from src.export import write_summary_csv

_TASK_COLS = ("task", "work_package", "name", "id", "activity", "Task")
_PLANNED_COLS = ("planned", "bcws", "planned_value", "budget", "pv", "Planned")
_EARNED_COLS = ("earned", "bcwp", "earned_value", "ev", "Earned")
_ACTUAL_COLS = ("actual", "acwp", "actual_cost", "ac", "spent", "Actual")


@dataclass(frozen=True)
class TaskEV:
    task: str
    spi: float
    cpi: float
    schedule_variance: float
    cost_variance: float


@dataclass(frozen=True)
class EvReport:
    portfolio: EarnedValue
    tasks: tuple[TaskEV, ...]      # sorted by CPI ascending (worst cost performance first)
    n_tasks: int
    n_behind: int
    n_over: int


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> list[dict]:
    """Sniff the task + planned/earned/actual columns."""
    params = params or {}
    task = _pick_column(df, params.get("task_col"), _TASK_COLS)
    planned = _pick_column(df, params.get("planned_col"), _PLANNED_COLS)
    earned = _pick_column(df, params.get("earned_col"), _EARNED_COLS)
    actual = _pick_column(df, params.get("actual_col"), _ACTUAL_COLS)
    missing = [n for n, c in (("task", task), ("planned", planned), ("earned", earned), ("actual", actual)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})")
    return [
        {"task": str(row[task]), "planned": float(row[planned]),
         "earned": float(row[earned]), "actual": float(row[actual])}
        for _, row in df.iterrows()
    ]


def prepare(data_path: str, params: dict | None = None) -> list[dict]:
    """Read a tasks CSV and build the earned-value records."""
    return prepare_records(pd.read_csv(data_path), params)


def run(records: list[dict]) -> EvReport:
    """Compute per-task EVM and roll up the portfolio."""
    tasks: list[TaskEV] = []
    for r in records:
        ev = earned_value(r["planned"], r["earned"], r["actual"])
        tasks.append(TaskEV(r["task"], ev.spi, ev.cpi, ev.schedule_variance, ev.cost_variance))
    tasks.sort(key=lambda t: t.cpi)
    portfolio = earned_value(
        sum(r["planned"] for r in records),
        sum(r["earned"] for r in records),
        sum(r["actual"] for r in records),
    )
    return EvReport(
        portfolio=portfolio,
        tasks=tuple(tasks),
        n_tasks=len(tasks),
        n_behind=sum(1 for t in tasks if t.schedule_variance < 0),
        n_over=sum(1 for t in tasks if t.cost_variance < 0),
    )


def verify(report: EvReport) -> list[str]:
    """QA gate: tasks present."""
    return [] if report.n_tasks > 0 else ["no tasks to evaluate"]


def write_operational(report: EvReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: per-task SPI/CPI, worst first."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {"task": t.task, "spi": round(t.spi, 3), "cpi": round(t.cpi, 3),
         "schedule_variance": round(t.schedule_variance, 2), "cost_variance": round(t.cost_variance, 2)}
        for t in report.tasks
    ]
    return {"csv": write_summary_csv(rows, d / "earned_value.csv")}


def build_deck(
    report: EvReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> Deliverable:
    """Compose the project-control study: where the project stands on cost and schedule."""
    p = report.portfolio
    summary = (
        f"Project across {report.n_tasks} task(s): SPI {p.spi:.2f}, CPI {p.cpi:.2f} "
        f"({'behind' if p.behind_schedule else 'on/ahead of'} schedule, "
        f"{'over' if p.over_budget else 'on/under'} budget)."
    )
    findings = [
        Finding(
            "Schedule and cost performance",
            f"SPI {p.spi:.2f} (schedule variance {p.schedule_variance:,.0f}), "
            f"CPI {p.cpi:.2f} (cost variance {p.cost_variance:,.0f}).",
            impact="SPI/CPI < 1.0 means behind / over - act on the gap",
        ),
    ]
    if report.tasks:
        w = report.tasks[0]
        findings.append(Finding(
            f"Worst cost performance: {w.task}",
            f"CPI {w.cpi:.2f}, cost variance {w.cost_variance:,.0f}.",
            impact="root-cause this work package first",
        ))
    findings.append(Finding(
        "Trouble spread",
        f"{report.n_behind} task(s) behind schedule, {report.n_over} over budget.",
        impact="focus management attention where both are red",
    ))
    kpis = (
        Kpi("SPI (schedule)", f"{p.spi:.2f}", target="maximize", rationale="Earned / planned (>=1 on time)"),
        Kpi("CPI (cost)", f"{p.cpi:.2f}", target="maximize", rationale="Earned / actual (>=1 on budget)"),
        Kpi("Tasks", str(report.n_tasks), rationale="Work packages tracked"),
        Kpi("Behind / over", f"{report.n_behind} / {report.n_over}", target="minimize",
            rationale="Tasks behind schedule / over budget"),
    )
    data_sources = (
        DataSource("Work packages (planned / earned / actual cost)", "project tracker", "weekly"),
    )
    recommendations = [
        "Recover the worst-CPI tasks first (re-scope, re-sequence, or re-resource).",
        "Use SPI to triage schedule slips and CPI to triage cost overruns separately.",
        "Re-baseline only after the recovery actions, not before.",
    ]
    return Deliverable(
        title="Earned Value (Project Control)",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        citations=tuple(citations),
        confidence=confidence,
        residual="EVM assumes the cost/earned figures are booked consistently - confirm the "
                 "earned-value method (0/100, milestone, % complete) before acting on SPI/CPI.",
        prepared=prepared,
    )
