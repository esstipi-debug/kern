"""Tests for src/pricing_intel/extract.py (Linchpin 3.0 PR-11, the cascade).

Guarantees under test (plan section 6.4 -- order and confidence are
load-bearing):
- each of the 5 tiers fires with the right confidence/extractor when it is
  the first one able to produce a valid price, proven against frozen HTML
  fixtures (tests/fixtures/pricing_intel/);
- the cascade STOPS at the first successful tier -- a page with JSON-LD
  never falls through to microdata/selector/etc even if those would also
  match;
- a malformed candidate at one tier (a JSON-LD Offer node whose price is
  unparseable) is skipped in favor of the next candidate at the SAME tier
  before falling through;
- list_price/promo data survives from extraction through to
  normalize.detect_promo;
- a page with no price anywhere raises ExtractionFailed (never a
  fabricated price), and the exception records every tier that was tried;
- tier 5 (LLM) is a documented no-op stub -- always returns None, never
  raises, never blocks the cascade reaching ExtractionFailed.
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path

import pytest

from src.pricing_intel.extract import (
    ExtractionFailed,
    _extract_via_llm,
    extract_price,
)
from src.pricing_intel.normalize import detect_promo

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pricing_intel"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_tier1_json_ld_confidence_and_provenance() -> None:
    result = extract_price(_load("jsonld_clean.html"))
    assert result.price == Decimal("199.99")
    assert result.currency == "USD"
    assert result.availability == "InStock"
    assert result.tier == "json_ld"
    assert result.confidence == 0.98
    assert result.extractor == "structured:extruct"


def test_tier1_skips_a_malformed_offer_node_for_a_good_sibling() -> None:
    # jsonld_with_promo_and_bad_offer.html has TWO Offer nodes: the first
    # has an unparseable price ("call for price"), the second is valid with
    # a promo list_price. The cascade must not fall through to tier 2 just
    # because the first candidate at tier 1 failed.
    result = extract_price(_load("jsonld_with_promo_and_bad_offer.html"))
    assert result.tier == "json_ld"
    assert result.price == Decimal("89.00")
    assert result.list_price == Decimal("120.00")
    assert detect_promo(result.price, result.list_price) is True


def test_tier2_microdata_confidence_and_provenance() -> None:
    result = extract_price(_load("microdata_only.html"))
    assert result.price == Decimal("349.50")
    assert result.currency == "USD"
    assert result.availability == "InStock"
    assert result.tier == "microdata"
    assert result.confidence == 0.90


def test_tier2_opengraph_sub_path() -> None:
    result = extract_price(_load("opengraph_only.html"))
    assert result.price == Decimal("74.25")
    assert result.currency == "EUR"
    assert result.tier == "microdata"


def test_tier3_selector_reads_only_the_selected_node() -> None:
    # selector_only.html has an unrelated "was $18.00" text node earlier in
    # the document -- the selector must isolate ".price-now" ($12.00), not
    # the first dollar amount encountered.
    result = extract_price(
        _load("selector_only.html"),
        selector=".price-now",
        selector_version="v1",
        currency_hint="USD",
    )
    assert result.price == Decimal("12.00")
    assert result.tier == "selector"
    assert result.confidence == 0.80
    assert result.extractor_version == "v1"


def test_tier3_selector_falls_through_when_selector_does_not_match() -> None:
    # No ".missing-class" node on the page -- tier 3 must fail over to
    # tier 4's text scan rather than erroring.
    result = extract_price(_load("text_only.html"), selector=".missing-class", currency_hint="USD")
    assert result.tier == "price_parser"


def test_tier4_price_parser_candidate_text() -> None:
    result = extract_price(_load("text_only.html"), currency_hint="USD")
    assert result.price == Decimal("24.50")
    assert result.currency == "USD"
    assert result.tier == "price_parser"
    assert result.confidence == 0.60


def test_malformed_json_ld_falls_through_to_microdata_not_llm() -> None:
    result = extract_price(_load("malformed_jsonld_with_microdata.html"))
    assert result.price == Decimal("15.75")
    assert result.tier == "microdata"


def test_all_tiers_fail_raises_extraction_failed_with_every_attempt_recorded() -> None:
    with pytest.raises(ExtractionFailed) as exc_info:
        extract_price(_load("no_price_anywhere.html"))
    assert exc_info.value.attempts == ("json_ld", "microdata", "selector", "price_parser", "llm")


def test_all_tiers_fail_without_a_currency_hint_even_when_text_has_a_bare_symbol() -> None:
    # text_only.html DOES contain "$24.50" -- but without currency_hint the
    # bare "$" is ambiguous and tier 4 must not guess USD. Proves the
    # cascade never fabricates a currency any more than it fabricates a
    # price.
    with pytest.raises(ExtractionFailed):
        extract_price(_load("text_only.html"))


def test_cascade_stops_at_tier1_even_when_selector_would_also_match() -> None:
    # jsonld_clean.html's body also has a ".price-display" node the
    # selector could match -- tier 1 must win regardless of selector being
    # supplied.
    result = extract_price(_load("jsonld_clean.html"), selector=".price-display", currency_hint="USD")
    assert result.tier == "json_ld"
    assert result.price == Decimal("199.99")


def test_llm_stub_always_returns_none_and_never_raises() -> None:
    assert _extract_via_llm("<html>anything</html>") is None
    assert _extract_via_llm("") is None
