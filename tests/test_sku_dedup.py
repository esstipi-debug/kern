"""Tests for SKU deduplication (capability M11, dep-gated with stdlib fallback).

Exact identity by valid GTIN, plus fuzzy name matching (rapidfuzz when installed,
difflib fallback otherwise).
"""

from src.sku_dedup import DuplicateCluster, find_duplicates


def test_exact_duplicate_by_shared_gtin():
    items = [
        {"product_id": "A", "name": "Blue Cap", "gtin": "036000291452"},
        {"product_id": "C", "name": "Green Hat", "gtin": "036000291452"},  # same GTIN
        {"product_id": "D", "name": "Red Sock", "gtin": ""},
    ]
    clusters = find_duplicates(items)
    gtin_clusters = [c for c in clusters if c.reason == "gtin"]
    assert len(gtin_clusters) == 1
    assert set(gtin_clusters[0].product_ids) == {"A", "C"}
    assert isinstance(gtin_clusters[0], DuplicateCluster)


def test_fuzzy_duplicate_by_near_identical_name():
    items = [
        {"product_id": "X", "name": "Blue Cap", "gtin": ""},
        {"product_id": "Y", "name": "  blue   cap ", "gtin": ""},  # same after normalization
        {"product_id": "Z", "name": "Green Hat", "gtin": ""},
    ]
    clusters = find_duplicates(items, name_threshold=85)
    name_clusters = [c for c in clusters if c.reason == "name"]
    assert len(name_clusters) == 1
    assert set(name_clusters[0].product_ids) == {"X", "Y"}


def test_distinct_items_have_no_duplicates():
    items = [
        {"product_id": "A", "name": "Blue Cap", "gtin": "036000291452"},
        {"product_id": "B", "name": "Totally Different Thing", "gtin": "4006381333931"},
    ]
    assert find_duplicates(items) == []


def test_empty_input():
    assert find_duplicates([]) == []
