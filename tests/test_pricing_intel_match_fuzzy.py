"""Tests for src/pricing_intel/match/fuzzy.py (Linchpin 3.0 PR-14, plan S6.5
step 2 -- cheap RapidFuzz blocking on title+brand).
"""

from __future__ import annotations

import pytest

from src.pricing_intel.match.fuzzy import (
    BLOCKING_THRESHOLD,
    ProductAttributes,
    block_candidates,
    blocking_score,
)


def _attrs(product_id: str, title: str, brand: str, **attributes: str) -> ProductAttributes:
    return ProductAttributes(product_id=product_id, title=title, brand=brand, attributes=attributes)


# -- ProductAttributes ---------------------------------------------------------


def test_product_attributes_rejects_empty_fields() -> None:
    with pytest.raises(ValueError):
        ProductAttributes(product_id="", title="x", brand="y")
    with pytest.raises(ValueError):
        ProductAttributes(product_id="p", title="   ", brand="y")
    with pytest.raises(ValueError):
        ProductAttributes(product_id="p", title="x", brand="")


def test_product_attributes_defaults_to_empty_attributes_dict() -> None:
    p = ProductAttributes(product_id="p", title="x", brand="y")
    assert p.attributes == {}


# -- blocking_score -------------------------------------------------------------


def test_blocking_score_is_100_for_identical_title_and_brand() -> None:
    our = _attrs("our-1", "Apple iPhone 15 Pro", "Apple")
    comp = _attrs("comp-1", "Apple iPhone 15 Pro", "Apple")
    result = blocking_score(our, comp)
    assert result.title_score == 100.0
    assert result.brand_score == 100.0
    assert result.blocking_score == 100.0


def test_blocking_score_is_100_despite_whitespace_and_case_differences() -> None:
    our = _attrs("our-1", "  Apple   iPhone 15 Pro  ", "Apple")
    comp = _attrs("comp-1", "apple iphone 15 pro", "APPLE")
    result = blocking_score(our, comp)
    assert result.title_score == 100.0
    assert result.brand_score == 100.0
    assert result.blocking_score == 100.0


def test_blocking_score_is_order_invariant_within_title() -> None:
    # token_sort_ratio sorts tokens before comparing -- moving "Apple" from
    # the front of the title to the end must not change the title score.
    our = _attrs("our-1", "Apple iPhone 15 Pro 128GB", "Apple")
    comp = _attrs("comp-1", "iPhone 15 Pro 128GB Apple", "Apple")
    result = blocking_score(our, comp)
    assert result.title_score == 100.0


def test_blocking_score_weights_title_higher_than_brand() -> None:
    # Title matches exactly, brand is completely different (no shared
    # characters at all under token_sort_ratio) -> title_score=100.0,
    # brand_score=0.0 -> blocking_score = 0.7*100 + 0.3*0 = 70.0 exactly.
    our = _attrs("our-1", "Wireless Mouse Model X", "Zorqath")
    comp = _attrs("comp-1", "Wireless Mouse Model X", "Blimwuv")
    result = blocking_score(our, comp)
    assert result.title_score == 100.0
    assert result.brand_score == 0.0
    assert result.blocking_score == pytest.approx(70.0)


def test_blocking_score_is_low_for_two_unrelated_products() -> None:
    our = _attrs("our-1", "Dell XPS 13 Laptop", "Dell")
    comp = _attrs("comp-1", "Coca-Cola Bottle 2L", "Coca-Cola")
    result = blocking_score(our, comp)
    assert result.blocking_score < BLOCKING_THRESHOLD


# -- block_candidates -----------------------------------------------------------


def test_block_candidates_filters_by_threshold_and_sorts_descending() -> None:
    our_catalog = [_attrs("our-1", "Sony WH-1000XM5 Headphones", "Sony")]
    competitor_catalog = [
        _attrs("comp-1", "Sony WH-1000XM5 Wireless Headphones", "Sony"),  # very close
        _attrs("comp-2", "Coca-Cola Bottle 2L", "Coca-Cola"),  # unrelated
        _attrs("comp-3", "Sony WH-1000XM5 Headphones", "Sony"),  # identical
    ]
    result = block_candidates(our_catalog, competitor_catalog, threshold=BLOCKING_THRESHOLD)

    ids = [c.competitor.product_id for c in result]
    assert "comp-2" not in ids  # below threshold, filtered out
    assert ids[0] == "comp-3"  # identical pair ranks first
    assert all(result[i].blocking_score >= result[i + 1].blocking_score for i in range(len(result) - 1))


def test_block_candidates_empty_catalog_returns_empty_list() -> None:
    assert block_candidates([], [_attrs("comp-1", "x", "y")]) == []
    assert block_candidates([_attrs("our-1", "x", "y")], []) == []


def test_block_candidates_rejects_out_of_range_threshold() -> None:
    with pytest.raises(ValueError):
        block_candidates([], [], threshold=101.0)
    with pytest.raises(ValueError):
        block_candidates([], [], threshold=-1.0)
