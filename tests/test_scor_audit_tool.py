"""Registration + routing tests for scor_iso_audit (E4 pack).

Do not steal "iso 9001" (procedure_audit) or iso_ops catalog/floor phrases.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from nosilo_core import llm
from nosilo_core.guided import HANDOFF
from nosilo_core.types import JobRequest

from scm_agent import intent, tools

_CSV = Path(__file__).resolve().parent / "fixtures" / "audit" / "scor_pack.csv"


class _SinLlm:
    def available(self) -> bool:
        return False


@pytest.fixture(scope="module")
def registry():
    return tools.build_default_registry()


def test_scor_iso_audit_is_registered(registry):
    tool = registry.get("scor_iso_audit")
    assert tool is not None
    assert tool.requires_data is True
    assert tool.required_columns == ("dataset",)


@pytest.mark.parametrize(
    "brief",
    [
        "run the scor iso audit pack for the plant scorecard",
        "emit a scor level 1 scorecard and iso 28000 checklist as evidence",
        "pack de preparacion para auditoria scor con lineage",
        "prepare scor iso audit evidence for the registrar auditor",
    ],
)
def test_client_brief_routes_to_scor_iso_audit(registry, brief: str):
    result = intent.classify(brief, registry, _SinLlm())
    assert result.job_type == "scor_iso_audit", (
        f"{brief!r} routed to {result.job_type!r} (candidates: {result.candidates})"
    )


def test_sop_register_brief_still_goes_to_procedure_audit(registry):
    brief = "revisa mi procedure register exportado de Notion, control de documentos ISO 9001"
    result = intent.classify(brief, registry, _SinLlm())
    assert result.job_type == "procedure_audit"


def test_iso_ops_catalog_brief_still_goes_to_iso_ops(registry):
    brief = "detectar normas iso de operaciones para la planta de inyeccion"
    result = intent.classify(brief, registry, _SinLlm())
    assert result.job_type == "iso_ops"


def test_pipeline_on_tagged_csv(registry, tmp_path):
    tool = registry.get("scor_iso_audit")
    params = {
        "as_of": "2026-09-03",
        "dso_days": 45.0,
        "dpo_days": 30.0,
        "produced_at": "2026-09-07T12:00:00Z",
        "run_id": "tool-scor",
    }
    prep = tool.prepare(
        JobRequest(brief="scor iso audit", data_path=str(_CSV), params=params),
        llm.RulesFallback(),
    )
    assert prep.status == "ok", prep.messages
    produced = tool.run(prep.payload, params)
    assert tool.qa(produced.report) == []
    assert produced.report.outcome.status == HANDOFF
    written = tool.deliver(produced.report, tmp_path, "Acme")
    assert written["workbook"].exists()
    assert written["evidence_json"].exists()
