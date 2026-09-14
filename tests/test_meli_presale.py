"""Tests for src/meli_presale.py -- preventa classification, no private core."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.connectors.meli_catalog import MANUFACTURING_TIME, InMemoryMeliCatalog, demo_meli_catalog
from src.meli_presale import (
    ALREADY_LISTED,
    CATALOG_ONLY,
    INACTIVE,
    NO_CATEGORY,
    NO_MATCH,
    PRESALE_READY,
    PresaleQuery,
    classify_presale,
    draft_listing,
)

_REPO = Path(__file__).resolve().parents[1]


def _by_status(scan):
    return {c.status: c for c in scan.candidates}


def test_demo_catalog_covers_every_verdict():
    catalog = demo_meli_catalog()
    scan = classify_presale(
        [
            PresaleQuery(q="Auriculares inalambricos demo", sku="SKU-PRE-1"),
            PresaleQuery(q="Cable USB-C demo"),
            PresaleQuery(product_identifier="7790000000003"),
            PresaleQuery(q="Cargador ya publicado demo"),
            PresaleQuery(q="producto que no existe en el catalogo"),
        ],
        catalog,
    )
    by = _by_status(scan)
    assert set(by) == {PRESALE_READY, CATALOG_ONLY, INACTIVE, ALREADY_LISTED, NO_MATCH}
    ready = by[PRESALE_READY]
    assert ready.preventa_allowed is True
    assert ready.draft is not None
    assert ready.draft["catalog_product_id"] == "MLA-PRE-1"
    assert ready.draft["catalog_listing"] is True
    assert ready.draft["sale_terms"][0]["id"] == MANUFACTURING_TIME
    assert ready.draft["seller_custom_field"] == "SKU-PRE-1"
    assert by[CATALOG_ONLY].draft is not None
    assert "sale_terms" not in by[CATALOG_ONLY].draft
    assert by[ALREADY_LISTED].draft is None
    assert by[INACTIVE].product is not None
    assert by[INACTIVE].product.product_id == "MLA-OFF-3"


def test_no_category_when_discovery_misses():
    catalog = InMemoryMeliCatalog(
        products=[{
            "id": "MLA-X", "name": "Sin categoria", "status": "active",
            "domain_id": "MLA-OTHER", "category_id": "", "attributes": [],
            "settings": {},
        }],
        sale_terms={},
        listed_catalog_ids=set(),
    )
    scan = classify_presale([PresaleQuery(q="Sin categoria")], catalog)
    assert scan.candidates[0].status == NO_CATEGORY
    assert scan.candidates[0].draft is None


def test_discover_category_fills_a_missing_id():
    catalog = InMemoryMeliCatalog(
        products=[{
            "id": "MLA-Y", "name": "Con discovery", "status": "active",
            "domain_id": "MLA-OTHER", "category_id": "", "attributes": [],
            "settings": {},
        }],
        sale_terms={"MLA1055": [{"id": MANUFACTURING_TIME}]},
        listed_catalog_ids=set(),
        domain_categories={"con discovery": "MLA1055"},
    )
    scan = classify_presale([PresaleQuery(q="Con discovery")], catalog)
    assert scan.candidates[0].status == PRESALE_READY
    assert scan.candidates[0].product is not None
    assert scan.candidates[0].product.category_id == "MLA1055"


def test_query_requires_q_or_gtin():
    with pytest.raises(ValueError, match="q or product_identifier"):
        PresaleQuery()


def test_empty_query_list_is_refused():
    from src.connectors.meli_catalog import MeliCatalogError

    with pytest.raises(MeliCatalogError, match="at least one"):
        classify_presale([], demo_meli_catalog())


def test_draft_listing_omits_invented_price():
    catalog = demo_meli_catalog()
    scan = classify_presale([PresaleQuery(q="Auriculares inalambricos demo")], catalog)
    draft = scan.candidates[0].draft
    assert draft is not None
    assert "price" not in draft
    product = scan.candidates[0].product
    assert product is not None
    priced = draft_listing(product, manufacturing_days=20, price=99.0, preventa=True)
    assert priced["price"] == 99.0
    assert priced["sale_terms"][0]["value_name"] == "20 dias"


def test_engine_does_not_import_writeback():
    text = (_REPO / "src/meli_presale.py").read_text(encoding="utf-8")
    assert "from nosilo_core" not in text
    assert "import nosilo_core" not in text
