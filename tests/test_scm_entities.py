"""The supply-chain entity types, and the bridge from existing connector DTOs.

The test that matters most here is
:func:`test_the_same_sku_in_odoo_and_shopify_is_one_entity` - it is the whole
reason this layer exists. Everything else guards the rules that make the answer
trustworthy: the ERP owns cost, prices and bank details do not move on their own,
and money never becomes a float on the way in.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from nosilo_core.entities.resolution import REASON_GUARDED, REASON_NOT_OWNER

from nosilo_core.entities import SourceRef, SqliteEntityStore
from src.connectors import Product
from src.entities import (
    LOCATION,
    SKU,
    SOURCE_MELI,
    SOURCE_ODOO,
    SOURCE_SHOPIFY,
    SUPPLIER,
    build_scm_registry,
    observe_attributes,
    observe_product,
    sku_attributes,
)


@pytest.fixture()
def store() -> SqliteEntityStore:
    made = SqliteEntityStore(build_scm_registry(), ":memory:")
    yield made
    made.close()


# ---- the declarations -------------------------------------------------------


def test_the_three_master_data_types_are_registered() -> None:
    assert build_scm_registry().names() == ("location", "sku", "supplier")


def test_supplier_guards_the_fields_that_get_companies_robbed() -> None:
    assert {"bank_account", "credit_limit", "payment_terms"} <= SUPPLIER.guarded_fields


def test_the_erp_owns_cost_and_legal_name() -> None:
    assert SKU.field_owners["unit_cost"] == SOURCE_ODOO
    assert SUPPLIER.field_owners["legal_name"] == SOURCE_ODOO


def test_every_type_can_be_matched_across_systems() -> None:
    for declared in (SKU, SUPPLIER, LOCATION):
        assert declared.natural_keys, f"{declared.name} has no natural key to match on"


# ---- attribute construction -------------------------------------------------


def test_money_arrives_as_decimal_not_float() -> None:
    attrs = sku_attributes("A1", unit_cost=0.1, list_price=19.99)
    assert attrs["unit_cost"] == Decimal("0.1")
    assert isinstance(attrs["unit_cost"], Decimal)
    assert attrs["list_price"] == Decimal("19.99")


def test_omitted_values_are_left_out_rather_than_sent_as_none() -> None:
    """"Not reported" and "reported as nothing" must not collapse into one claim."""
    assert sku_attributes("A1") == {"sku_code": "A1"}
    assert "unit_cost" not in sku_attributes("A1", title="Bolt")


# ---- the point of the layer -------------------------------------------------


def test_the_same_sku_in_odoo_and_shopify_is_one_entity(store: SqliteEntityStore) -> None:
    odoo = observe_product(
        store,
        Product(sku="A1", title="Bolt", price=19.99, cost=5.0),
        system=SOURCE_ODOO,
        source_entity="product.product",
        source_id="42",
    )
    shopify = observe_product(
        store,
        Product(sku="A1", title="Bolt M8", price=21.50, cost=0.0),
        system=SOURCE_SHOPIFY,
        source_entity="variant",
        source_id="99",
    )

    assert shopify.nosilo_id == odoo.nosilo_id
    assert not shopify.created
    assert {str(x.source) for x in store.xrefs(odoo.nosilo_id)} == {
        "odoo:product.product:42",
        "shopify:variant:99",
    }


def test_a_third_channel_joins_the_same_entity(store: SqliteEntityStore) -> None:
    first = observe_product(
        store, Product("A1", "Bolt", 19.99, 5.0),
        system=SOURCE_ODOO, source_entity="product.product", source_id="42",
    )
    meli = observe_attributes(
        store, SKU.name,
        system=SOURCE_MELI, source_entity="item", source_id="MLA123",
        attributes={"sku_code": "A1", "title": "Bolt (MELI)"},
    )
    assert meli.nosilo_id == first.nosilo_id
    assert len(store.xrefs(first.nosilo_id)) == 2


def test_the_source_id_not_the_sku_code_keys_the_xref(store: SqliteEntityStore) -> None:
    """So a SKU recode does not orphan the link back to the source record."""
    result = observe_product(
        store, Product("A1", "Bolt", 19.99, 5.0),
        system=SOURCE_ODOO, source_entity="product.product", source_id="42",
    )
    assert store.resolve_source(SourceRef(SOURCE_ODOO, "product.product", "42")) == result.nosilo_id


# ---- the ownership and guard rules, on real SCM fields ----------------------


def test_a_storefront_cannot_overwrite_the_erp_book_cost(store: SqliteEntityStore) -> None:
    observe_product(
        store, Product("A1", "Bolt", 19.99, 5.0),
        system=SOURCE_ODOO, source_entity="product.product", source_id="42",
    )
    shopify = observe_attributes(
        store, SKU.name,
        system=SOURCE_SHOPIFY, source_entity="variant", source_id="99",
        attributes={"sku_code": "A1", "unit_cost": Decimal("99.00")},
    )
    assert [c.reason for c in shopify.conflicts] == [REASON_NOT_OWNER]
    assert store.current(shopify.nosilo_id).attributes["unit_cost"] == Decimal("5.0")


def test_list_price_is_held_for_a_human_when_a_second_channel_disagrees(store: SqliteEntityStore) -> None:
    observe_product(
        store, Product("A1", "Bolt", 19.99, 5.0),
        system=SOURCE_ODOO, source_entity="product.product", source_id="42",
    )
    shopify = observe_product(
        store, Product("A1", "Bolt", 21.50, 0.0),
        system=SOURCE_SHOPIFY, source_entity="variant", source_id="99",
    )
    assert [c.reason for c in shopify.conflicts] == [REASON_GUARDED]
    assert store.current(shopify.nosilo_id).attributes["list_price"] == Decimal("19.99")


def test_a_supplier_bank_account_never_changes_by_data_feed(store: SqliteEntityStore) -> None:
    """The control that turns a common fraud into an impossible one."""
    first = observe_attributes(
        store, SUPPLIER.name,
        system=SOURCE_ODOO, source_entity="res.partner", source_id="7",
        attributes={"tax_id": "B12345678", "legal_name": "Tornillos SA", "bank_account": "ES11 1111"},
    )
    takeover = observe_attributes(
        store, SUPPLIER.name,
        system="portal", source_entity="vendor", source_id="v-7",
        attributes={"tax_id": "B12345678", "bank_account": "ES99 9999"},
        trust=1000.0,
    )
    assert takeover.nosilo_id == first.nosilo_id
    assert [c.reason for c in takeover.conflicts] == [REASON_GUARDED]
    assert store.current(first.nosilo_id).attributes["bank_account"] == "ES11 1111"


def test_locations_resolve_on_their_code(store: SqliteEntityStore) -> None:
    wms = observe_attributes(
        store, LOCATION.name,
        system="wms", source_entity="warehouse", source_id="W1",
        attributes={"location_code": "CD-SCL", "name": "Centro Santiago"},
    )
    erp = observe_attributes(
        store, LOCATION.name,
        system=SOURCE_ODOO, source_entity="stock.location", source_id="12",
        attributes={"location_code": "CD-SCL", "address": "Av. Ejemplo 123"},
    )
    assert erp.nosilo_id == wms.nosilo_id
    current = store.current(erp.nosilo_id)
    assert current.attributes["name"] == "Centro Santiago"
    assert current.attributes["address"] == "Av. Ejemplo 123"


# ---- history over a real sequence -------------------------------------------


def test_the_history_answers_what_did_we_know_then(store: SqliteEntityStore) -> None:
    ref_kwargs = dict(system=SOURCE_ODOO, source_entity="product.product", source_id="42")
    observe_product(
        store, Product("A1", "Bolt", 19.99, 5.0), recorded_at="2026-01-01T00:00:00+00:00", **ref_kwargs
    )
    observe_product(
        store, Product("A1", "Bolt M8", 19.99, 6.5), recorded_at="2026-06-01T00:00:00+00:00", **ref_kwargs
    )
    entity = store.resolve_source(SourceRef(SOURCE_ODOO, "product.product", "42"))

    assert store.as_of(entity, known_at="2026-03-01T00:00:00+00:00").attributes["unit_cost"] == Decimal("5.0")
    assert store.as_of(entity, known_at="2026-09-01T00:00:00+00:00").attributes["unit_cost"] == Decimal("6.5")
    assert len(store.history(entity)) == 2
