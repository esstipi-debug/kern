"""Registration + routing tests for iso_ops (tool #54).

Keyword risk: procedure_audit already owns "iso 9001". iso_ops must win on
catalog/detector/floor-evidence phrasing and must not steal SOP-register briefs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from nosilo_core import llm
from nosilo_core.guided import OPTIONS
from nosilo_core.types import JobRequest
from scm_agent import intent, tools

_CSV = Path(__file__).resolve().parent / "fixtures" / "iso" / "planta.csv"


class _SinLlm:
    def available(self) -> bool:
        return False


@pytest.fixture(scope="module")
def registry():
    return tools.build_default_registry()


def test_iso_ops_is_registered_and_count_is_60(registry):
    assert registry.get("iso_ops") is not None
    assert registry.get("iso_ops").requires_data is False
    assert len(registry.list()) == 60


@pytest.mark.parametrize(
    "brief",
    [
        "detectar normas iso de operaciones para la planta de inyeccion",
        "which iso standards apply to this plant given iatf 16949 and product carbon footprint",
        "evaluar evidencia de piso iso 14067 y iso 14083",
        "catalogo iso de cadena de suministro y cobertura iso kern",
    ],
)
def test_client_brief_routes_to_iso_ops(registry, brief: str):
    result = intent.classify(brief, registry, _SinLlm())
    assert result.job_type == "iso_ops", (
        f"{brief!r} routed to {result.job_type!r} (candidates: {result.candidates})"
    )


def test_sop_register_brief_still_goes_to_procedure_audit(registry):
    brief = "revisa mi procedure register exportado de Notion, control de documentos ISO 9001"
    result = intent.classify(brief, registry, _SinLlm())
    assert result.job_type == "procedure_audit"


def test_prepare_without_ficha_asks(registry):
    tool = registry.get("iso_ops")
    prep = tool.prepare(JobRequest(brief="detectar normas iso", data_path=""), llm.RulesFallback())
    assert prep.status == "needs_clarification"
    assert any("ficha" in m.lower() for m in prep.messages)


def test_pipeline_on_plant_csv(registry, tmp_path):
    tool = registry.get("iso_ops")
    prep = tool.prepare(
        JobRequest(brief="detectar normas iso de operaciones", data_path=str(_CSV)),
        llm.RulesFallback(),
    )
    assert prep.status == "ok"
    produced = tool.run(prep.payload, {})
    assert tool.qa(produced.report) == []
    assert produced.report.outcome.status == OPTIONS
    written = tool.deliver(produced.report, tmp_path, "Planta")
    assert written["excel"].exists()
    assert written["csv"].exists()
