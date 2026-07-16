"""Tests for the read-only MCP server (Phase A go-to-market).

Guarantees under test:
- exactly the intended 33 analysis tools are exposed, all annotated read-only/
  non-destructive - the writeback pair (`odoo_replenishment`,
  `excel_replenishment`) and the two non-tabular tools (`leadership_chain`,
  `warehouse_layout`) stay off this surface;
- a real call bridges rows -> temp CSV -> Orchestrator.run() -> a structured
  JSON response carrying the same client-ready report the webapp already builds
  - including for loop-registered (spec-table) tools;
- malformed/insufficient input degrades to a clear status, never a crash;
- the temp CSV a call writes does not leak past that call.
"""

from __future__ import annotations

import json

import pytest

from scm_agent import Orchestrator
from webapp.mcp_server import SERVER_NAME, build_mcp_server

# Deliberately hardcoded (NOT derived from webapp.mcp_tool_specs) so that any
# accidental addition/removal on the exposed surface fails this pin.
EXPECTED_TOOL_NAMES = {
    "linchpin_inventory_optimize",
    "linchpin_classify_abc_xyz",
    "linchpin_newsvendor_order_quantity",
    "linchpin_forecast_demand",
    "linchpin_financial_kpis",
    "linchpin_price_optimize",
    "linchpin_audit_data_quality",
    "linchpin_whatif_sensitivity",
    "linchpin_cost_to_serve",
    "linchpin_landed_cost",
    "linchpin_earned_value",
    "linchpin_learning_curve",
    "linchpin_excess_obsolete",
    "linchpin_markdown_liquidation",
    "linchpin_fefo_expiry",
    "linchpin_inventory_record_accuracy",
    "linchpin_cycle_count_plan",
    "linchpin_returns_disposition",
    "linchpin_sop_plan",
    "linchpin_ddmrp_buffers",
    "linchpin_multi_echelon_stock",
    "linchpin_drp_plan",
    "linchpin_simulate_policy",
    "linchpin_supplier_sourcing",
    "linchpin_acceptance_sampling",
    "linchpin_risk_assessment",
    "linchpin_efficiency_benchmark",
    "linchpin_queuing_staffing",
    "linchpin_job_sequencing",
    "linchpin_transport_mode_select",
    "linchpin_facility_location",
    "linchpin_warehouse_slotting",
    "linchpin_vehicle_routing",
}

_ABC_XYZ_ROWS = [
    {"product_id": "SKU-A", "quantity": 40, "unit_cost": 12.0},
    {"product_id": "SKU-A", "quantity": 42, "unit_cost": 12.0},
    {"product_id": "SKU-A", "quantity": 38, "unit_cost": 12.0},
    {"product_id": "SKU-B", "quantity": 5, "unit_cost": 3.0},
    {"product_id": "SKU-B", "quantity": 60, "unit_cost": 3.0},
    {"product_id": "SKU-B", "quantity": 2, "unit_cost": 3.0},
]


@pytest.fixture
def mcp():
    return build_mcp_server(Orchestrator())


async def _call(mcp, tool_name: str, **params) -> dict:
    content, _structured = await mcp.call_tool(tool_name, {"params": params})
    return json.loads(content[0].text)


# -- surface: exactly the intended 33, all read-only ----------------------------


async def test_server_name():
    assert build_mcp_server().name == SERVER_NAME == "linchpin_mcp"


async def test_exposes_exactly_the_intended_tools(mcp):
    tools = await mcp.list_tools()
    assert {t.name for t in tools} == EXPECTED_TOOL_NAMES


async def test_odoo_replenishment_and_writeback_tools_are_never_exposed(mcp):
    """The writeback pair (odoo/excel replenishment - the only tools that mutate
    a client's system of record) must not be reachable through this surface."""
    tools = await mcp.list_tools()
    names = {t.name for t in tools}
    assert not any("odoo" in n or "writeback" in n or "replenish" in n or "excel" in n for n in names)


async def test_every_tool_is_annotated_read_only_and_non_destructive(mcp):
    tools = await mcp.list_tools()
    assert len(tools) == len(EXPECTED_TOOL_NAMES) == 33
    for tool in tools:
        assert tool.annotations is not None, f"{tool.name} has no annotations"
        assert tool.annotations.readOnlyHint is True, f"{tool.name} must be readOnlyHint=True"
        assert tool.annotations.destructiveHint is False, f"{tool.name} must be destructiveHint=False"


async def test_every_tool_documents_the_shared_input_shape(mcp):
    """Every tool's inputSchema must require `params` with a `rows` field - an
    agent should be able to discover the calling convention from the schema alone."""
    tools = await mcp.list_tools()
    for tool in tools:
        assert "params" in tool.inputSchema.get("properties", {}), tool.name


async def test_every_tool_carries_a_column_contract_description(mcp):
    """The spec-table registration must not silently drop descriptions - each
    tool's description is its calling contract (which columns the rows need)."""
    tools = await mcp.list_tools()
    for tool in tools:
        assert tool.description and "Rows" in tool.description, tool.name


# -- end-to-end bridge: rows -> orchestrator -> structured response -------------


async def test_abc_xyz_end_to_end_returns_ok_with_a_report(mcp):
    result = await _call(mcp, "linchpin_classify_abc_xyz", rows=_ABC_XYZ_ROWS)

    assert result["status"] == "ok"
    assert result["tool"] == "abc_xyz"
    assert result["confidence"] == pytest.approx(1.0)  # job_type override, not brief-matched
    assert result["report_markdown"] and "ABC-XYZ" in result["report_markdown"]


async def test_inventory_optimize_end_to_end_returns_ok_with_a_report(mcp):
    rows = [
        {"date": "2026-01-01", "product_id": "SKU-A", "quantity": 40, "unit_cost": 12.0, "lead_time_days": 7},
        {"date": "2026-01-08", "product_id": "SKU-A", "quantity": 45, "unit_cost": 12.0, "lead_time_days": 7},
        {"date": "2026-01-15", "product_id": "SKU-A", "quantity": 38, "unit_cost": 12.0, "lead_time_days": 7},
        {"date": "2026-01-22", "product_id": "SKU-A", "quantity": 41, "unit_cost": 12.0, "lead_time_days": 7},
    ]

    result = await _call(mcp, "linchpin_inventory_optimize", rows=rows)

    assert result["status"] == "ok"
    assert result["tool"] == "inventory_optimization"
    assert result["report_markdown"]


async def test_spec_registered_tool_end_to_end_excess_obsolete(mcp):
    """A tool exposed via the spec table (not one of the original hand-written 8)
    must bridge rows -> orchestrator -> report exactly the same way."""
    rows = [
        {"product_id": "SKU-A", "on_hand": 900, "daily_demand": 1.0, "unit_cost": 7.5},
        {"product_id": "SKU-B", "on_hand": 40, "daily_demand": 2.0, "unit_cost": 3.0},
    ]

    result = await _call(mcp, "linchpin_excess_obsolete", rows=rows)

    assert result["status"] == "ok"
    assert result["tool"] == "excess_obsolete"
    assert result["confidence"] == pytest.approx(1.0)
    assert result["report_markdown"]


async def test_vehicle_routing_without_capacity_degrades_not_crashes(mcp):
    """vehicle_routing documents `capacity` as a REQUIRED param - without it the
    bridge must surface the tool's needs_data/clarification, never a crash."""
    rows = [{"stop_id": "A", "x": 1.0, "y": 2.0}, {"stop_id": "B", "x": 3.0, "y": 1.0}]

    result = await _call(mcp, "linchpin_vehicle_routing", rows=rows)

    assert result["status"] in ("needs_data", "needs_clarification", "error")
    assert result["report_markdown"] is None


async def test_vehicle_routing_with_capacity_end_to_end(mcp):
    rows = [
        {"stop_id": "A", "x": 1.0, "y": 2.0, "demand": 5},
        {"stop_id": "B", "x": 3.0, "y": 1.0, "demand": 4},
        {"stop_id": "C", "x": -2.0, "y": 2.5, "demand": 6},
    ]

    result = await _call(mcp, "linchpin_vehicle_routing", rows=rows, params={"capacity": 10})

    assert result["status"] == "ok"
    assert result["tool"] == "vehicle_routing"
    assert result["report_markdown"]


async def test_client_label_is_passed_through_to_the_report(mcp):
    result = await _call(mcp, "linchpin_classify_abc_xyz", rows=_ABC_XYZ_ROWS, client_label="Acme Co")

    assert "Acme Co" in result["report_markdown"]


async def test_tool_specific_params_reach_the_underlying_engine(mcp):
    """abc_thresholds narrowed to force a different class split than the default -
    proves `params.params` actually flows through, not just `params.rows`."""
    default_result = await _call(mcp, "linchpin_classify_abc_xyz", rows=_ABC_XYZ_ROWS)
    narrowed = await _call(
        mcp, "linchpin_classify_abc_xyz", rows=_ABC_XYZ_ROWS, params={"abc_thresholds": [0.10, 0.20]}
    )

    assert default_result["status"] == narrowed["status"] == "ok"
    assert default_result["report_markdown"] != narrowed["report_markdown"]


# -- never-unprotected contract, exposed over MCP (parity with POST /api/jobs) ---

# The four legitimate GuidedOutcome statuses (src/guided.py). Three of them
# (options/handoff/escalated) require a human - that contract is the product.
_GUIDED_STATUSES = {"executed", "options", "handoff", "escalated"}


async def test_response_carries_the_guided_outcome_with_an_executable_path(mcp):
    """The never-unprotected contract (src/guided.py) must be machine-readable on
    the MCP surface, exactly as POST /api/jobs already serializes it - an MCP
    client (another agent) needs the ranked options / prepared handoff /
    escalation, not just the human-readable report. abc_xyz on this input
    resolves to `options` with ranked policy moves."""
    result = await _call(mcp, "linchpin_classify_abc_xyz", rows=_ABC_XYZ_ROWS)

    assert result["status"] == "ok"
    guided = result["guided"]
    assert isinstance(guided, dict), "guided must be a serialized GuidedOutcome, not absent"
    assert guided["status"] in _GUIDED_STATUSES
    # A non-EXECUTED outcome must carry an executable path for the human - the
    # whole point of surfacing it (mirrors src/guided.py's own verify gate).
    if guided["status"] != "executed":
        assert guided["options"] or guided["handoffs"] or guided["escalation"] is not None
    # abc_xyz specifically offers ranked options with a recommended one.
    assert guided["status"] == "options"
    assert any(opt["recommended"] for opt in guided["options"])


async def test_guided_key_is_always_present_even_when_degraded(mcp):
    """Even a needs_data/error degrade must include the `guided` key (mirroring
    app.py's `asdict(...) if result.guided is not None else None`), so an MCP
    client never has to guess whether the field was omitted or genuinely null."""
    result = await _call(mcp, "linchpin_inventory_optimize", rows=[{"totally": "unrelated", "columns": 1}])

    assert result["status"] in ("needs_data", "needs_clarification", "error")
    assert "guided" in result  # key present regardless of status; value may be null


# -- graceful degradation, not crashes -------------------------------------------


async def test_missing_required_columns_reports_needs_data_not_a_crash(mcp):
    result = await _call(mcp, "linchpin_inventory_optimize", rows=[{"totally": "unrelated", "columns": 1}])

    assert result["status"] in ("needs_data", "needs_clarification", "error")
    assert result["report_markdown"] is None


async def test_empty_rows_are_rejected_by_input_validation(mcp):
    from mcp.server.fastmcp.exceptions import ToolError

    with pytest.raises(ToolError, match="at least 1 item"):
        await mcp.call_tool("linchpin_classify_abc_xyz", {"params": {"rows": []}})


async def test_non_tabular_row_values_do_not_crash_the_bridge(mcp):
    result = await _call(mcp, "linchpin_classify_abc_xyz", rows=[{"product_id": "SKU-A", "quantity": "not-a-number"}])

    assert result["status"] != "ok"  # degrades to an error/needs_data status, never raises


# -- resource hygiene -------------------------------------------------------------


async def test_temp_csv_does_not_leak_past_the_call(mcp, tmp_path, monkeypatch):
    import tempfile

    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))
    await _call(mcp, "linchpin_classify_abc_xyz", rows=_ABC_XYZ_ROWS)

    leftover = list(tmp_path.glob("linchpin_mcp_*"))
    assert leftover == [], f"temp dir(s) not cleaned up: {leftover}"
