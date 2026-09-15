"""Guided Execution Layer — the "never leave the user unprotected" contract.

Cross-cutting layer (Capability §2.14 of the expansion plan) that wraps every other
capability. Its job is simple and strict: **no task ends in a dead end.** A result is
either EXECUTED safely by the agent, or it carries at least one *executable path* the
human can act on — ranked options, a prepared handoff packet, or an escalation — plus
an explicit statement of any residual the human must cover and the risk of skipping it.

This module is pure (no external deps), mirroring the analytical-core style: frozen
dataclasses + pure functions, with a `verify_guided` QA gate shaped exactly like
``jobs/qa.py`` (returns ``list[str]``; empty == passed).
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass, field, replace

# Who must perform a residual step.
OWNER_HUMAN = "human"
OWNER_AGENT = "agent"

# Outcome statuses — the four ways a task can legitimately end.
EXECUTED = "executed"     # the agent did it safely (or staged it for one-click apply)
OPTIONS = "options"       # the agent offers ranked, executable options to choose from
HANDOFF = "handoff"       # a human-only step, prepared by the agent (artifact + steps)
ESCALATED = "escalated"   # routed to the right human with context (dispute/legal/$)

_NON_EXECUTED = (OPTIONS, HANDOFF, ESCALATED)
_ALL_STATUSES = (EXECUTED, *_NON_EXECUTED)


@dataclass(frozen=True)
class ExecutionOption:
    """One executable choice with its trade-offs and the exact action behind it."""

    label: str
    summary: str
    score: float = 0.0
    recommended: bool = False
    action: str = ""        # the concrete, ready-to-run action (e.g. a staged change id)
    tradeoffs: str = ""


@dataclass(frozen=True)
class Residual:
    """Something the agent did NOT do: what the human must cover, and the risk if skipped."""

    description: str
    owner: str = OWNER_HUMAN
    risk_if_skipped: str = ""


@dataclass(frozen=True)
class HandoffPacket:
    """A human-executable action prepared by the agent: steps + pre-filled artifact + risk."""

    title: str
    steps: list[str] = field(default_factory=list)
    artifact: str = ""      # pre-filled draft: PO text, email, count sheet, claim form...
    data: dict = field(default_factory=dict)
    deadline: str = ""
    risk_if_skipped: str = ""


@dataclass(frozen=True)
class EscalationPacket:
    """Everything a human needs to act on a dispute / legal / financial-threshold case."""

    reason: str
    route_to: str
    recommendation: str = ""
    options: list[ExecutionOption] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    sla: str = ""


@dataclass(frozen=True)
class GuidedOutcome:
    """The never-dead-end contract for any consequential agent result."""

    status: str
    summary: str
    confidence: float = 1.0
    options: list[ExecutionOption] = field(default_factory=list)
    handoffs: list[HandoffPacket] = field(default_factory=list)
    escalation: EscalationPacket | None = None
    residuals: list[Residual] = field(default_factory=list)


def recommend(options: list[ExecutionOption]) -> ExecutionOption:
    """Return the option the user should default to: the flagged one, else the highest score.

    Raises ``ValueError`` on an empty list — there is no safe default to surface.
    """
    if not options:
        raise ValueError("no options to recommend from")
    flagged = [o for o in options if o.recommended]
    if flagged:
        return max(flagged, key=lambda o: o.score)
    return max(options, key=lambda o: o.score)


def _has_executable_path(outcome: GuidedOutcome) -> bool:
    return bool(outcome.options or outcome.handoffs or outcome.escalation is not None)


def verify_guided(outcome: GuidedOutcome) -> list[str]:
    """Return a list of QA issues. Empty list = the outcome honors the contract."""
    issues: list[str] = []

    # An invented status used to pass this gate untouched: it is not in
    # _NON_EXECUTED, so the unprotected check skips it, and it equals none of
    # OPTIONS / HANDOFF / ESCALATED, so the three packet checks skip it too. A
    # result with an invalid status produced zero issues. The builders below
    # make it unlikely, but GuidedOutcome(status="ok", ...) built by hand is
    # legal Python, and the contract's conformance vectors caught it.
    #
    # Deliberately NOT an early return. The rules that read the status stay
    # silent on their own - they are keyed on the four names - while the ones
    # that do not depend on it (confidence, handoff shape, residual risk,
    # non-finite score) still have something true to say and must keep saying
    # it. Returning here would hide them behind a typo in one field.
    if outcome.status not in _ALL_STATUSES:
        issues.append(
            f"unknown status '{outcome.status}': not one of "
            + ", ".join(_ALL_STATUSES)
        )

    if not 0.0 <= outcome.confidence <= 1.0:
        issues.append(f"confidence out of [0,1]: {outcome.confidence}")

    # The core guarantee: a non-executed outcome must offer an executable path.
    if outcome.status in _NON_EXECUTED and not _has_executable_path(outcome):
        issues.append(
            f"unprotected: status '{outcome.status}' offers no executable path "
            "(no options, handoff, or escalation)"
        )

    if outcome.status == OPTIONS and not outcome.options:
        issues.append("status 'options' but no options provided")
    if outcome.status == HANDOFF and not outcome.handoffs:
        issues.append("status 'handoff' but no handoff packet provided")
    if outcome.status == ESCALATED and outcome.escalation is None:
        issues.append("status 'escalated' but no escalation packet provided")

    for h in outcome.handoffs:
        if not h.steps and not h.artifact:
            issues.append(f"handoff '{h.title}' has neither steps nor a prepared artifact")

    for r in outcome.residuals:
        if not r.risk_if_skipped.strip():
            issues.append(f"residual '{r.description}' lacks a stated risk_if_skipped")

    # Every GuidedOutcome is now serialized to JSON as an API response
    # (webapp/app.py's SafeJSONResponse rejects NaN/Infinity per allow_nan=False) - a
    # non-finite score must never silently reach that boundary. Checked at this ONE
    # shared gate (every tool's verify() calls this) rather than relying on each
    # tool's own bespoke aggregate check to happen to catch it. Escalation packets
    # carry their own (possibly identical) options list - checked too.
    all_options = list(outcome.options)
    if outcome.escalation is not None:
        all_options += outcome.escalation.options
    for o in all_options:
        if not math.isfinite(o.score):
            issues.append(f"option '{o.label}' has a non-finite score: {o.score}")

    return issues


def passed_guided(outcome: GuidedOutcome) -> bool:
    """True when the outcome honors the never-unprotected contract."""
    return not verify_guided(outcome)


# ── Builders — construct outcomes that are protected by construction ──────────────
# Domain skills use these instead of the raw dataclass so a result can never come
# out as a dead end (empty options / handoff / escalation raise immediately).


# ── Confidence from coverage ──────────────────────────────────────────────────────
# The builders below default to confidence=1.0, which is only honest when the inputs
# were complete. Each named gap below is a measured way the inputs were NOT complete;
# a caller that knows about one must subtract it rather than keep claiming certainty.
COVERAGE_PENALTY: dict[str, float] = {
    # a ranking criterion every alternative scored identically on (src/mcdm.py)
    "ignored_criterion": 0.15,
    # a canonical column intake synthesized wholesale (jobs/intake.py)
    "synthesized_critical_column": 0.20,
    # annual_value reduces to units because unit cost is flat (src/classification.py)
    "value_ranks_on_units": 0.25,
    # most registered data tools have not declared a data contract, so a folder audit
    # can only speak for a minority of them (jobs/intake_scan_job.py). The verdict it
    # DOES give is sound; its COVERAGE of the registry is not.
    "undeclared_tools_dominate": 0.25,
    # un hito de la cadena que el export del cliente nunca registro, asi que el
    # dwell de esa etapa no existe (jobs/fulfillment_chain_job.py). Se repite una
    # vez por hito ausente: perder pack_end y load_end cuesta el doble que perder
    # solo uno, porque cada hueco parte la cadena en un lugar distinto.
    "milestone_hop_unavailable": 0.15,
}
CONFIDENCE_FLOOR = 0.10


def confidence_from_coverage(
    gaps: Iterable[str], *, base: float = 1.0, floor: float = CONFIDENCE_FLOOR
) -> float:
    """Derive a confidence from named coverage gaps, one documented penalty each.

    Repeat a gap name once per occurrence (two dead criteria cost twice one). Raises
    ``KeyError`` on an unknown gap so a typo cannot silently mean "no penalty".
    """
    total = 0.0
    for gap in gaps:
        total += COVERAGE_PENALTY[gap]
    return max(floor, base - total)


def as_executed(
    summary: str,
    *,
    confidence: float = 1.0,
    residuals: list[Residual] | None = None,
) -> GuidedOutcome:
    """The agent did the work (deliverables produced / change staged for one-click apply)."""
    return GuidedOutcome(
        status=EXECUTED, summary=summary, confidence=confidence, residuals=list(residuals or [])
    )


def as_options(
    summary: str,
    options: list[ExecutionOption],
    *,
    confidence: float = 1.0,
    residuals: list[Residual] | None = None,
) -> GuidedOutcome:
    """Offer ranked options; auto-flag the best as recommended if none is marked."""
    if not options:
        raise ValueError("as_options requires at least one option")
    opts = list(options)
    if not any(o.recommended for o in opts):
        best = recommend(opts)
        opts = [replace(o, recommended=(o is best)) for o in opts]
    return GuidedOutcome(
        status=OPTIONS, summary=summary, confidence=confidence, options=opts,
        residuals=list(residuals or []),
    )


def as_handoff(
    summary: str,
    packets: list[HandoffPacket],
    *,
    confidence: float = 1.0,
    residuals: list[Residual] | None = None,
) -> GuidedOutcome:
    """Hand a prepared, human-executable step to the user (artifact + steps + risk)."""
    if not packets:
        raise ValueError("as_handoff requires at least one handoff packet")
    return GuidedOutcome(
        status=HANDOFF, summary=summary, confidence=confidence, handoffs=list(packets),
        residuals=list(residuals or []),
    )


def as_escalation(
    summary: str,
    packet: EscalationPacket,
    *,
    confidence: float = 1.0,
    residuals: list[Residual] | None = None,
) -> GuidedOutcome:
    """Route a dispute / legal / financial-threshold case to the right human, with context."""
    return GuidedOutcome(
        status=ESCALATED, summary=summary, confidence=confidence, escalation=packet,
        residuals=list(residuals or []),
    )
