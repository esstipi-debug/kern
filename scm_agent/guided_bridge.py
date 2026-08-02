"""Bridge: map any JobResult to a protected GuidedOutcome.

This is where the orchestrator honors the "never leave the user unprotected"
contract. Whatever status a job ends in, the caller receives an executable path:
- ok                  -> EXECUTED  (deliverables produced)
- needs_clarification -> OPTIONS   (the candidate capabilities to choose from)
- needs_data          -> HANDOFF   (a prepared "provide the data" step)
- qa_failed           -> ESCALATED (issues bundled for the data owner to fix)
- error / unknown     -> ESCALATED (routed to support)
"""

from __future__ import annotations

from nosilo_core.guided import (
    EscalationPacket,
    ExecutionOption,
    GuidedOutcome,
    HandoffPacket,
    as_escalation,
    as_executed,
    as_handoff,
    as_options,
)
from nosilo_core.types import (
    STATUS_NEEDS_CLARIFICATION,
    STATUS_NEEDS_DATA,
    STATUS_OK,
    STATUS_QA_FAILED,
    JobResult,
)


def to_guided_outcome(result: JobResult) -> GuidedOutcome:
    """Return a protected GuidedOutcome for any JobResult — never a dead end."""
    if result.status == STATUS_OK:
        return as_executed(result.summary, confidence=result.confidence)

    if result.status == STATUS_NEEDS_CLARIFICATION:
        options = [ExecutionOption(label=c, summary=c) for c in result.clarifications]
        if not options:
            options = [ExecutionOption(label="clarify", summary="describe the goal in more detail")]
        return as_options(result.summary, options, confidence=result.confidence)

    if result.status == STATUS_NEEDS_DATA:
        steps = list(result.clarifications) or ["provide the required data file"]
        packet = HandoffPacket(
            title="provide data",
            steps=steps,
            risk_if_skipped="the agent cannot run this capability without the data",
        )
        return as_handoff(result.summary, [packet], confidence=result.confidence)

    if result.status == STATUS_QA_FAILED:
        packet = EscalationPacket(
            reason="QA gate failed — output withheld to protect the client",
            route_to="data owner / analyst",
            recommendation="fix the flagged inputs and re-run",
            citations=list(result.qa_issues),
        )
        return as_escalation(result.summary, packet, confidence=result.confidence)

    # STATUS_ERROR or any unrecognized status
    packet = EscalationPacket(
        reason="internal error",
        route_to="support",
        recommendation="retry the request or report the issue",
    )
    return as_escalation(result.summary, packet, confidence=result.confidence)
