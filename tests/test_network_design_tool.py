"""Tests for the network-design (p-median) agent tool: a nodes CSV -> which p sites to open."""
from pathlib import Path  # noqa: F401  (used by the routing/orchestrator tests Task 3 appends here)

import pandas as pd
import pytest

from jobs import network_design_job as ndj
from scm_agent import intent, llm, tools  # noqa: F401  (Task 3 routing tests)
from scm_agent.orchestrator import Orchestrator  # noqa: F401  (Task 3 orchestrator tests)
from src.deliverable import Deliverable
from src.guided import OPTIONS  # noqa: F401  (Task 3 orchestrator tests)


def _nodes_df() -> pd.DataFrame:
    # two clusters of three; no role column -> candidates default to the demand nodes
    return pd.DataFrame({
        "name": ["D0", "D1", "D2", "D3", "D4", "D5"],
        "x": [0, 1, 2, 10, 11, 12],
        "y": [0, 0, 0, 0, 0, 0],
        "weight": [1, 1, 1, 1, 1, 1],
    })


def test_prepare_defaults_candidates_to_demand_nodes(tmp_path):
    csv = tmp_path / "nodes.csv"
    _nodes_df().to_csv(csv, index=False)
    payload = ndj.prepare(str(csv), {"p": 2})
    assert len(payload["demands"]) == 6
    assert len(payload["sites"]) == 6
    assert payload["p"] == 2


def test_prepare_errors_without_coordinates(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1]}).to_csv(csv, index=False)
    with pytest.raises(ValueError, match="x|y"):
        ndj.prepare(str(csv), {})


def test_prepare_splits_demand_and_candidate_roles(tmp_path):
    df = pd.DataFrame({
        "name": ["cust", "siteA", "siteB"],
        "x": [0, 0, 10], "y": [0, 0, 0],
        "weight": [5, 0, 0],
        "role": ["demand", "candidate", "candidate"],
    })
    csv = tmp_path / "roles.csv"
    df.to_csv(csv, index=False)
    payload = ndj.prepare(str(csv), {"p": 1})
    assert len(payload["demands"]) == 1
    assert len(payload["sites"]) == 2


def test_run_opens_the_two_cluster_medians():
    report = ndj.run(ndj.prepare_records(_nodes_df(), {"p": 2}))
    assert report.feasible
    assert set(report.open_sites) == {"D1", "D4"}
    assert report.total_weighted_distance == pytest.approx(4.0)
    assert report.saving_vs_baseline == pytest.approx(26.0)
    assert report.p == 2
    assert ndj.verify(report) == []


def test_verify_flags_an_infeasible_network():
    df = pd.DataFrame({
        "name": ["A", "B", "C", "only"],
        "x": [0, 1, 2, 0], "y": [0, 0, 0, 0],
        "weight": [10, 10, 10, 0],
        "role": ["demand", "demand", "demand", "candidate"],
        "capacity": [None, None, None, 20],   # 20 < 30 total demand
    })
    report = ndj.run(ndj.prepare_records(df, {"p": 1}))
    assert report.feasible is False
    assert ndj.verify(report) != []


def test_build_deck_is_ascii_deliverable():
    report = ndj.run(ndj.prepare_records(_nodes_df(), {"p": 2}))
    deck = ndj.build_deck(report, client="Acme", citations=("Chopra network design",), confidence=0.85)
    assert isinstance(deck, Deliverable)
    md = deck.to_markdown()
    assert md.isascii()
    assert "Network Design" in md and "## Coverage & handoff" in md


def test_brief_routes_to_network_design():
    reg = tools.build_default_registry()
    res = intent.classify(
        "p-median network optimization: how many distribution centers to open and which sites",
        reg, llm.RulesFallback(),
    )
    assert res.job_type == "network_design"


def test_facility_location_brief_still_routes_to_facility_location():
    # guard: the new tool's keywords must not steal single-facility briefs
    reg = tools.build_default_registry()
    res = intent.classify(
        "facility location / network design: center of gravity for the optimal DC location",
        reg, llm.RulesFallback(),
    )
    assert res.job_type == "facility_location"


def test_orchestrator_runs_network_design_with_ranked_options(tmp_path):
    csv = tmp_path / "nodes.csv"
    _nodes_df().to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())
    res = orch.run(
        "p-median multi-facility: how many dcs to open and which sites",
        data_path=str(csv), overrides={"p": 2}, client="Acme", out_dir=tmp_path,
    )
    assert res.status == "ok" and res.tool == "network_design"
    assert Path(res.deliverables["deck_report"]).exists()
    assert Path(res.deliverables["csv"]).exists()
    assert res.guided is not None and res.guided.status == OPTIONS
    assert sum(1 for o in res.guided.options if o.recommended) == 1
