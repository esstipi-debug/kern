"""Tests for src/pricing_intel/acquire/structured.py (Linchpin 3.0 PR-11).

Guarantees under test:
- extract_product_metadata finds JSON-LD Offer nodes (including ones nested
  under a Product inside a "@graph" array), microdata Offer nodes, and an
  OpenGraph product:price:* pair;
- an empty/whitespace-only HTML string never raises -- returns an all-empty
  ProductMetadata;
- a page with no structured data at all returns an all-empty
  ProductMetadata, not an error;
- the verified extruct 0.18.0 failure mode (a malformed JSON-LD <script>
  block crashes extruct.extract()'s whole call, including other syntaxes)
  does not blind extraction to valid microdata on the same page -- the
  adapter retries extruct without json-ld;
- the hand-rolled JSON-LD-only fallback (extruct unavailable) recovers a
  clean JSON-LD block via json.loads, and a malformed-but-JS-ish block via
  chompjs -- and marks its own provenance distinctly from extruct's.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.pricing_intel.acquire import structured
from src.pricing_intel.acquire.structured import extract_product_metadata

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pricing_intel"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_extracts_json_ld_offer_from_clean_fixture() -> None:
    meta = extract_product_metadata(_load("jsonld_clean.html"))
    assert len(meta.json_ld_offers) == 1
    offer = meta.json_ld_offers[0]
    assert offer["price"] == "199.99"
    assert offer["priceCurrency"] == "USD"
    assert meta.json_ld_source == "extruct"
    assert meta.microdata_offers == ()
    assert meta.opengraph_price is None


def test_finds_offer_nested_under_graph_and_skips_nothing() -> None:
    meta = extract_product_metadata(_load("jsonld_with_promo_and_bad_offer.html"))
    # Both Offer nodes are structurally found -- validity of the price text
    # itself is extract.py's concern, not this adapter's.
    assert len(meta.json_ld_offers) == 2
    prices = {o.get("price") for o in meta.json_ld_offers}
    assert prices == {"call for price", "89.00"}


def test_extracts_microdata_offer_when_no_json_ld_present() -> None:
    meta = extract_product_metadata(_load("microdata_only.html"))
    assert meta.json_ld_offers == ()
    assert len(meta.microdata_offers) == 1
    assert meta.microdata_offers[0]["price"] == "349.50"
    assert meta.microdata_offers[0]["priceCurrency"] == "USD"


def test_extracts_opengraph_price_pair() -> None:
    meta = extract_product_metadata(_load("opengraph_only.html"))
    assert meta.json_ld_offers == ()
    assert meta.microdata_offers == ()
    assert meta.opengraph_price == {"price": "74.25", "priceCurrency": "EUR"}


def test_page_with_no_structured_data_returns_all_empty_metadata() -> None:
    meta = extract_product_metadata(_load("text_only.html"))
    # extruct still runs (and its provenance is honestly recorded even
    # though it found nothing) -- only json_ld has a "not attempted" state
    # distinct from "attempted, found nothing", since it can also come from
    # the hand-rolled fallback.
    assert meta.json_ld_offers == ()
    assert meta.json_ld_source == "none"
    assert meta.microdata_offers == ()
    assert meta.opengraph_price is None
    assert meta.structured_source == "extruct"


def test_empty_html_string_returns_all_empty_metadata_without_raising() -> None:
    meta = extract_product_metadata("")
    assert meta.json_ld_offers == ()
    assert meta.microdata_offers == ()
    assert meta.opengraph_price is None


def test_whitespace_only_html_returns_all_empty_metadata_without_raising() -> None:
    meta = extract_product_metadata("   \n  ")
    assert meta.json_ld_offers == ()


def test_malformed_json_ld_does_not_blind_extraction_to_sibling_microdata() -> None:
    # The verified extruct 0.18.0 failure mode: one broken <script
    # type="application/ld+json"> raises out of extruct.extract() for the
    # WHOLE call. This must not cost us the microdata on the same page.
    meta = extract_product_metadata(_load("malformed_jsonld_with_microdata.html"))
    assert meta.json_ld_offers == ()  # unrecoverable block, honestly empty
    assert len(meta.microdata_offers) == 1
    assert meta.microdata_offers[0]["price"] == "15.75"
    assert meta.structured_source == "extruct"  # microdata still came from extruct


def test_extruct_raising_on_the_combined_call_is_handled(monkeypatch: pytest.MonkeyPatch) -> None:
    """Directly exercises the retry-without-json-ld branch, independent of
    which real HTML triggers it in practice."""
    calls: list[list[str]] = []
    real_extract = structured.extruct.extract

    def flaky_extract(html: str, syntaxes: list[str]):
        calls.append(list(syntaxes))
        if "json-ld" in syntaxes:
            raise ValueError("simulated extruct crash on a malformed script block")
        return real_extract(html, syntaxes=syntaxes)

    monkeypatch.setattr(structured.extruct, "extract", flaky_extract)
    meta = extract_product_metadata(_load("malformed_jsonld_with_microdata.html"))
    assert calls[0] == ["json-ld", "microdata", "opengraph"]
    assert calls[1] == ["microdata", "opengraph"]
    assert len(meta.microdata_offers) == 1


# -- hand-rolled fallback (extruct unavailable) ------------------------------


def test_fallback_recovers_clean_json_ld_when_extruct_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(structured, "_HAS_EXTRUCT", False)
    meta = extract_product_metadata(_load("jsonld_clean.html"))
    assert len(meta.json_ld_offers) == 1
    assert meta.json_ld_offers[0]["price"] == "199.99"
    assert meta.json_ld_source == "ldjson_fallback"
    assert meta.json_ld_source_version == structured.LDJSON_FALLBACK_VERSION
    # No hand-rolled equivalent for microdata/opengraph -- honest empty,
    # never fabricated.
    assert meta.structured_source == "none"


def test_fallback_recovers_js_style_malformed_json_via_chompjs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(structured, "_HAS_EXTRUCT", False)
    html = (
        "<html><head><script type=\"application/ld+json\">"
        '{"@type": "Offer", price: "42.00", priceCurrency: "USD",}'
        "</script></head><body></body></html>"
    )
    meta = extract_product_metadata(html)
    assert len(meta.json_ld_offers) == 1
    assert meta.json_ld_offers[0]["price"] == "42.00"
    assert meta.json_ld_source == "ldjson_fallback"


def test_fallback_skips_unrecoverable_block_without_raising(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(structured, "_HAS_EXTRUCT", False)
    meta = extract_product_metadata(_load("malformed_jsonld_with_microdata.html"))
    assert meta.json_ld_offers == ()
    assert meta.json_ld_source == "none"


def test_fallback_via_regex_when_lxml_also_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(structured, "_HAS_EXTRUCT", False)
    monkeypatch.setattr(structured, "_HAS_LXML", False)
    meta = extract_product_metadata(_load("jsonld_clean.html"))
    assert len(meta.json_ld_offers) == 1
    assert meta.json_ld_offers[0]["price"] == "199.99"
