"""Channel -> ERP replenishment: the join, the divergence, and what must not go quiet.

Three things here are load-bearing and would each look fine if they broke:

1. The book cost must come from the ERP even though the storefront also reports one. Get
   this wrong and the restock is valued at a display artefact, which moves the financial
   escalation threshold -- silently, in the direction of not escalating.
2. A SKU the storefront sells and the ERP does not carry must surface. It cannot be
   replenished, and dropping it reads as "everything is covered".
3. Two systems disagreeing about on-hand must surface. Planning picks the ERP's figure;
   if the storefront's was right, the reorder point is calibrated against stock that is
   not there.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from jobs import channel_replenishment_job as job  # noqa: E402
from scm_agent import tools  # noqa: E402
from src.connectors.odoo import InMemoryOdoo, demo_odoo  # noqa: E402
from src.connectors.shopify import InMemoryShopifyCommerce, demo_shopify  # noqa: E402

LOC = "gid://shopify/Location/1"


def _run(**params):
    return job.run(job.prepare(None, {"shopify_rpc": demo_shopify(), "odoo_rpc": demo_odoo(), **params}))


def _variant(sku: str, cost: float) -> dict:
    return {
        "id": f"gid://shopify/ProductVariant/{sku}",
        "sku": sku,
        "displayName": sku,
        "price": "10.00",
        "inventoryItem": {"id": f"gid://shopify/InventoryItem/{sku}", "unitCost": {"amount": f"{cost:.2f}"}},
    }


def _order(oid: str, date: str, sku: str, qty: int) -> dict:
    return {
        "id": f"gid://shopify/Order/{oid}",
        "createdAt": f"{date}T10:00:00Z",
        "lineItems": {"edges": [{"node": {"sku": sku, "quantity": qty,
                                          "originalUnitPriceSet": {"shopMoney": {"amount": "10.00"}}}}]},
    }


# -- the join --------------------------------------------------------------------


def test_the_erp_cost_wins_even_though_the_storefront_reports_its_own():
    """The single most consequential assertion in this file.

    In the demo fixtures the two systems disagree on SKU-1's cost: Odoo says 12.00,
    Shopify says 18.00. ``src/entities.py`` declares ``unit_cost`` owned by
    ``SOURCE_ODOO``, so the restock must be valued at the ERP's book cost. Valued at
    Shopify's the total would be 172.0, and nothing else in the report would look
    different -- which is exactly why this is asserted on the number, not on the wiring.
    """
    report = _run()
    # SKU-1: 4 units short x 12.00 (Odoo)  +  SKU-3: 20 short x 5.00 (both agree) = 148
    assert report.restock == {"SKU-1": 4.0, "SKU-3": 20.0}
    assert report.restock_value == pytest.approx(148.0)


def test_demand_comes_from_the_channel_and_stock_from_the_erp():
    """The whole point of the composition: neither number comes from one system."""
    report = _run()
    line = next(ln for ln in report.lines if ln.sku == "SKU-3")
    assert line.channel_demand_per_period > 0      # Shopify has 8 days of orders
    assert line.erp_on_hand == 20.0                # Odoo's stock, not Shopify's 12
    assert line.channel_on_hand == 12.0            # carried for the divergence report


def test_a_sku_in_only_one_system_is_classified_not_silently_dropped():
    shopify = InMemoryShopifyCommerce(
        variants=[_variant("BOTH", 5.0), _variant("CHANNEL-ONLY", 5.0)],
        orders=[_order("1", "2026-01-01", "BOTH", 3), _order("2", "2026-01-02", "CHANNEL-ONLY", 9)],
        stock={"BOTH": {LOC: 1}, "CHANNEL-ONLY": {LOC: 1}},
    )
    payload = job.prepare(None, {"shopify_rpc": shopify, "odoo_rpc": demo_odoo()})
    assert payload["matched"] == []                       # demo Odoo carries SKU-1/2/3
    assert payload["channel_only"] == ("BOTH", "CHANNEL-ONLY")
    assert set(payload["erp_only"]) == {"SKU-1", "SKU-2", "SKU-3"}


def test_channel_only_skus_reach_the_deck_as_a_finding():
    """They cannot be replenished -- there is no ERP product to hold a reorder point.
    Reporting the plan without them reads as 'everything is covered'."""
    shopify = InMemoryShopifyCommerce(
        variants=[_variant("SKU-1", 5.0), _variant("GHOST", 5.0)],
        orders=[_order("1", "2026-01-01", "SKU-1", 3), _order("2", "2026-01-02", "SKU-1", 4)],
        stock={"SKU-1": {LOC: 1}, "GHOST": {LOC: 1}},
    )
    report = job.run(job.prepare(None, {"shopify_rpc": shopify, "odoo_rpc": demo_odoo()}))
    assert report.channel_only == ("GHOST",)
    deck = job.build_deck(report, client="Demo", confidence=0.85)
    assert any("GHOST" in f.detail for f in deck.findings)


# -- the divergence report -------------------------------------------------------


def test_stock_divergence_between_the_two_systems_is_reported():
    """Neither single-connector tool can produce this: it needs both readings."""
    report = _run()
    by_sku = {d.sku: d for d in report.divergences}
    assert by_sku["SKU-1"].channel_on_hand == 150.0 and by_sku["SKU-1"].erp_on_hand == 12.0
    # Positive delta = the storefront claims more than the ERP holds: the overselling direction
    assert by_sku["SKU-1"].delta == 138.0
    assert by_sku["SKU-2"].delta == -255.0


def test_agreement_within_one_unit_is_not_reported_as_divergence():
    """An order in flight moves one system before the other. Reporting that would bury
    the divergences that matter."""
    odoo = demo_odoo()
    shopify = InMemoryShopifyCommerce(
        variants=[_variant("SKU-1", 5.0)],
        orders=[_order("1", "2026-01-01", "SKU-1", 1), _order("2", "2026-01-02", "SKU-1", 1)],
        # Odoo's demo holds 12 of SKU-1; 12.5 is inside the tolerance
        stock={"SKU-1": {LOC: 12}},
    )
    report = job.run(job.prepare(None, {"shopify_rpc": shopify, "odoo_rpc": odoo}))
    assert report.divergences == ()


def test_divergences_add_a_ranked_option_below_the_plan():
    report = _run()
    options = sorted(report.outcome.options, key=lambda o: -o.score)
    assert options[0].recommended is True
    assert "divergence" in options[1].label.lower()
    assert options[1].recommended is False


def test_the_divergence_csv_is_written_only_when_there_are_divergences(tmp_path):
    assert "divergence_csv" in job.write_operational(_run(), tmp_path)

    quiet = InMemoryShopifyCommerce(
        variants=[_variant("SKU-1", 5.0)],
        orders=[_order("1", "2026-01-01", "SKU-1", 1)],
        stock={"SKU-1": {LOC: 12}},
    )
    report = job.run(job.prepare(None, {"shopify_rpc": quiet, "odoo_rpc": demo_odoo()}))
    assert "divergence_csv" not in job.write_operational(report, tmp_path)


# -- the write ------------------------------------------------------------------


def test_the_restock_is_staged_into_the_erp_as_a_reversible_dry_run():
    """Unlike the channel-only tool, this one HAS somewhere to write the plan."""
    report = _run()
    assert report.changeset is not None
    assert report.changeset.target == "odoo"
    assert {c.entity_id for c in report.changeset.changes} == {"SKU-1", "SKU-3"}
    assert all(c.field == "product_min_qty" for c in report.changeset.changes)


def test_nothing_is_staged_when_nothing_is_short():
    quiet = InMemoryShopifyCommerce(
        variants=[_variant("SKU-2", 5.0)],
        orders=[_order("1", "2026-01-01", "SKU-2", 1)],   # Odoo holds 300 of SKU-2
        stock={"SKU-2": {LOC: 300}},
    )
    report = job.run(job.prepare(None, {"shopify_rpc": quiet, "odoo_rpc": demo_odoo()}))
    assert report.restock == {} and report.changeset is None


def test_a_large_restock_escalates_for_finance_sign_off():
    payload = job.prepare(None, {"shopify_rpc": demo_shopify(), "odoo_rpc": demo_odoo()})
    assert job.run(payload, financial_threshold=10.0).outcome.status == "escalated"


# -- the QA gate ----------------------------------------------------------------


def test_verify_blocks_when_the_two_catalogues_do_not_intersect():
    """An empty plan from a failed join reads as a healthy inventory position. It is
    almost always a SKU-spelling mismatch between the two systems."""
    shopify = InMemoryShopifyCommerce(
        variants=[_variant("OTHER-1", 5.0)],
        orders=[_order("1", "2026-01-01", "OTHER-1", 3)],
        stock={"OTHER-1": {LOC: 1}},
    )
    report = job.run(job.prepare(None, {"shopify_rpc": shopify, "odoo_rpc": demo_odoo()}))
    assert any("do not intersect" in issue for issue in job.verify(report))


def test_verify_blocks_when_the_channel_has_no_order_history():
    shopify = InMemoryShopifyCommerce(
        variants=[_variant("SKU-1", 5.0)], orders=[], stock={"SKU-1": {LOC: 1}}
    )
    report = job.run(job.prepare(None, {"shopify_rpc": shopify, "odoo_rpc": demo_odoo()}))
    assert any("no channel order history" in issue for issue in job.verify(report))


def test_verify_passes_on_the_demo_pair():
    assert job.verify(_run()) == []


# -- registration ---------------------------------------------------------------


def test_channel_replenishment_is_a_registered_agent_tool():
    reg = tools.build_default_registry()
    tool = next((t for t in reg.list() if t.key == "channel_replenishment"), None)
    assert tool is not None
    assert tool.requires_data is False


def test_prepare_refuses_a_non_intersecting_pair_before_planning():
    """The tool's prepare must fail loudly rather than hand run() an empty match set."""
    reg = tools.build_default_registry()
    tool = reg.get("channel_replenishment")
    shopify = InMemoryShopifyCommerce(
        variants=[_variant("OTHER-1", 5.0)],
        orders=[_order("1", "2026-01-01", "OTHER-1", 3)],
        stock={"OTHER-1": {LOC: 1}},
    )
    from nosilo_core.types import JobRequest

    request = JobRequest(brief="replenish from the storefront", job_type="channel_replenishment",
                         params={"shopify_rpc": shopify, "odoo_rpc": InMemoryOdoo()})
    prepared = tool.prepare(request, None)
    assert prepared.status == "needs_data"
    assert any("do not intersect" in m for m in prepared.messages)
