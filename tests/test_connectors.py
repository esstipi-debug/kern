"""Tests for the offline connector simulator (Gap #5 unblock, no live API keys).

A ``SimulatedStore`` stands in for a Shopify/Amazon/ERP backend entirely in memory:
products, inventory levels and orders on the read side; a demand bridge that feeds the
existing engines; and restock on the write side routed through the battle-tested
safe-staging writeback plane (dry-run -> idempotent apply -> audit/rollback). This lets
the whole pipeline run end-to-end offline; swapping in a real adapter later only changes
the backend, not the chain.
"""

from src.connectors import InventoryLevel, InventorySource, Order, OrderLine, Product
from src.connectors.simulator import SimulatedStore, demo_store
from src.sources import DataFrameDemandSource

_PRODUCTS = [Product("SKU-1", "Widget", price=20.0, cost=12.0),
             Product("SKU-2", "Gadget", price=50.0, cost=30.0)]
_LEVELS = [InventoryLevel("SKU-1", 100.0), InventoryLevel("SKU-2", 40.0)]
_ORDERS = [
    Order("o1", "2026-01-05", (OrderLine("SKU-1", 3.0, 20.0), OrderLine("SKU-2", 1.0, 50.0))),
    Order("o2", "2026-01-06", (OrderLine("SKU-1", 2.0, 20.0),)),
    Order("o3", "2026-02-10", (OrderLine("SKU-2", 4.0, 50.0),)),
]


def _store() -> SimulatedStore:
    return SimulatedStore(_PRODUCTS, _LEVELS, _ORDERS)


# -- read side ----------------------------------------------------------------


def test_simulated_store_satisfies_the_inventory_source_protocol():
    assert isinstance(_store(), InventorySource)


def test_lists_products_and_inventory_levels():
    store = _store()

    assert {p.sku for p in store.list_products()} == {"SKU-1", "SKU-2"}
    levels = {lvl.sku: lvl.available for lvl in store.inventory_levels()}
    assert levels == {"SKU-1": 100.0, "SKU-2": 40.0}


def test_orders_can_be_filtered_since_a_date():
    store = _store()

    recent = store.orders(since="2026-02-01")

    assert [o.order_id for o in recent] == ["o3"]


# -- demand bridge into the existing engines ----------------------------------


def test_demand_frame_feeds_the_demand_source_pipeline():
    store = _store()

    src = DataFrameDemandSource(store.demand_frame())

    assert set(src.list_products()) == {"SKU-1", "SKU-2"}
    # SKU-1 was ordered 3 then 2 -> the engine sees that demand series.
    assert list(src.demand_series("SKU-1")) == [3.0, 2.0]


# -- write side: restock through the safe-staging plane -----------------------


def test_stage_restock_is_a_dry_run_until_applied():
    store = _store()

    changeset = store.stage_restock({"SKU-2": 60.0}, idempotency_key="r1", reason="cover Q1")

    # staging does not mutate the store
    assert {lvl.sku: lvl.available for lvl in store.inventory_levels()}["SKU-2"] == 40.0
    assert changeset.risk_tier == "reversible"


def test_apply_restock_updates_inventory_and_is_idempotent():
    store = _store()
    changeset = store.stage_restock({"SKU-2": 60.0}, idempotency_key="r1")

    first = store.apply_restock(changeset)
    levels = {lvl.sku: lvl.available for lvl in store.inventory_levels()}

    assert first.applied is True
    assert levels["SKU-2"] == 100.0           # 40 + 60

    # same idempotency key never lands twice
    second = store.apply_restock(changeset)
    assert second.applied is False and second.idempotent_skip is True


def test_restock_can_be_rolled_back():
    store = _store()
    changeset = store.stage_restock({"SKU-1": 50.0}, idempotency_key="r2")
    store.apply_restock(changeset)

    store.rollback("r2")

    assert {lvl.sku: lvl.available for lvl in store.inventory_levels()}["SKU-1"] == 100.0


# -- demo factory -------------------------------------------------------------


def test_demo_store_is_a_consistent_non_empty_store():
    store = demo_store()

    products = store.list_products()
    levels = store.inventory_levels()
    assert products and levels
    # every inventory level corresponds to a real product
    skus = {p.sku for p in products}
    assert all(lvl.sku in skus for lvl in levels)
    assert not store.demand_frame().empty
