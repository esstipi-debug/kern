"""Tests for src/connectors/shopify_inventory.py."""

from __future__ import annotations

import pytest

from src import writeback
from src.connectors.shopify_inventory import (
    ShopifyInventoryError,
    ShopifyInventoryStore,
    demo_shopify_inventory,
)


def test_in_memory_shopify_inventory_basic():
    store_backend = demo_shopify_inventory()
    loc = "gid://shopify/Location/1"

    # Find SKU
    item = store_backend.find_inventory_item_by_sku("SKU-1")
    assert item is not None
    assert item["sku"] == "SKU-1"

    # Get level
    qty = store_backend.get_inventory_level(item["inventory_item_id"], loc)
    assert qty == 150

    # Adjust level
    new_qty = store_backend.adjust_inventory(item["inventory_item_id"], loc, -10)
    assert new_qty == 140
    assert store_backend.get_inventory_level(item["inventory_item_id"], loc) == 140

    # Set level
    set_qty = store_backend.set_inventory(item["inventory_item_id"], loc, 200)
    assert set_qty == 200
    assert store_backend.get_inventory_level(item["inventory_item_id"], loc) == 200


def test_shopify_inventory_store_writeback_commit_and_rollback():
    rpc = demo_shopify_inventory()
    loc = "gid://shopify/Location/1"
    store = ShopifyInventoryStore(rpc, location_id=loc)

    # Initial state
    assert store.read("SKU-1") == {"quantity": 150}

    # Prepare changeset
    cs = writeback.Changeset(
        idempotency_key="key-inv-001",
        target="shopify_inventory",
        risk_tier=writeback.TIER_REVERSIBLE,
        changes=(
            writeback.Change(
                entity_id="SKU-1",
                field="quantity",
                before=150,
                after=180,
            ),
        ),
    )

    # Claim & Commit
    assert store.claim(cs.idempotency_key) is True
    entry = store.commit(cs, approved_by="operator@kern.ai")

    assert entry.idempotency_key == "key-inv-001"
    assert store.read("SKU-1") == {"quantity": 180}

    # Rollback
    store.rollback("key-inv-001")
    assert store.read("SKU-1") == {"quantity": 150}


def test_shopify_inventory_store_unknown_sku():
    rpc = demo_shopify_inventory()
    loc = "gid://shopify/Location/1"
    store = ShopifyInventoryStore(rpc, location_id=loc)

    cs = writeback.Changeset(
        idempotency_key="key-inv-err",
        target="shopify_inventory",
        risk_tier=writeback.TIER_REVERSIBLE,
        changes=(
            writeback.Change(
                entity_id="SKU-UNKNOWN",
                field="quantity",
                before=0,
                after=50,
            ),
        ),
    )

    with pytest.raises(ShopifyInventoryError, match="unknown SKU 'SKU-UNKNOWN'"):
        store.commit(cs, approved_by="operator@kern.ai")
