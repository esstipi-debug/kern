"""Registration + routing tests for audit_evidence (MUS workpaper tool).

Do not steal bare "reconciliation" (IRA) or "acceptance sampling" / "aql".
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nosilo_core import llm
from nosilo_core.guided import HANDOFF
from nosilo_core.types import JobRequest
from scm_agent import intent, tools

_CSV = Path(__file__).resolve().parent / "fixtures" / "audit" / "population.csv"


class _SinLlm:
    def available(self) -> bool:
        return False


@pytest.fixture(scope="module")
def registry():
    return tools.build_default_registry()


def test_audit_evidence_is_registered_and_count_is_58(registry):
    assert registry.get("audit_evidence") is not None
    assert registry.get("audit_evidence").requires_data is True
    assert registry.get("scor_iso_audit") is not None
    assert len(registry.list()) == 58


@pytest.mark.parametrize(
    "brief",
    [
        "prepare a monetary unit sampling workpaper for the inventory audit",
        "run mus sample and gl reconciliation tie-out for audit evidence",
        "build the audit workpaper with trial balance and substantive testing",
        "papel de trabajo mus de evidencia de auditoria del inventario",
    ],
)
def test_client_brief_routes_to_audit_evidence(registry, brief: str):
    result = intent.classify(brief, registry, _SinLlm())
    assert result.job_type == "audit_evidence", (
        f"{brief!r} routed to {result.job_type!r} (candidates: {result.candidates})"
    )


def test_ira_brief_still_goes_to_reconciliation(registry):
    brief = "reconcile system vs physical inventory record accuracy"
    result = intent.classify(brief, registry, _SinLlm())
    assert result.job_type == "reconciliation"


def test_aql_brief_still_goes_to_acceptance_sampling(registry):
    brief = "design an acceptance sampling plan from aql and ltpd"
    result = intent.classify(brief, registry, _SinLlm())
    assert result.job_type == "acceptance_sampling"


def test_prepare_without_file_asks(registry):
    tool = registry.get("audit_evidence")
    prep = tool.prepare(JobRequest(brief="mus sample", data_path=""), llm.RulesFallback())
    assert prep.status == "needs_data"


def test_pipeline_on_population_csv(registry, tmp_path):
    tool = registry.get("audit_evidence")
    params = {
        "tolerable_misstatement": 20000.0,
        "selection_seed": 0,
        "produced_at": "2026-09-07T12:00:00Z",
        "run_id": "tool-audit",
    }
    prep = tool.prepare(
        JobRequest(brief="mus sample", data_path=str(_CSV), params=params),
        llm.RulesFallback(),
    )
    assert prep.status == "ok", prep.messages
    produced = tool.run(prep.payload, params)
    assert tool.qa(produced.report) == []
    assert produced.report.outcome.status == HANDOFF
    written = tool.deliver(produced.report, tmp_path, "Acme")
    assert written["workpaper"].exists()
    assert written["evidence_json"].exists()
