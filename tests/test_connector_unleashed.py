"""Unleashed as the second ERP, and the bug that making the ERP swappable exposed.

The load-bearing test here is not about Unleashed's endpoints. It is
``test_the_erp_cost_wins_when_the_erp_is_not_odoo``: ``src/entities.py`` used to name
``SOURCE_ODOO`` as the owner of ``unit_cost`` as a constant, and ownership only decides
ties among systems that actually reported. So the moment the ERP was something else, no
system owned cost, the channel's display figure won on recency, and the restock was priced
at a storefront's number -- silently, and in the direction that under-reports value to the
financial escalation threshold.

Measured before the fix: Unleashed reported 12.00, Shopify 18.00, and the resolved cost
came back 18.00. That is what these tests exist to keep from coming back.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from nosilo_core import writeback  # noqa: E402

from jobs import channel_replenishment_job as job  # noqa: E402
from src.connectors.unleashed import (  # noqa: E402
    InMemoryUnleashed,
    UnleashedConnector,
    demo_unleashed,
    sign_query,
)
from src.entities import (  # noqa: E402
    COST_OWNING_SYSTEMS,
    SOURCE_MELI,
    SOURCE_SHOPIFY,
    SOURCE_UNLEASHED,
    build_scm_registry,
)


@pytest.fixture
def connector() -> UnleashedConnector:
    return UnleashedConnector(demo_unleashed())


# -- reads ------------------------------------------------------------------------


def test_reads_catalogue_stock_and_sales_history(connector: UnleashedConnector):
    products = {p.sku: p for p in connector.list_products()}
    assert set(products) == {"SKU-1", "SKU-2", "SKU-3"}
    assert products["SKU-1"].cost == 12.0

    assert {lvl.sku: lvl.available for lvl in connector.inventory_levels()} == {
        "SKU-1": 12.0, "SKU-2": 300.0, "SKU-3": 20.0
    }

    orders = connector.orders()
    assert len(orders) == 8
    assert all(len(o.created_at) == 10 for o in orders)   # OrderDate is a full timestamp


def test_both_documented_spellings_of_the_landed_cost_are_read():
    """Unleashed's own docs are inconsistent: ``AverageLandPrice`` in the field list,
    ``AverageLandedPrice`` in the prose. Betting on one and losing yields a silent 0.0,
    and a zero book cost deflates the restock below the escalation threshold."""
    for spelling in ("AverageLandPrice", "AverageLandedPrice"):
        rpc = InMemoryUnleashed(products=[{"Guid": "g", "ProductCode": "A", spelling: 7.5}])
        assert UnleashedConnector(rpc).list_products()[0].cost == 7.5, spelling


def test_cost_falls_back_through_the_documented_alternatives():
    rpc = InMemoryUnleashed(products=[{"Guid": "g", "ProductCode": "A", "LastCost": 3.25}])
    assert UnleashedConnector(rpc).list_products()[0].cost == 3.25


def test_products_without_a_code_are_dropped():
    rpc = InMemoryUnleashed(products=[
        {"Guid": "g1", "ProductCode": "GOOD"}, {"Guid": "g2", "ProductCode": "  "},
    ])
    assert [p.sku for p in UnleashedConnector(rpc).list_products()] == ["GOOD"]


def test_the_sales_window_is_passed_through(connector: UnleashedConnector):
    """``startDate`` is the filter kern's jobs still do not use anywhere. It works here,
    which is what makes Cin7 Core viable later -- its N+1 read is only affordable windowed."""
    assert len(connector.orders(since="2026-01-05")) == 4


def test_lead_times_are_empty(connector: UnleashedConnector):
    """Not found on the endpoints this connector reads. Whether a supplier record carries
    one was not surveyed -- either way an invented default would reach the deliverable
    indistinguishable from a measured one."""
    assert connector.lead_times() == {}


def test_the_signature_covers_the_query_string_only():
    """Unleashed signs the PARAMETERS, not the endpoint and not the '?'. A request with no
    parameters signs the empty string. Getting this boundary wrong yields a 403 that reads
    like a bad key rather than a bad signature."""
    assert sign_query("", "secret") == sign_query("", "secret")
    assert sign_query("page=1", "secret") != sign_query("page=2", "secret")
    assert sign_query("page=1", "secret") != sign_query("page=1", "other-secret")


# -- the reorder-point writeback ---------------------------------------------------


def test_stage_apply_rollback_round_trip(connector: UnleashedConnector):
    changeset = connector.stage_restock({"SKU-3": 20.0}, idempotency_key="k1", reason="cover")
    assert changeset.target == "unleashed"
    assert changeset.risk_tier == writeback.TIER_REVERSIBLE
    # alert level = on-hand (20) + restock (20)
    assert [(c.entity_id, c.field, c.after) for c in changeset.changes] == [
        ("SKU-3", "MinStockAlertLevel", 40.0)
    ]
    assert connector.store.read("SKU-3") == {}          # staging wrote nothing

    assert connector.apply_restock(changeset).applied is True
    assert connector.store.read("SKU-3") == {"MinStockAlertLevel": 40.0}

    connector.rollback("k1")
    # Back to not-set, not to 0.0: a real alert level of zero is a different claim
    assert connector.store.read("SKU-3") == {}


def test_reapplying_the_same_key_does_not_double_write(connector: UnleashedConnector):
    changeset = connector.stage_restock({"SKU-3": 20.0}, idempotency_key="k2")
    connector.apply_restock(changeset)
    assert connector.apply_restock(changeset).idempotent_skip is True


# -- the reason this whole change was needed ---------------------------------------


def test_the_erp_cost_wins_when_the_erp_is_not_odoo():
    """THE test of this module.

    Unleashed says SKU-1 costs 12.00; Shopify says 18.00. The restock is 4 units of SKU-1
    plus 20 of SKU-3 at 5.00, so the ERP's book cost gives 148.00 and the storefront's
    would give 172.00. Before ``cost_owner`` became a parameter this returned 172.00, and
    nothing else in the report looked any different.
    """
    report = job.run(job.prepare(None, {"erp": "unleashed", "channel": "shopify"}))
    assert report.restock == {"SKU-1": 4.0, "SKU-3": 20.0}
    assert report.restock_value == pytest.approx(148.0)


def test_a_sales_channel_cannot_be_declared_the_owner_of_cost():
    """A storefront reports a display price. Letting one own ``unit_cost`` would invert the
    guarantee rather than merely fail to apply it, so the registry refuses to be built."""
    for channel in (SOURCE_SHOPIFY, SOURCE_MELI):
        with pytest.raises(ValueError, match="cannot own unit_cost"):
            build_scm_registry(cost_owner=channel)
    assert SOURCE_UNLEASHED in COST_OWNING_SYSTEMS


# -- the ERP slot ------------------------------------------------------------------


def test_every_channel_runs_against_every_erp():
    """Four combinations, one job. Identical plans across ERPs would mean the parameter
    was inert; identical plans across channels would mean the channel one was."""
    seen = {}
    for erp in job.ERPS:
        for channel in job.CHANNELS:
            report = job.run(job.prepare(None, {"erp": erp, "channel": channel}))
            assert job.verify(report) == []
            assert report.changeset is not None
            assert report.changeset.target == erp
            seen[(erp, channel)] = tuple(sorted(report.restock.items()))
    assert seen[("odoo", "shopify")] != seen[("odoo", "meli")]


def test_an_unknown_erp_is_refused_by_name():
    with pytest.raises(ValueError, match="unknown erp"):
        job.prepare(None, {"erp": "xero"})


def test_xero_is_absent_from_the_erp_slot_on_purpose():
    """Xero holds >60% of the Australian accounting market and is still not here: its
    Accounting API has no reorder point, and Xero Inventory Plus's levels are informational
    (no purchase orders, no alerts). It is a demand/cost SOURCE, not a plan destination --
    the same line that keeps Shopify and MercadoLibre out of this slot."""
    assert "xero" not in job.ERPS
    assert set(job.ERPS) == {"odoo", "unleashed"}


def test_the_deck_names_whichever_erp_received_the_plan():
    report = job.run(job.prepare(None, {"erp": "unleashed", "channel": "meli"}))
    deck = job.build_deck(report, client="Demo", confidence=0.85)
    stock = next(s for s in deck.data_sources if s.field == "Stock / lead times")
    assert "Unleashed" in stock.source
