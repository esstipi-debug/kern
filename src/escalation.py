"""Escalation-packet builder (Guided Execution Layer, plan §2.14).

When a result must go to a human (dispute, legal exposure, financial threshold), this
bundles context + ranked options + the recommended response + citations and routes it
to the right role with an SLA. Filling the route/SLA defaults per trigger keeps every
escalation actionable - it never lands as an unrouted dead end.

Pure: frozen dataclasses + pure functions, over ``src/guided.py``'s contract.
"""

from __future__ import annotations

from src.guided import EscalationPacket, ExecutionOption, GuidedOutcome, as_escalation

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
