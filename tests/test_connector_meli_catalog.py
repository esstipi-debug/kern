"""Tests for src/connectors/meli_catalog.py -- official catalog search, offline."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.connectors.meli_catalog import (
    CATALOG_SITES,
    MANUFACTURING_TIME,
    MeliCatalogClient,
    MeliCatalogError,
    category_of,
    demo_meli_catalog,
    gtin_of,
    manufacturing_time_allowed,
    parse_product_search,
)

_REPO = Path(__file__).resolve().parents[1]


def test_manufacturing_time_allowed_detects_the_official_term():
    assert manufacturing_time_allowed([{"id": MANUFACTURING_TIME, "name": "Disponibilidad"}])
    assert not manufacturing_time_allowed([{"id": "WARRANTY_TYPE"}])
    assert not manufacturing_time_allowed([])


def test_gtin_and_category_helpers():
    product = {
        "category_id": "",
        "buy_box_winner": {"category_id": "MLA1055"},
        "attributes": [{"id": "GTIN", "value_name": "7791"}],
    }
    assert gtin_of(product) == "7791"
    assert category_of(product) == "MLA1055"


def test_parse_product_search_round_trips_a_documented_shape():
    body = {
        "keywords": "auriculares",
        "results": [{
            "id": "MLA16240160",
            "status": "active",
            "domain_id": "MLA-CELLPHONES",
            "name": "Auriculares demo",
            "permalink": "https://www.mercadolibre.com.ar/p/MLA16240160",
            "settings": {"listing_strategy": "catalog_required"},
            "attributes": [{"id": "GTIN", "value_name": "08801643066918"}],
            "category_id": "MLA1055",
        }],
    }
    hits = parse_product_search(body, site_id="MLA")
    assert len(hits) == 1
    assert hits[0].product_id == "MLA16240160"
    assert hits[0].gtin == "08801643066918"
    assert hits[0].listing_strategy == "catalog_required"
    assert hits[0].status == "active"


def test_parse_empty_results_is_a_miss_not_an_error():
    assert parse_product_search({"results": []}, site_id="MLA") == ()


def test_parse_error_envelope_raises():
    with pytest.raises(MeliCatalogError, match="not_found|Resource"):
        parse_product_search({"error": "not_found", "message": "Resource not found"}, site_id="MLA")


def test_inmemory_search_by_q_and_gtin():
    catalog = demo_meli_catalog()
    by_q = catalog.search_products(site_id="MLA", q="Auriculares")
    assert [p["id"] for p in by_q["results"]] == ["MLA-PRE-1"]
    by_gtin = catalog.search_products(site_id="MLA", product_identifier="7790000000002")
    assert [p["id"] for p in by_gtin["results"]] == ["MLA-CAT-2"]


def test_inmemory_search_requires_q_or_gtin():
    with pytest.raises(MeliCatalogError, match="q or product_identifier"):
        demo_meli_catalog().search_products(site_id="MLA")


def test_inmemory_sale_terms_and_listed_ids():
    catalog = demo_meli_catalog()
    assert manufacturing_time_allowed(catalog.category_sale_terms("MLA1055"))
    assert not manufacturing_time_allowed(catalog.category_sale_terms("MLA1234"))
    assert catalog.seller_catalog_ids() == frozenset({"MLA-OWN-4"})


def test_live_client_refuses_without_bearer():
    class _Bare:
        headers = {}

    with pytest.raises(MeliCatalogError, match="Bearer"):
        MeliCatalogClient(_Bare())


def test_live_client_hits_products_search_and_sale_terms():
    seen: list[tuple[str, dict]] = []

    class _Resp:
        def __init__(self, body):
            self._body = body

        def raise_for_status(self) -> None:
            return None

        def json(self):
            return self._body

    class _Http:
        headers = {"Authorization": "Bearer test-token"}

        def get(self, path, params=None):
            seen.append((path, params or {}))
            if path == "/products/search":
                return _Resp({"results": [{"id": "MLA1", "name": "X", "status": "active"}]})
            if path.startswith("/categories/"):
                return _Resp([{"id": MANUFACTURING_TIME}])
            if path.endswith("/domain_discovery/search"):
                return _Resp([{"category_id": "MLA1055"}])
            raise AssertionError(path)

    client = MeliCatalogClient(_Http(), user_id="")
    body = client.search_products(site_id="MLA", q="notebook")
    assert body["results"][0]["id"] == "MLA1"
    assert seen[0] == ("/products/search", {
        "site_id": "MLA", "q": "notebook", "status": "active", "limit": 10, "offset": 0,
    })
    terms = client.category_sale_terms("MLA1055")
    assert manufacturing_time_allowed(terms)
    assert client.discover_category("MLA", "notebook") == "MLA1055"
    assert not any(path.startswith("/items") and False for path, _ in seen)
    assert all(not path.startswith("/items") or "search" in path for path, _ in seen)


def test_live_client_rejects_unknown_site():
    class _Http:
        headers = {"Authorization": "Bearer x"}

    with pytest.raises(MeliCatalogError, match="documented catalog site"):
        MeliCatalogClient(_Http()).search_products(site_id="XX", q="a")


def test_catalog_sites_match_the_official_list():
    assert CATALOG_SITES == frozenset({"MLA", "MLB", "MLM", "MLC", "MCO", "MLU"})


def test_module_does_not_import_writeback_or_post_items():
    text = (_REPO / "src/connectors/meli_catalog.py").read_text(encoding="utf-8")
    assert "from nosilo_core" not in text
    assert "import nosilo_core" not in text
    assert ".post(" not in text
    assert ".put(" not in text


def test_public_meli_api_fetcher_still_has_no_search():
    """The robots-prohibited L0 fetcher must not grow /products/search."""
    text = (_REPO / "src/pricing_intel/acquire/meli_api.py").read_text(encoding="utf-8")
    assert "def search" not in text
    assert "products/search" not in text or "NOT wired" in text or "NOT implemented" in text
    assert "class MeliApiFetcher" in text
