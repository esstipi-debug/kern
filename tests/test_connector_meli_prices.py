"""Tests for src/connectors/meli_prices.py (Linchpin 3.0 PR-18, P3
repricing_multichannel), fully offline against InMemoryMeli.

Reference fixture (see ``_seller()``): SKU-1 @ 20.0, SKU-2 @ 50.0 -- the same
starting prices as ``src.connectors.odoo.demo_odoo()`` so a cross-channel
repricing scenario reads intuitively. Distinct from PR-15's finding (public,
unauthenticated MELI search is robots.txt-blocked): this connector is an
authenticated SELLER writing THEIR OWN listing prices via the official
Items API, a different integration surface (see module docstring).
"""

from __future__ import annotations

import pytest

from src import writeback
from src.connectors.meli_prices import (
    InMemoryMeli,
    MeliPricesError,
    MeliPriceStore,
    demo_meli,
)

TARGET = "meli:demo-seller"


def _seller() -> InMemoryMeli:
    return InMemoryMeli(
        {
            "SKU-1": {"item_id": "MLA1", "price": 20.0},
            "SKU-2": {"item_id": "MLA2", "price": 50.0},
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
    store = MeliPriceStore(_seller())
    assert store.read("SKU-1") == {"price": 20.0}


def test_read_unknown_sku_returns_empty():
    store = MeliPriceStore(_seller())
    assert store.read("SKU-999") == {}


# ---- stage (dry-run) ----------------------------------------------------------


def test_stage_is_a_dry_run_until_applied():
    rpc = _seller()
    store = MeliPriceStore(rpc)
    cs = _stage(store, {"SKU-1": {"price": 17.5}})

    assert cs.risk_tier == writeback.TIER_REVERSIBLE
    assert cs.changes[0].before == 20.0
    assert cs.changes[0].after == 17.5
    assert rpc.find_item_by_sku("SKU-1")["price"] == 20.0  # untouched


# ---- apply + idempotency -------------------------------------------------------


def test_apply_without_approval_is_refused():
    store = MeliPriceStore(_seller())
    cs = _stage(store, {"SKU-1": {"price": 17.5}})
    with pytest.raises(writeback.WritebackRefused):
        writeback.apply(store, cs, now=0.0)
    assert store.read("SKU-1")["price"] == 20.0


def test_apply_with_approval_writes_and_is_idempotent():
    rpc = _seller()
    store = MeliPriceStore(rpc)
    cs = _stage(store, {"SKU-1": {"price": 17.5}})

    first = _approved_apply(store, cs, now=0.0)
    assert first.applied is True
    assert rpc.find_item_by_sku("SKU-1")["price"] == 17.5

    second = writeback.apply(store, cs, approval=writeback.approve(cs, "operator", now=0.0), now=1.0)
    assert second.applied is False and second.idempotent_skip is True


def test_apply_unknown_sku_raises_and_leaves_store_untouched():
    rpc = _seller()
    store = MeliPriceStore(rpc)
    cs = _stage(store, {"SKU-999": {"price": 5.0}})

    with pytest.raises(MeliPricesError, match="SKU-999"):
        _approved_apply(store, cs, now=0.0)


# ---- rollback -----------------------------------------------------------------


def test_apply_then_rollback_restores_original_price():
    rpc = _seller()
    store = MeliPriceStore(rpc)
    cs = _stage(store, {"SKU-1": {"price": 17.5}})
    _approved_apply(store, cs, now=0.0)

    store.rollback("cs-1")

    assert rpc.find_item_by_sku("SKU-1")["price"] == 20.0


def test_rollback_unknown_key_raises():
    store = MeliPriceStore(_seller())
    with pytest.raises(KeyError):
        store.rollback("nope")


# ---- partial-failure compensating rollback -------------------------------------


class _FailingRpc:
    def __init__(self, inner, *, fail_after: int) -> None:
        self._inner = inner
        self._fail_after = fail_after
        self._count = 0

    def find_item_by_sku(self, sku):
        return self._inner.find_item_by_sku(sku)

    def update_item_price(self, item_id, price):
        self._count += 1
        if self._count == self._fail_after:
            raise MeliPricesError("simulated transient failure mid-commit")
        self._inner.update_item_price(item_id, price)


def test_commit_rolls_back_writes_already_applied_when_a_later_one_fails():
    rpc = _seller()
    failing = _FailingRpc(rpc, fail_after=2)
    store = MeliPriceStore(failing)
    cs = _stage(store, {"SKU-1": {"price": 17.5}, "SKU-2": {"price": 45.0}})

    with pytest.raises(MeliPricesError, match="simulated"):
        _approved_apply(store, cs, now=0.0)

    assert rpc.find_item_by_sku("SKU-1")["price"] == 20.0
    assert rpc.find_item_by_sku("SKU-2")["price"] == 50.0
    assert store.applied_keys() == set()


# ---- demo fixture ---------------------------------------------------------------


def test_demo_meli_is_a_consistent_non_empty_seller():
    seller = demo_meli()
    assert seller.find_item_by_sku("SKU-1")["price"] == 20.0
    assert seller.find_item_by_sku("SKU-2")["price"] == 50.0
    assert seller.find_item_by_sku("SKU-3")["price"] == 8.0


def test_live_transport_without_httpx_raises_clear_error(monkeypatch):
    import src.connectors.meli_prices as mod

    monkeypatch.setattr(mod, "_HAS_HTTPX", False)
    with pytest.raises(MeliPricesError, match="repricing"):
        mod.MeliClient(http=object(), user_id="123")
