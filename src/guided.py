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
