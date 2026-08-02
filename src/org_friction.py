"""Organizational friction detector -- the political layer no data-quality audit sees.

Pure and dependency-light (stdlib only), mirroring the other analytical cores
(``src/flow_map.py``, ``src/data_quality.py``): it takes control points a client already
tracks -- a KPI, a data-load step, any checkpoint with an official number -- and returns
which ones show a pattern of information being *managed* rather than *reported*. It never
renders a verdict on any person; every finding is a question for a human to confirm.

Four patterns, each requiring its OWN minimum of independent evidence before it fires --
never one number's word against itself, the same discipline ``scm_agent/citation_gate.py``
applies to citations (``MIN_CITATIONS = 2``):

- **HIDDEN_FACTORY** -- the official value and an independently-sourced ("shadow") value
  for the same control point disagree by more than ``shadow_delta_threshold``.
  Structurally requires two channels: cannot fire from one number alone.
- **GOODHART_RATCHET** -- a target was raised in the period immediately after the metric
  met or beat the *prior* target, with no independent note of a process change.
  Structurally requires 2+ periods of history: cannot fire from one snapshot.
- **ACCOUNTABILITY_VACUUM** -- no owner is assigned to the control point. A single fact,
  not an inference, so it is always reported (typically at low severity on its own) and
  named as a contributing factor whenever it co-occurs with either pattern above.
- **UNCLASSIFIED_WORKAROUND** -- an informal channel runs in parallel to the system of
  record and nobody has classified it ``essential`` (compensates for a real system gap)
  or ``hindrance`` (protects someone's deniability). This module never classifies it
  either -- that call is always handed to a human, because "why does this workaround
  exist" is a conversation, not a query.

Never emits ``EXECUTED``: even the strongest finding here is a reason to go ask someone,
not an automated verdict on their honesty. See ``analyze()``'s ``GuidedOutcome``.
"""

from __future__ import annotations

from dataclasses import dataclass

from nosilo_core.guided import (
    ExecutionOption,
    GuidedOutcome,
    HandoffPacket,
    Residual,
    as_handoff,
    as_options,
)

HIDDEN_FACTORY = "hidden_factory"
GOODHART_RATCHET = "goodhart_ratchet"
ACCOUNTABILITY_VACUUM = "accountability_vacuum"
UNCLASSIFIED_WORKAROUND = "unclassified_workaround"

ESSENTIAL = "essential"
HINDRANCE = "hindrance"

DEFAULT_SHADOW_DELTA_THRESHOLD = 0.15  # share of the shadow value the official value may diverge by

_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}
_SEVERITY_SCORE = {"high": 3.0, "medium": 2.0, "low": 1.0}
_TOP_OPTIONS = 5


@dataclass(frozen=True)
class TargetPeriod:
    """One period's actual vs. target for a control point. Order in the tuple is
    chronological -- the ratchet detector reads consecutive periods pairwise."""

    period: str
    actual: float
    target: float


@dataclass(frozen=True)
class ControlPoint:
    """One KPI / process checkpoint under audit.

    ``official_value``/``shadow_value`` are the two independent channels HIDDEN_FACTORY
    triangulates. ``shadow_value`` is whatever a side channel says -- an interview, a
    parallel spreadsheet, a direct gemba observation -- never a second read of the same
    system of record (that would not be independent evidence).
    """

    name: str
    official_value: float | None = None
    shadow_value: float | None = None
    owner: str = ""
    workaround_present: bool = False
    workaround_classification: str = ""  # "" | ESSENTIAL | HINDRANCE -- "" means unclassified
    history: tuple[TargetPeriod, ...] = ()


@dataclass(frozen=True)
class FrictionFinding:
    """One detected pattern on one control point, with the evidence that triangulated it.

    ``evidence`` is never empty for a real finding (enforced by ``jobs/org_friction_job.py``'s
    QA gate) -- a finding with no stated evidence is indistinguishable from an opinion.
    """

    control_point: str
    pattern: str
    severity: str  # high | medium | low
    detail: str
    evidence: tuple[str, ...] = ()


@dataclass(frozen=True)
class FrictionReport:
    """Everything the job layer needs: every finding, ranked, and the guided verdict."""

    n_control_points: int
    n_flagged: int
    findings: tuple[FrictionFinding, ...]  # ranked, most severe first
    outcome: GuidedOutcome
    summary: str


def _hidden_factory(cp: ControlPoint, *, threshold: float) -> FrictionFinding | None:
    """Fires only when BOTH channels are present and disagree beyond threshold."""
    if cp.official_value is None or cp.shadow_value is None:
        return None
    if cp.shadow_value == 0:
        if cp.official_value == 0:
            return None
        share = 1.0  # any nonzero official value against a zero shadow value is a full split
    else:
        share = abs(cp.official_value - cp.shadow_value) / abs(cp.shadow_value)
    if share < threshold:
        return None
    severity = "high" if share >= 2 * threshold else "medium"
    return FrictionFinding(
        control_point=cp.name,
        pattern=HIDDEN_FACTORY,
        severity=severity,
        detail=(
            f"official value {cp.official_value:g} vs. independently-sourced value "
            f"{cp.shadow_value:g} ({share * 100:.0f}% apart)"
        ),
        evidence=(
            f"system of record reports {cp.official_value:g}",
            f"independent channel reports {cp.shadow_value:g}",
        ),
    )


def _goodhart_ratchet(cp: ControlPoint) -> FrictionFinding | None:
    """Fires on the first consecutive pair where the prior target was met/beaten and the
    next period's target was then raised. Needs 2+ periods; a single snapshot can't show
    a target moving."""
    if len(cp.history) < 2:
        return None
    for prev, nxt in zip(cp.history, cp.history[1:]):
        beat_prior_target = prev.actual >= prev.target
        target_raised = nxt.target > prev.target
        if beat_prior_target and target_raised:
            return FrictionFinding(
                control_point=cp.name,
                pattern=GOODHART_RATCHET,
                severity="medium",
                detail=(
                    f"{prev.period}: hit {prev.actual:g} against a target of {prev.target:g}; "
                    f"{nxt.period}'s target was then raised to {nxt.target:g}"
                ),
                evidence=(
                    f"{prev.period} actual {prev.actual:g} >= target {prev.target:g}",
                    f"{nxt.period} target {nxt.target:g} > {prev.period} target {prev.target:g}",
                ),
            )
    return None


def _accountability_vacuum(cp: ControlPoint) -> FrictionFinding | None:
    """A single fact (owner blank), not an inference -- always reported when true."""
    if cp.owner.strip():
        return None
    return FrictionFinding(
        control_point=cp.name,
        pattern=ACCOUNTABILITY_VACUUM,
        severity="low",
        detail="no accountable owner is assigned to this control point",
        evidence=(f"owner field is blank for '{cp.name}'",),
    )


def _unclassified_workaround(cp: ControlPoint) -> FrictionFinding | None:
    """Flags the gap; never guesses essential vs. hindrance."""
    if not cp.workaround_present or cp.workaround_classification.strip():
        return None
    return FrictionFinding(
        control_point=cp.name,
        pattern=UNCLASSIFIED_WORKAROUND,
        severity="medium",
        detail=(
            "an informal channel runs in parallel to the system of record and has not "
            "been classified essential (compensates for a real gap) or hindrance "
            "(protects someone's deniability) -- this tool will not guess which"
        ),
        evidence=(f"workaround_present=True, workaround_classification='' for '{cp.name}'",),
    )


_DETECTORS = (_goodhart_ratchet, _accountability_vacuum, _unclassified_workaround)


def detect(
    control_points: list[ControlPoint],
    *,
    shadow_delta_threshold: float = DEFAULT_SHADOW_DELTA_THRESHOLD,
) -> tuple[FrictionFinding, ...]:
    """Run all four detectors over every control point; rank by severity, then name."""
    findings: list[FrictionFinding] = []
    for cp in control_points:
        hf = _hidden_factory(cp, threshold=shadow_delta_threshold)
        if hf is not None:
            findings.append(hf)
        for finder in _DETECTORS:
            found = finder(cp)
            if found is not None:
                findings.append(found)
    findings.sort(key=lambda f: (_SEVERITY_RANK.get(f.severity, 3), f.control_point, f.pattern))
    return tuple(findings)


def _options(findings: tuple[FrictionFinding, ...]) -> GuidedOutcome:
    if not findings:
        packet = HandoffPacket(
            title="Gather a shadow reading and an owner map before re-running",
            steps=[
                "For each control point that matters, capture one independently-sourced "
                "reading (interview, side ledger, direct observation) alongside the "
                "official value.",
                "Record who owns each control point, and 2+ periods of actual-vs-target "
                "history where a target exists.",
                "Re-run the audit on the completed intake.",
            ],
            risk_if_skipped=(
                "With only the official channel and a single snapshot, none of the four "
                "patterns can structurally be detected -- not because nothing is wrong, "
                "but because there is nothing yet to triangulate against."
            ),
        )
        residual = Residual(
            description="No control point carried enough independent evidence to triangulate.",
            risk_if_skipped="Organizational friction stays invisible, not absent.",
        )
        return as_handoff(
            "No control point has enough independent evidence to flag; gather a shadow "
            "reading, an owner map, and target history, then re-run.",
            [packet],
            residuals=[residual],
        )

    options: list[ExecutionOption] = []
    for rank, f in enumerate(findings[:_TOP_OPTIONS]):
        options.append(ExecutionOption(
            label=f"Triage {f.control_point} ({f.pattern})",
            summary=f.detail,
            score=_SEVERITY_SCORE.get(f.severity, 0.0),
            recommended=rank == 0,
            action=f"interview:{f.control_point}:{f.pattern}",
            tradeoffs=(
                "Every finding here is a question to ask a person, not a verdict -- "
                "confirm with the control point's owner (or assign one first) before "
                "concluding anything is being gamed or hidden."
            ),
        ))
    residual = Residual(
        description=(
            "This tool never classifies WHY a gap, ratchet, or workaround exists -- only "
            "that the pattern is present. The reason (a genuine system gap, a rational "
            "act of self-protection, or something worth escalating) requires a conversation."
        ),
        risk_if_skipped="Acting on the pattern without the reason risks fixing the wrong thing.",
    )
    n_points = len({f.control_point for f in findings})
    return as_options(
        f"{len(findings)} pattern(s) flagged across {n_points} control point(s); none "
        "auto-resolved -- each is a triage conversation.",
        options,
        residuals=[residual],
    )


def analyze(
    control_points: list[ControlPoint],
    *,
    shadow_delta_threshold: float = DEFAULT_SHADOW_DELTA_THRESHOLD,
) -> FrictionReport:
    """Detect the four patterns across every control point and rank what to triage first."""
    if not control_points:
        raise ValueError("at least one control point is required")
    findings = detect(control_points, shadow_delta_threshold=shadow_delta_threshold)
    outcome = _options(findings)
    n_flagged = len({f.control_point for f in findings})

    by_pattern: dict[str, int] = {}
    for f in findings:
        by_pattern[f.pattern] = by_pattern.get(f.pattern, 0) + 1
    pattern_share = ", ".join(f"{k} x{v}" for k, v in sorted(by_pattern.items()))
    top = (
        f"{findings[0].control_point} ({findings[0].pattern}) is the top-priority triage"
        if findings else "no control point could be triangulated with the evidence given"
    )
    summary = (
        f"Audited {len(control_points)} control point(s); {n_flagged} flagged "
        f"({pattern_share or 'none'}). {top}."
    )
    return FrictionReport(
        n_control_points=len(control_points),
        n_flagged=n_flagged,
        findings=findings,
        outcome=outcome,
        summary=summary,
    )
