"""Shopify replenishment: the connector's read side, and the job's refusal to write a plan
into physical stock.

The Odoo job and this one look alike and must not behave alike. Odoo's recommended option
stages a reorder point; Shopify has none, and its only writable inventory number is the
physical count. The tests that matter here are the ones that would fail if someone
"harmonised" the two jobs later: the recommended option must stay write-free, and the
writeback must stay gated on a real count and an explicit human approval.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from jobs import shopify_job  # noqa: E402
from nosilo_core import writeback  # noqa: E402
from scm_agent import tools  # noqa: E402
from src.connectors.shopify import (  # noqa: E402
    InMemoryShopifyCommerce,
    ShopifyConnector,
    demo_shopify,
)

LOC = "gid://shopify/Location/1"


@pytest.fixture
def connector() -> ShopifyConnector:
    return ShopifyConnector(demo_shopify())


# -- read side: Shopify as an InventorySource ----------------------------------


def test_reads_catalog_on_hand_and_orders(connector: ShopifyConnector):
    products = {p.sku: p for p in connector.list_products()}
    assert set(products) == {"SKU-1", "SKU-2", "SKU-3"}
    # price and cost both arrive as decimal STRINGS from Shopify; they must land as floats
    assert products["SKU-1"].price == 40.0
    assert products["SKU-1"].cost == 18.0

    levels = {lvl.sku: lvl.available for lvl in connector.inventory_levels()}
    assert levels == {"SKU-1": 150.0, "SKU-2": 45.0, "SKU-3": 12.0}

    orders = connector.orders()
    assert len(orders) == 8
    assert sum(line.quantity for o in orders for line in o.lines if line.sku == "SKU-3") == 40.0


def test_order_timestamps_are_truncated_to_a_date():
    """The replenishment engine buckets demand per ``created_at`` and sorts lexically.
    Left as a full ISO-8601 timestamp, two orders on the same day become two periods,
    halving the forecast."""
    orders = ShopifyConnector(demo_shopify()).orders()
    assert all(len(o.created_at) == 10 for o in orders)
    assert len({o.created_at for o in orders}) == 8


def test_variants_without_a_sku_are_dropped_not_blanked():
    """Every engine downstream is SKU-indexed. Carrying an unlabelled variant as ""
    would collide all of them into one phantom row."""
    rpc = InMemoryShopifyCommerce(
        variants=[
            {"id": "v1", "sku": "SKU-1", "price": "10.00", "inventoryItem": {"id": "i1"}},
            {"id": "v2", "sku": "  ", "price": "10.00", "inventoryItem": {"id": "i2"}},
            {"id": "v3", "sku": None, "price": "10.00", "inventoryItem": {"id": "i3"}},
        ],
        stock={"SKU-1": {LOC: 5}},
    )
    assert [p.sku for p in ShopifyConnector(rpc).list_products()] == ["SKU-1"]


def test_missing_unit_cost_reads_as_zero_not_a_crash():
    """``inventoryItem.unitCost`` is null on variants that were never costed."""
    rpc = InMemoryShopifyCommerce(
        variants=[{"id": "v1", "sku": "SKU-1", "price": "10.00",
                   "inventoryItem": {"id": "i1", "unitCost": None}}],
        stock={"SKU-1": {LOC: 5}},
    )
    assert ShopifyConnector(rpc).list_products()[0].cost == 0.0


def test_lead_times_are_empty_because_shopify_has_none(connector: ShopifyConnector):
    """Not a stub to fill in later: Shopify models no supplier. A defaulted 14 days would
    reach the deliverable indistinguishable from a measured one."""
    assert connector.lead_times() == {}


# -- writeback: reconciliation only, gated on a count and a human ---------------


def test_reconciliation_round_trip_stage_apply_rollback(connector: ShopifyConnector):
    changeset = connector.stage_reconciliation({"SKU-3": 9}, idempotency_key="count-1")
    assert changeset.risk_tier == writeback.TIER_REVERSIBLE
    assert [(c.entity_id, c.field, c.before, c.after) for c in changeset.changes] == [("SKU-3", "quantity", 12, 9)]
    # staging alone must not have touched the storefront
    assert connector.store.read("SKU-3") == {"quantity": 12}

    result = connector.apply_reconciliation(changeset, approval=writeback.approve(changeset, "operator", now=0.0), now=1.0)
    assert result.applied is True
    assert connector.store.read("SKU-3") == {"quantity": 9}

    connector.rollback("count-1")
    assert connector.store.read("SKU-3") == {"quantity": 12}


def test_reconciliation_never_auto_applies_without_an_approval(connector: ShopifyConnector):
    """``OdooConnector.apply_restock`` auto-applies reversible edits; this one must not.
    Odoo's edit moves a planning parameter -- this one overwrites what the storefront
    believes is on the shelf, and overselling is what happens when it is wrong."""
    changeset = connector.stage_reconciliation({"SKU-3": 9}, idempotency_key="count-2")
    with pytest.raises(Exception):
        connector.apply_reconciliation(changeset)
    assert connector.store.read("SKU-3") == {"quantity": 12}


def test_staging_an_empty_count_is_refused(connector: ShopifyConnector):
    with pytest.raises(ValueError):
        connector.stage_reconciliation({}, idempotency_key="count-3")


# -- the job: a plan is handed off, never written into stock --------------------


def _report(**params):
    payload = shopify_job.prepare(None, {"shopify_rpc": demo_shopify(), **params})
    return shopify_job.run(payload)


def test_the_recommended_option_writes_nothing_to_shopify():
    """The load-bearing test of this whole module. If someone later mirrors the Odoo job
    and makes a write the default, this fails."""
    report = _report()
    assert report.n_restock == 1 and report.total_restock == 28.0
    recommended = [o for o in report.outcome.options if o.recommended]
    assert len(recommended) == 1
    assert "export" in recommended[0].action.lower()
    assert "no write-back" in recommended[0].action.lower()


def test_no_writeback_option_is_offered_without_a_physical_count():
    labels = [o.label for o in _report().outcome.options]
    assert not any("reconcile" in lbl.lower() for lbl in labels)


def test_a_supplied_count_unlocks_the_reconciliation_option_but_not_the_top_rank():
    report = _report(counted={"SKU-3": 9})
    options = sorted(report.outcome.options, key=lambda o: -o.score)
    assert "reconcile" in options[1].label.lower()
    assert options[1].recommended is False
    assert options[0].recommended is True


def test_report_carries_no_changeset_so_autonomy_never_treats_it_as_writeback():
    """``scm_agent/autonomy.py`` detects writeback-capable results by duck-typing
    ``report.changeset``. This report must not grow one by accident."""
    assert not hasattr(_report(), "changeset")


def test_verify_flags_an_empty_order_history():
    """Zero orders means every forecast is zero, every target is zero and nothing is ever
    short -- which reads as 'inventory is healthy' instead of 'we read nothing'."""
    rpc = InMemoryShopifyCommerce(
        variants=[{"id": "v1", "sku": "SKU-1", "price": "10.00", "inventoryItem": {"id": "i1"}}],
        orders=[],
        stock={"SKU-1": {LOC: 5}},
    )
    report = shopify_job.run(shopify_job.prepare(None, {"shopify_rpc": rpc}))
    assert report.n_restock == 0
    assert any("no order history" in issue for issue in shopify_job.verify(report))


def test_verify_passes_on_the_demo_storefront():
    assert shopify_job.verify(_report()) == []


def test_restock_is_priced_from_shopifys_own_unit_cost():
    # SKU-3 is 28 units short at a 5.00 unit cost
    assert _report().restock_value == pytest.approx(140.0)


def test_a_large_restock_escalates_for_finance_sign_off():
    payload = shopify_job.prepare(None, {"shopify_rpc": demo_shopify()})
    report = shopify_job.run(payload, financial_threshold=10.0)
    assert report.outcome.status == "escalated"


# -- registration ---------------------------------------------------------------


def test_shopify_replenishment_is_a_registered_agent_tool():
    reg = tools.build_default_registry()
    tool = next((t for t in reg.list() if t.key == "shopify_replenishment"), None)
    assert tool is not None
    assert tool.requires_data is False  # it reads a live storefront, not an uploaded file


def test_the_deck_states_the_lead_time_gap_rather_than_hiding_it():
    deck = shopify_job.build_deck(_report(), client="Demo", confidence=0.85)
    sources = {s.field: s for s in deck.data_sources}
    assert "unavailable" in sources["Supplier lead times"].source
    assert sources["Supplier lead times"].cadence == "absent"
