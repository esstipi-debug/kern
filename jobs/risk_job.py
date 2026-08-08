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
from nosilo_core.guided import ExecutionOption, GuidedOutcome, as_options, verify_guided

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.risk import (
    DEFAULT_SEVERITY_THRESHOLDS,
    MitigationOption,
    RiskFactor,
    RiskReport,
    assess_portfolio,
)

# -- resilience (TTR>TTS) verdict vocabulary ----------------------------------
# Named like cross_impact_job's: `no_evaluable` is a first-class outcome, not an
# error, and it outranks the clean verdict on purpose.
RESILIENT = "resilient"
RESILIENCE_GAP = "gap"
RESILIENCE_NO_EVALUABLE = "no_evaluable"
RESILIENCE_VERDICTS = (RESILIENT, RESILIENCE_GAP, RESILIENCE_NO_EVALUABLE)

# Confidence cost of a run whose recovery-vs-survival test could not be settled.
# Deliberately local rather than imported: nosilo_core.guided.COVERAGE_PENALTY has
# no key for this gap and raises KeyError on an unknown one (by design, so a typo
# cannot mean "no penalty"), and that table lives in the pinned external core, not
# here. Same call jobs/cross_impact_job.py made for its own unevaluable areas.
UNEVALUABLE_RESILIENCE_PENALTY = 0.25   # nothing in the register could be tested
PARTIAL_RESILIENCE_PENALTY = 0.10       # some risks tested, some without the data
CONFIDENCE_FLOOR = 0.10

# What an unmeasured resilience cell says in risk_register.csv. The gap column is
# written EMPTY for those rows (cross_impact_job.write's precedent) - a blank cell
# in the spreadsheet has to look like a blank, and 0 does not.
UNMEASURED_CELL = "no TTR/TTS in register"

# A 200-risk register must not paste 200 names into one finding; the CSV carries
# every row, the note names the head of the list and counts the rest.
_MAX_NAMED_RISKS = 8

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
class RegisterRiskFactor(RiskFactor):
    """A RiskFactor that also remembers WHETHER the register carried a TTR/TTS number.

    ``src.risk.RiskFactor`` defaults both to 0.0, and 0.0 survives ``max(0, TTR - TTS)``
    as a zero exposure gap that is byte-identical to a measured "recovers well inside its
    survival window". That is how a register with no TTR/TTS columns at all used to earn
    the most favourable statement this tool can make. src/risk.py is the shared scoring
    engine (other jobs read it), so the provenance is recorded HERE, at the boundary that
    actually saw the cell, instead of teaching the engine about absence.
    """

    ttr_measured: bool = False
    tts_measured: bool = False


@dataclass(frozen=True)
class ResilienceGap:
    """The recovery-vs-survival (TTR>TTS) finding, or its declared absence.

    ``gap_days is None`` means the test was never run, not that it came out at zero -
    the whole point of the type. ``__post_init__`` refuses to build an instance whose
    story does not hold together, so no downstream reader has to re-check it.
    """

    worst_risk: str | None      # the risk carrying the worst measured gap
    gap_days: float | None      # None => NOT measured. Never 0.0 standing in for "fine".
    n_evaluated: int            # risks that carried BOTH numbers
    n_risks: int
    unmeasured: tuple[str, ...]  # register order; risks missing TTR and/or TTS
    note: str                   # always legible, always names what is missing

    def __post_init__(self) -> None:
        if not self.note.strip():
            raise ValueError("a ResilienceGap must carry a legible note: the reason IS the value")
        if self.n_evaluated + len(self.unmeasured) != self.n_risks:
            raise ValueError(
                f"counts do not reconcile: {self.n_evaluated} evaluated + "
                f"{len(self.unmeasured)} unmeasured != {self.n_risks} risks")
        if (self.gap_days is None) != (self.n_evaluated == 0):
            raise ValueError(
                "gap_days must be absent exactly when nothing was evaluated "
                f"(gap_days={self.gap_days!r}, n_evaluated={self.n_evaluated})")
        if (self.gap_days is None) != (self.worst_risk is None):
            raise ValueError(
                "a gap in days without the worst_risk that carries it (or the reverse) "
                f"is not a finding (gap_days={self.gap_days!r}, worst_risk={self.worst_risk!r})")
        if self.gap_days is not None and not math.isfinite(self.gap_days):
            raise ValueError(f"gap_days is not finite: {self.gap_days}")


@dataclass(frozen=True)
class RiskJobReport:
    """Wraps the pure ``RiskReport`` plus the protected ranked-mitigation options outcome."""

    risk_report: RiskReport
    outcome: GuidedOutcome                 # ranked, executable mitigations for the top risk
    top_risk: str
    n_risks: int
    summary: str
    resilience: ResilienceGap
    # Stored, not derived, so verify() has something it can actually veto - the same
    # split cross_impact_job uses for its coordination verdict.
    resilience_verdict: str


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def _cell(row: pd.Series, col: str | None) -> float | None:
    """The number the register actually held, or None when it held nothing.

    Absent column and empty cell are the SAME answer - "nobody wrote this down" - and
    both must stay distinguishable from a 0 the operator did write.
    """
    if not col or not pd.notna(row[col]):
        return None
    return float(row[col])


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
        ttr = _cell(row, ttr_col)
        tts = _cell(row, tts_col)
        records.append(RegisterRiskFactor(
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
            # The 0.0 below is the arithmetic placeholder src.risk's float field demands,
            # NOT a reading: the two flags beside it are what the job reports from, and
            # they keep an absent cell out of every resilience statement downstream.
            time_to_recover=ttr if ttr is not None else 0.0,
            time_to_survive=tts if tts is not None else 0.0,
            ttr_measured=ttr is not None,
            tts_measured=tts is not None,
            owner=str(row[owner_col]) if owner_col and pd.notna(row[owner_col]) else "",
            mitigations=mitigations_by_name.get(name, ()),
        ))
    if not records:
        raise ValueError("no risk factors found in the data")
    return records


def prepare(data_path: str, params: dict | None = None) -> list[RiskFactor]:
    """Read a risk register CSV and build the RiskFactor records."""
    return prepare_records(pd.read_csv(data_path), params)


def _is_measured(record: RiskFactor, value_field: str, flag_field: str) -> bool:
    """Did this record's TTR (or TTS) come from somewhere, or from a default?

    A record built by ``prepare_records`` carries the answer and is believed verbatim -
    including an operator who genuinely wrote 0, which is their declaration to make.
    A bare ``src.risk.RiskFactor`` handed straight to ``run()`` carries no provenance,
    so the job falls back to the engine's own sentinel: its field default is 0.0, i.e.
    "unset". That fallback can only under-claim - it never manufactures a certificate.
    """
    declared = getattr(record, flag_field, None)
    if declared is not None:
        return bool(declared)
    return float(getattr(record, value_field)) > 0.0


def _name_list(names: tuple[str, ...]) -> str:
    if len(names) <= _MAX_NAMED_RISKS:
        return ", ".join(names)
    head = ", ".join(names[:_MAX_NAMED_RISKS])
    return f"{head} (+{len(names) - _MAX_NAMED_RISKS} more; see risk_register.csv)"


def assess_resilience(records: list[RiskFactor]) -> ResilienceGap:
    """Run the TTR>TTS test on the risks that have the data, and declare the ones that don't.

    Never returns a zero gap to stand in for an untested risk: the worst gap is taken over
    the evaluated subset only, and every skipped risk is named so no reader can mistake
    silence for a pass.
    """
    evaluated: list[tuple[str, float]] = []
    unmeasured: list[str] = []
    for r in records:
        if _is_measured(r, "time_to_recover", "ttr_measured") and \
                _is_measured(r, "time_to_survive", "tts_measured"):
            evaluated.append((r.name, max(0.0, r.time_to_recover - r.time_to_survive)))
        else:
            unmeasured.append(r.name)

    n_risks = len(records)
    missing = tuple(unmeasured)
    if not evaluated:
        return ResilienceGap(
            worst_risk=None, gap_days=None, n_evaluated=0, n_risks=n_risks,
            unmeasured=missing,
            note=(f"the TTR>TTS test was not run: none of the {n_risks} risk(s) carries both a "
                  f"time-to-recover and a time-to-survive ({_name_list(missing)})."),
        )

    worst_risk, gap_days = max(evaluated, key=lambda t: t[1])
    if missing:
        note = (f"the TTR>TTS test covered {len(evaluated)} of {n_risks} risk(s); "
                f"{len(missing)} carry no time-to-recover/time-to-survive "
                f"({_name_list(missing)}) and were not tested.")
    else:
        note = f"all {n_risks} risk(s) carry both a time-to-recover and a time-to-survive."
    return ResilienceGap(
        worst_risk=worst_risk, gap_days=gap_days, n_evaluated=len(evaluated),
        n_risks=n_risks, unmeasured=missing, note=note,
    )


def resilience_verdict(resilience: ResilienceGap) -> str:
    """A measured gap is a fact whatever else is missing; a clean bill needs full coverage.

    The middle case is the one that used to lie: every tested risk came out at zero AND
    some risks were never tested. That is not resilience, it is an unfinished test.
    """
    if resilience.gap_days is not None and resilience.gap_days > 0:
        return RESILIENCE_GAP
    if resilience.n_evaluated > 0 and not resilience.unmeasured:
        return RESILIENT
    return RESILIENCE_NO_EVALUABLE


def _resilience_penalty(resilience: ResilienceGap, verdict: str) -> float:
    """What the missing basis costs the run's confidence, priced by how much is missing."""
    if verdict == RESILIENT or not resilience.unmeasured:
        return 0.0
    if resilience.n_evaluated == 0:
        return UNEVALUABLE_RESILIENCE_PENALTY
    return PARTIAL_RESILIENCE_PENALTY


def _degrade(base: float, resilience: ResilienceGap, verdict: str) -> float:
    """Cap a caller-supplied confidence by the resilience coverage it does not have.

    The caller's optimism does not survive contact with a missing input - the same
    override jobs/pricing.py applies when the cost it optimized against was imputed.
    """
    return max(CONFIDENCE_FLOOR, base - _resilience_penalty(resilience, verdict))


_BASE_OPTIONS_CONFIDENCE = 0.8


def run(
    records: list[RiskFactor], *, severity_thresholds: tuple[float, ...] = DEFAULT_SEVERITY_THRESHOLDS
) -> RiskJobReport:
    """Score the register and present the top risk's mitigations as protected ranked options."""
    risk_report = assess_portfolio(records, severity_thresholds=severity_thresholds)
    top = risk_report.assessments[0]
    resilience = assess_resilience(records)
    verdict = resilience_verdict(resilience)
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
    # The summary is the one line the caller is guaranteed to read (it is what the tool
    # wrapper hands back as Produced.summary), so a missing basis is declared IN it -
    # never left for a reader who happens to open the deck.
    if resilience.unmeasured:
        summary += f" {resilience.note}"
    outcome = as_options(
        summary, options,
        confidence=_degrade(_BASE_OPTIONS_CONFIDENCE, resilience, verdict),
    )
    return RiskJobReport(
        risk_report=risk_report,
        outcome=outcome,
        top_risk=top.name,
        n_risks=len(risk_report.assessments),
        summary=summary,
        resilience=resilience,
        resilience_verdict=verdict,
    )


def verify(report: RiskJobReport) -> list[str]:
    """QA gate: the never-unprotected contract, finite EMV, and no unearned resilience.

    Vetoes the DISHONEST combination, not the gap (cross_impact_job's rule): a run that
    reports ``no_evaluable`` with the missing risks named and its confidence degraded is
    a perfectly valid delivery. What must never ship is a resilience verdict standing on
    a TTR nobody measured.
    """
    issues = list(verify_guided(report.outcome))
    if report.n_risks <= 0:
        issues.append("no risk factors to assess")
    if not math.isfinite(report.risk_report.total_emv):
        issues.append("total EMV is not finite")

    res, verdict = report.resilience, report.resilience_verdict
    if verdict not in RESILIENCE_VERDICTS:
        issues.append(
            f"unknown resilience verdict {verdict!r} (expected one of {RESILIENCE_VERDICTS})")
    elif verdict != resilience_verdict(res):
        issues.append(
            f"resilience verdict {verdict!r} contradicts the measurements it claims to "
            f"summarise ({res.n_evaluated} of {res.n_risks} risk(s) evaluated, worst gap "
            f"{res.gap_days!r}) - the honest verdict is {resilience_verdict(res)!r}")

    if res.unmeasured and verdict == RESILIENT:
        issues.append(
            f"a resilience certificate cannot be issued while {len(res.unmeasured)} risk(s) "
            f"carry no TTR/TTS ({_name_list(res.unmeasured)})")

    if res.n_risks != report.n_risks:
        issues.append(
            f"the resilience finding covers {res.n_risks} risk(s) but the register holds "
            f"{report.n_risks}")

    # The gap has to survive into the prose. A summary that drops it hands the reader a
    # clean-looking headline whose basis is missing - exactly the defect, one layer up.
    if res.unmeasured and res.note not in report.summary:
        issues.append(
            "risks without TTR/TTS were found but the summary does not declare them - "
            "the text must not read clean when the basis is missing")

    expected = _degrade(_BASE_OPTIONS_CONFIDENCE, res, verdict)
    if report.outcome.confidence > expected:
        issues.append(
            f"options confidence {report.outcome.confidence} exceeds the {expected} the "
            f"resilience coverage supports ({len(res.unmeasured)} risk(s) without TTR/TTS)")

    return issues


def write_operational(report: RiskJobReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: one row per risk with its rating and recommendation."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    # A blank cell has to look like a blank. Rounding an untested risk's default 0.0
    # into this column is how a spreadsheet full of "0" reads as "no gap anywhere",
    # which is the most favourable reading available and nobody chose it.
    unmeasured = set(report.resilience.unmeasured)
    rows = [
        {
            "name": a.name,
            "category": a.category,
            "zone": a.zone,
            "score": a.score,
            "rpn": a.rpn,
            "emv": round(a.emv, 2),
            "exposure_gap_days": None if a.name in unmeasured else round(a.exposure_gap_days, 2),
            "resilience": UNMEASURED_CELL if a.name in unmeasured else "measured",
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
    # NOT rr.top_exposure_gap: that is the engine's max over every record, and
    # src.risk.RiskFactor defaults an absent TTR/TTS to 0.0, so a register with no
    # such columns came out as a measured zero - the most favourable statement this
    # tool can make, issued from nothing. The verdict below already separates the
    # three cases and refuses the clean one unless coverage is complete.
    res = report.resilience
    verdict = report.resilience_verdict
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
    if verdict == RESILIENCE_GAP:
        findings.append(Finding(
            f"Resilience gap (TTR>TTS): {res.worst_risk}",
            f"time-to-recover outlasts time-to-survive by {res.gap_days:,.0f} day(s) -- the network "
            f"cannot ride this disruption out. {res.note}",
            impact="close the gap with pre-positioned buffers or a faster recovery playbook (Simchi-Levi)",
        ))
    elif verdict == RESILIENT:
        findings.append(Finding(
            "No TTR>TTS resilience gap",
            f"every risk's time-to-recover is within its time-to-survive at the current inputs. "
            f"{res.note}",
            impact="resilient to the listed disruptions on the recovery-vs-survival test",
        ))
    else:
        # The case that used to print the certificate. Say what was not tested and by
        # name, so nobody reads the absence of a gap as the absence of a problem.
        findings.append(Finding(
            "Resilience NOT evaluated (TTR>TTS)",
            res.note,
            impact="no recovery-vs-survival verdict can be issued until those risks carry both "
                   "a time-to-recover and a time-to-survive",
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
        Kpi(
            "Worst TTR>TTS gap",
            f"{res.worst_risk}: {res.gap_days:,.0f}d" if res.gap_days is not None else "not evaluated",
            target="0",
            rationale=(
                "Largest shortfall of survival time vs recovery time (Simchi-Levi). "
                f"{res.note}"
            ),
        ),
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
        # The caller's confidence is a ceiling, not a floor: a deck whose resilience
        # test never ran cannot be as trustworthy as one whose did, however sure the
        # caller was. Same cap the protected options outcome already applies at :367.
        confidence=_degrade(confidence, res, verdict),
        residual="scores are estimates; confirm likelihoods/impacts with owners.",
        prepared=prepared,
    )
