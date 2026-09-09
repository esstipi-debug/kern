"""Wiring tests for the import_case tool: registry, routing, orchestrator deck."""

from pathlib import Path

from nosilo_core import llm

from jobs import import_case_job as icj
from scm_agent import intent, tools
from scm_agent.orchestrator import Orchestrator

FIXTURE = Path(__file__).parent / "fixtures" / "import_case" / "documents.json"


def test_prepare_reads_the_fixture_bundle():
    payload = icj.prepare(str(FIXTURE), {})
    types = {row["doc_type"] for row in payload["slices"]}
    assert types == {"commercial_invoice", "packing_list", "bill_of_lading"}


def test_run_emits_a_chile_pack_without_conflicts():
    report = icj.run(icj.prepare(str(FIXTURE), {}))
    assert icj.verify(report) == []
    assert report.n_conflicts == 0
    labels = {row.label for row in report.pack}
    assert "importador" in labels and "partida_arancelaria_propuesta" in labels
    assert report.unified["invoice_no"] == "F-1001"


def test_write_operational_writes_pack_csv(tmp_path):
    report = icj.run(icj.prepare(str(FIXTURE), {}))
    paths = icj.write_operational(report, tmp_path, "Andes")
    assert paths["pack_csv"].is_file()
    assert paths["json"].is_file()
    text = paths["json"].read_text(encoding="utf-8")
    assert "Not a filed customs" in text


def test_brief_routes_to_import_case():
    res = intent.classify(
        "unify import documents into an expediente de importacion",
        tools.build_default_registry(),
        llm.RulesFallback(),
    )
    assert res.job_type == "import_case"


def test_import_case_does_not_steal_landed_cost_briefs():
    res = intent.classify(
        "what's our landed cost and duty by incoterm",
        tools.build_default_registry(),
        llm.RulesFallback(),
    )
    assert res.job_type == "landed_cost"


def test_orchestrator_runs_import_case(tmp_path):
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())
    res = orch.run(
        "expediente de importacion and chile din pack",
        data_path=str(FIXTURE),
        out_dir=tmp_path,
        client="Andes",
    )
    assert res.status == "ok"
    assert res.tool == "import_case"
    assert "pack_csv" in res.deliverables
