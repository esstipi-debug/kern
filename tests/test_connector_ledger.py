"""Tests for the canonical multichannel ledger (plan §2.7, Gap #5 offline).

``CanonicalLedger`` merges several ``InventorySource`` channels into one SKU-indexed
view: total on-hand per SKU, the per-channel breakdown, which channels carry a SKU, and
a combined demand history that feeds the engines. Because it speaks only the
``InventorySource`` protocol, an in-memory ``SimulatedStore`` and a store reached over
HTTP (``StoreApiClient``) merge identically — proving the canonical layer is
transport-agnostic.
"""

from fastapi.testclient import TestClient

from src.connectors import InventoryLevel, Order, OrderLine, Product
from src.connectors.emulator import create_app
from src.connectors.http_client import StoreApiClient
from src.connectors.ledger import CanonicalLedger
from src.connectors.simulator import SimulatedStore
from src.sources import DataFrameDemandSource


def _shopify() -> SimulatedStore:
    return SimulatedStore(
        [Product("SKU-A", "A", 30.0, 10.0), Product("SKU-B", "B", 40.0, 20.0)],
        [InventoryLevel("SKU-A", 50.0), InventoryLevel("SKU-B", 30.0)],
        [Order("s1", "2026-01-05", (OrderLine("SKU-A", 2.0, 30.0), OrderLine("SKU-B", 1.0, 40.0)))],
    )


def _amazon() -> SimulatedStore:
    return SimulatedStore(
        [Product("SKU-A", "A", 30.0, 10.0), Product("SKU-C", "C", 12.0, 5.0)],
        [InventoryLevel("SKU-A", 20.0), InventoryLevel("SKU-C", 100.0)],
        [Order("a1", "2026-01-06", (OrderLine("SKU-A", 3.0, 30.0), OrderLine("SKU-C", 4.0, 12.0)))],
    )


def _ledger() -> CanonicalLedger:
    return CanonicalLedger({"shopify": _shopify(), "amazon": _amazon()})


# -- merged catalog + inventory -----------------------------------------------


def test_products_are_the_union_across_channels():
    assert {p.sku for p in _ledger().products()} == {"SKU-A", "SKU-B", "SKU-C"}


def test_inventory_is_summed_per_sku_across_channels():
    totals = _ledger().inventory_by_sku()
    assert totals == {"SKU-A": 70.0, "SKU-B": 30.0, "SKU-C": 100.0}   # A: 50 + 20


def test_inventory_breaks_down_by_channel():
    by_channel = _ledger().inventory_by_channel()
    assert by_channel["shopify"] == {"SKU-A": 50.0, "SKU-B": 30.0}
    assert by_channel["amazon"] == {"SKU-A": 20.0, "SKU-C": 100.0}


def test_channels_for_a_shared_sku():
    assert _ledger().channels_for("SKU-A") == ["shopify", "amazon"]
    assert _ledger().channels_for("SKU-C") == ["amazon"]


# -- combined demand into the engines -----------------------------------------


def test_demand_frame_combines_channels_and_feeds_the_engines():
    src = DataFrameDemandSource(_ledger().demand_frame())

    assert set(src.list_products()) == {"SKU-A", "SKU-B", "SKU-C"}
    # SKU-A: 2 units (shopify, Jan 5) then 3 (amazon, Jan 6) -> two periods.
    assert list(src.demand_series("SKU-A")) == [2.0, 3.0]


# -- transport-agnostic: a simulated channel + an HTTP-emulated channel --------


def test_merges_a_simulated_and_an_http_channel_identically():
    amazon_http = StoreApiClient(TestClient(create_app(_amazon())))
    ledger = CanonicalLedger({"shopify": _shopify(), "amazon": amazon_http})

    assert ledger.inventory_by_sku() == {"SKU-A": 70.0, "SKU-B": 30.0, "SKU-C": 100.0}
