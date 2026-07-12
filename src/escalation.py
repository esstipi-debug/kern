"""Escalation-packet builder (Guided Execution Layer, plan §2.14).

When a result must go to a human (dispute, legal exposure, financial threshold), this
bundles context + ranked options + the recommended response + citations and routes it
to the right role with an SLA. Filling the route/SLA defaults per trigger keeps every
escalation actionable - it never lands as an unrouted dead end.

Pure: frozen dataclasses + pure functions, over ``src/guided.py``'s contract.
"""

from __future__ import annotations

from dataclasses import replace

from src.guided import ESCALATED, OPTIONS, EscalationPacket, ExecutionOption, GuidedOutcome, as_escalation

# Escalation triggers.
DISPUTE = "dispute"                 # OS&D, billing, booking-reference disputes
LEGAL = "legal"                     # customs classification, liability, contracts
FINANCIAL = "financial_threshold"   # spend/commitment above an auto-approve limit
OPERATIONAL = "operational"         # generic ops decision needing a human

# Default route + SLA per trigger. Tuple is (route_to, sla).
_DEFAULTS: dict[str, tuple[str, str]] = {
    DISPUTE: ("claims / account manager", "same business day"),
    LEGAL: ("legal / licensed customs broker", "before any action"),
    FINANCIAL: ("finance approver", "before commitment"),
    OPERATIONAL: ("operations owner", "same business day"),
}
_GENERIC = ("human owner", "same business day")


def build_escalation(
    trigger: str,
    reason: str,
    *,
    route_to: str | None = None,
    recommendation: str = "",
    options: list[ExecutionOption] | None = None,
    citations: list[str] | None = None,
    sla: str | None = None,
) -> EscalationPacket:
    """Build a fully-routed escalation packet, defaulting route/SLA from the trigger."""
    if not reason.strip():
        raise ValueError("escalation requires a non-empty reason")
    default_route, default_sla = _DEFAULTS.get(trigger, _GENERIC)
    return EscalationPacket(
        reason=reason,
        route_to=route_to or default_route,
        recommendation=recommendation,
        options=list(options or []),
        citations=list(citations or []),
        sla=sla or default_sla,
    )


def escalate(
    summary: str,
    trigger: str,
    reason: str,
    *,
    route_to: str | None = None,
    recommendation: str = "",
    options: list[ExecutionOption] | None = None,
    citations: list[str] | None = None,
    sla: str | None = None,
    confidence: float = 1.0,
) -> GuidedOutcome:
    """Route a case to the right human as a protected, never-dead-end outcome."""
    packet = build_escalation(
        trigger,
        reason,
        route_to=route_to,
        recommendation=recommendation,
        options=options,
        citations=citations,
        sla=sla,
    )
    return as_escalation(summary, packet, confidence=confidence)


def _maybe_escalate(outcome: GuidedOutcome, should_escalate: bool, trigger: str, reason: str) -> GuidedOutcome:
    """Shared engine behind ``maybe_escalate_financial``/``maybe_escalate_data_quality``:
    re-route an 'options' outcome to ESCALATED when ``should_escalate`` is True,
    preserving the same ranked options — both inside the escalation packet AND at
    the outcome's top level (``GuidedOutcome.options``, the field every existing
    deck-builder already reads: ``scm_agent/orchestrator.py``'s
    ``deck_options = list(guided.options)`` and ``scm_agent/packages.py``'s
    equivalent) so nothing silently vanishes from the rendered deliverable — but a
    named human must sign off within the trigger's SLA before anything is acted
    on. Pair with ``escalation_banner()`` so the deck states that requirement in
    words, not just data.

    Passes ``outcome`` through unchanged when ``should_escalate`` is False, and
    for any outcome that isn't currently 'options' (already escalated, a handoff,
    or executed) — this only ever *adds* a gate, never removes a stronger one
    already in place.
    """
    if outcome.status != OPTIONS or not should_escalate:
        return outcome
    packet = build_escalation(trigger, reason, recommendation=outcome.summary, options=list(outcome.options))
    escalated = as_escalation(
        outcome.summary, packet, confidence=outcome.confidence, residuals=list(outcome.residuals)
    )
    return replace(escalated, options=list(outcome.options))


def maybe_escalate_financial(outcome: GuidedOutcome, value: float, threshold: float) -> GuidedOutcome:
    """Re-route an 'options' outcome to ESCALATED (financial-threshold trigger) when
    ``value`` exceeds ``threshold`` — the auto-approve limit for acting without a
    human sign-off.

    A writeback tool's ranked options (e.g. "apply the staged restock" / "export
    only") are otherwise presented as freely actionable regardless of size — a
    $340k purchase order gets the exact same treatment as a $500 one. See
    ``_maybe_escalate`` for how the gate itself works.
    """
    return _maybe_escalate(
        outcome, value > threshold, FINANCIAL,
        f"{value:,.0f} exceeds the {threshold:,.0f} auto-approve threshold - "
        "requires finance sign-off before committing.",
    )


def maybe_escalate_data_quality(
    outcome: GuidedOutcome, dropped_fraction: float, threshold: float, *, detail: str
) -> GuidedOutcome:
    """Re-route an 'options' outcome to ESCALATED (operational trigger) when the
    share of source rows dropped during intake (``jobs/intake.py``'s
    ``IntakeQuality.dropped_fraction`` — bad dates, missing quantities, negative
    quantities) exceeds ``threshold``.

    The report itself can be perfectly internally consistent (``jobs/qa.py``'s
    math checks all pass) while still being built on a shrunken, unrepresentative
    slice of the client's real data — the same failure class
    ``jobs/forecast_job.py``'s MASE=inf handling addresses for unvalidated SKUs,
    applied here to the intake step every tool's demand history passes through.
    ``detail`` names which reasons drove the drop (e.g. "48 bad date, 14 negative
    qty") so the escalation reason is actionable, not just a percentage. See
    ``_maybe_escalate`` for how the gate itself works.
    """
    return _maybe_escalate(
        outcome, dropped_fraction > threshold, OPERATIONAL,
        f"{dropped_fraction:.0%} of source rows were dropped during intake ({detail}) - "
        f"exceeds the {threshold:.0%} auto-proceed threshold; verify the source file before trusting this result.",
    )


def escalation_banner(outcome: GuidedOutcome) -> str | None:
    """A one-line, deck-ready statement of why/who/by-when — ``None`` when
    ``outcome`` isn't escalated.

    Every rendered deliverable (the deck's findings, its Coverage & handoff
    section, and the writeback tools' apply-howto document) prepends this when
    present, so an escalation is never silently absent from the actual document a
    human reads — the *data* being correct (``outcome.status == ESCALATED``) is
    not the same guarantee as a human ever seeing it.
    """
    if outcome.status != ESCALATED or outcome.escalation is None:
        return None
    e = outcome.escalation
    return f"ESCALATED - {e.reason} Route to: {e.route_to}. SLA: {e.sla}."
