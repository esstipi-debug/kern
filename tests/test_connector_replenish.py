"""Tests for the connector-backed replenishment flow (Gap #5, offline execution loop).

``plan_replenishment`` reads an ``InventorySource`` (simulated or over HTTP), forecasts
each SKU from its orders, computes the restock to a target cover, and returns a guided
never-unprotected outcome: a dry-run restock staged through the safe-staging plane when
the source is writable, or a ready-to-execute restock packet otherwise. No task ends in
a dead end and nothing is mutated until a human approves.
"""

from fastapi.testclient import TestClient

from src.connectors import InventoryLevel, Order, OrderLine, Product
from src.connectors.emulator import create_app
from src.connectors.http_client import StoreApiClient
from src.connectors.replenish import plan_replenishment
from src.connectors.simulator import SimulatedStore
from src.guided import EXECUTED, HANDOFF, passed_guided

_PRODUCTS = [Product("SKU-1", "Thin", 20.0, 6.0), Product("SKU-2", "Deep", 50.0, 12.0)]
_STEADY_ORDERS = [
    Order("o1", "2026-01-01", (OrderLine("SKU-1", 10.0, 20.0), OrderLine("SKU-2", 5.0, 50.0))),
    Order("o2", "2026-01-08", (OrderLine("SKU-1", 10.0, 20.0), OrderLine("SKU-2", 5.0, 50.0))),
    Order("o3", "2026-01-15", (OrderLine("SKU-1", 10.0, 20.0), OrderLine("SKU-2", 5.0, 50.0))),
]


def _store(sku1_on_hand: float, sku2_on_hand: float) -> SimulatedStore:
    levels = [InventoryLevel("SKU-1", sku1_on_hand), InventoryLevel("SKU-2", sku2_on_hand)]
    return SimulatedStore(_PRODUCTS, levels, _STEADY_ORDERS)


def test_computes_restock_to_target_cover_for_the_thin_sku():
    store = _store(sku1_on_hand=5.0, sku2_on_hand=500.0)

    plan = plan_replenishment(store, cover_periods=8.0, store=store)

    # SKU-1 forecasts 10/period -> target 80, on-hand 5 -> restock 75. SKU-2 is deep.
    assert plan.restock == {"SKU-1": 75.0}
    line = {ln.sku: ln for ln in plan.lines}["SKU-1"]
    assert line.forecast_per_period == 10.0          # steady 10/period
    assert line.target == 80.0 and line.on_hand == 5.0


def test_thin_sku_yields_a_protected_handoff_packet_staged_dry_run():
    store = _store(sku1_on_hand=5.0, sku2_on_hand=500.0)

    plan = plan_replenishment(store, cover_periods=8.0, store=store)

    assert plan.outcome.status == HANDOFF
    assert passed_guided(plan.outcome)                 # never a dead end
    # staged as a dry-run: inventory is NOT mutated until someone applies it
    assert plan.changeset is not None
    assert {lvl.sku: lvl.available for lvl in store.inventory_levels()}["SKU-1"] == 5.0


def test_applying_the_staged_changeset_updates_inventory():
    store = _store(sku1_on_hand=5.0, sku2_on_hand=500.0)
    plan = plan_replenishment(store, cover_periods=8.0, store=store)

    store.apply_restock(plan.changeset)

    assert {lvl.sku: lvl.available for lvl in store.inventory_levels()}["SKU-1"] == 80.0


def test_well_stocked_store_needs_no_restock_and_is_executed():
    store = _store(sku1_on_hand=500.0, sku2_on_hand=500.0)

    plan = plan_replenishment(store, cover_periods=8.0, store=store)

    assert plan.restock == {}
    assert plan.outcome.status == EXECUTED              # nothing to do, no dead end
    assert plan.changeset is None


def test_read_only_http_source_still_yields_a_restock_packet():
    # Reading over HTTP (no writable store) -> a ready-to-execute PO packet, not a stage.
    backing = _store(sku1_on_hand=5.0, sku2_on_hand=500.0)
    client = StoreApiClient(TestClient(create_app(backing)))

    plan = plan_replenishment(client, cover_periods=8.0)  # no store= -> cannot stage

    assert plan.restock == {"SKU-1": 75.0}
    assert plan.outcome.status == HANDOFF
    assert passed_guided(plan.outcome)
    assert plan.changeset is None                        # nothing staged, but still protected
