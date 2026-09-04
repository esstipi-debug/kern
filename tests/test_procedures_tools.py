"""Registration + routing tests for procedure_audit and procedure_draft.

Wires two new tools atop jobs/procedure_audit_job.py and jobs/procedure_draft_job.py
(Tasks 1-4): a SOP document-control audit that reads a client's exported procedure
register, and a bilingual SOP draft (Job Breakdown Sheet + control plan + training)
rendered from one of three templates.

The routing risk is the whole point of this file: the ``sop*`` family elsewhere in
this repo means Sales & Operations Planning, a different discipline from a Standard
Operating Procedure, and ``scm_agent/intent.py``'s strict keyword win (top_score >= 1
AND top_score > second_score, scm_agent/intent.py:59) means a tie with ``sop``,
``cycle_count``, ``org_friction_audit``, ``inventory_optimization``,
``acceptance_sampling`` or ``data_quality`` sends the brief to needs_clarification
instead of the right tool -- never runs it at all.
"""

from __future__ import annotations

import pytest
from nosilo_core import llm
from nosilo_core.guided import HANDOFF
from nosilo_core.types import JobRequest

from scm_agent import intent, tools


class _SinLlm:
    """No LLM backend: forces routing to be resolved by keywords alone.

    Copied from tests/test_agent_service_scan.py:20 -- the strictest condition, and
    the one CI runs under. If a tool only routes when an LLM is available, it is
    unreachable on an install with no key.
    """

    def available(self) -> bool:
        return False


@pytest.fixture(scope="module")
def registry():
    return tools.build_default_registry()


def test_both_tools_are_registered_and_count_is_53(registry):
    assert registry.get("procedure_audit") is not None
    assert registry.get("procedure_draft") is not None
    assert len(registry.list()) == 53


@pytest.mark.parametrize(
    "brief",
    [
        "auditar el registro de procedimientos: cuales SOPs estan vencidos y sin dueno",
        "which SOPs are overdue for review and who owns each procedure",
        "revisa mi procedure register exportado de Notion, control de documentos ISO 9001",
        "procedimientos operativos sin dueno y revisiones vencidas",
    ],
)
def test_client_brief_routes_to_procedure_audit(registry, brief: str):
    result = intent.classify(brief, registry, _SinLlm())
    assert result.job_type == "procedure_audit", (
        f"{brief!r} routed to {result.job_type!r} (candidates: {result.candidates})"
    )


@pytest.mark.parametrize(
    "brief",
    [
        "borrador de procedimiento operativo estandar para reposicion con hoja de desglose de tarea",
        "draft a standard operating procedure for cycle counting with a control plan",
        "write the SOP for replenishment as a job breakdown sheet with a training timetable",
        "redactame el sop de conteo ciclico con plan de control",
    ],
)
def test_client_brief_routes_to_procedure_draft(registry, brief: str):
    result = intent.classify(brief, registry, _SinLlm())
    assert result.job_type == "procedure_draft", (
        f"{brief!r} routed to {result.job_type!r} (candidates: {result.candidates})"
    )


@pytest.mark.parametrize(
    "brief, expected",
    [
        ("redacta el SOP del ciclo S&OP con dueno, aprobador y cadencia de revision", "procedure_draft"),
        ("run the monthly S&OP demand and supply plan", "sop"),
    ],
)
def test_mixed_sop_and_s_and_op_briefs_win_strictly(registry, brief: str, expected: str):
    """The bare word 'SOP' never routes to ``sop`` (that tool only matches '"s&op"',
    with the ampersand) -- but the new tools' keywords must not create a tie with it
    either. Each brief here mixes both vocabularies on purpose, and each one still
    has a strict winner: 'redacta el SOP...' scores procedure_draft 2 ("redacta el
    sop", "cadencia de revision") against sop's 1 ("s&op"); 'run the monthly S&OP...'
    scores sop 3 against procedure_draft/procedure_audit's 0. See
    ``test_a_brief_naming_both_disciplines_is_ambiguous_and_asks`` below for the one
    case in this family that does NOT have a strict winner -- verified, and ruled
    correct behavior, not a bug."""
    result = intent.classify(brief, registry, _SinLlm())
    assert result.job_type == expected, (
        f"{brief!r} routed to {result.job_type!r} (candidates: {result.candidates})"
    )


def test_a_brief_naming_both_disciplines_is_ambiguous_and_asks(registry):
    """'registro de procedimientos' (procedure_audit, SOP document control) and
    's&op' (sop, Sales & Operations Planning) each score exactly one keyword hit on
    this brief -- naming both disciplines at equal weight is unresolvable by keyword
    rank, so the strict-win rule (top_score >= 1 AND top_score > second_score,
    scm_agent/intent.py:59) correctly refuses to guess and asks instead. This is the
    designed behavior, not a bug: forcing a winner here would mean widening a
    keyword tuple, which the brief for this tool explicitly forbids and which would
    risk stealing briefs elsewhere. What makes this worth asserting (not just
    accepting None) is that the operator still sees both real options."""
    brief = "registro de procedimientos del ciclo s&op: cuales estan vencidos"
    result = intent.classify(brief, registry, _SinLlm())
    assert result.job_type is None, (
        f"{brief!r} unexpectedly routed to {result.job_type!r}: no longer a tie?"
    )
    assert set(result.candidates) == {"sop", "procedure_audit"}, (
        f"{brief!r} candidates were {result.candidates!r}, expected exactly sop + procedure_audit tied"
    )


@pytest.mark.parametrize(
    "brief, expected",
    [
        # Verbatim from examples/run_capability_smoke.py::_BRIEF for these five keys.
        ("run the S&OP demand cycle", "sop"),
        ("build a cycle count program", "cycle_count"),
        ("set up reorder points and safety stock", "inventory_optimization"),
        ("design an acceptance sampling plan", "acceptance_sampling"),
        ("audit master data quality", "data_quality"),
        # examples/run_capability_smoke.py::_BRIEF has no entry for org_friction_audit
        # (that dict covers 40 of the 51 already-registered tools, not all of them) --
        # taken instead from tests/test_org_friction_tool.py::test_brief_routes_to_org_friction,
        # the established routing test for that tool.
        ("check if the fill rate KPI is being gamed, some kind of hidden factory or ratchet effect",
         "org_friction_audit"),
    ],
)
def test_s_and_op_cycle_count_and_org_friction_briefs_keep_routing_home(registry, brief: str, expected: str):
    result = intent.classify(brief, registry, _SinLlm())
    assert result.job_type == expected, (
        f"{brief!r} routed to {result.job_type!r} (candidates: {result.candidates})"
    )


def test_no_procedure_tool_steals_a_replenishment_draft_brief(registry):
    result = intent.classify("draft a replenishment plan for next month", registry, _SinLlm())
    assert not (result.job_type or "").startswith("procedure_"), (
        f"routed to {result.job_type!r} (candidates: {result.candidates})"
    )


def test_audit_pipeline_end_to_end_on_sample_fixture(registry, tmp_path):
    tool = registry.get("procedure_audit")
    params = {"as_of": "2026-09-03"}
    prepared = tool.prepare(
        JobRequest(
            brief="auditar el registro de procedimientos",
            data_path="data/sample_procedure_register.csv",
            params=params,
        ),
        llm.RulesFallback(),
    )
    assert prepared.status == "ok", prepared.messages

    produced = tool.run(prepared.payload, params)
    assert tool.qa(produced.report) == []
    assert produced.report.outcome.status == HANDOFF

    paths = tool.deliver(produced.report, tmp_path / "out", "Acme")
    assert paths["csv"].exists()
    assert paths["calendar_csv"].exists()

    deck_paths = tool.deck(
        produced.report, tmp_path / "deck_out", "Acme", (), 0.7, produced.report.outcome.options,
    )
    assert deck_paths["report"].exists() and deck_paths["workbook"].exists()


def test_draft_pipeline_end_to_end_without_data(registry, tmp_path):
    tool = registry.get("procedure_draft")
    params = {
        "process": "replenishment", "owner": "Jefe de Operaciones",
        "approver": "Gerente General", "as_of": "2026-09-03",
    }
    prepared = tool.prepare(
        JobRequest(brief="redacta el sop de reposicion", params=params), llm.RulesFallback(),
    )
    assert prepared.status == "ok", prepared.messages

    produced = tool.run(prepared.payload, params)
    assert tool.qa(produced.report) == []
    assert produced.report.outcome.status == HANDOFF

    paths = tool.deliver(produced.report, tmp_path / "out", "Acme")
    assert paths["md"].exists()
    assert paths["xlsx"].exists()

    deck_paths = tool.deck(
        produced.report, tmp_path / "deck_out", "Acme", (), 0.7, produced.report.outcome.options,
    )
    assert deck_paths["report"].exists() and deck_paths["workbook"].exists()


def test_audit_prepare_without_data_asks_for_the_register(registry):
    tool = registry.get("procedure_audit")
    prepared = tool.prepare(
        JobRequest(brief="auditar el registro de procedimientos", data_path=None), llm.RulesFallback(),
    )
    assert prepared.status == "needs_data"
    joined = " ".join(prepared.messages).lower()
    assert "name" in joined or "procedimiento" in joined
    assert "last_reviewed_at" in joined or "ultima_revision" in joined or "ultima revision" in joined
    assert "as_of" in joined


def test_audit_prepare_rejects_a_malformed_as_of(registry):
    """Reproduces the whole-branch review's finding: as_of='03/09/2026' used to reach
    run() unvalidated and surface through the orchestrator as a generic "An internal
    error occurred." instead of the job's own clear message. Validating here, at the
    prepare() boundary, means the orchestrator's tool.prepare() short-circuits with
    needs_data before tool.run() is ever called -- see scm_agent/orchestrator.py's
    prepare/run split."""
    tool = registry.get("procedure_audit")
    prepared = tool.prepare(
        JobRequest(
            brief="auditar el registro de procedimientos",
            data_path="data/sample_procedure_register.csv",
            params={"as_of": "03/09/2026"},
        ),
        llm.RulesFallback(),
    )
    assert prepared.status == "needs_data"
    joined = " ".join(prepared.messages)
    assert "as_of" in joined
    assert "03/09/2026" in joined


def test_audit_prepare_rejects_a_non_numeric_review_cadence_days(registry):
    tool = registry.get("procedure_audit")
    prepared = tool.prepare(
        JobRequest(
            brief="auditar el registro de procedimientos",
            data_path="data/sample_procedure_register.csv",
            params={"as_of": "2026-09-03", "review_cadence_days": "abc"},
        ),
        llm.RulesFallback(),
    )
    assert prepared.status == "needs_data"
    assert "review_cadence_days" in " ".join(prepared.messages)


def test_audit_prepare_rejects_a_non_positive_review_cadence_days(registry):
    """Minor 3: cadence <= 0 must be refused here too -- a silently accepted 0 flips
    OVERDUE_REVIEW's severity mix on a nonsense parameter (see
    jobs/procedure_audit_job.py::_parse_positive_int)."""
    tool = registry.get("procedure_audit")
    prepared = tool.prepare(
        JobRequest(
            brief="auditar el registro de procedimientos",
            data_path="data/sample_procedure_register.csv",
            params={"as_of": "2026-09-03", "review_cadence_days": 0},
        ),
        llm.RulesFallback(),
    )
    assert prepared.status == "needs_data"
    assert "review_cadence_days" in " ".join(prepared.messages)


def test_audit_prepare_rejects_a_negative_due_soon_days(registry):
    tool = registry.get("procedure_audit")
    prepared = tool.prepare(
        JobRequest(
            brief="auditar el registro de procedimientos",
            data_path="data/sample_procedure_register.csv",
            params={"as_of": "2026-09-03", "due_soon_days": -5},
        ),
        llm.RulesFallback(),
    )
    assert prepared.status == "needs_data"
    assert "due_soon_days" in " ".join(prepared.messages)


def test_draft_prepare_without_process_asks_which_of_three(registry):
    tool = registry.get("procedure_draft")
    prepared = tool.prepare(JobRequest(brief="redacta el sop", params={}), llm.RulesFallback())
    assert prepared.status == "needs_clarification"
    joined = " ".join(prepared.messages).lower()
    for key in ("replenishment", "cycle_count", "sop_cycle"):
        assert key in joined
