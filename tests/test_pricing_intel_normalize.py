"""Tests for src/pricing_intel/normalize.py (Linchpin 3.0 PR-11).

Guarantees under test:
- the five locale reference strings from plan section 6.10 normalize to the
  exact hand-verified Decimal + ISO currency (USD/EUR/MXN/BRL/CLP);
- an explicit currency both resolves the ISO code AND disambiguates
  price-parser's own decimal-separator heuristics;
- a genuinely ambiguous bare-symbol currency ("$" with no hint) raises
  PriceNormalizationError rather than defaulting to USD -- "un precio
  dudoso es peor que ningun precio";
- an unparseable amount raises, never returns a fabricated Decimal;
- detect_promo's list_price/price divergence rule;
- extract_pack_size's EN/ES/PT phrase coverage and the "no match -> None"
  contract (never guesses 1);
- unit_price's arithmetic and its positive-pack_size guard;
- parse_shipping_note's free-shipping short-circuit vs. funneling through
  the same normalizer, and its "mentions shipping but unparseable" guard.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from src.pricing_intel.normalize import (
    PriceNormalizationError,
    detect_promo,
    extract_pack_size,
    normalize_price_string,
    parse_shipping_note,
    unit_price,
)

# -- plan section 6.10's required locale set --------------------------------


def test_normalizes_usd_thousands_comma_decimal_dot() -> None:
    result = normalize_price_string("$1,234.56", currency="USD")
    assert result.amount == Decimal("1234.56")
    assert result.currency == "USD"


def test_normalizes_eur_thousands_dot_decimal_comma_with_symbol() -> None:
    result = normalize_price_string("1.234,56 €")
    assert result.amount == Decimal("1234.56")
    assert result.currency == "EUR"


def test_normalizes_mxn_with_explicit_currency_no_symbol_in_text() -> None:
    # Bare digits, no symbol at all -- only resolvable with an explicit ISO
    # hint, which is also what disambiguates the European-style separators.
    result = normalize_price_string("1.234,56", currency="MXN")
    assert result.amount == Decimal("1234.56")
    assert result.currency == "MXN"


def test_normalizes_brl_with_reais_symbol() -> None:
    result = normalize_price_string("R$ 1.234,56")
    assert result.amount == Decimal("1234.56")
    assert result.currency == "BRL"


def test_normalizes_clp_no_decimals_thousands_dot() -> None:
    result = normalize_price_string("12.345", currency="CLP")
    assert result.amount == Decimal("12345")
    assert result.currency == "CLP"


# -- currency resolution ------------------------------------------------


def test_bare_dollar_symbol_without_hint_is_rejected_as_ambiguous() -> None:
    # "$" alone is USD/MXN/ARS/CLP/... -- must not silently default to USD.
    with pytest.raises(PriceNormalizationError, match="ambiguous"):
        normalize_price_string("$19.99")


def test_bare_dollar_symbol_with_explicit_currency_resolves() -> None:
    result = normalize_price_string("$19.99", currency="USD")
    assert result.amount == Decimal("19.99")
    assert result.currency == "USD"


def test_iso_code_embedded_in_text_resolves_without_a_hint() -> None:
    result = normalize_price_string("USD 42.00")
    assert result.amount == Decimal("42.00")
    assert result.currency == "USD"


def test_explicit_currency_must_be_a_3_letter_code() -> None:
    with pytest.raises(PriceNormalizationError, match="ISO 4217"):
        normalize_price_string("19.99", currency="US")


def test_unparseable_text_raises_rather_than_fabricating_a_price() -> None:
    with pytest.raises(PriceNormalizationError, match="could not parse"):
        normalize_price_string("call for price", currency="USD")


def test_empty_or_blank_raw_text_raises() -> None:
    with pytest.raises(PriceNormalizationError):
        normalize_price_string("   ", currency="USD")


# -- promo detection ----------------------------------------------------


def test_detect_promo_true_when_list_price_exceeds_price() -> None:
    assert detect_promo(Decimal("89.00"), Decimal("120.00")) is True


def test_detect_promo_false_when_list_price_missing() -> None:
    assert detect_promo(Decimal("89.00"), None) is False


def test_detect_promo_false_when_list_price_equals_price() -> None:
    assert detect_promo(Decimal("89.00"), Decimal("89.00")) is False


def test_detect_promo_false_when_list_price_below_price() -> None:
    # A "list_price" lower than the actual price is not a promo signal --
    # more likely bad data than a real discount.
    assert detect_promo(Decimal("89.00"), Decimal("50.00")) is False


# -- pack size --------------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Acme Screws, Pack of 6", 6),
        ("Acme Screws 6-pack", 6),
        ("Acme Screws 6 Pack", 6),
        ("Tornillos, paquete de 12", 12),
        ("Parafusos, pacote de 12", 12),
        ("Acme Screws, caja de 24", 24),
        ("Acme Screws x6", 6),
        ("Acme Screws 6x", 6),
    ],
)
def test_extract_pack_size_recognizes_en_es_pt_phrases(text: str, expected: int) -> None:
    assert extract_pack_size(text) == expected


def test_extract_pack_size_returns_none_when_no_pattern_matches() -> None:
    # Never guesses 1 -- "unknown" stays unknown.
    assert extract_pack_size("Acme Widget 3000") is None


def test_extract_pack_size_returns_none_for_empty_text() -> None:
    assert extract_pack_size("") is None


# -- unit price ---------------------------------------------------------


def test_unit_price_divides_by_pack_size() -> None:
    assert unit_price(Decimal("12.00"), 6) == Decimal("2.00")


def test_unit_price_rejects_non_positive_pack_size() -> None:
    with pytest.raises(ValueError, match="positive integer"):
        unit_price(Decimal("12.00"), 0)


def test_unit_price_rejects_negative_pack_size() -> None:
    with pytest.raises(ValueError, match="positive integer"):
        unit_price(Decimal("12.00"), -3)


# -- shipping notes -------------------------------------------------------


def test_parse_shipping_note_none_for_missing_text() -> None:
    assert parse_shipping_note(None) is None


def test_parse_shipping_note_none_for_blank_text() -> None:
    assert parse_shipping_note("   ") is None


@pytest.mark.parametrize(
    "text",
    [
        "Free shipping",
        "FREE SHIPPING on this item",
        "Envio gratis",
        "Envío gratis a todo el pais",
        "Frete grátis",
        "Shipping included",
    ],
)
def test_parse_shipping_note_recognizes_free_shipping_phrases(text: str) -> None:
    assert parse_shipping_note(text) == Decimal("0")


def test_parse_shipping_note_parses_an_explicit_amount() -> None:
    assert parse_shipping_note("+ $5.99 shipping", currency="USD") == Decimal("5.99")


def test_parse_shipping_note_raises_when_unparseable_and_not_free() -> None:
    with pytest.raises(PriceNormalizationError):
        parse_shipping_note("Shipping calculated at checkout", currency="USD")
