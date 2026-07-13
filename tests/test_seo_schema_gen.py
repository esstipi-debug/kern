"""Tests for src/seo/schema_gen.py (Linchpin 3.0 PR-23, S2 schema.org JSON-LD
generation from a client's own catalog).

Numbers/structures verified by hand in each test's own comments -- no
network, no file I/O outside the two write() tests (which use tmp_path).
"""

from __future__ import annotations

import json

from src.seo import schema_gen as sg


def _full_item(**overrides) -> sg.CatalogItem:
    defaults = dict(
        product_id="SKU-001", title="Wireless Mouse", price=29.99, currency="USD",
        url="https://shop.example.com/mouse", description="Ergonomic wireless mouse",
        image_url="https://shop.example.com/mouse.jpg", brand="Acme", gtin="0012345000015",
        mpn="WM-100", on_hand=12.0, condition="new",
    )
    defaults.update(overrides)
    return sg.CatalogItem(**defaults)


# -- build_product_jsonld ----------------------------------------------------


def test_build_product_jsonld_matches_hand_verified_structure() -> None:
    item = _full_item()
    doc, source_fields = sg.build_product_jsonld(item)

    assert doc == {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Wireless Mouse",
        "sku": "SKU-001",
        "description": "Ergonomic wireless mouse",
        "image": "https://shop.example.com/mouse.jpg",
        "brand": {"@type": "Brand", "name": "Acme"},
        "gtin": "0012345000015",
        "mpn": "WM-100",
        "offers": {
            "@type": "Offer",
            "priceCurrency": "USD",
            "price": 29.99,
            "availability": "https://schema.org/InStock",
            "url": "https://shop.example.com/mouse",
            "itemCondition": "https://schema.org/NewCondition",
        },
    }
    # Golden Rule 7: every emitted field traces to a named CatalogItem column.
    assert source_fields == {
        "name": "title", "sku": "product_id", "description": "description", "image": "image_url",
        "brand.name": "brand", "gtin": "gtin", "mpn": "mpn", "offers.priceCurrency": "currency",
        "offers.price": "price", "offers.availability": "on_hand", "offers.url": "url",
        "offers.itemCondition": "condition",
    }
    assert sg.validate_product_jsonld(doc) == []


def test_build_product_jsonld_omits_absent_optional_fields() -> None:
    item = sg.CatalogItem(
        product_id="SKU-002", title="Bare Widget", price=10.0, currency="USD", on_hand=1.0,
    )
    doc, source_fields = sg.build_product_jsonld(item)
    for key in ("description", "image", "brand", "gtin", "mpn"):
        assert key not in doc
    for key in ("url", "itemCondition"):
        assert key not in doc["offers"]
    assert "offers.url" not in source_fields
    assert sg.validate_product_jsonld(doc) == []


def test_build_product_jsonld_derives_availability_from_on_hand_zero_is_out_of_stock() -> None:
    item = sg.CatalogItem(product_id="SKU-003", title="Keyboard", price=79.5, currency="USD", on_hand=0.0)
    doc, source_fields = sg.build_product_jsonld(item)
    assert doc["offers"]["availability"] == "https://schema.org/OutOfStock"
    assert source_fields["offers.availability"] == "on_hand"


def test_build_product_jsonld_explicit_availability_wins_over_on_hand() -> None:
    item = sg.CatalogItem(
        product_id="SKU-004", title="Stand", price=45.0, currency="EUR",
        on_hand=999.0, availability="preorder",
    )
    doc, source_fields = sg.build_product_jsonld(item)
    assert doc["offers"]["availability"] == "https://schema.org/PreOrder"
    assert source_fields["offers.availability"] == "availability"


def test_build_product_jsonld_raises_on_missing_required_fields() -> None:
    item = sg.CatalogItem(product_id="SKU-005", title="No Price", currency="USD", on_hand=1.0)
    try:
        sg.build_product_jsonld(item)
        raised = False
    except ValueError as exc:
        raised = True
        assert "price" in str(exc)
    assert raised


# -- missing_required_fields --------------------------------------------------


def test_missing_required_fields_flags_missing_price_only() -> None:
    item = sg.CatalogItem(product_id="SKU-006", title="Cable", currency="USD", on_hand=50.0)
    assert sg.missing_required_fields(item) == ["price"]


def test_missing_required_fields_flags_missing_title() -> None:
    item = sg.CatalogItem(product_id="SKU-007", title="   ", price=15.0, currency="USD", on_hand=5.0)
    assert sg.missing_required_fields(item) == ["title"]


def test_missing_required_fields_flags_unresolvable_availability() -> None:
    item = sg.CatalogItem(product_id="SKU-008", title="Mystery Box", price=5.0, currency="USD")
    missing = sg.missing_required_fields(item)
    assert len(missing) == 1
    assert missing[0].startswith("availability")


def test_missing_required_fields_empty_for_complete_item() -> None:
    assert sg.missing_required_fields(_full_item()) == []


# -- normalize_availability_token / normalize_condition_token ----------------


def test_normalize_availability_token_accepts_aliases_bare_and_full_uri() -> None:
    assert sg.normalize_availability_token("in_stock") == "InStock"
    assert sg.normalize_availability_token("out of stock") == "OutOfStock"
    assert sg.normalize_availability_token("PreOrder") == "PreOrder"
    assert sg.normalize_availability_token("https://schema.org/BackOrder") == "BackOrder"


def test_normalize_availability_token_rejects_unrecognized() -> None:
    assert sg.normalize_availability_token("bogus-status") is None


def test_normalize_condition_token_variants() -> None:
    assert sg.normalize_condition_token(None) is None
    assert sg.normalize_condition_token("") is None
    assert sg.normalize_condition_token("used") == "UsedCondition"
    assert sg.normalize_condition_token("https://schema.org/RefurbishedCondition") == "RefurbishedCondition"
    assert sg.normalize_condition_token("not-a-condition") is None


# -- validate_product_jsonld (structural checker) -----------------------------


def test_validate_product_jsonld_accepts_a_correct_example() -> None:
    doc, _ = sg.build_product_jsonld(_full_item())
    assert sg.validate_product_jsonld(doc) == []
    assert sg.is_valid_product_jsonld(doc) is True


def test_validate_product_jsonld_catches_a_deliberately_malformed_example() -> None:
    malformed = {
        "@context": "https://schema.org",
        "@type": "Product",
        "name": "Bad Product",
        # "sku" missing entirely
        "offers": {
            "@type": "Offer",
            "price": -5,  # not positive
            "priceCurrency": "US",  # not a 3-letter code
            "availability": "https://schema.org/Nope",  # not a recognized value
        },
    }
    issues = sg.validate_product_jsonld(malformed)
    assert any("sku is required" in i for i in issues)
    assert any("price is required" in i for i in issues)
    assert any("priceCurrency" in i for i in issues)
    assert any("availability must be one of" in i for i in issues)
    assert sg.is_valid_product_jsonld(malformed) is False


def test_validate_product_jsonld_rejects_non_dict() -> None:
    assert sg.validate_product_jsonld("not a document") == ["document is not a JSON object (dict)"]


def test_validate_product_jsonld_rejects_missing_offers() -> None:
    doc = {"@context": "https://schema.org", "@type": "Product", "name": "X", "sku": "S1"}
    issues = sg.validate_product_jsonld(doc)
    assert issues == ["offers is required and must be a JSON object"]


def test_validate_product_jsonld_rejects_wrong_context_and_type() -> None:
    doc, _ = sg.build_product_jsonld(_full_item())
    doc["@context"] = "http://schema.org"  # wrong scheme
    doc["@type"] = "Thing"
    issues = sg.validate_product_jsonld(doc)
    assert any("@context" in i for i in issues)
    assert any("@type" in i for i in issues)


# -- catalog_to_jsonld (batch, the QA instruction's exact scenario) ----------


def _synthetic_catalog() -> list[sg.CatalogItem]:
    # 5 SKUs: 3 valid, 2 excluded (one missing price, one missing title) --
    # the exact QA scenario from the PR instructions.
    return [
        sg.CatalogItem(
            product_id="SKU-001", title="Wireless Mouse", price=29.99, currency="USD",
            url="https://shop.example.com/mouse", on_hand=12.0, description="Ergonomic wireless mouse",
            gtin="0012345000015", brand="Acme",
        ),
        sg.CatalogItem(
            product_id="SKU-002", title="Mechanical Keyboard", price=79.5, currency="USD",
            url="https://shop.example.com/keyboard", on_hand=0.0,
        ),
        sg.CatalogItem(
            product_id="SKU-003", title="USB-C Cable", price=None, currency="USD",
            url="https://shop.example.com/cable", on_hand=50.0,
        ),  # missing price -> excluded
        sg.CatalogItem(
            product_id="SKU-004", title="", price=15.0, currency="USD",
            url="https://shop.example.com/blank", on_hand=5.0,
        ),  # missing title -> excluded
        sg.CatalogItem(
            product_id="SKU-005", title="Laptop Stand", price=45.0, currency="EUR",
            url="https://shop.example.com/stand", availability="preorder",
        ),
    ]


def test_catalog_to_jsonld_hand_verified_counts() -> None:
    report = sg.catalog_to_jsonld(_synthetic_catalog())
    assert report.n_generated == 3
    assert report.n_excluded == 2
    assert {g.product_id for g in report.generated} == {"SKU-001", "SKU-002", "SKU-005"}
    assert {e.product_id for e in report.excluded} == {"SKU-003", "SKU-004"}


def test_catalog_to_jsonld_reports_missing_price_reason_not_silently_dropped() -> None:
    report = sg.catalog_to_jsonld(_synthetic_catalog())
    excluded_by_id = {e.product_id: e.reasons for e in report.excluded}
    assert excluded_by_id["SKU-003"] == ("price",)
    assert excluded_by_id["SKU-004"] == ("title",)


def test_catalog_to_jsonld_every_generated_document_is_valid() -> None:
    report = sg.catalog_to_jsonld(_synthetic_catalog())
    for g in report.generated:
        assert sg.validate_product_jsonld(g.json_ld) == []


def test_catalog_to_jsonld_empty_catalog() -> None:
    report = sg.catalog_to_jsonld([])
    assert report.n_generated == 0
    assert report.n_excluded == 0


# -- write_catalog_jsonld (local file write, tmp_path) -----------------------


def test_write_catalog_jsonld_round_trips_generated_documents(tmp_path) -> None:
    report = sg.catalog_to_jsonld(_synthetic_catalog())
    written = sg.write_catalog_jsonld(report, tmp_path)

    combined = json.loads(written["combined"].read_text(encoding="utf-8"))
    assert len(combined) == 3

    mouse_path = written["jsonld:SKU-001"]
    assert mouse_path.exists()
    on_disk = json.loads(mouse_path.read_text(encoding="utf-8"))
    assert on_disk["sku"] == "SKU-001"
    assert on_disk["offers"]["price"] == 29.99

    excluded_csv = written["excluded_csv"].read_text(encoding="utf-8")
    assert "SKU-003" in excluded_csv
    assert "price" in excluded_csv


def test_write_catalog_jsonld_sanitizes_unsafe_product_id_into_a_filename(tmp_path) -> None:
    item = sg.CatalogItem(
        product_id="SKU/1 #A?", title="Odd Id", price=5.0, currency="USD", on_hand=1.0,
    )
    report = sg.catalog_to_jsonld([item])
    assert report.n_generated == 1
    written = sg.write_catalog_jsonld(report, tmp_path)
    path = written["jsonld:SKU/1 #A?"]
    assert path.exists()
    assert path.parent == tmp_path / "jsonld"
    # No path separators leaked into the filename itself.
    assert "/" not in path.name and "\\" not in path.name


def test_write_catalog_jsonld_empty_report_writes_header_only_excluded_csv(tmp_path) -> None:
    report = sg.catalog_to_jsonld([])
    written = sg.write_catalog_jsonld(report, tmp_path)
    assert written["combined"].exists()
    assert json.loads(written["combined"].read_text(encoding="utf-8")) == []
    excluded_csv_text = written["excluded_csv"].read_text(encoding="utf-8")
    assert "product_id" in excluded_csv_text
