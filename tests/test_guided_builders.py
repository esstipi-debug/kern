"""Tests for the Guided Execution Layer builders — constructors that produce a
*protected by construction* GuidedOutcome (they cannot return a dead end)."""

import pytest

from nosilo_core.guided import (
    ESCALATED,
    EXECUTED,
    HANDOFF,
    OPTIONS,
    EscalationPacket,
    ExecutionOption,
    HandoffPacket,
    Residual,
    as_escalation,
    as_executed,
    as_handoff,
    as_options,
    passed_guided,
)


def test_as_executed_is_protected():
    out = as_executed("reorder plan applied", confidence=0.9)
    assert out.status == EXECUTED
    assert passed_guided(out)


def test_as_executed_carries_residuals_with_risk():
    out = as_executed(
        "plan applied",
        residuals=[Residual("count bin A12", risk_if_skipped="IRA unproven")],
    )
    assert passed_guided(out)


def test_as_options_auto_flags_recommended_when_none_marked():
    out = as_options(
        "three reorder scenarios",
        [
            ExecutionOption(label="lean", summary="min capital", score=0.5),
            ExecutionOption(label="safe", summary="high service", score=0.8),
        ],
    )
    assert out.status == OPTIONS
    recommended = [o for o in out.options if o.recommended]
    assert len(recommended) == 1
    assert recommended[0].label == "safe"  # highest score
    assert passed_guided(out)


def test_as_options_keeps_explicit_recommendation():
    out = as_options(
        "two scenarios",
        [
            ExecutionOption(label="a", summary="x", score=0.9),
            ExecutionOption(label="b", summary="y", score=0.3, recommended=True),
        ],
    )
    recommended = [o for o in out.options if o.recommended]
    assert [o.label for o in recommended] == ["b"]


def test_as_options_empty_raises():
    with pytest.raises(ValueError):
        as_options("nothing to choose", [])


def test_as_handoff_is_protected():
    out = as_handoff(
        "send the drafted PO",
        [HandoffPacket(title="send PO-1042", artifact="PO text", risk_if_skipped="stockout")],
        confidence=0.85,
    )
    assert out.status == HANDOFF
    assert passed_guided(out)


def test_as_handoff_empty_raises():
    with pytest.raises(ValueError):
        as_handoff("nothing prepared", [])


def test_as_escalation_is_protected():
    out = as_escalation(
        "damage dispute",
        EscalationPacket(reason="liability", route_to="claims manager", sla="24h"),
    )
    assert out.status == ESCALATED
    assert passed_guided(out)
