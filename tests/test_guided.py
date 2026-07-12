"""Tests for the Guided Execution Layer — the "never leave the user unprotected" contract.

A GuidedOutcome is the agent's promise that no task ends in a dead end: it is either
EXECUTED safely, or it carries at least one executable path for the human (ranked
options, a prepared handoff packet, or an escalation). `verify_guided` is the QA gate
(same shape as jobs/qa.py): empty list = passed.
"""

import pytest

from src.guided import (
    ESCALATED,
    EXECUTED,
    HANDOFF,
    OPTIONS,
    OWNER_HUMAN,
    EscalationPacket,
    ExecutionOption,
    GuidedOutcome,
    HandoffPacket,
    Residual,
    passed_guided,
    recommend,
    verify_guided,
)

# ── recommend(): always surfaces a default the user can act on ──────────────────

def test_recommend_returns_flagged_option():
    opts = [
        ExecutionOption(label="A", summary="cheap", score=0.4),
        ExecutionOption(label="B", summary="balanced", score=0.5, recommended=True),
    ]
    assert recommend(opts).label == "B"


def test_recommend_falls_back_to_highest_score():
    opts = [
        ExecutionOption(label="A", summary="low", score=0.2),
        ExecutionOption(label="B", summary="high", score=0.9),
    ]
    assert recommend(opts).label == "B"


def test_recommend_empty_raises():
    with pytest.raises(ValueError):
        recommend([])


# ── the core guarantee: a non-executed outcome must carry an executable path ─────

def test_executed_outcome_is_protected():
    outcome = GuidedOutcome(status=EXECUTED, summary="reorder plan applied", confidence=0.95)
    assert verify_guided(outcome) == []


def test_non_executed_without_any_path_is_flagged_unprotected():
    outcome = GuidedOutcome(status=OPTIONS, summary="needs a decision")  # no options attached
    issues = verify_guided(outcome)
    assert any("unprotected" in i.lower() for i in issues)


def test_options_outcome_requires_options():
    good = GuidedOutcome(
        status=OPTIONS,
        summary="three reorder scenarios",
        confidence=0.8,
        options=[
            ExecutionOption(label="lean", summary="min capital", score=0.6),
            ExecutionOption(label="safe", summary="high service", score=0.7, recommended=True),
        ],
    )
    assert verify_guided(good) == []


def test_handoff_outcome_requires_a_handoff():
    empty = GuidedOutcome(status=HANDOFF, summary="please sign")
    assert any("handoff" in i.lower() for i in verify_guided(empty))


def test_handoff_must_have_steps_or_artifact():
    packet = HandoffPacket(title="approve the count adjustment", risk_if_skipped="stock stays wrong")
    outcome = GuidedOutcome(status=HANDOFF, summary="human step", handoffs=[packet])
    assert any("steps" in i.lower() or "artifact" in i.lower() for i in verify_guided(outcome))


def test_handoff_with_artifact_is_protected():
    packet = HandoffPacket(
        title="send the drafted PO",
        artifact="PO-1042 to Acme: 240 units SKU-A @ $50, deliver by 2026-07-15",
        risk_if_skipped="stockout in ~3 weeks",
    )
    outcome = GuidedOutcome(status=HANDOFF, summary="PO ready to send", confidence=0.9, handoffs=[packet])
    assert verify_guided(outcome) == []


def test_escalated_outcome_requires_escalation_packet():
    outcome = GuidedOutcome(status=ESCALATED, summary="OS&D dispute")
    assert any("escalation" in i.lower() for i in verify_guided(outcome))


def test_escalated_with_packet_is_protected():
    packet = EscalationPacket(
        reason="damage liability dispute",
        route_to="claims manager",
        recommendation="file claim within carrier window",
        sla="24h",
    )
    outcome = GuidedOutcome(status=ESCALATED, summary="hand to claims", escalation=packet)
    assert verify_guided(outcome) == []


# ── residuals must always state the risk (no silent gaps) ────────────────────────

def test_residual_without_stated_risk_is_flagged():
    outcome = GuidedOutcome(
        status=EXECUTED,
        summary="plan applied, one human step remains",
        residuals=[Residual(description="physically count bin A12", owner=OWNER_HUMAN)],
    )
    assert any("risk" in i.lower() for i in verify_guided(outcome))


def test_residual_with_risk_is_protected():
    outcome = GuidedOutcome(
        status=EXECUTED,
        summary="plan applied",
        residuals=[
            Residual(
                description="physically count bin A12",
                owner=OWNER_HUMAN,
                risk_if_skipped="adjustment not validated; IRA stays unproven",
            )
        ],
    )
    assert verify_guided(outcome) == []


# ── confidence sanity ────────────────────────────────────────────────────────────

def test_confidence_out_of_range_is_flagged():
    outcome = GuidedOutcome(status=EXECUTED, summary="done", confidence=1.5)
    assert any("confidence" in i.lower() for i in verify_guided(outcome))


# ── option score finiteness — options are ExecutionOption.score-ranked, and every
# GuidedOutcome using this contract is now serialized to JSON as an API response
# (webapp/app.py's SafeJSONResponse uses allow_nan=False) — a non-finite score must
# never silently reach that boundary; catch it here, at the ONE shared QA gate every
# tool's verify() already calls, instead of relying on each tool's own bespoke check
# to happen to catch it (e.g. jobs/risk_job.py's total-EMV check does, coincidentally
# — but nothing guarantees every tool's aggregate check would).

def test_nan_option_score_is_flagged():
    outcome = GuidedOutcome(
        status=OPTIONS, summary="pick one",
        options=[ExecutionOption(label="a", summary="bad", score=float("nan"), recommended=True)],
    )
    issues = verify_guided(outcome)
    assert any("score" in i.lower() and "a" in i for i in issues)


def test_infinite_option_score_is_flagged():
    outcome = GuidedOutcome(
        status=OPTIONS, summary="pick one",
        options=[ExecutionOption(label="a", summary="bad", score=float("inf"), recommended=True)],
    )
    assert any("score" in i.lower() for i in verify_guided(outcome))


def test_non_finite_score_inside_an_escalation_packets_options_is_also_flagged():
    """maybe_escalate_financial (src/escalation.py) copies the same ranked options
    into EscalationPacket.options - that nested copy must be checked too, not just
    the top-level GuidedOutcome.options."""
    bad_option = ExecutionOption(label="a", summary="bad", score=float("nan"))
    outcome = GuidedOutcome(
        status=ESCALATED, summary="needs finance sign-off",
        escalation=EscalationPacket(reason="over threshold", route_to="finance", options=[bad_option]),
    )
    assert any("score" in i.lower() for i in verify_guided(outcome))


def test_finite_option_scores_pass_clean():
    outcome = GuidedOutcome(
        status=OPTIONS, summary="pick one",
        options=[
            ExecutionOption(label="a", summary="fine", score=3.0, recommended=True),
            ExecutionOption(label="b", summary="fine too", score=-1.5),
        ],
    )
    assert verify_guided(outcome) == []


# ── passed_guided mirrors jobs/qa.py passed() ────────────────────────────────────

def test_passed_guided_true_when_clean():
    outcome = GuidedOutcome(status=EXECUTED, summary="done", confidence=0.9)
    assert passed_guided(outcome) is True


def test_passed_guided_false_when_unprotected():
    outcome = GuidedOutcome(status=HANDOFF, summary="needs human")  # nothing attached
    assert passed_guided(outcome) is False
