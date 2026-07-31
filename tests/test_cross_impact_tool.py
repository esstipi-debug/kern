"""Integration guards for the cross_impact tool (spec sec 6.4 + 9.3)."""

from pathlib import Path

import pandas as pd
import pytest

from scm_agent.citation_gate import TOOL_CONCEPTS
from scm_agent.knowledge import KnowledgeBase
from scm_agent.orchestrator import Orchestrator
from scm_agent.tools import build_default_registry


@pytest.fixture
def demand_csv(tmp_path):
    path = tmp_path / "demanda.csv"
    pd.DataFrame({
        "product_id": ["SKU-1"] * 30,
        "quantity": ([100, 110, 95, 105, 120] * 6)[:30],
        "unit_cost": [10.0] * 30,
        "lead_time_days": [14.0] * 30,
    }).to_csv(path, index=False)
    return str(path)


def _params(**over):
    base = {
        "sku": "SKU-1",
        "current": {"q": 200.0, "sl": 0.95},
        "proposed": {"q": 400.0, "sl": 0.95},
        "proposed_by": "comprador",
    }
    base.update(over)
    return base


def test_tool_is_registered():
    assert build_default_registry().get("cross_impact").key == "cross_impact"


def test_tool_declares_the_threshold_as_a_required_client_param():
    tool = build_default_registry().get("cross_impact")
    assert tool.required_client_params == ("objection_thresholds",)


def test_tool_exposes_ranked_options():
    assert build_default_registry().get("cross_impact").options is not None


def test_brief_routes_to_cross_impact():
    reg = build_default_registry()
    top, _ = reg.match("que impacto tiene esta decision en las otras areas")[0]
    assert top.key == "cross_impact"


def test_end_to_end_run_delivers(demand_csv, tmp_path):
    result = Orchestrator().run(
        brief="impacto cruzado de la decision propuesta",
        data_path=demand_csv, job_type="cross_impact",
        overrides=_params(objection_thresholds={"comprador": 1e9, "planner": 1e9,
                                                "cfo": 1e9, "comercial": 1e9}),
        out_dir=tmp_path)
    assert result.status == "ok"
    assert result.guided.status == "options"
    assert "cross_impact.csv" in {Path(p).name for p in result.deliverables.values()}


def test_strict_params_blocks_without_thresholds(demand_csv, tmp_path):
    result = Orchestrator().run(
        brief="impacto cruzado", data_path=demand_csv, job_type="cross_impact",
        overrides=_params(), strict_params=True, out_dir=tmp_path)
    assert result.status == "needs_clarification"
    assert any("objection_thresholds" in c for c in result.clarifications)


def test_without_strict_params_it_still_runs_and_says_indeterminado(demand_csv, tmp_path):
    # honesty must NOT depend on the opt-in flag
    result = Orchestrator().run(
        brief="impacto cruzado", data_path=demand_csv, job_type="cross_impact",
        overrides=_params(), out_dir=tmp_path)
    assert result.status == "ok"
    assert "indeterminado" in result.summary


def test_missing_current_is_needs_clarification(demand_csv, tmp_path):
    params = _params()
    del params["current"]
    result = Orchestrator().run(
        brief="impacto cruzado", data_path=demand_csv, job_type="cross_impact",
        overrides=params, out_dir=tmp_path)
    assert result.status == "needs_clarification"


def test_anchor_concepts_exist_in_the_graph():
    kb = KnowledgeBase()
    for concept in TOOL_CONCEPTS["cross_impact"]:
        assert kb.node_exists(concept), f"anchor {concept} missing from the books graph"


def test_anchors_are_the_expected_four():
    assert TOOL_CONCEPTS["cross_impact"] == (
        "safety_stock", "service_level", "cycle_service_level",
        "cross_functional_integration")
