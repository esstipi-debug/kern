"""Tests for src/pricing_intel/match/gtin.py (Linchpin 3.0 PR-14, plan S6.5
step 1).

Hand-verified EAN-13 check digit (see gtin.py's own module docstring for the
worked-by-hand computation): 4006381333931's check digit is 1, computed as
(10 - 89 % 10) % 10 = 1 from the weighted-3/1 sum of the first 12 digits.
"""

from __future__ import annotations

from datetime import datetime, timezone

from src.pricing_intel.match.gtin import (
    GTIN_CONFIRMED_BY,
    GTIN_CONFIRMED_SCORE,
    match_by_gtin,
    normalize_gtin,
)

VALID_EAN13 = "4006381333931"
VALID_EAN13_HYPHENATED = "400-638-133-3931"
BAD_CHECK_DIGIT_EAN13 = "4006381333930"  # last digit flipped 1 -> 0
VALID_UPC_A = "036000291452"  # 12-digit UPC-A, real check-digit-valid example
DIFFERENT_VALID_EAN13 = "5000112637922"  # a different, independently valid GTIN

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)


# -- normalize_gtin -----------------------------------------------------------


def test_normalize_gtin_accepts_a_valid_ean13() -> None:
    assert normalize_gtin(VALID_EAN13) == VALID_EAN13


def test_normalize_gtin_strips_hyphen_separators() -> None:
    assert normalize_gtin(VALID_EAN13_HYPHENATED) == VALID_EAN13


def test_normalize_gtin_accepts_a_valid_upc_a() -> None:
    assert normalize_gtin(VALID_UPC_A) == VALID_UPC_A


def test_normalize_gtin_rejects_bad_check_digit() -> None:
    assert normalize_gtin(BAD_CHECK_DIGIT_EAN13) is None


def test_normalize_gtin_rejects_non_digit_garbage() -> None:
    assert normalize_gtin("not-a-barcode") is None


def test_normalize_gtin_handles_none_and_empty() -> None:
    assert normalize_gtin("") is None
    assert normalize_gtin("   ") is None


# -- match_by_gtin --------------------------------------------------------------


def test_match_by_gtin_confirms_on_identical_valid_codes() -> None:
    candidate = match_by_gtin(
        "SKU-1", VALID_EAN13, "https://example-retailer.test/p/123", "example-retailer.test", VALID_EAN13, now=NOW
    )
    assert candidate is not None
    assert candidate.status == "confirmed"
    assert candidate.score == GTIN_CONFIRMED_SCORE
    assert candidate.score == 0.99
    assert candidate.method == "gtin"
    assert candidate.confirmed_by == GTIN_CONFIRMED_BY
    assert candidate.confirmed_by == "auto"
    assert candidate.confirmed_at == NOW
    assert candidate.reason == f"gtin_exact_match:{VALID_EAN13}"


def test_match_by_gtin_confirms_across_separator_formatting() -> None:
    # Same physical code, one side hyphenated -- normalization must equate them.
    candidate = match_by_gtin(
        "SKU-1", VALID_EAN13, "https://example-retailer.test/p/123", "example-retailer.test",
        VALID_EAN13_HYPHENATED, now=NOW,
    )
    assert candidate is not None
    assert candidate.status == "confirmed"


def test_match_by_gtin_returns_none_for_bad_check_digit() -> None:
    candidate = match_by_gtin(
        "SKU-1", VALID_EAN13, "https://example-retailer.test/p/123", "example-retailer.test",
        BAD_CHECK_DIGIT_EAN13, now=NOW,
    )
    assert candidate is None


def test_match_by_gtin_returns_none_when_our_gtin_missing() -> None:
    candidate = match_by_gtin(
        "SKU-1", None, "https://example-retailer.test/p/123", "example-retailer.test", VALID_EAN13, now=NOW
    )
    assert candidate is None


def test_match_by_gtin_returns_none_when_competitor_gtin_missing() -> None:
    candidate = match_by_gtin(
        "SKU-1", VALID_EAN13, "https://example-retailer.test/p/123", "example-retailer.test", None, now=NOW
    )
    assert candidate is None


def test_match_by_gtin_returns_none_for_two_different_valid_codes() -> None:
    # Both check-digit-valid, but genuinely different products.
    candidate = match_by_gtin(
        "SKU-1", VALID_EAN13, "https://example-retailer.test/p/123", "example-retailer.test",
        DIFFERENT_VALID_EAN13, now=NOW,
    )
    assert candidate is None


def test_match_by_gtin_defaults_now_to_current_utc_time() -> None:
    before = datetime.now(timezone.utc)
    candidate = match_by_gtin(
        "SKU-1", VALID_EAN13, "https://example-retailer.test/p/123", "example-retailer.test", VALID_EAN13
    )
    after = datetime.now(timezone.utc)
    assert candidate is not None
    assert before <= candidate.confirmed_at <= after
