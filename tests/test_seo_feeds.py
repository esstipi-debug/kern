"""Tests for src/seo/feeds.py (Linchpin 3.0 PR-23, S2 product feeds).

Round-trips both feed formats through their own parsers (stdlib
xml.etree.ElementTree / json) to verify key fields survive serialization
intact -- no network I/O anywhere in this file.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET

from src.deliverable import Branding
from src.seo import feeds
from src.seo import schema_gen as sg

_NS = {"g": feeds.GOOGLE_MERCHANT_NS}


def _catalog() -> list[sg.CatalogItem]:
    return [
        sg.CatalogItem(
            product_id="SKU-001", title="Wireless Mouse", price=29.99, currency="USD",
            url="https://shop.example.com/mouse", on_hand=12.0, description="Ergonomic wireless mouse",
            gtin="0012345000015", brand="Acme", condition="new",
        ),
        sg.CatalogItem(
            product_id="SKU-002", title="No URL Item", price=10.0, currency="USD", on_hand=1.0,
        ),  # missing url -> excluded from FEEDS (but not from schema_gen)
        sg.CatalogItem(
            product_id="SKU-003", title="Discontinued Widget", price=5.0, currency="EUR",
            url="https://shop.example.com/discontinued", availability="discontinued",
        ),
        sg.CatalogItem(
            product_id="SKU-004", title="Limited Run", price=99.0, currency="USD",
            url="https://shop.example.com/limited", availability="limited",
        ),
    ]


# -- missing_required_feed_fields (the feed-only 'url' requirement) ---------


def test_missing_required_feed_fields_requires_url_unlike_schema_gen() -> None:
    item = sg.CatalogItem(product_id="SKU-100", title="No Link", price=1.0, currency="USD", on_hand=1.0)
    assert sg.missing_required_fields(item) == []  # schema_gen: url is optional
    assert feeds.missing_required_feed_fields(item) == ["url"]  # feeds: url is required


def test_missing_required_feed_fields_still_flags_schema_gens_own_requirements() -> None:
    item = sg.CatalogItem(product_id="SKU-101", title="No Price", currency="USD", on_hand=1.0)
    missing = feeds.missing_required_feed_fields(item)
    assert "price" in missing
    assert "url" in missing


# -- Merchant XML feed --------------------------------------------------------


def test_build_merchant_feed_xml_excludes_item_missing_url() -> None:
    report = feeds.build_merchant_feed_xml(
        _catalog(), feed_title="Shop", feed_link="https://shop.example.com", feed_description="A shop",
    )
    assert report.n_included == 3
    assert report.n_excluded == 1
    assert report.excluded[0].product_id == "SKU-002"
    assert report.excluded[0].reasons == ("url",)


def test_build_merchant_feed_xml_has_generator_and_namespace() -> None:
    report = feeds.build_merchant_feed_xml(
        _catalog(), feed_title="Shop", feed_link="https://shop.example.com", feed_description="A shop",
    )
    assert "xmlns:g" in report.xml
    assert feeds.FEED_GENERATOR in report.xml
    root = ET.fromstring(report.xml)
    generator = root.find("./channel/generator")
    assert generator is not None and generator.text == "Kern SEO Feed Generator"


def test_build_merchant_feed_xml_uses_custom_branding_in_generator() -> None:
    report = feeds.build_merchant_feed_xml(
        _catalog(), feed_title="Shop", feed_link="https://shop.example.com", feed_description="A shop",
        branding=Branding(name="Partner Co"),
    )
    root = ET.fromstring(report.xml)
    generator = root.find("./channel/generator")
    assert generator is not None and generator.text == "Partner Co SEO Feed Generator"
    assert "Kern" not in report.xml


def test_build_merchant_feed_xml_round_trips_key_fields() -> None:
    report = feeds.build_merchant_feed_xml(
        _catalog(), feed_title="Shop", feed_link="https://shop.example.com", feed_description="A shop",
    )
    root = ET.fromstring(report.xml)
    items_by_id = {}
    for entry in root.findall(".//item"):
        pid = entry.find("g:id", _NS).text
        items_by_id[pid] = {
            "title": entry.find("g:title", _NS).text,
            "link": entry.find("g:link", _NS).text,
            "price": entry.find("g:price", _NS).text,
            "availability": entry.find("g:availability", _NS).text,
        }

    assert items_by_id["SKU-001"]["title"] == "Wireless Mouse"
    assert items_by_id["SKU-001"]["price"] == "29.99 USD"
    assert items_by_id["SKU-001"]["availability"] == "in stock"
    assert items_by_id["SKU-001"]["link"] == "https://shop.example.com/mouse"
    condition = root.find(".//item/g:condition", _NS)
    assert condition is not None and condition.text == "new"
    gtin = root.find(".//item/g:gtin", _NS)
    assert gtin is not None and gtin.text == "0012345000015"


def test_build_merchant_feed_xml_documented_availability_approximations() -> None:
    """Golden Rule 14: LimitedAvailability/Discontinued have no distinct
    Merchant equivalent -- this asserts the DOCUMENTED mapping, not a
    silent collapse."""
    report = feeds.build_merchant_feed_xml(
        _catalog(), feed_title="Shop", feed_link="https://shop.example.com", feed_description="A shop",
    )
    root = ET.fromstring(report.xml)
    by_id = {entry.find("g:id", _NS).text: entry.find("g:availability", _NS).text for entry in root.findall(".//item")}
    assert by_id["SKU-003"] == "out of stock"  # Discontinued -> out of stock
    assert by_id["SKU-004"] == "in stock"  # LimitedAvailability -> in stock


# -- generic JSON feed ---------------------------------------------------------


def test_build_generic_json_feed_round_trips_key_fields() -> None:
    report = feeds.build_generic_json_feed(
        _catalog(), feed_title="Shop", feed_link="https://shop.example.com", feed_description="A shop",
    )
    assert report.n_included == 3
    assert report.n_excluded == 1

    serialized = json.dumps(report.feed)
    parsed = json.loads(serialized)

    assert parsed["feed_info"]["generated_by"] == "Kern"
    assert parsed["feed_info"]["format"] == "kern-generic-product-feed-v1"

    products_by_id = {p["id"]: p for p in parsed["products"]}
    assert products_by_id["SKU-001"]["price"] == 29.99
    assert products_by_id["SKU-001"]["currency"] == "USD"
    assert products_by_id["SKU-001"]["availability"] == "in stock"
    assert products_by_id["SKU-001"]["condition"] == "new"
    assert "SKU-002" not in products_by_id  # excluded (missing url)


def test_build_generic_json_feed_uses_custom_branding_in_generated_by() -> None:
    report = feeds.build_generic_json_feed(
        _catalog(), feed_title="Shop", feed_link="https://shop.example.com", feed_description="A shop",
        branding=Branding(name="Partner Co"),
    )
    assert report.feed["feed_info"]["generated_by"] == "Partner Co"


def test_generic_json_feed_field_result_matches_serialized_product() -> None:
    report = feeds.build_generic_json_feed(
        _catalog(), feed_title="Shop", feed_link="https://shop.example.com", feed_description="A shop",
    )
    result_by_id = {r.product_id: r.fields for r in report.items}
    product_by_id = {p["id"]: p for p in report.feed["products"]}
    for pid, fields in result_by_id.items():
        assert fields == product_by_id[pid]


# -- write_* (local file writes, tmp_path) ------------------------------------


def test_write_merchant_feed_xml_and_generic_json_feed(tmp_path) -> None:
    merchant = feeds.build_merchant_feed_xml(
        _catalog(), feed_title="Shop", feed_link="https://shop.example.com", feed_description="A shop",
    )
    generic = feeds.build_generic_json_feed(
        _catalog(), feed_title="Shop", feed_link="https://shop.example.com", feed_description="A shop",
    )

    xml_path = feeds.write_merchant_feed_xml(merchant, tmp_path / "merchant_feed.xml")
    json_path = feeds.write_generic_json_feed(generic, tmp_path / "generic_feed.json")

    assert xml_path.exists()
    root = ET.fromstring(xml_path.read_text(encoding="utf-8"))
    assert root.find("./channel/title").text == "Shop"

    on_disk = json.loads(json_path.read_text(encoding="utf-8"))
    assert on_disk["feed_info"]["title"] == "Shop"
    assert len(on_disk["products"]) == 3


def test_write_excluded_csv_reports_reasons_and_handles_empty(tmp_path) -> None:
    merchant = feeds.build_merchant_feed_xml(
        _catalog(), feed_title="Shop", feed_link="https://shop.example.com", feed_description="A shop",
    )
    csv_path = feeds.write_excluded_csv(merchant.excluded, tmp_path / "excluded.csv")
    text = csv_path.read_text(encoding="utf-8")
    assert "SKU-002" in text
    assert "url" in text

    empty_path = feeds.write_excluded_csv([], tmp_path / "excluded_empty.csv")
    empty_text = empty_path.read_text(encoding="utf-8")
    assert "product_id" in empty_text
    assert "SKU" not in empty_text
