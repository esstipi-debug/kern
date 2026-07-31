"""Tests for the flow_map tool wiring and the orchestrator end to end."""
from pathlib import Path

import pandas as pd

from scm_agent import intent, llm, tools
from src.guided import OPTIONS


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
    res = intent.classify(
        "where should I open distribution centres to minimise weighted distance",
        reg, llm.RulesFallback(),
    )
    assert res.job_type in {"network_design", "facility_location"}


def test_prepare_without_data_asks_for_the_movements_file():
    from scm_agent.types import JobRequest

    tool = tools.build_default_registry().get("flow_map")
    prepared = tool.prepare(JobRequest(brief="map my flows", data_path=None), llm.RulesFallback())
    assert prepared.status == "needs_data"
    assert any("movement" in m for m in prepared.messages)


def test_end_to_end_produces_options_and_both_files(tmp_path):
    from scm_agent.types import JobRequest

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
