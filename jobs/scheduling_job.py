"""Job-sequencing agent job: a jobs CSV -> recommended run order + flow/lateness.

The data-prep + deck half of the scheduling tool. Reads jobs (processing time + optional due
date) with pandas directly (not jobs/intake.py), evaluates the single-machine dispatching
rules via ``src.scheduling`` (SPT minimizes mean flow time, EDD minimizes maximum lateness),
and recommends the rule that fits the objective. For shop-floor / pick-line / batch ordering.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.scheduling import DispatchResult, Job, dispatch_sequence

_JOB_COLS = ("job", "job_id", "id", "order", "task", "Job", "sku")
_PROCESSING_COLS = ("processing_time", "processing", "time", "duration", "hours", "Processing Time")
_DUE_COLS = ("due_date", "due", "deadline", "Due Date", "due_in")

_RULES = ("SPT", "EDD", "FCFS", "LPT")


@dataclass(frozen=True)
class SchedulingReport:
    n_jobs: int
    recommended_rule: str
    sequence: tuple[str, ...]              # the recommended rule's order
    mean_flow_time: float
    mean_lateness: float
    max_lateness: float
    rule_metrics: dict                     # rule -> DispatchResult
    has_due_dates: bool


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> list[Job]:
    """Sniff the job columns and build one Job per row."""
    params = params or {}
    job = _pick_column(df, params.get("job_col"), _JOB_COLS)
    proc = _pick_column(df, params.get("processing_col"), _PROCESSING_COLS)
    missing = [n for n, c in (("job", job), ("processing_time", proc)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})")
    due = _pick_column(df, params.get("due_col"), _DUE_COLS)
    return [
        Job(
            id=str(row[job]),
            processing=float(row[proc]),
            due=float(row[due]) if due and pd.notna(row[due]) else 0.0,
        )
        for _, row in df.iterrows()
    ]


def prepare(data_path: str, params: dict | None = None) -> list[Job]:
    """Read a jobs CSV and build the Job records."""
    return prepare_records(pd.read_csv(data_path), params)


def run(jobs: list[Job], *, objective: str = "auto") -> SchedulingReport:
    """Evaluate the dispatching rules and recommend the one that fits the objective.

    ``objective``: 'flow' -> SPT (minimize mean flow time); 'due' -> EDD (minimize max
    lateness); 'auto' (default) -> EDD when due dates are present, else SPT.
    """
    metrics = {rule: dispatch_sequence(jobs, rule=rule) for rule in _RULES}
    has_due = any(j.due > 0 for j in jobs)
    objective = objective.lower()
    if objective == "flow":
        rule = "SPT"
    elif objective == "due":
        rule = "EDD"
    else:
        rule = "EDD" if has_due else "SPT"
    chosen: DispatchResult = metrics[rule]
    return SchedulingReport(
        n_jobs=len(jobs),
        recommended_rule=rule,
        sequence=tuple(chosen.sequence),
        mean_flow_time=chosen.mean_flow_time,
        mean_lateness=chosen.mean_lateness,
        max_lateness=chosen.max_lateness,
        rule_metrics=metrics,
        has_due_dates=has_due,
    )


def verify(report: SchedulingReport) -> list[str]:
    """QA gate: jobs sequenced and a full permutation returned."""
    issues: list[str] = []
    if report.n_jobs <= 0:
        issues.append("no jobs to sequence")
    if len(report.sequence) != report.n_jobs:
        issues.append("sequence does not cover every job")
    return issues


def write_operational(report: SchedulingReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: the recommended run order by position."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [{"position": i + 1, "job": jid} for i, jid in enumerate(report.sequence)]
    return {"csv": write_summary_csv(rows, d / "job_sequence.csv")}


def build_deck(
    report: SchedulingReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> Deliverable:
    """Compose the sequencing study: the recommended run order and the rule trade-off."""
    spt = report.rule_metrics["SPT"]
    edd = report.rule_metrics["EDD"]
    summary = (
        f"Sequenced {report.n_jobs} job(s); recommended rule '{report.recommended_rule}' -> "
        f"mean flow time {report.mean_flow_time:.2f}, max lateness {report.max_lateness:.2f}."
    )
    findings = [
        Finding(
            f"Recommended order ({report.recommended_rule})",
            "Run order: " + " -> ".join(report.sequence) + ".",
            impact="follow this sequence on the line/queue",
        ),
        Finding(
            "Rule trade-off: throughput vs due dates",
            f"SPT mean flow {spt.mean_flow_time:.2f} (fastest throughput); "
            f"EDD max lateness {edd.max_lateness:.2f} (best on-time).",
            impact="pick SPT to clear work fastest, EDD to protect due dates",
        ),
    ]
    kpis = (
        Kpi("Jobs", str(report.n_jobs), rationale="Jobs sequenced"),
        Kpi("Recommended rule", report.recommended_rule,
            rationale="Best fit for the objective (due dates -> EDD, else SPT)"),
        Kpi("Mean flow time", f"{report.mean_flow_time:.2f}", target="minimize",
            rationale="Average time a job spends in the system"),
        Kpi("Max lateness", f"{report.max_lateness:.2f}", target="minimize",
            rationale="Worst job tardiness vs its due date"),
    )
    data_sources = (
        DataSource("Jobs (processing time + optional due date)", "shop floor / work queue", "per run"),
        DataSource("Dispatching rules", "src.scheduling", "deterministic"),
    )
    recommendations = [
        f"Run the jobs in the '{report.recommended_rule}' order above.",
        "Use SPT to minimize mean flow time, EDD to minimize maximum lateness.",
        "Re-sequence as new jobs arrive (the order is recomputed cheaply).",
    ]
    return Deliverable(
        title="Job-Sequencing Study",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        citations=tuple(citations),
        confidence=confidence,
        residual="Single-machine sequencing ignores setups and multi-stage routing - confirm "
                 "the bottleneck stage before applying to a multi-step process.",
        prepared=prepared,
    )
