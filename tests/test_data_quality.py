"""Tests for the deterministic data-quality core (capability M11).

GTIN/UPC/EAN check-digit validation, SKU normalization, and canonical column
mapping. The fuzzy/probabilistic dedup (rapidfuzz/Splink) is an optional extra on
top; this core is dependency-free.
"""

from src.data_quality import gtin_check_digit, is_valid_gtin, map_columns, normalize_sku


def test_gtin_check_digit_for_upc_a():
    # UPC-A 036000291452 -> payload 03600029145, check digit 2
    assert gtin_check_digit("03600029145") == 2


def test_valid_upc_a():
    assert is_valid_gtin("036000291452") is True


def test_invalid_when_check_digit_wrong():
    assert is_valid_gtin("036000291453") is False


def test_valid_ean_13():
    assert is_valid_gtin("4006381333931") is True


def test_valid_ean_8():
    assert is_valid_gtin("73513537") is True


def test_rejects_non_numeric_and_bad_length():
    assert is_valid_gtin("abc") is False
    assert is_valid_gtin("12345") is False  # not a GTIN length


def test_normalize_sku_collapses_and_uppercases():
    assert normalize_sku("  sku-a   01 ") == "SKU-A 01"


def test_map_columns_to_canonical_fields():
    headers = ["Date", "Item #", "Qty", "Cost", "Lead Time"]
    mapping = map_columns(headers)
    assert mapping["date"] == "Date"
    assert mapping["product_id"] == "Item #"
    assert mapping["quantity"] == "Qty"
    assert mapping["unit_cost"] == "Cost"
    assert mapping["lead_time_days"] == "Lead Time"


def test_map_columns_ignores_unknown_headers():
    mapping = map_columns(["foo", "bar"])
    assert mapping == {}
