"""Supply-chain risk agent job: a risk register CSV -> ranked, executable mitigation options.

The data-prep + deck + guided-options half of the risk tool. Reads a risk register (name,
category, likelihood, impact, detectability, TTR/TTS) with pandas directly (deliberately
*not* via jobs/intake.py, which the parallel loop owns), scores it via ``src.risk``
(likelihood x impact -> EMV, FMEA RPN, 5x5 heatmap, TTR>TTS resilience gap), and emits a
protected ``GuidedOutcome`` with **ranked mitigation options for the top risk** (net-benefit
ranked, recommended flagged) so the tool offers *choices to act*, not just a heatmap.

Mirrors jobs/whatif_job.py (prepare/sniff) and jobs/returns_job.py (guided options) style.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.guided import ExecutionOption, GuidedOutcome, as_options, verify_guided
from src.risk import (
    DEFAULT_SEVERITY_THRESHOLDS,
    MitigationOption,
    RiskFactor,
    RiskReport,
    assess_portfolio,
)

_NAME_COLS = ("name", "risk", "risk_name", "Risk", "Name", "factor", "title")
_CATEGORY_COLS = ("category", "type", "Category", "risk_category", "source")
_LIKELIHOOD_COLS = ("likelihood", "probability", "prob", "Likelihood", "annual_probability", "p")
_IMPACT_COLS = ("impact_value", "impact", "Impact", "loss", "impact_usd", "value")
_EXPOSURE_COLS = ("exposure", "Exposure", "exposed_value")
_VELOCITY_COLS = ("velocity_days", "velocity", "Velocity")
_DETECT_COLS = ("detectability_days", "detectability", "detection_days", "Detection")
_TTR_COLS = ("time_to_recover", "ttr", "TTR", "recovery_days")
_TTS_COLS = ("time_to_survive", "tts", "TTS", "survive_days")
_OWNER_COLS = ("owner", "Owner", "responsible")


@dataclass(frozen=True)
class RiskJobReport:
    """Wraps the pure ``RiskReport`` plus the protected ranked-mitigation options outcome."""

    risk_report: RiskReport
    outcome: GuidedOutcome                 # ranked, executable mitigations for the top risk
    top_risk: str
    n_risks: int
    summary: str


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def _coerce_mitigations(spec: object) -> dict[str, tuple[MitigationOption, ...]]:
    """Build per-risk mitigation tuples from a params dict {risk_name: [ {name, kind, ...} ]}."""
    if not spec:
        return {}
    out: dict[str, tuple[MitigationOption, ...]] = {}
    for risk_name, items in dict(spec).items():
        opts = [
            MitigationOption(
                name=str(m["name"]),
                kind=str(m.get("kind", "control")),
                cost=float(m.get("cost", 0.0)),
                likelihood_reduction=float(m.get("likelihood_reduction", 0.0)),
                impact_reduction=float(m.get("impact_reduction", 0.0)),
            )
            for m in items
        ]
        out[str(risk_name)] = tuple(opts)
    return out


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> list[RiskFactor]:
    """Sniff the risk-register columns and build one RiskFactor per row.

    Required columns: name, likelihood, impact_value (raises ValueError listing any missing).
    Mitigations are optional and supplied via ``params['mitigations']`` keyed by risk name.
    """
    params = params or {}
    name_col = _pick_column(df, params.get("name_col"), _NAME_COLS)
    likelihood_col = _pick_column(df, params.get("likelihood_col"), _LIKELIHOOD_COLS)
    impact_col = _pick_column(df, params.get("impact_col"), _IMPACT_COLS)
    missing = [
        n for n, c in (("name", name_col), ("likelihood", likelihood_col), ("impact_value", impact_col))
        if c is None
    ]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(
            f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})"
        )

    category_col = _pick_column(df, params.get("category_col"), _CATEGORY_COLS)
    exposure_col = _pick_column(df, params.get("exposure_col"), _EXPOSURE_COLS)
    velocity_col = _pick_column(df, params.get("velocity_col"), _VELOCITY_COLS)
    detect_col = _pick_column(df, params.get("detectability_col"), _DETECT_COLS)
    ttr_col = _pick_column(df, params.get("ttr_col"), _TTR_COLS)
    tts_col = _pick_column(df, params.get("tts_col"), _TTS_COLS)
    owner_col = _pick_column(df, params.get("owner_col"), _OWNER_COLS)
    mitigations_by_name = _coerce_mitigations(params.get("mitigations"))

    records: list[RiskFactor] = []
    for _, row in df.iterrows():
        name = str(row[name_col]).strip()
        records.append(RiskFactor(
            name=name,
            category=(
                str(row[category_col]).strip()
                if category_col and pd.notna(row[category_col]) else "uncategorized"
            ),
            likelihood=float(row[likelihood_col]),
            impact_value=float(row[impact_col]),
            exposure=float(row[exposure_col]) if exposure_col and pd.notna(row[exposure_col]) else 0.0,
            velocity_days=float(row[velocity_col]) if velocity_col and pd.notna(row[velocity_col]) else 30.0,
            detectability_days=(
                float(row[detect_col]) if detect_col and pd.notna(row[detect_col]) else 7.0
            ),
            time_to_recover=float(row[ttr_col]) if ttr_col and pd.notna(row[ttr_col]) else 0.0,
            time_to_survive=float(row[tts_col]) if tts_col and pd.notna(row[tts_col]) else 0.0,
            owner=str(row[owner_col]) if owner_col and pd.notna(row[owner_col]) else "",
            mitigations=mitigations_by_name.get(name, ()),
        ))
    if not records:
        raise ValueError("no risk factors found in the data")
    return records


def prepare(data_path: str, params: dict | None = None) -> list[RiskFactor]:
    """Read a risk register CSV and build the RiskFactor records."""
    return prepare_records(pd.read_csv(data_path), params)


def run(
    records: list[RiskFactor], *, severity_thresholds: tuple[float, ...] = DEFAULT_SEVERITY_THRESHOLDS
) -> RiskJobReport:
    """Score the register and present the top risk's mitigations as protected ranked options."""
    risk_report = assess_portfolio(records, severity_thresholds=severity_thresholds)
    top = risk_report.assessments[0]
    options = [
        ExecutionOption(
            label=m.name,
            summary=(
                f"residual EMV ${m.residual_emv:,.0f} (cuts ${m.risk_reduction:,.0f}); "
                f"net benefit ${m.net_benefit:,.0f}"
            ),
            score=m.net_benefit,
            recommended=m.recommended,
            action=f"apply '{m.name}' ({m.kind}) to {top.name}",
            tradeoffs=(f"{m.kind}; ${m.cost:,.0f} spend" if m.cost > 0 else "no spend; risk retained"),
        )
        for m in top.mitigations
    ]
    summary = (
        f"{len(risk_report.assessments)} risks, ${risk_report.total_emv:,.0f}/yr expected loss "
        f"-> ${risk_report.residual_emv:,.0f} after recommended mitigations; top: {top.name}."
    )
    outcome = as_options(summary, options, confidence=0.8)
    return RiskJobReport(
        risk_report=risk_report,
        outcome=outcome,
        top_risk=top.name,
        n_risks=len(risk_report.assessments),
        summary=summary,
    )


def verify(report: RiskJobReport) -> list[str]:
    """QA gate: the options outcome honours the never-unprotected contract + finite EMV."""
    issues = list(verify_guided(report.outcome))
    if report.n_risks <= 0:
        issues.append("no risk factors to assess")
    if not math.isfinite(report.risk_report.total_emv):
        issues.append("total EMV is not finite")
    return issues


def write_operational(report: RiskJobReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: one row per risk with its rating and recommendation."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "name": a.name,
            "category": a.category,
            "zone": a.zone,
            "score": a.score,
            "rpn": a.rpn,
            "emv": round(a.emv, 2),
            "exposure_gap_days": round(a.exposure_gap_days, 2),
            "recommended": a.recommended,
        }
        for a in report.risk_report.assessments
    ]
    return {"csv": write_summary_csv(rows, d / "risk_register.csv")}


def _redundancy_vs_flexibility(report: RiskReport) -> str:
    """A redundancy-vs-flexibility steer from the recommended mitigation kinds across the register."""
    kinds = [
        m.kind for a in report.assessments for m in a.mitigations
        if m.recommended and m.kind != "accept"
    ]
    n_redundancy = sum(1 for k in kinds if k == "redundancy")
    n_flexibility = sum(1 for k in kinds if k == "flexibility")
    if n_redundancy > 0 and n_redundancy >= n_flexibility:
        return (
            "Redundancy leads the recommended mitigations: pre-position back-stop capacity/inventory "
            "for the highest-EMV, hard-to-flex risks."
        )
    if n_flexibility > 0:
        return (
            "Flexibility leads the recommended mitigations: invest in multi-sourcing/reallocation so the "
            "network absorbs shocks without dedicated buffers."
        )
    return (
        "No active mitigation beats accept/monitor yet: most risks are currently retained -- price in "
        "redundancy (buffers) for severe risks and flexibility (multi-sourcing) for volatile ones."
    )


def build_deck(
    report: RiskJobReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.8,
) -> Deliverable:
    """Compose the risk assessment: where the expected loss sits, and the ranked ways to cut it."""
    rr = report.risk_report
    top = rr.assessments[0]
    gap_name, gap_days = rr.top_exposure_gap
    n_critical = rr.heatmap.get("critical", 0)
    n_high = rr.heatmap.get("high", 0)

    summary = (
        f"Risk assessment of {report.n_risks} factor(s): ${rr.total_emv:,.0f}/yr expected loss, "
        f"${rr.residual_emv:,.0f} after recommended mitigations ({n_critical} critical, {n_high} high). "
        f"Top risk: {top.name} (EMV ${top.emv:,.0f}, zone {top.zone})."
    )

    findings = [
        Finding(
            f"Top risk by expected loss: {top.name}",
            f"{top.category}; likelihood x impact {top.occurrence}x{top.severity} (zone {top.zone}), "
            f"EMV ${top.emv:,.0f}, FMEA RPN {top.rpn}; recommended: {top.recommended}.",
            impact="treat this first; it carries the largest expected annual loss",
        ),
    ]
    for a in rr.assessments[1:3]:
        findings.append(Finding(
            f"Next exposure: {a.name}",
            f"{a.category}; zone {a.zone}, EMV ${a.emv:,.0f}, RPN {a.rpn}; recommended: {a.recommended}.",
            impact="queue behind the top risk in the mitigation backlog",
        ))
    if gap_days > 0:
        findings.append(Finding(
            f"Resilience gap (TTR>TTS): {gap_name}",
            f"time-to-recover outlasts time-to-survive by {gap_days:,.0f} day(s) -- the network cannot "
            "ride this disruption out.",
            impact="close the gap with pre-positioned buffers or a faster recovery playbook (Simchi-Levi)",
        ))
    else:
        findings.append(Finding(
            "No TTR>TTS resilience gap",
            "every risk's time-to-recover is within its time-to-survive at the current inputs.",
            impact="resilient to the listed disruptions on the recovery-vs-survival test",
        ))
    findings.append(Finding(
        "Mitigation strategy: redundancy vs flexibility",
        _redundancy_vs_flexibility(rr),
        impact="pick the cheaper of equal-protection options; redundancy is dedicated, flexibility is shared",
    ))

    kpis = (
        Kpi("Total expected loss (EMV)", f"${rr.total_emv:,.0f}", target="minimize",
            rationale="Sum of likelihood x impact across the register (annual)"),
        Kpi("Residual EMV (after mitigation)", f"${rr.residual_emv:,.0f}", target="minimize",
            rationale="Expected loss once each risk's recommended mitigation is applied"),
        Kpi("Critical risks", f"{n_critical}", target="0",
            rationale="Risks in the 5x5 critical zone (score 16-25)"),
        Kpi("High risks", f"{n_high}", target="minimize",
            rationale="Risks in the high zone (score 10-15)"),
        Kpi("Worst TTR>TTS gap", f"{gap_name}: {gap_days:,.0f}d", target="0",
            rationale="Largest shortfall of survival time vs recovery time (Simchi-Levi)"),
    )

    data_sources = (
        DataSource(
            "Risk register (likelihood / impact / detectability / TTR / TTS)",
            "risk owners + incident history", "quarterly",
        ),
        DataSource("Mitigation options (cost / likelihood & impact reduction)", "engagement parameters", "per run"),
    )

    recommendations = [
        f"Action the recommended mitigation for the top risk ({top.name}) first.",
        "Re-rate likelihoods and impacts with the named owners each quarter; the EMV ranking can flip.",
        "Track residual EMV as the KPI the mitigation budget is buying down.",
    ]

    return Deliverable(
        title="Supply-Chain Risk Assessment",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=tuple(recommendations),
        options=tuple(report.outcome.options),   # ranked mitigations -> the deck's "Options to act"
        citations=tuple(citations),
        confidence=confidence,
        residual="scores are estimates; confirm likelihoods/impacts with owners.",
        prepared=prepared,
    )
