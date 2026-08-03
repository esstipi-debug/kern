"""Tests for the guided bridge — maps every JobResult status to a protected
GuidedOutcome, and verifies the orchestrator attaches one at its boundary.

This is the operational guarantee: no matter how a job ends (ok, needs data,
ambiguous, QA failure, internal error), the caller receives an executable path.
"""

from nosilo_core.guided import ESCALATED, EXECUTED, HANDOFF, OPTIONS, passed_guided
from nosilo_core.types import (
    STATUS_ERROR,
    STATUS_NEEDS_CLARIFICATION,
    STATUS_NEEDS_DATA,
    STATUS_OK,
    STATUS_QA_FAILED,
    JobResult,
)

from scm_agent.guided_bridge import to_guided_outcome
from scm_agent.orchestrator import Orchestrator


def _result(status, **kw):
    base = dict(status=status, tool="inventory_optimization", confidence=0.7,
                deliverables={}, summary="summary line")
    base.update(kw)
    return JobResult(**base)


def test_ok_maps_to_executed_and_protected():
    g = to_guided_outcome(_result(STATUS_OK, deliverables={"excel": "out.xlsx"}))
    assert g.status == EXECUTED
    assert passed_guided(g)


def test_needs_clarification_maps_to_protected_options():
    g = to_guided_outcome(_result(STATUS_NEEDS_CLARIFICATION,
                                  clarifications=["inventory_optimization", "pricing"]))
    assert g.status == OPTIONS
    assert len(g.options) == 2
    assert passed_guided(g)


def test_needs_clarification_without_candidates_still_protected():
    g = to_guided_outcome(_result(STATUS_NEEDS_CLARIFICATION, clarifications=[]))
    assert g.status == OPTIONS
    assert passed_guided(g)  # falls back to a generic "clarify" option, never empty


def test_needs_data_maps_to_handoff():
    g = to_guided_outcome(_result(STATUS_NEEDS_DATA, clarifications=["provide a demand CSV"]))
    assert g.status == HANDOFF
    assert passed_guided(g)


def test_qa_failed_maps_to_escalation_carrying_issues():
    g = to_guided_outcome(_result(STATUS_QA_FAILED, qa_issues=["investment != cycle + safety"]))
    assert g.status == ESCALATED
    assert g.escalation is not None
    assert "investment != cycle + safety" in g.escalation.citations
    assert passed_guided(g)


def test_error_maps_to_escalation():
    g = to_guided_outcome(_result(STATUS_ERROR, confidence=0.0, summary="An internal error occurred."))
    assert g.status == ESCALATED
    assert passed_guided(g)


def test_every_status_yields_a_protected_outcome():
    for status in (STATUS_OK, STATUS_NEEDS_CLARIFICATION, STATUS_NEEDS_DATA,
                   STATUS_QA_FAILED, STATUS_ERROR):
        assert passed_guided(to_guided_outcome(_result(status)))


def test_orchestrator_attaches_guided_outcome():
    # An ambiguous brief with no data ends non-OK, but must still arrive protected.
    result = Orchestrator().run("zzz qqq", out_dir="deliverables/_test_guided")
    assert result.guided is not None
    assert passed_guided(result.guided)
