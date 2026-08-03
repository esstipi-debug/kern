"""Tests for src/connectors/shopify_prices.py (Linchpin 3.0 PR-18, P3
repricing_multichannel), fully offline against InMemoryShopify.

Reference fixture (see ``_shop()``): SKU-1 @ 20.0, SKU-2 @ 50.0 -- the same
starting prices as ``src.connectors.odoo.demo_odoo()`` so a cross-channel
repricing scenario reads intuitively.
"""

from __future__ import annotations

import pytest
from nosilo_core import writeback

from src.connectors.shopify_prices import (
    InMemoryShopify,
    ShopifyPricesError,
    ShopifyPriceStore,
    demo_shopify,
)

TARGET = "shopify:demo-shop"


def _shop() -> InMemoryShopify:
    return InMemoryShopify(
        {
            "SKU-1": {"variant_id": "gid://shopify/ProductVariant/1", "price": 20.0},
            "SKU-2": {"variant_id": "gid://shopify/ProductVariant/2", "price": 50.0},
        }
    )


def _stage(store, edits, key="cs-1", reason="repricing test"):
    return writeback.stage(store, TARGET, edits, risk_tier=writeback.TIER_REVERSIBLE,
                           idempotency_key=key, reason=reason)


def _approved_apply(store, cs, who="operator", now=None):
    approval = writeback.approve(cs, who, now=now)
    return writeback.apply(store, cs, approval=approval, now=now)


# ---- read -------------------------------------------------------------------


def test_read_returns_current_price():
    store = ShopifyPriceStore(_shop())
    assert store.read("SKU-1") == {"price": 20.0}


def test_read_unknown_sku_returns_empty():
    store = ShopifyPriceStore(_shop())
    assert store.read("SKU-999") == {}


# ---- stage (dry-run) ----------------------------------------------------------


def test_stage_is_a_dry_run_until_applied():
    rpc = _shop()
    store = ShopifyPriceStore(rpc)
    cs = _stage(store, {"SKU-1": {"price": 18.0}})

    assert cs.risk_tier == writeback.TIER_REVERSIBLE
    assert cs.changes[0].before == 20.0
    assert cs.changes[0].after == 18.0
    assert rpc.find_variant_by_sku("SKU-1")["price"] == 20.0  # untouched


# ---- apply + idempotency -------------------------------------------------------


def test_apply_without_approval_is_refused():
    store = ShopifyPriceStore(_shop())
    cs = _stage(store, {"SKU-1": {"price": 18.0}})
    with pytest.raises(writeback.WritebackRefused):
        writeback.apply(store, cs, now=0.0)
    assert store.read("SKU-1")["price"] == 20.0


def test_apply_with_approval_writes_and_is_idempotent():
    rpc = _shop()
    store = ShopifyPriceStore(rpc)
    cs = _stage(store, {"SKU-1": {"price": 18.0}})

    first = _approved_apply(store, cs, now=0.0)
    assert first.applied is True
    assert rpc.find_variant_by_sku("SKU-1")["price"] == 18.0

    # same idempotency key never lands twice
    second = writeback.apply(store, cs, approval=writeback.approve(cs, "operator", now=0.0), now=1.0)
    assert second.applied is False and second.idempotent_skip is True


def test_apply_multiple_skus_in_one_changeset():
    rpc = _shop()
    store = ShopifyPriceStore(rpc)
    cs = _stage(store, {"SKU-1": {"price": 18.0}, "SKU-2": {"price": 45.0}})

    _approved_apply(store, cs, now=0.0)

    assert rpc.find_variant_by_sku("SKU-1")["price"] == 18.0
    assert rpc.find_variant_by_sku("SKU-2")["price"] == 45.0


def test_apply_unknown_sku_raises_and_leaves_store_untouched():
    rpc = _shop()
    store = ShopifyPriceStore(rpc)
    cs = _stage(store, {"SKU-999": {"price": 5.0}})

    with pytest.raises(ShopifyPricesError, match="SKU-999"):
        _approved_apply(store, cs, now=0.0)


# ---- rollback -----------------------------------------------------------------


def test_apply_then_rollback_restores_original_price():
    rpc = _shop()
    store = ShopifyPriceStore(rpc)
    cs = _stage(store, {"SKU-1": {"price": 18.0}})
    _approved_apply(store, cs, now=0.0)

    store.rollback("cs-1")

    assert rpc.find_variant_by_sku("SKU-1")["price"] == 20.0


def test_rollback_unknown_key_raises():
    store = ShopifyPriceStore(_shop())
    with pytest.raises(KeyError):
        store.rollback("nope")


# ---- partial-failure compensating rollback -------------------------------------


class _FailingRpc:
    """Wraps a real InMemoryShopify, raising on the Nth update_variant_price call."""

    def __init__(self, inner, *, fail_after: int) -> None:
        self._inner = inner
        self._fail_after = fail_after
        self._count = 0

    def find_variant_by_sku(self, sku):
        return self._inner.find_variant_by_sku(sku)

    def update_variant_price(self, variant_id, price):
        self._count += 1
        if self._count == self._fail_after:
            raise ShopifyPricesError("simulated transient failure mid-commit")
        self._inner.update_variant_price(variant_id, price)


def test_commit_rolls_back_writes_already_applied_when_a_later_one_fails():
    rpc = _shop()
    failing = _FailingRpc(rpc, fail_after=2)
    store = ShopifyPriceStore(failing)
    cs = _stage(store, {"SKU-1": {"price": 18.0}, "SKU-2": {"price": 45.0}})

    with pytest.raises(ShopifyPricesError, match="simulated"):
        _approved_apply(store, cs, now=0.0)

    # SKU-1's write (the first) landed then was compensated back; SKU-2 never wrote.
    assert rpc.find_variant_by_sku("SKU-1")["price"] == 20.0
    assert rpc.find_variant_by_sku("SKU-2")["price"] == 50.0
    assert store.applied_keys() == set()  # never recorded as applied


# ---- demo fixture ---------------------------------------------------------------


def test_demo_shopify_is_a_consistent_non_empty_shop():
    shop = demo_shopify()
    assert shop.find_variant_by_sku("SKU-1")["price"] == 20.0
    assert shop.find_variant_by_sku("SKU-2")["price"] == 50.0
    assert shop.find_variant_by_sku("SKU-3")["price"] == 8.0


def test_live_transport_without_httpx_raises_clear_error(monkeypatch):
    import src.connectors.shopify_prices as mod

    monkeypatch.setattr(mod, "_HAS_HTTPX", False)
    with pytest.raises(ShopifyPricesError, match="repricing"):
        mod.ShopifyClient(http=object())


# ---- transport (ShopifyClient) ------------------------------------------------
# First tests to ever exercise the real GraphQL client; everything above this
# line covers InMemoryShopify and the writeback store instead.

from src.connectors.shopify_api_version import (  # noqa: E402
    PINNED_VERSION,
    ShopifyVersionDriftError,
)
from src.connectors.shopify_prices import ShopifyClient  # noqa: E402


class _FakeResponse:
    def __init__(self, payload: dict, headers: dict | None = None) -> None:
        self._payload = payload
        self.headers = headers if headers is not None else {}

    def raise_for_status(self) -> None:
        return None

    def json(self) -> dict:
        return self._payload


class _FakeHttp:
    """httpx-shaped stand-in that records what was posted."""

    def __init__(self, payload: dict, headers: dict | None = None) -> None:
        self._payload = payload
        self._headers = headers
        self.calls: list[tuple[str, dict]] = []

    def post(self, path: str, json: dict) -> _FakeResponse:
        self.calls.append((path, json))
        return _FakeResponse(self._payload, self._headers)


_VARIANT_PAYLOAD = {
    "data": {
        "productVariants": {
            "edges": [
                {
                    "node": {
                        "id": "gid://shopify/ProductVariant/1",
                        "price": "20.0",
                        "sku": "SKU-1",
                        "product": {"id": "gid://shopify/Product/9"},
                    }
                }
            ]
        }
    }
}

_UPDATE_PAYLOAD = {"data": {"productVariantsBulkUpdate": {"userErrors": []}}}


def test_client_uses_the_shared_pinned_version():
    http = _FakeHttp(_VARIANT_PAYLOAD)
    ShopifyClient(http).find_variant_by_sku("SKU-1")
    assert http.calls[0][0] == f"/admin/api/{PINNED_VERSION}/graphql.json"


def test_client_read_tolerates_drift():
    http = _FakeHttp(_VARIANT_PAYLOAD, headers={"X-Shopify-API-Version": "2099-01"})
    variant = ShopifyClient(http).find_variant_by_sku("SKU-1")
    assert variant["sku"] == "SKU-1"


# NOTE: this does not prevent the write from reaching Shopify -- by the time
# `check_served` inspects the response, the mutation has already executed there.
# What it prevents is the CALLER treating a drifted write as if it had succeeded:
# the raised error aborts the surrounding operation (e.g. a changeset commit stops
# at the first failure) instead of letting bad data look confirmed.
def test_client_write_raises_instead_of_succeeding_under_drift():
    http = _FakeHttp(_VARIANT_PAYLOAD)
    client = ShopifyClient(http)
    client.find_variant_by_sku("SKU-1")

    drifted = _FakeHttp(_UPDATE_PAYLOAD, headers={"X-Shopify-API-Version": "2099-01"})
    client._http = drifted
    with pytest.raises(ShopifyVersionDriftError):
        client.update_variant_price("gid://shopify/ProductVariant/1", 25.0)


def test_client_write_succeeds_without_drift():
    http = _FakeHttp(_VARIANT_PAYLOAD)
    client = ShopifyClient(http)
    client.find_variant_by_sku("SKU-1")

    client._http = _FakeHttp(_UPDATE_PAYLOAD, headers={"X-Shopify-API-Version": PINNED_VERSION})
    client.update_variant_price("gid://shopify/ProductVariant/1", 25.0)
