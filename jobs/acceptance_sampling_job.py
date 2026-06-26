"""Acceptance-sampling agent job: a parts CSV -> receiving inspection plans.

Reads incoming parts with their AQL / LTPD quality targets via pandas directly, and designs
the smallest single sampling plan (inspect n, accept on <= c defects) that protects both the
producer and the consumer risk, via ``src.acceptance_sampling``.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.acceptance_sampling import design_single_sampling_plan
from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv

_PART_COLS = ("part", "component", "sku", "item", "id", "Part")
_AQL_COLS = ("aql", "AQL", "acceptable_quality")
_LTPD_COLS = ("ltpd", "LTPD", "rejectable_quality", "rql")


@dataclass(frozen=True)
class PartPlan:
    part: str
    aql: float
    ltpd: float
    sample_size: int      # n
    accept_number: int    # c


@dataclass(frozen=True)
class SamplingReport:
    parts: tuple[PartPlan, ...]      # sorted by sample size desc (strictest first)
    n_parts: int
    total_sample: int
    strictest_part: str
    producer_risk: float
    consumer_risk: float


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> list[dict]:
    """Sniff the part + AQL/LTPD columns."""
    params = params or {}
    part = _pick_column(df, params.get("part_col"), _PART_COLS)
    aql = _pick_column(df, params.get("aql_col"), _AQL_COLS)
    ltpd = _pick_column(df, params.get("ltpd_col"), _LTPD_COLS)
    missing = [n for n, c in (("part", part), ("aql", aql), ("ltpd", ltpd)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})")
    return [
        {"part": str(row[part]), "aql": float(row[aql]), "ltpd": float(row[ltpd])}
        for _, row in df.iterrows()
    ]


def prepare(data_path: str, params: dict | None = None) -> list[dict]:
    """Read a parts CSV and build the sampling records."""
    return prepare_records(pd.read_csv(data_path), params)


def run(records: list[dict], *, producer_risk: float = 0.05, consumer_risk: float = 0.10) -> SamplingReport:
    """Design the smallest single sampling plan per part."""
    plans: list[PartPlan] = []
    for r in records:
        plan = design_single_sampling_plan(
            r["aql"], r["ltpd"], producer_risk=producer_risk, consumer_risk=consumer_risk,
        )
        plans.append(PartPlan(r["part"], r["aql"], r["ltpd"], plan.n, plan.c))
    plans.sort(key=lambda p: p.sample_size, reverse=True)
    return SamplingReport(
        parts=tuple(plans),
        n_parts=len(plans),
        total_sample=sum(p.sample_size for p in plans),
        strictest_part=plans[0].part if plans else "n/a",
        producer_risk=producer_risk,
        consumer_risk=consumer_risk,
    )


def verify(report: SamplingReport) -> list[str]:
    """QA gate: plans designed for every part."""
    issues: list[str] = []
    if report.n_parts <= 0:
        issues.append("no parts to plan")
    for p in report.parts:
        if p.sample_size <= 0:
            issues.append(f"no valid plan for {p.part}")
    return issues


def write_operational(report: SamplingReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: the inspect-n / accept-c plan per part."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {"part": p.part, "aql": p.aql, "ltpd": p.ltpd, "sample_size": p.sample_size, "accept_number": p.accept_number}
        for p in report.parts
    ]
    return {"csv": write_summary_csv(rows, d / "sampling_plans.csv")}


def build_deck(
    report: SamplingReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> Deliverable:
    """Compose the receiving-inspection study: how much to inspect, and when to accept."""
    summary = (
        f"Designed sampling plans for {report.n_parts} part(s) at {report.producer_risk * 100:.0f}% "
        f"producer / {report.consumer_risk * 100:.0f}% consumer risk; {report.total_sample} total units "
        f"to inspect, strictest is '{report.strictest_part}'."
    )
    findings = [
        Finding(
            "Risk-balanced inspection",
            f"Each plan is the smallest (n, c) that keeps the producer risk at AQL and the consumer "
            f"risk at LTPD within bounds; {report.total_sample} units inspected across the set.",
            impact="inspect only as much as the risk targets require",
        ),
    ]
    if report.parts:
        s = report.parts[0]
        findings.append(Finding(
            f"Strictest plan: {s.part}",
            f"Inspect {s.sample_size}, accept on <= {s.accept_number} defect(s) (AQL {s.aql}, LTPD {s.ltpd}).",
            impact="tightest quality gate - allocate inspection capacity here",
        ))
    kpis = (
        Kpi("Parts planned", str(report.n_parts), rationale="Incoming parts with a plan"),
        Kpi("Total inspection", str(report.total_sample), target="minimize",
            rationale="Units to inspect across all plans"),
        Kpi("Strictest part", report.strictest_part, rationale="Largest sample size"),
        Kpi("Risk posture", f"{report.producer_risk * 100:.0f}% / {report.consumer_risk * 100:.0f}%",
            rationale="Producer / consumer risk targets"),
    )
    data_sources = (
        DataSource("Incoming parts (AQL / LTPD quality targets)", "quality engineering", "per part"),
    )
    recommendations = [
        "Adopt the per-part plans at the receiving dock.",
        "Move reliable suppliers to skip-lot / reduced inspection once they hold AQL.",
        "Tighten (lower AQL) only on safety/critical parts where the risk justifies the cost.",
    ]
    return Deliverable(
        title="Acceptance Sampling (Receiving Quality)",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        citations=tuple(citations),
        confidence=confidence,
        residual="Sampling assumes lots are homogeneous and defects independent - confirm with QA "
                 "before relying on the plan for safety-critical parts.",
        prepared=prepared,
    )
