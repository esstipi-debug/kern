"""The data contract every tool declares, and the guard that it stays true.

Two properties: (1) a tool that needs data eventually declares which columns it
needs, tracked against an explicit shrinking list; (2) a declared required column
is actually required -- remove it and the tool's own prepare() must refuse.
"""
import csv

import pytest
from nosilo_core import llm
from nosilo_core.types import JobRequest

from scm_agent import tools

# Tools with requires_data=True that have NOT yet declared their columns.
# This list starts long and shrinks; it never grows. Removing a name here without
# adding a declaration makes test_every_data_tool_declares_its_columns fail.
# Count: 41 (46 requires_data tools total, 5 declared -- data_quality, abc_xyz,
# excess_obsolete, financial_kpis, procedure_audit). Verified against the real
# registry 2026-09-04 -- this header's prior "40 (44 total, 4 declared)" was
# already stale before this task touched it: the list below already had 41
# entries, not 40. procedure_audit (declared, so it does NOT appear below) is
# the only entry this task's own change adds to the true count. Derived via:
#   python -c "from scm_agent import tools; r=tools.build_default_registry(); \
#   print(sorted(t.key for t in r.list() if t.requires_data and not t.required_columns))"
#
# "intake_scan" is a PERMANENT entry here, not a pending one: it consumes a
# raw FOLDER of client files, not one canonical table, so it has no
# `required_columns` to ever declare -- there is nothing to "fix" by moving it
# out of this set in a future batch.
UNDECLARED_TOOLS = frozenset({
    "acceptance_sampling",
    "cost_to_serve",
    "cross_impact",
    "cycle_count",
    "ddmrp",
    "dea",
    "disruption_scan",
    "drp",
    "earned_value",
    "excel_replenishment",
    "facility_location",
    "fefo",
    "flow_map",
    "forecast",
    "hat_settlement",
    "hat_tension",
    "intake_scan",
    "inventory_optimization",
    "landed_cost",
    "launch_readiness",
    "learning_curve",
    "markdown_liquidation",
    "multi_echelon",
    "network_design",
    "org_friction_audit",
    "newsvendor",
    "price_intelligence",
    "pricing",
    "queuing",
    "reconciliation",
    "returns",
    "risk",
    "scheduling",
    "simulation",
    "slotting",
    "sop",
    "sourcing",
    "supplier_management",
    "transportation",
    "vehicle_routing",
    "whatif",
})

DECLARED = ("data_quality", "abc_xyz", "excess_obsolete", "financial_kpis", "procedure_audit")

# One canonical value per column name we know how to synthesize.
_SAMPLE = {
    "product_id": "SKU-1", "name": "Widget", "gtin": "07350053850019",
    "unit_cost": "5.0", "quantity": "10", "on_hand": "100",
    "daily_demand": "2.5", "days_since_last_sale": "30",
    "cogs": "1000.0", "avg_inventory_value": "500.0", "gross_margin": "0.3",
    "units_sold": "80", "units_on_hand": "20", "net_sales": "1500.0",
    "date": "2026-01-01",
    "last_reviewed_at": "2026-01-15", "procedure_id": "PROC-1", "process_area": "Bodega",
    "owner": "Jefe de Operaciones", "approver": "Gerente General", "version": "1.0",
    "approved_at": "2026-01-01", "review_cadence_days": "365", "location": "https://example.com/proc",
    "linked_kpi": "fill_rate", "reaction_plan": "escalate to owner", "status": "approved",
}


def _registry():
    return tools.build_default_registry()


def _write_csv(path, columns):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        for i in range(12):
            writer.writerow([_SAMPLE[c] if c != "product_id" else f"SKU-{i}" for c in columns])
    return path


def test_declared_tools_carry_both_tuples():
    reg = _registry()
    for key in DECLARED:
        tool = reg.get(key)
        assert tool.required_columns, key
        assert isinstance(tool.required_columns, tuple), key
        assert isinstance(tool.optional_columns, tuple), key


def test_every_data_tool_declares_its_columns():
    """A requires_data tool either declares its columns or is on the undeclared list."""
    reg = _registry()
    missing = sorted(
        t.key for t in reg.list()
        if t.requires_data and not t.required_columns and t.key not in UNDECLARED_TOOLS
    )
    assert missing == [], f"undeclared and not listed: {missing}"


def test_undeclared_list_has_no_stale_entries():
    """A tool that declared its columns must be removed from the undeclared list."""
    reg = _registry()
    stale = sorted(k for k in UNDECLARED_TOOLS if reg.get(k) and reg.get(k).required_columns)
    assert stale == [], f"declared but still listed as undeclared: {stale}"


@pytest.mark.parametrize("key", DECLARED)
def test_every_declared_required_column_is_really_required(key, tmp_path):
    """Drop one required column at a time; the tool's own prepare() must refuse.

    This is the anti-drift guard. It covers the dangerous direction: a column the
    tool truly needs but nobody declared would produce a false 'verde'.
    """
    reg = _registry()
    tool = reg.get(key)
    provider = llm.RulesFallback()
    full = list(tool.required_columns) + list(tool.optional_columns)

    complete = _write_csv(tmp_path / f"{key}_full.csv", full)
    ok = tool.prepare(JobRequest(brief="scan", data_path=str(complete)), provider)
    assert ok.status == "ok", f"{key}: the complete fixture should prepare cleanly: {ok.messages}"

    for column in tool.required_columns:
        reduced = [c for c in full if c != column]
        path = _write_csv(tmp_path / f"{key}_no_{column}.csv", reduced)
        result = tool.prepare(JobRequest(brief="scan", data_path=str(path)), provider)
        assert result.status == "needs_data", (
            f"{key} declares '{column}' as required but prepare() accepted a file without it"
        )
