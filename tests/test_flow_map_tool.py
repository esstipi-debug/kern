"""Tests for the flow_map tool wiring and the orchestrator end to end."""
from pathlib import Path

import pandas as pd
from nosilo_core import llm
from nosilo_core.guided import OPTIONS

from scm_agent import intent, tools


def _movements_csv(tmp_path: Path) -> Path:
    rows = []
    for i in range(8):
        rows.append({
            "ship_date": f"2026-01-{1 + i:02d}", "receipt_date": f"2026-01-{3 + i:02d}",
            "origin": "SUP-1", "destination": "DC-1", "sku": "SKU-A",
            "quantity": 10.0, "unit_cost": 5.0,
        })
    for i in range(8):
        rows.append({
            "ship_date": f"2026-01-{2 + i:02d}", "receipt_date": f"2026-01-{6 + i:02d}",
            "origin": "DC-1", "destination": "STORE-1", "sku": "SKU-A",
            "quantity": 10.0, "unit_cost": 5.0,
        })
    path = tmp_path / "movimientos.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def test_flow_map_is_registered():
    reg = tools.build_default_registry()
    assert reg.get("flow_map") is not None


def test_brief_routes_to_flow_map():
    reg = tools.build_default_registry()
    res = intent.classify(
        "map my end to end flow from suppliers to stores and show the transit time per lane",
        reg, llm.RulesFallback(),
    )
    assert res.job_type == "flow_map"


def test_network_design_brief_is_not_stolen():
    reg = tools.build_default_registry()
    # "weighted distance" was briefly added to facility_location_tool()'s intent_keywords
    # while wiring flow_map (tool #47), then reverted: it collided with no OTHER tool's
    # keywords, but facility_location and network_design both matched "weighted distance"
    # equally, and intent.classify (scm_agent/intent.py) requires a strict score win --
    # so the added keyword turned this brief's former network_design win into a tie,
    # which classify() reports as None. Use a brief that carries a genuine network_design
    # keyword ("which sites to open") alongside "weighted distance" so it still resolves.
    res = intent.classify(
        "which sites to open minimising weighted distance",
        reg, llm.RulesFallback(),
    )
    # The invariant this test exists to protect: flow_map's own intent keywords must
    # not steal a network-design-shaped brief.
    assert res.job_type != "flow_map"
    # The positive half: the brief should actually resolve to one of the two network
    # tools.
    assert res.job_type in {"network_design", "facility_location"}


def test_weighted_distance_briefs_still_route_to_network_design():
    """Regression for the reverted 'weighted distance' keyword (IMPORTANT 3 of the
    2026-07-31 flow_map fix wave): these four briefs already route to network_design
    today via its OWN keywords ("how many dcs", "p-median", "which sites to open",
    "network optimization"), with no help from facility_location. If a future change
    adds a colliding keyword to another tool, intent.classify's strict-score-win rule
    (scm_agent/intent.py:59) can silently turn these wins into ties -> None. Pin them.
    """
    reg = tools.build_default_registry()
    briefs = [
        "how many dcs should I open to minimise weighted distance",
        "p-median study to minimise weighted distance across our network",
        "which sites to open minimising weighted distance",
        "network optimization to reduce weighted distance",
    ]
    for brief in briefs:
        res = intent.classify(brief, reg, llm.RulesFallback())
        assert res.job_type == "network_design", f"{brief!r} routed to {res.job_type!r}"


def test_prepare_without_data_asks_for_the_movements_file():
    from nosilo_core.types import JobRequest

    tool = tools.build_default_registry().get("flow_map")
    prepared = tool.prepare(JobRequest(brief="map my flows", data_path=None), llm.RulesFallback())
    assert prepared.status == "needs_data"
    assert any("movement" in m for m in prepared.messages)


def test_end_to_end_produces_options_and_both_files(tmp_path):
    from nosilo_core.types import JobRequest

    tool = tools.build_default_registry().get("flow_map")
    prepared = tool.prepare(
        JobRequest(brief="map my flows", data_path=str(_movements_csv(tmp_path))),
        llm.RulesFallback(),
    )
    assert prepared.status == "ok"
    produced = tool.run(prepared.payload, {})
    assert tool.qa(produced.report) == []
    paths = tool.deliver(produced.report, tmp_path / "out", "Acme")
    assert paths["csv"].exists() and paths["html"].exists()
    assert produced.report.outcome.status == OPTIONS
