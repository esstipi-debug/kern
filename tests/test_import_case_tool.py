"""Wiring tests for the import_case tool: registry, routing, orchestrator deck."""

import json
from dataclasses import replace
from pathlib import Path

from nosilo_core import llm

from jobs import import_case_job as icj
from scm_agent import intent, tools
from scm_agent.orchestrator import Orchestrator
from src.import_case import Conflict, DocumentSlice, merge_case

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


# --- QA invariants ----------------------------------------------------------
# A QA failure wipes every deliverable (scm_agent/orchestrator.py:206), so
# verify() guards the report's own integrity, not the completeness of the
# client's paperwork: an incomplete case IS the deliverable.


def _case(**over):
    report = icj.run(icj.prepare(str(FIXTURE), {}))
    return replace(report, **over) if over else report


def test_verify_does_not_block_an_incomplete_case():
    case = merge_case([
        DocumentSlice("commercial_invoice", {"invoice_no": "F-1"}, "inv"),
    ])

    assert case.n_missing > 0
    assert case.complete_enough is False
    assert icj.verify(case) == []  # the gap list is what the client is paying for


def test_verify_flags_counters_that_do_not_reconcile():
    assert any("conflict" in i for i in icj.verify(_case(n_conflicts=7)))
    assert any("missing" in i for i in icj.verify(_case(n_missing=7)))
    assert any("document" in i for i in icj.verify(_case(n_documents=7)))


def test_verify_flags_a_conflict_that_is_not_marked_contested():
    report = _case()
    conflicted = replace(
        report,
        conflicts=(Conflict("total_qty", (("commercial_invoice", "1"),), "test"),),
        n_conflicts=1,
    )

    issues = icj.verify(conflicted)
    assert any("contested" in i and "total_qty" in i for i in issues)


def test_verify_flags_a_pack_value_without_a_source():
    report = _case()
    orphan = tuple(
        replace(row, value="X", sources=()) if row.canonical == "incoterm" else row
        for row in report.pack
    )

    assert any("source" in i for i in icj.verify(replace(report, pack=orphan)))


def test_verify_flags_an_impossible_copy_reduction():
    assert icj.verify(_case(copy_reduction_pct=140.0))


def test_case_json_records_whether_the_case_is_presentable(tmp_path):
    report = icj.run(icj.prepare(str(FIXTURE), {}))
    paths = icj.write_operational(report, tmp_path, "Andes")
    payload = json.loads(paths["json"].read_text(encoding="utf-8"))

    assert payload["complete_enough"] is report.complete_enough
