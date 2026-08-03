"""Tests for jobs/shopify_stock_sync.py."""

from __future__ import annotations

from jobs.shopify_stock_sync import (
    TARGET,
    idempotency_key,
    plan_stock_sync,
)
from src.connectors.shopify_inventory import (
    InMemoryShopifyInventory,
    ShopifyInventoryStore,
)

LOC = "gid://shopify/Location/1"


def _store(stock: dict[str, int]) -> ShopifyInventoryStore:
    return ShopifyInventoryStore(InMemoryShopifyInventory(stock={s: {LOC: q} for s, q in stock.items()}),
                                 location_id=LOC)


def test_no_divergence_produces_no_changeset():
    report = plan_stock_sync(_store({"SKU-1": 40}), {"SKU-1": 40})
    assert report.changeset is None
    assert report.missing_skus == ()


def test_divergence_produces_a_changeset_naming_the_sku():
    report = plan_stock_sync(_store({"SKU-1": 37}), {"SKU-1": 40})
    assert report.changeset is not None
    assert report.changeset.target == TARGET
    changes = report.changeset.changes
    assert len(changes) == 1
    assert changes[0].entity_id == "SKU-1"
    assert changes[0].field == "quantity"
    assert float(changes[0].before) == 37.0
    assert float(changes[0].after) == 40.0


def test_planning_never_writes_to_shopify():
    rpc = InMemoryShopifyInventory(stock={"SKU-1": {LOC: 37}})
    store = ShopifyInventoryStore(rpc, location_id=LOC)
    plan_stock_sync(store, {"SKU-1": 40})
    # stage() is a dry run: Shopify must be untouched, and no write key recorded.
    assert rpc.get_inventory_level("gid://shopify/InventoryItem/SKU-1", LOC) == 37
    assert rpc.idempotency_keys == []


def test_sku_missing_in_shopify_is_reported_and_skipped():
    report = plan_stock_sync(_store({"SKU-1": 37}), {"SKU-1": 40, "SKU-NOPE": 5})
    assert report.missing_skus == ("SKU-NOPE",)
    assert report.changeset is not None
    assert [c.entity_id for c in report.changeset.changes] == ["SKU-1"]


def test_only_divergent_skus_enter_the_changeset():
    report = plan_stock_sync(_store({"SKU-1": 40, "SKU-2": 12}), {"SKU-1": 40, "SKU-2": 20})
    assert [c.entity_id for c in report.changeset.changes] == ["SKU-2"]


def test_empty_desired_is_a_valid_run_with_nothing_to_do():
    report = plan_stock_sync(_store({"SKU-1": 40}), {})
    assert report.changeset is None
    assert report.missing_skus == ()


def test_idempotency_key_is_deterministic_and_content_derived():
    a = idempotency_key({"SKU-1": {"quantity": 40.0}})
    b = idempotency_key({"SKU-1": {"quantity": 40.0}})
    c = idempotency_key({"SKU-1": {"quantity": 41.0}})
    assert a == b
    assert a != c
    assert a.startswith("shopify-stock-sync:")


def test_idempotency_key_ignores_dict_ordering():
    one = idempotency_key({"SKU-1": {"quantity": 1.0}, "SKU-2": {"quantity": 2.0}})
    two = idempotency_key({"SKU-2": {"quantity": 2.0}, "SKU-1": {"quantity": 1.0}})
    assert one == two
