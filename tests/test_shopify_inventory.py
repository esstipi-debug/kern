"""Tests for src/connectors/shopify_inventory.py."""

from __future__ import annotations

import pytest

from nosilo_core import writeback
from src.connectors.shopify_inventory import (
    InMemoryShopifyInventory,
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


# ---- transport (ShopifyInventoryClient) ---------------------------------------
# First tests to ever exercise the real GraphQL client.

from src.connectors.shopify_api_version import (  # noqa: E402
    PINNED_VERSION,
    ShopifyVersionDriftError,
)
from src.connectors.shopify_inventory import ShopifyInventoryClient  # noqa: E402


class _FakeResponse:
    def __init__(self, payload: dict, headers: dict | None = None) -> None:
        self._payload = payload
        self.headers = headers if headers is not None else {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeHttp:
    def __init__(self, payload: dict, headers: dict | None = None) -> None:
        self._payload = payload
        self._headers = headers
        self.calls: list[tuple[str, dict]] = []

    def post(self, path: str, json: dict) -> _FakeResponse:
        self.calls.append((path, json))
        return _FakeResponse(self._payload, self._headers)


_LOCATIONS_PAYLOAD = {
    "data": {
        "locations": {
            "edges": [
                {"node": {"id": "gid://shopify/Location/1", "name": "Main", "isPrimary": True}}
            ]
        }
    }
}

_SET_PAYLOAD = {
    "data": {
        "inventorySetQuantities": {
            "inventoryAdjustmentGroup": {"changes": [{"quantityAfterChange": 180}]},
            "userErrors": [],
        }
    }
}

_SET_PAYLOAD_EMPTY = {
    "data": {"inventorySetQuantities": {"inventoryAdjustmentGroup": None, "userErrors": []}}
}


def test_inventory_client_uses_the_shared_pinned_version():
    http = _FakeHttp(_LOCATIONS_PAYLOAD)
    ShopifyInventoryClient(http).list_locations()
    assert http.calls[0][0] == f"/admin/api/{PINNED_VERSION}/graphql.json"


def test_inventory_client_read_tolerates_drift():
    http = _FakeHttp(_LOCATIONS_PAYLOAD, headers={"X-Shopify-API-Version": "2099-01"})
    assert ShopifyInventoryClient(http).list_locations()[0]["name"] == "Main"


def test_inventory_client_write_refuses_to_run_under_drift():
    http = _FakeHttp(_SET_PAYLOAD, headers={"X-Shopify-API-Version": "2099-01"})
    with pytest.raises(ShopifyVersionDriftError):
        ShopifyInventoryClient(http).set_inventory(
            "gid://shopify/InventoryItem/SKU-1",
            "gid://shopify/Location/1",
            180,
            idempotency_key="key-t4",
        )


def test_set_inventory_reads_the_inventory_adjustment_group():
    http = _FakeHttp(_SET_PAYLOAD, headers={"X-Shopify-API-Version": PINNED_VERSION})
    qty = ShopifyInventoryClient(http).set_inventory(
        "gid://shopify/InventoryItem/SKU-1",
        "gid://shopify/Location/1",
        180,
        idempotency_key="key-t4",
    )
    assert qty == 180


def test_set_inventory_fails_instead_of_echoing_the_requested_quantity():
    # The old code returned `available` when the payload field was missing, so a
    # silent failure was indistinguishable from a confirmed write.
    http = _FakeHttp(_SET_PAYLOAD_EMPTY, headers={"X-Shopify-API-Version": PINNED_VERSION})
    with pytest.raises(ShopifyInventoryError, match="did not confirm"):
        ShopifyInventoryClient(http).set_inventory(
            "gid://shopify/InventoryItem/SKU-1",
            "gid://shopify/Location/1",
            180,
            idempotency_key="key-t4",
        )


def test_set_inventory_sends_the_idempotent_directive_and_key():
    http = _FakeHttp(_SET_PAYLOAD, headers={"X-Shopify-API-Version": PINNED_VERSION})
    ShopifyInventoryClient(http).set_inventory(
        "gid://shopify/InventoryItem/SKU-1",
        "gid://shopify/Location/1",
        180,
        idempotency_key="key-abc",
    )
    _path, sent = http.calls[0]
    assert "@idempotent" in sent["query"]
    assert sent["variables"]["idempotencyKey"] == "key-abc"


def test_adjust_inventory_sends_the_idempotent_directive_and_key():
    payload = {
        "data": {
            "inventoryAdjustQuantities": {
                "inventoryAdjustmentGroup": {"changes": [{"delta": -10, "quantityAfterChange": 140}]},
                "userErrors": [],
            }
        }
    }
    http = _FakeHttp(payload, headers={"X-Shopify-API-Version": PINNED_VERSION})
    ShopifyInventoryClient(http).adjust_inventory(
        "gid://shopify/InventoryItem/SKU-1",
        "gid://shopify/Location/1",
        -10,
        idempotency_key="key-xyz",
    )
    _path, sent = http.calls[0]
    assert "@idempotent" in sent["query"]
    assert sent["variables"]["idempotencyKey"] == "key-xyz"


def test_adjust_inventory_fails_instead_of_returning_zero():
    # `0` is a plausible stock level, so the old fallback made an unconfirmed
    # write indistinguishable from an adjustment down to zero.
    payload = {
        "data": {"inventoryAdjustQuantities": {"inventoryAdjustmentGroup": None, "userErrors": []}}
    }
    http = _FakeHttp(payload, headers={"X-Shopify-API-Version": PINNED_VERSION})
    with pytest.raises(ShopifyInventoryError, match="did not confirm"):
        ShopifyInventoryClient(http).adjust_inventory(
            "gid://shopify/InventoryItem/SKU-1",
            "gid://shopify/Location/1",
            -10,
            idempotency_key="key-adj",
        )


def test_store_commit_passes_the_changeset_key_through():
    loc = "gid://shopify/Location/1"
    rpc = InMemoryShopifyInventory(stock={"SKU-1": {loc: 150}})
    store = ShopifyInventoryStore(rpc, location_id=loc)
    cs = writeback.Changeset(
        idempotency_key="key-inv-777",
        target="shopify_inventory",
        risk_tier=writeback.TIER_REVERSIBLE,
        changes=(writeback.Change(entity_id="SKU-1", field="quantity", before=150, after=180),),
    )
    store.claim(cs.idempotency_key)
    store.commit(cs, approved_by="operator@kern.ai")
    # The key is derived per (changeset, entity): deterministic but distinct per SKU.
    assert rpc.idempotency_keys == ["key-inv-777:SKU-1"]

    store.rollback("key-inv-777")
    # A restore is a distinct write: sharing the forward-write key would make
    # Shopify drop it.
    assert rpc.idempotency_keys[-1] == "key-inv-777:SKU-1:restore"


def test_store_commit_derives_a_distinct_key_per_entity_in_a_multi_change_changeset():
    # A restock touching several SKUs in one changeset must not reuse the same
    # key across SKUs -- Shopify rejects a reused key with different parameters
    # (IDEMPOTENCY_KEY_PARAMETER_MISMATCH) rather than silently deduping.
    loc = "gid://shopify/Location/1"
    rpc = demo_shopify_inventory()
    store = ShopifyInventoryStore(rpc, location_id=loc)
    cs = writeback.Changeset(
        idempotency_key="key-restock-001",
        target="shopify_inventory",
        risk_tier=writeback.TIER_REVERSIBLE,
        changes=(
            writeback.Change(entity_id="SKU-1", field="quantity", before=150, after=160),
            writeback.Change(entity_id="SKU-2", field="quantity", before=45, after=55),
            writeback.Change(entity_id="SKU-3", field="quantity", before=12, after=20),
        ),
    )
    store.claim(cs.idempotency_key)
    store.commit(cs, approved_by="operator@kern.ai")

    commit_keys = rpc.idempotency_keys[-3:]
    assert len(set(commit_keys)) == 3
    assert commit_keys == [
        "key-restock-001:SKU-1",
        "key-restock-001:SKU-2",
        "key-restock-001:SKU-3",
    ]

    store.rollback("key-restock-001")
    restore_keys = rpc.idempotency_keys[-3:]
    assert len(set(restore_keys)) == 3
    assert restore_keys == [
        "key-restock-001:SKU-1:restore",
        "key-restock-001:SKU-2:restore",
        "key-restock-001:SKU-3:restore",
    ]
