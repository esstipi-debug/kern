"""The data contract every tool declares, and the guard that it stays true.

Two properties: (1) a tool that needs data eventually declares which columns it
needs, tracked against an explicit shrinking list; (2) a declared required column
is actually required -- remove it and the tool's own prepare() must refuse.
"""
import csv
from datetime import date, timedelta

import pytest
from nosilo_core import llm
from nosilo_core.types import JobRequest

from scm_agent import tools

# Tools with requires_data=True that have NOT yet declared their columns.
# This list starts long and shrinks; it never grows. Removing a name here without
# adding a declaration makes test_every_data_tool_declares_its_columns fail.
# Count: 41 (45 requires_data tools out of 50 registered, 4 declared --
# abc_xyz, data_quality, excess_obsolete, financial_kpis). The previous comment
# said "40 (44 ... total)" and both halves were wrong; the set below has always
# had 41 entries. Nothing reads this comment, which is exactly why it drifted.
# Re-derive rather than trust it:
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
    "hat_settlement",
    "hat_tension",
    "intake_scan",
    "landed_cost",
    "launch_readiness",
    "learning_curve",
    "multi_echelon",
    "network_design",
    "org_friction_audit",
    "newsvendor",
    "price_intelligence",
    "queuing",
    "reconciliation",
    "returns",
    "risk",
    "scheduling",
    "simulation",
    "slotting",
    "sourcing",
    "supplier_management",
    "transportation",
    "vehicle_routing",
    "whatif",
})

DECLARED = (
    "data_quality", "abc_xyz", "excess_obsolete", "financial_kpis",
    # batch 1 -- the per-SKU demand-history family
    "forecast", "inventory_optimization", "markdown_liquidation", "pricing", "sop",
)

_DAY_ZERO = date(2026, 1, 1)

# One canonical value per column name we know how to synthesize.
_SAMPLE = {
    "product_id": "SKU-1", "name": "Widget", "gtin": "07350053850019",
    "unit_cost": "5.0", "quantity": "10", "on_hand": "100",
    "daily_demand": "2.5", "days_since_last_sale": "30",
    "cogs": "1000.0", "avg_inventory_value": "500.0", "gross_margin": "0.3",
    "units_sold": "80", "units_on_hand": "20", "net_sales": "1500.0",
    "date": "2026-01-01",
    # -- batch 1: per-SKU demand history ------------------------------------
    # `price` is the SELLING price (jobs/pricing.py), deliberately a different
    # number from unit_cost: a fixture where they matched would let a job that
    # confuses the two still look correct.
    "price": "12.5", "lead_time_days": "14", "lead_time_std": "3.0",
    "revenue": "125.0",
}

# Columns whose value must VARY, and along which axis. A constant column is not
# just unrealistic, it changes what the fixture proves: a job that groups by key
# sees one group, and a job that reads a time series sees one repeated instant.
#
# `key` varies per SKU-block, `row` varies per line. Everything not listed here
# takes its constant from _SAMPLE, which is the right default -- most columns are
# per-SKU attributes where repetition is faithful.
_VARYING = {
    "product_id": ("key", lambda k, r: f"SKU-{k}"),
    # Ten-day steps, not consecutive days: a job that resamples to months or
    # quarters before it counts periods needs the fixture to SPAN more than one.
    # jobs/sop_job.py refuses with "need at least two periods of demand history"
    # on fifteen consecutive January days, and that refusal is about the fixture,
    # not about the declaration under test.
    "date": ("row", lambda k, r: (_DAY_ZERO + timedelta(days=10 * r)).isoformat()),
}

# Rows per key, and keys per file. Several jobs refuse a SKU with too few
# observations before they ever look at the columns -- jobs/hats_job.py sets
# MIN_OBS = 5 -- so a fixture of 12 rows with 12 distinct SKUs prepares to
# "no usable SKUs" and reads as a wrong declaration when the declaration is fine.
# Three keys is also the minimum that makes a ranking or a panel meaningful.
_KEYS = 3
_ROWS_PER_KEY = 5


def _registry():
    return tools.build_default_registry()


def _cell(column, key_index, row_index):
    axis, make = _VARYING.get(column, (None, None))
    if axis is None:
        return _SAMPLE[column]
    return make(key_index, row_index)


def _write_csv(path, columns, keys=_KEYS, rows_per_key=_ROWS_PER_KEY):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(columns)
        row = 0
        for key_index in range(keys):
            for _ in range(rows_per_key):
                writer.writerow([_cell(c, key_index, row) for c in columns])
                row += 1
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


# A job whose refusal message uses its own vocabulary for a field, rather than the
# declared column name. Declaring the translation here is the point: it is how the
# next person finds out that abc_xyz calls `quantity` "demand" without reading
# jobs/abc_xyz_job.py. Silently accepting any refusal would hide the whole class
# of bug this assertion exists to catch.
_REFUSAL_LABELS = {
    ("abc_xyz", "quantity"): ("demand",),
    ("sop", "quantity"): ("demand",),
}


def _names_the_column(messages, key, column):
    """Does this refusal actually blame the column we dropped?"""
    said = " ".join(messages).lower()
    candidates = {column.lower()}
    # `product_id` -> "product", `avg_inventory_value` -> "inventory", "value".
    # Short fragments ("id", "kg") match too much to be evidence.
    candidates |= {tok for tok in column.lower().split("_") if len(tok) >= 4}
    candidates |= set(_REFUSAL_LABELS.get((key, column), ()))
    return any(c in said for c in candidates)


@pytest.mark.parametrize("key", DECLARED)
def test_every_declared_required_column_is_really_required(key, tmp_path):
    """Drop one required column at a time; the tool's own prepare() must refuse.

    This is the anti-drift guard. It covers the dangerous direction: a column the
    tool truly needs but nobody declared would produce a false 'verde'.

    The refusal must also NAME the dropped column. Checking only the status is a
    hole big enough to drive a false declaration through: a job that rejects the
    fixture for an unrelated reason -- a numeric coercion failure, a negative
    quantity, an empty-result guard like "no SKUs found in the data" -- returns
    the same `needs_data`, and the test goes green over a column the tool never
    required. Asserting on the message turns "the status matched" into "the tool
    said it missed this exact field".
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
        assert _names_the_column(result.messages, key, column), (
            f"{key} refused a file without '{column}', but its message never names that "
            f"field, so the refusal may be about something else entirely: {result.messages}. "
            f"Either the declaration is wrong, or the job's own word for the field belongs "
            f"in _REFUSAL_LABELS."
        )
