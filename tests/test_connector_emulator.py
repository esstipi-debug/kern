"""Tests for the offline HTTP store emulator + its client adapter (Gap #5).

``create_app(store)`` exposes a SimulatedStore over commerce-shaped HTTP endpoints
(Shopify/SP-API-ish); ``StoreApiClient`` is a transport-agnostic adapter that satisfies
the ``InventorySource`` protocol by calling them. Tests drive the round-trip in-process
with FastAPI's TestClient — no network, no API keys — proving a real HTTP client works
against the emulator. Swap the TestClient for a live ``httpx.Client`` later, unchanged.
"""

from fastapi.testclient import TestClient

from src.connectors import InventoryLevel, InventorySource, Product
from src.connectors.emulator import create_app
from src.connectors.http_client import StoreApiClient
from src.connectors.simulator import SimulatedStore, demo_store


def _client(store=None):
    store = store or demo_store()
    return StoreApiClient(TestClient(create_app(store))), store


def test_client_satisfies_the_inventory_source_protocol():
    client, _ = _client()
    assert isinstance(client, InventorySource)


def test_products_round_trip_over_http():
    client, store = _client()
    assert {p.sku for p in client.list_products()} == {p.sku for p in store.list_products()}


def test_inventory_levels_round_trip():
    client, store = _client()
    got = {lvl.sku: lvl.available for lvl in client.inventory_levels()}
    want = {lvl.sku: lvl.available for lvl in store.inventory_levels()}
    assert got == want


def test_orders_filter_since_over_http():
    client, store = _client()
    recent = client.orders(since="2026-03-01")
    assert [o.order_id for o in recent] == [o.order_id for o in store.orders(since="2026-03-01")]


def test_restock_over_http_applies_and_is_idempotent():
    store = SimulatedStore([Product("S1", "x", 10.0, 6.0)], [InventoryLevel("S1", 5.0)], [])
    client = StoreApiClient(TestClient(create_app(store)))

    res = client.restock({"S1": 20.0}, idempotency_key="k1", reason="cover")

    assert res["applied"] is True
    assert {lvl.sku: lvl.available for lvl in client.inventory_levels()}["S1"] == 25.0
    # same key never lands twice, even over HTTP
    assert client.restock({"S1": 20.0}, idempotency_key="k1")["idempotent_skip"] is True


def test_emulator_endpoints_are_commerce_shaped():
    raw = TestClient(create_app(demo_store()))

    products = raw.get("/admin/products").json()
    assert "products" in products and "sku" in products["products"][0]

    orders = raw.get("/admin/orders").json()["orders"]
    assert "line_items" in orders[0] and "created_at" in orders[0]
