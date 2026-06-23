"""Tests for the coverage gate (Guided Execution Layer, plan §2.14).

Extends jobs/qa.py: a deliverable is *covered* when it honours the never-unprotected
contract PLUS the §2.14 residual-block requirements - every handoff states the risk if
skipped, and every escalation routes to a named human with an SLA. Ties together the
decision-options engine and the escalation builder through one gate.
"""

from jobs.qa import coverage_gate, covered
from src.decision_options import Objective, Scenario, decide
from src.escalation import FINANCIAL, escalate
from src.guided import (
    EscalationPacket,
    ExecutionOption,
    GuidedOutcome,
    HandoffPacket,
    as_executed,
    as_handoff,
)


def test_executed_outcome_is_covered():
    assert coverage_gate(as_executed("done safely")) == []
    assert covered(as_executed("done safely"))


def test_unprotected_outcome_is_flagged():
    # status says escalated but no packet -> the never-unprotected contract fails.
    bad = GuidedOutcome(status="escalated", summary="oops")

    issues = coverage_gate(bad)

    assert issues  # includes the verify_guided "unprotected" finding
    assert not covered(bad)


def test_handoff_without_risk_is_flagged():
    packet = HandoffPacket(title="enter counts", steps=["count bin A"], risk_if_skipped="")
    outcome = as_handoff("manual count needed", [packet])

    issues = coverage_gate(outcome)

    assert any("risk" in i.lower() for i in issues)


def test_handoff_with_risk_is_covered():
    packet = HandoffPacket(
        title="enter counts", steps=["count bin A"], risk_if_skipped="stockout if skipped"
    )
    assert covered(as_handoff("manual count needed", [packet]))


def test_escalation_without_sla_is_flagged():
    outcome = GuidedOutcome(
        status="escalated",
        summary="x",
        escalation=EscalationPacket(reason="dispute", route_to="legal", sla=""),
    )
    assert any("sla" in i.lower() for i in coverage_gate(outcome))


def test_escalation_without_route_is_flagged():
    outcome = GuidedOutcome(
        status="escalated",
        summary="x",
        escalation=EscalationPacket(reason="dispute", route_to="", sla="today"),
    )
    assert any("route" in i.lower() for i in coverage_gate(outcome))


def test_escalation_without_reason_is_flagged():
    outcome = GuidedOutcome(
        status="escalated",
        summary="x",
        escalation=EscalationPacket(reason="", route_to="legal", sla="today"),
    )
    assert any("reason" in i.lower() for i in coverage_gate(outcome))


def test_builder_escalation_passes_the_gate():
    outcome = escalate(
        "claim needs a human",
        FINANCIAL,
        "above the $5k threshold",
        options=[ExecutionOption("settle", "settle", score=0.5)],
        citations=["BOL #1"],
    )
    assert covered(outcome)


def test_decision_options_output_passes_the_gate():
    outcome = decide(
        "pick a plan",
        [Scenario("A", "lean", {"cost": 100.0}), Scenario("B", "rich", {"cost": 200.0})],
        [Objective("cost", maximize=False)],
    )
    assert covered(outcome)
