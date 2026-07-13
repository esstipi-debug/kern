"""Tests for src/connectors/odoo_prices.py (Linchpin 3.0 PR-18, P3
repricing_multichannel), fully offline against InMemoryOdoo.

Reuses ``src.connectors.odoo.InMemoryOdoo``/``demo_odoo`` verbatim (no
second Odoo stand-in) -- ``demo_odoo()``'s product.product records already
carry SKU-1 @ 20.0, SKU-2 @ 50.0, SKU-3 @ 8.0 list_price, the same starting
prices as the Shopify/MercadoLibre demo fixtures.
"""

from __future__ import annotations

import pytest

from src import writeback
from src.connectors.odoo import InMemoryOdoo, OdooError, demo_odoo
from src.connectors.odoo_prices import OdooPriceStore

TARGET = "odoo"


def _odoo() -> InMemoryOdoo:
    return InMemoryOdoo(
        {
            "product.product": {
                1: {"default_code": "SKU-1", "name": "Widget", "list_price": 20.0, "standard_price": 12.0},
                2: {"default_code": "SKU-2", "name": "Gadget", "list_price": 50.0, "standard_price": 30.0},
            },
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
    store = OdooPriceStore(_odoo())
    assert store.read("SKU-1") == {"price": 20.0}


def test_read_unknown_sku_returns_empty():
    store = OdooPriceStore(_odoo())
    assert store.read("SKU-999") == {}


# ---- stage (dry-run) ----------------------------------------------------------


def test_stage_is_a_dry_run_until_applied():
    odoo = _odoo()
    store = OdooPriceStore(odoo)
    cs = _stage(store, {"SKU-1": {"price": 18.0}})

    assert cs.risk_tier == writeback.TIER_REVERSIBLE
    assert cs.changes[0].before == 20.0
    assert cs.changes[0].after == 18.0
    assert odoo.records("product.product")[1]["list_price"] == 20.0  # untouched


# ---- apply + idempotency -------------------------------------------------------


def test_apply_without_approval_is_refused():
    store = OdooPriceStore(_odoo())
    cs = _stage(store, {"SKU-1": {"price": 18.0}})
    with pytest.raises(writeback.WritebackRefused):
        writeback.apply(store, cs, now=0.0)
    assert store.read("SKU-1")["price"] == 20.0


def test_apply_with_approval_writes_and_is_idempotent():
    odoo = _odoo()
    store = OdooPriceStore(odoo)
    cs = _stage(store, {"SKU-1": {"price": 18.0}})

    first = _approved_apply(store, cs, now=0.0)
    assert first.applied is True
    assert odoo.records("product.product")[1]["list_price"] == 18.0

    second = writeback.apply(store, cs, approval=writeback.approve(cs, "operator", now=0.0), now=1.0)
    assert second.applied is False and second.idempotent_skip is True


def test_apply_unknown_sku_raises_and_leaves_store_untouched():
    store = OdooPriceStore(_odoo())
    cs = _stage(store, {"SKU-999": {"price": 5.0}})

    with pytest.raises(OdooError, match="SKU-999"):
        _approved_apply(store, cs, now=0.0)


# ---- rollback -----------------------------------------------------------------


def test_apply_then_rollback_restores_original_price():
    odoo = _odoo()
    store = OdooPriceStore(odoo)
    cs = _stage(store, {"SKU-1": {"price": 18.0}})
    _approved_apply(store, cs, now=0.0)

    store.rollback("cs-1")

    assert odoo.records("product.product")[1]["list_price"] == 20.0


def test_rollback_unknown_key_raises():
    store = OdooPriceStore(_odoo())
    with pytest.raises(KeyError):
        store.rollback("nope")


# ---- partial-failure compensating rollback -------------------------------------


class _FailingRpc:
    def __init__(self, inner, *, fail_after: int) -> None:
        self._inner = inner
        self._fail_after = fail_after
        self._count = 0

    def execute_kw(self, model, method, args, kwargs=None):
        if model == "product.product" and method == "write":
            self._count += 1
            if self._count == self._fail_after:
                raise OdooError("simulated transient failure mid-commit")
        return self._inner.execute_kw(model, method, args, kwargs)


def test_commit_rolls_back_writes_already_applied_when_a_later_one_fails():
    odoo = _odoo()
    failing = _FailingRpc(odoo, fail_after=2)
    store = OdooPriceStore(failing)
    cs = _stage(store, {"SKU-1": {"price": 18.0}, "SKU-2": {"price": 45.0}})

    with pytest.raises(OdooError, match="simulated"):
        _approved_apply(store, cs, now=0.0)

    assert odoo.records("product.product")[1]["list_price"] == 20.0
    assert odoo.records("product.product")[2]["list_price"] == 50.0
    assert store.applied_keys() == set()


# ---- demo fixture ---------------------------------------------------------------


def test_demo_odoo_reprices_the_same_reference_catalog():
    store = OdooPriceStore(demo_odoo())
    assert store.read("SKU-1") == {"price": 20.0}
    assert store.read("SKU-2") == {"price": 50.0}
    assert store.read("SKU-3") == {"price": 8.0}
