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
# Some entries are PERMANENT, not pending. Each one says why, because "still on
# the list" and "cannot be on any list" are different facts and collapsing them
# is how a batch programme ends up chasing a tool that was never declarable.
#
# "intake_scan" -- consumes a raw FOLDER of client files, not one canonical
# table, so it has no `required_columns` to ever declare.
#
# "cycle_count" -- its contract is a DISJUNCTION and `required_columns` is a
# conjunction. jobs/cycle_count_job.py needs a product column plus ANY ONE of:
# an `abc` class column, a value column, or demand + unit_cost together (see its
# refusal text, "need an 'abc' column, or a value column, or demand + unit_cost
# to classify"). Declaring the demand+cost branch is provable and would pass
# this file's drop test -- and would then tell a client who sent a perfectly
# good ABC file that they are missing two columns they do not need. That is not
# a cautious over-declaration, it is a wrong answer; `no_declarado` at least
# says "I cannot tell" instead of misdirecting the data request.
UNDECLARED_TOOLS = frozenset({
    "cost_to_serve",
    "cross_impact",
    "cycle_count",
    "dea",
    "earned_value",
    "excel_replenishment",
    "hat_settlement",
    "hat_tension",
    "intake_scan",
    "launch_readiness",
    "learning_curve",
    "org_friction_audit",
    "price_intelligence",
    "queuing",
    "risk",
    "scheduling",
    "vehicle_routing",
    "whatif",
})

DECLARED = (
    "data_quality", "abc_xyz", "excess_obsolete", "financial_kpis",
    # batch 1 -- the per-SKU demand-history family
    "forecast", "inventory_optimization", "markdown_liquidation", "pricing", "sop",
    # batch 2 -- inventory and replenishment policy
    "ddmrp", "drp", "fefo", "multi_echelon", "newsvendor", "simulation",
    # batch 3 -- warehouse, count and audit
    "reconciliation", "returns", "slotting",
    # batch 4 -- network, geometry and flow
    "facility_location", "flow_map", "network_design", "transportation",
    # batch 5 -- suppliers and procurement
    "acceptance_sampling", "disruption_scan", "landed_cost", "sourcing", "supplier_management",
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
    # -- batch 2: inventory and replenishment policy ------------------------
    "mean_demand": "100.0", "std_demand": "20.0", "salvage_value": "2.0",
    "goodwill": "4.0", "lead_time_periods": "3", "holding_cost": "1.5",
    "order_cost": "75.0", "backorder_cost": "9.0", "review_period": "4",
    "safety_stock": "25", "lot_size": "50", "adu": "12.0", "dlt": "10",
    "ltf": "0.5", "vf": "0.5", "moq": "40", "order_cycle_days": "7",
    "on_order": "30", "qualified_demand": "15", "supplier": "PROV-1",
    "days_to_expiry": "45",
    # -- batch 3: warehouse, count and audit --------------------------------
    "system_qty": "100", "physical_qty": "97", "returned_units": "5",
    "reason": "damaged", "resale_value": "8.0", "sellable": "1",
    "unit_volume": "0.02",
    # -- batch 4: network, geometry and flow --------------------------------
    "longitude": "-70.65", "latitude": "-33.45", "role": "dc",
    "fixed_cost": "50000", "capacity": "9000", "weight_kg": "120.0",
    "distance_km": "340.0", "shipment_id": "ENV-1", "lane": "SCL-ANF",
    "order_value": "1500.0", "departure_date": "2026-01-01",
    "arrival_date": "2026-01-04", "origin": "SCL", "destination": "ANF",
    # -- batch 5: suppliers and procurement ---------------------------------
    "country": "CL", "annual_value": "250000", "on_time": "1", "in_full": "1",
    "defects": "2", "aql": "0.01", "ltpd": "0.05", "freight": "300",
    "insurance": "50", "duty_rate": "0.06", "handling": "40",
    "broker_fee": "80", "incoterm": "FOB", "single_source": "0",
    "defect_ppm": "120", "financial_risk": "0.3", "geo_risk": "0.2",
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
    # Batch 2 brought jobs that group by something other than the SKU. A drp
    # file with one constant (branch, period) pair is twelve rows of the same
    # cell, and a multi-echelon chain with one stage is not a chain.
    "branch": ("key", lambda k, r: f"SUC-{k}"),
    "stage": ("key", lambda k, r: f"ETAPA-{k}"),
    "period": ("row", lambda k, r: str(1 + r)),
    "lot_id": ("row", lambda k, r: f"LOTE-{r}"),
    # slotting mines order-product affinity: one repeated basket is no affinity.
    "order_id": ("row", lambda k, r: f"OC-{r}"),
    # Coordinates and endpoints must differ per node or there is no network to
    # design, and the arrival must follow the departure or transit is negative.
    "longitude": ("key", lambda k, r: str(-70.65 + k)),
    "latitude": ("key", lambda k, r: str(-33.45 + k)),
    "origin": ("key", lambda k, r: f"O-{k}"),
    "destination": ("key", lambda k, r: f"D-{k}"),
    "departure_date": ("row", lambda k, r: (_DAY_ZERO + timedelta(days=2 * r)).isoformat()),
    "arrival_date": ("row", lambda k, r: (_DAY_ZERO + timedelta(days=2 * r + 1)).isoformat()),
    # network_design splits rows into demand points and candidate sites by role;
    # one constant role means one of the two sets is empty and it refuses.
    "role": ("key", lambda k, r: "dc" if k == 0 else "customer"),
    # A one-supplier panel is not a panel, and an OTIF column that is all 1s
    # measures nothing. supplier_management refuses outright when every risk
    # driver holds the same value for every supplier -- "the risk axis is flat
    # and the Kraljic matrix collapses to a spend ranking" -- so at least one
    # driver has to move.
    "supplier": ("key", lambda k, r: f"PROV-{k}"),
    "on_time": ("row", lambda k, r: "1" if r % 4 else "0"),
    "in_full": ("row", lambda k, r: "1" if r % 5 else "0"),
    "defect_ppm": ("key", lambda k, r: str(80 + 60 * k)),
    "lead_time_days": ("key", lambda k, r: str(10 + 4 * k)),
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
    ("drp", "quantity"): ("demand",),
    # ddmrp is the one tool declaring the shared `product_id` while its own
    # sniffer calls the field part_id. Both names live in its _PART_COLS, and
    # declaring the shared one is what keeps the scan from asking a client for
    # `product_id` and `part_id` as if they were two different columns.
    ("ddmrp", "product_id"): ("part_id",),
    # The p-median jobs name their coordinates x and y. The label carries the
    # surrounding words on purpose: a bare "x" would match almost any message and
    # turn this assertion back into the status-only check it replaced.
    ("facility_location", "longitude"): ("could not find x",),
    ("facility_location", "latitude"): ("could not find y",),
    ("network_design", "longitude"): ("could not find x",),
    ("network_design", "latitude"): ("could not find y",),
    # flow_map keeps its contract in Spanish; the declaration uses the shared
    # canonical, and its own alias table resolves both.
    ("flow_map", "departure_date"): ("fecha_salida",),
    ("flow_map", "arrival_date"): ("fecha_llegada",),
    ("flow_map", "origin"): ("origen",),
    ("flow_map", "destination"): ("destino",),
    ("flow_map", "product_id"): ("producto",),
    ("flow_map", "quantity"): ("cantidad",),
    # Batch 5 keeps its own nouns for the product column.
    ("landed_cost", "product_id"): ("sku",),
    ("landed_cost", "quantity"): ("qty",),
    ("acceptance_sampling", "product_id"): ("part",),
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
def test_every_declared_optional_column_is_really_optional(key, tmp_path):
    """Drop one optional column at a time; the tool must still prepare.

    The mirror of the required check, and the half that was missing. Marking a
    column optional when the tool actually refuses without it is UNDER-declaring,
    and under-declaring is the dangerous direction: src/intake_scan.py reports a
    tool GREEN when every required column is present, so a client whose folder
    lacks a wrongly-optional column is told the tool will run, and it will not.
    A false red costs a pointless data request; a false green costs a wrong
    answer.

    This is not hypothetical. multi_echelon shipped with `mean_demand` and
    `std_demand` marked optional; both are refusals under default params
    ("end-customer demand is required"). The required-only check could never
    have caught it, because the complete fixture carries every optional too.
    """
    reg = _registry()
    tool = reg.get(key)
    provider = llm.RulesFallback()
    full = list(tool.required_columns) + list(tool.optional_columns)

    for column in tool.optional_columns:
        reduced = [c for c in full if c != column]
        path = _write_csv(tmp_path / f"{key}_opt_{column}.csv", reduced)
        result = tool.prepare(JobRequest(brief="scan", data_path=str(path)), provider)
        assert result.status == "ok", (
            f"{key} declares '{column}' as OPTIONAL but prepare() refused a file without "
            f"it: {result.messages}. Either it belongs in required_columns, or the tool "
            f"should stop needing it."
        )


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
