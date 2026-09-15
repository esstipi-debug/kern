"""MercadoLibre as a demand source, and the channel slot it plugs into.

``meli_prices.py`` could already reprice a seller account; nothing could plan against one,
because no adapter implemented ``InventorySource``. These tests cover the read half and the
thing it makes possible: running the channel -> ERP loop over a second channel without the
job knowing which one it got.

The MercadoLibre case is a STRONGER test of field ownership than the Shopify one. Shopify
reports a competing cost that the ERP's must beat; MercadoLibre reports no cost at all, so
the failure mode is the opposite -- an absent cost collapsing to zero and deflating the
restock's value below the financial escalation threshold.
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
from src.connectors.meli import (  # noqa: E402
    InMemoryMeliCommerce,
    MeliConnector,
    demo_meli_commerce,
)


@pytest.fixture
def connector() -> MeliConnector:
    return MeliConnector(demo_meli_commerce())


# -- the read half ---------------------------------------------------------------


def test_reads_catalogue_stock_and_orders(connector: MeliConnector):
    products = {p.sku: p for p in connector.list_products()}
    assert set(products) == {"SKU-1", "SKU-2", "SKU-3"}
    assert products["SKU-1"].price == 42.0

    assert {lvl.sku: lvl.available for lvl in connector.inventory_levels()} == {
        "SKU-1": 30.0, "SKU-2": 60.0, "SKU-3": 4.0
    }

    orders = connector.orders()
    assert len(orders) == 8
    assert sum(ln.quantity for o in orders for ln in o.lines if ln.sku == "SKU-3") == 32.0


def test_both_sku_spellings_are_read():
    """A MercadoLibre item carries the seller SKU in ``seller_custom_field`` (legacy, still
    the most populated) OR ``attributes[SELLER_SKU]`` (modern). The demo fixture uses both;
    reading only one would make a chunk of any real catalogue look SKU-less and get dropped."""
    skus = {p.sku for p in MeliConnector(demo_meli_commerce()).list_products()}
    assert skus == {"SKU-1", "SKU-2", "SKU-3"}   # SKU-2 is the attributes[] one


def test_items_without_any_seller_sku_are_dropped():
    rpc = InMemoryMeliCommerce(
        items=[
            {"id": "MLA1", "title": "ok", "price": 10.0, "available_quantity": 5,
             "seller_custom_field": "GOOD"},
            {"id": "MLA2", "title": "sin sku", "price": 10.0, "available_quantity": 5,
             "seller_custom_field": None, "attributes": []},
        ]
    )
    assert [p.sku for p in MeliConnector(rpc).list_products()] == ["GOOD"]


def test_order_timestamps_are_truncated_to_a_date(connector: MeliConnector):
    """MercadoLibre returns a full offset-bearing ISO timestamp. Left whole, each order
    becomes its own demand period and the forecast collapses."""
    orders = connector.orders()
    assert all(len(o.created_at) == 10 for o in orders)
    assert len({o.created_at for o in orders}) == 8


def test_cost_is_absent_rather_than_zero(connector: MeliConnector):
    """MercadoLibre models no cost of goods. Reported as the DTO default so
    ``src.entities._reported_money`` treats it as 'not reported' -- see
    test_an_absent_channel_cost_does_not_deflate_the_restock_value for why that matters."""
    assert all(p.cost == 0.0 for p in connector.list_products())


def test_lead_times_are_empty_because_the_marketplace_has_none(connector: MeliConnector):
    assert connector.lead_times() == {}


def test_a_full_catalogue_read_announces_truncation():
    """More items than the page walk covers must set ``truncated``, so the deck can say the
    plan is partial instead of presenting it as complete."""
    rpc = InMemoryMeliCommerce(items=[
        {"id": f"MLA{i}", "title": str(i), "price": 1.0, "available_quantity": 1,
         "seller_custom_field": f"SKU-{i}"}
        for i in range(300)
    ])
    connector = MeliConnector(rpc)
    connector.list_products()
    assert connector.truncated is True


# -- the channel slot ------------------------------------------------------------


def _run(channel: str):
    return job.run(job.prepare(None, {"channel": channel}))


def test_the_job_runs_over_mercadolibre_as_a_second_channel():
    report = _run("meli")
    assert report.n_matched == 3
    assert report.restock == {"SKU-3": 12.0}
    assert report.changeset is not None and report.changeset.target == "odoo"


def test_an_absent_channel_cost_does_not_deflate_the_restock_value():
    """The load-bearing assertion for this connector.

    MercadoLibre reports no cost. If that absence resolved to 0.0 instead of deferring to
    the ERP's owned ``unit_cost``, the restock would value at $0 -- and a $0 restock never
    crosses the financial escalation threshold, so a large buy would silently stop being
    escalated. 12 units of SKU-3 at Odoo's 5.00 book cost = 60.00.
    """
    assert _run("meli").restock_value == pytest.approx(60.0)


def test_each_channel_yields_its_own_plan_from_the_same_erp():
    """Different demand, same stock: the plans must differ. Identical results would mean
    the channel parameter was inert."""
    shopify, meli = _run("shopify"), _run("meli")
    assert shopify.restock != meli.restock
    assert shopify.erp_source == meli.erp_source


def test_divergence_is_computed_against_whichever_channel_was_read():
    by_sku = {d.sku: d for d in _run("meli").divergences}
    # MercadoLibre publishes 30 of SKU-1; Odoo holds 12
    assert by_sku["SKU-1"].channel_on_hand == 30.0 and by_sku["SKU-1"].erp_on_hand == 12.0


def test_an_unknown_channel_is_refused_by_name():
    with pytest.raises(ValueError, match="unknown channel"):
        job.prepare(None, {"channel": "ebay"})


def test_the_registered_tool_routes_mercadolibre_intents():
    tool = tools.build_default_registry().get("channel_replenishment")
    assert any("mercadolibre" in kw for kw in tool.intent_keywords)


def test_the_deck_names_the_channel_that_was_actually_read():
    deck = job.build_deck(_run("meli"), client="Demo", confidence=0.85)
    demand = next(s for s in deck.data_sources if s.field == "Demand")
    assert "MercadoLibre" in demand.source


# -- what this connector deliberately does NOT do --------------------------------


def test_the_connector_exposes_no_stock_writeback_surface():
    """``available_quantity`` is physical stock, exactly like a Shopify location's
    ``available``. Writing a target cover there would publish stock the seller does not
    hold. Prices stay writable through meli_prices.py -- a price is a decision, not a
    physical fact -- but stock does not, and this asserts the absence on purpose."""
    connector = MeliConnector(demo_meli_commerce())
    for forbidden in ("commit", "claim", "applied_keys", "stage_restock", "stage_reconciliation"):
        assert not hasattr(connector, forbidden), forbidden
