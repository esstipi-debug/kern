"""Tests for jobs/repricing.py (Linchpin 3.0 PR-18, plan section 7 P3
repricing_multichannel).

Full stage -> gate -> approve -> apply -> verify cycle, exercised against
each of the three offline connector stand-ins (InMemoryShopify, InMemoryMeli,
InMemoryOdoo via odoo_prices.OdooPriceStore) plus the generic
writeback.InMemoryStore for the primitive-level guardrail/approval/
verification behaviors that do not depend on which channel is underneath.
"""

from __future__ import annotations

import pytest

from jobs.repricing import (
    ChannelRepricingResult,
    RepricingGuardrailBlocked,
    RepricingVerificationFailed,
    apply_repricing,
    prices_from_optimizer,
    run_channel_repricing,
    stage_repricing,
    verify_applied,
)
from src import writeback
from src.connectors.meli_prices import InMemoryMeli, MeliPriceStore
from src.connectors.odoo import demo_odoo
from src.connectors.odoo_prices import OdooPriceStore
from src.connectors.shopify_prices import InMemoryShopify, ShopifyPriceStore
from src.price_optimizer import PriceOptimizationResult

REASON = "Repricing to close a margin gap vs landed cost (elasticity-driven)."


def _price_store() -> writeback.InMemoryStore:
    return writeback.InMemoryStore({"SKU-1": {"price": 20.0}, "SKU-2": {"price": 50.0}})


# ---- prices_from_optimizer -----------------------------------------------------


def _opt_result(status: str, proposed_price: float | None = None) -> PriceOptimizationResult:
    return PriceOptimizationResult(
        product_id="SKU-1", status=status, reason=None if status == "ok" else "no signal",
        current_price=20.0, proposed_price=proposed_price, landed_cost=10.0,
        elasticity_used=-2.0 if status == "ok" else None, shrinkage_weight=None,
        category=None, floor_applied=False, price_capped=False, competitor_context=None,
    )


def test_prices_from_optimizer_keeps_only_ok_results_with_a_price():
    results = {
        "SKU-1": _opt_result("ok", proposed_price=18.5),
        "SKU-2": _opt_result("needs_data"),
    }
    assert prices_from_optimizer(results) == {"SKU-1": 18.5}


# ---- stage_repricing: central gate (PR-17) --------------------------------------


def test_stage_repricing_blocks_a_changeset_with_no_reason():
    store = _price_store()
    with pytest.raises(RepricingGuardrailBlocked):
        stage_repricing(store, "shopify:demo", {"SKU-1": 18.0}, idempotency_key="r1", reason="")
    # never reached staging: the store is untouched and nothing was ever applied/claimed
    assert store.read("SKU-1")["price"] == 20.0
    assert store.applied_keys() == set()


def test_stage_repricing_approves_with_a_real_reason_and_real_citations():
    store = _price_store()
    cs = stage_repricing(store, "shopify:demo", {"SKU-1": 18.0}, idempotency_key="r1", reason=REASON)
    assert cs.reason == REASON
    assert cs.risk_tier == writeback.TIER_REVERSIBLE
    assert cs.changes[0].before == 20.0
    assert cs.changes[0].after == 18.0


# ---- apply_repricing: approval required, never auto-applies ---------------------


def test_apply_repricing_without_approval_is_refused():
    store = _price_store()
    cs = stage_repricing(store, "shopify:demo", {"SKU-1": 18.0}, idempotency_key="r1", reason=REASON)
    with pytest.raises(writeback.WritebackRefused):
        apply_repricing(store, cs, None, now=0.0)
    assert store.read("SKU-1")["price"] == 20.0


def test_apply_repricing_with_valid_approval_writes():
    store = _price_store()
    cs = stage_repricing(store, "shopify:demo", {"SKU-1": 18.0}, idempotency_key="r1", reason=REASON)
    approval = writeback.approve(cs, "operator", now=0.0)
    result = apply_repricing(store, cs, approval, now=10.0)
    assert result.applied is True
    assert store.read("SKU-1")["price"] == 18.0


def test_apply_repricing_never_auto_applies_an_expired_approval():
    store = _price_store()
    cs = stage_repricing(store, "shopify:demo", {"SKU-1": 18.0}, idempotency_key="r1", reason=REASON)
    approval = writeback.approve(cs, "operator", now=0.0, ttl_seconds=900.0)
    with pytest.raises(writeback.WritebackRefused):
        apply_repricing(store, cs, approval, now=1000.0)  # past the 900s TTL


# ---- verify_applied: post-apply read-back ----------------------------------------


def test_verify_applied_passes_when_live_matches_staged():
    store = _price_store()
    cs = stage_repricing(store, "shopify:demo", {"SKU-1": 18.0}, idempotency_key="r1", reason=REASON)
    approval = writeback.approve(cs, "operator", now=0.0)
    apply_repricing(store, cs, approval, now=0.0)
    verify_applied(store, cs, "shopify:demo")  # does not raise


class _MismatchStore:
    """Wraps a real store's commit but always reports a stale price for one
    SKU on ``read()`` -- simulates a channel whose apply silently didn't
    take (or an eventual-consistency race), to test ``verify_applied``'s
    incident surfacing (plan section 7: "apply sin verificacion =
    incidente")."""

    def __init__(self, inner: writeback.InMemoryStore, *, lie_about: dict[str, float]) -> None:
        self._inner = inner
        self._lie_about = lie_about

    def read(self, entity_id: str) -> dict:
        live = dict(self._inner.read(entity_id))
        if entity_id in self._lie_about and "price" in live:
            live["price"] = self._lie_about[entity_id]
        return live

    def applied_keys(self) -> set[str]:
        return self._inner.applied_keys()

    def claim(self, idempotency_key: str, *, now: float | None = None) -> bool:
        return self._inner.claim(idempotency_key, now=now)

    def release(self, idempotency_key: str) -> None:
        self._inner.release(idempotency_key)

    def commit(self, changeset: writeback.Changeset, approved_by: str) -> writeback.AuditEntry:
        return self._inner.commit(changeset, approved_by)

    def rollback(self, idempotency_key: str) -> None:
        self._inner.rollback(idempotency_key)


def test_verify_applied_raises_an_incident_on_a_live_mismatch():
    inner = _price_store()
    store = _MismatchStore(inner, lie_about={"SKU-1": 19.99})  # apply "took" 18.0 but channel reports 19.99
    cs = stage_repricing(store, "shopify:demo", {"SKU-1": 18.0}, idempotency_key="r1", reason=REASON)
    approval = writeback.approve(cs, "operator", now=0.0)
    apply_repricing(store, cs, approval, now=0.0)

    with pytest.raises(RepricingVerificationFailed) as excinfo:
        verify_applied(store, cs, "shopify:demo")
    assert excinfo.value.channel == "shopify:demo"
    assert excinfo.value.mismatches == (("SKU-1", 18.0, 19.99),)


# ---- full cycle against each of the three InMemory connector stand-ins ----------


def test_full_cycle_against_inmemory_shopify():
    rpc = InMemoryShopify({"SKU-1": {"variant_id": "gid://shopify/ProductVariant/1", "price": 20.0}})
    store = ShopifyPriceStore(rpc)
    result = run_channel_repricing(
        store, "shopify:demo-shop", {"SKU-1": 18.0},
        idempotency_key="cycle-1", reason=REASON, approved_by="operator", now=0.0,
    )
    assert isinstance(result, ChannelRepricingResult)
    assert result.verified is True
    assert result.apply_result.applied is True
    assert rpc.find_variant_by_sku("SKU-1")["price"] == 18.0


def test_full_cycle_against_inmemory_meli():
    rpc = InMemoryMeli({"SKU-1": {"item_id": "MLA1", "price": 20.0}})
    store = MeliPriceStore(rpc)
    result = run_channel_repricing(
        store, "meli:demo-seller", {"SKU-1": 17.5},
        idempotency_key="cycle-1", reason=REASON, approved_by="operator", now=0.0,
    )
    assert result.verified is True
    assert rpc.find_item_by_sku("SKU-1")["price"] == 17.5


def test_full_cycle_against_inmemory_odoo():
    rpc = demo_odoo()
    store = OdooPriceStore(rpc)
    result = run_channel_repricing(
        store, "odoo", {"SKU-1": 19.0},
        idempotency_key="cycle-1", reason=REASON, approved_by="operator", now=0.0,
    )
    assert result.verified is True
    assert rpc.records("product.product")[1]["list_price"] == 19.0


def test_full_cycle_blocked_by_guardrails_never_calls_apply():
    rpc = InMemoryShopify({"SKU-1": {"variant_id": "gid://shopify/ProductVariant/1", "price": 20.0}})
    store = ShopifyPriceStore(rpc)
    with pytest.raises(RepricingGuardrailBlocked):
        run_channel_repricing(
            store, "shopify:demo-shop", {"SKU-1": 18.0},
            idempotency_key="cycle-1", reason="", approved_by="operator", now=0.0,
        )
    assert rpc.find_variant_by_sku("SKU-1")["price"] == 20.0  # never written
