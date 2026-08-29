"""Wiring tests for the prospect_diagnostic Tool: registration, routing, E2E."""

from pathlib import Path

from nosilo_core import llm
from nosilo_core.guided import OPTIONS
from nosilo_core.types import JobRequest

from jobs.prospect_diagnostic_job import EXAMPLE_FIXTURE
from scm_agent import intent, tools
from scm_agent.orchestrator import Orchestrator


def test_registry_includes_prospect_diagnostic_tool():
    reg = tools.build_default_registry()
    tool = reg.get("prospect_diagnostic")
    assert tool.requires_data is True
    assert tool.required_columns == ("date", "product_id", "quantity")
    assert tool.optional_columns == ("unit_cost",)


def test_brief_routes_to_prospect_diagnostic():
    reg = tools.build_default_registry()
    res = intent.classify(
        "run el espejo prospect diagnostic: dead catalog share and recoverable lost sales range",
        reg, llm.RulesFallback(),
    )
    assert res.job_type == "prospect_diagnostic"


def test_espejo_keywords_do_not_steal_other_briefs():
    reg = tools.build_default_registry()
    p = llm.RulesFallback()
    assert intent.classify(
        "set up reorder points and safety stock", reg, p,
    ).job_type == "inventory_optimization"
    assert intent.classify(
        "audit our sku master for duplicate skus and validate gtins (data quality / mdm)",
        reg, p,
    ).job_type == "data_quality"
    assert intent.classify(
        "scan the folder and tell me what can i run given the data readiness",
        reg, p,
    ).job_type == "intake_scan"
    assert intent.classify(
        "classify the portfolio by abc xyz", reg, p,
    ).job_type == "abc_xyz"


def test_prepare_without_data_needs_data():
    tool = tools.build_default_registry().get("prospect_diagnostic")
    prepared = tool.prepare(JobRequest(brief="el espejo"), llm.RulesFallback())
    assert prepared.status == "needs_data"


def test_end_to_end_prepare_run_qa_deliver(tmp_path):
    tool = tools.build_default_registry().get("prospect_diagnostic")
    provider = llm.RulesFallback()

    prepared = tool.prepare(
        JobRequest(brief="el espejo", data_path=str(EXAMPLE_FIXTURE)), provider,
    )
    assert prepared.status == "ok"

    produced = tool.run(prepared.payload, {})
    assert tool.qa(produced.report) == []

    paths = tool.deliver(produced.report, tmp_path / "out", "Acme")
    assert paths["report"].exists()
    assert paths["csv"].exists()
    paths["report"].read_text(encoding="utf-8").encode("ascii")


def test_orchestrator_espejo_end_to_end_emits_deck_and_options(tmp_path):
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())
    res = orch.run(
        "run el espejo prospect diagnostic: dead catalog share and recoverable lost sales range",
        data_path=str(EXAMPLE_FIXTURE), client="Acme", out_dir=tmp_path,
    )

    assert res.status == "ok"
    assert res.tool == "prospect_diagnostic"
    assert "report" in res.deliverables
    assert Path(res.deliverables["deck_report"]).exists()
    assert res.guided is not None
    assert res.guided.status == OPTIONS
    assert len(res.guided.options) >= 2
    assert sum(1 for o in res.guided.options if o.recommended) == 1
