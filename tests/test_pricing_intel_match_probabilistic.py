"""Tests for src/pricing_intel/match/probabilistic.py (Linchpin 3.0 PR-14,
plan S6.5 step 3).

Reference numbers below are copied verbatim from actually running
``score_pair`` against this repo's pinned ``rapidfuzz`` -- see
probabilistic.py's own module docstring for the three fully worked examples
(reworded exact match, attribute-conflict ceiling, genuinely ambiguous) this
file exercises as tests.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.pricing_intel.match.fuzzy import ProductAttributes
from src.pricing_intel.match.probabilistic import (
    ATTRIBUTE_CONFLICT_CEILING,
    CONFIRM_THRESHOLD,
    SUSPECT_THRESHOLD,
    classify_score,
    score_pair,
    score_to_match_candidate,
)

NOW = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)


def _attrs(product_id: str, title: str, brand: str, **attributes: str) -> ProductAttributes:
    return ProductAttributes(product_id=product_id, title=title, brand=brand, attributes=attributes)


# -- worked example 1: reworded title, matching attribute -> confirmed --------


def test_score_pair_reworded_title_with_matching_attribute_is_confirmed() -> None:
    our = _attrs("our-1", "Coca-Cola Bottle 2L", "Coca-Cola", pack_size="2l")
    comp = _attrs("comp-1", "Coca-Cola 2L Bottle", "Coca-Cola", pack_size="2l")
    result = score_pair(our, comp)

    assert result.title_similarity == 1.0
    assert result.brand_similarity == 1.0
    assert result.attribute_similarity == 1.0
    assert result.attribute_conflict is False
    assert result.score == 1.0
    assert classify_score(result.score) == "confirmed"


def test_score_pair_near_reword_with_matching_model_is_confirmed() -> None:
    our = _attrs(
        "our-1", "Sony WH-1000XM5 Wireless Noise Cancelling Headphones", "Sony", model="xm5"
    )
    comp = _attrs(
        "comp-1", "WH-1000XM5 Wireless Noise Cancelling Headphones - Sony", "Sony", model="xm5"
    )
    result = score_pair(our, comp)

    assert result.title_similarity == pytest.approx(0.9811320754716981)
    assert result.score == pytest.approx(0.989622641509434)
    assert classify_score(result.score) == "confirmed"


# -- worked example 2: decisive attribute conflict -> hard-capped, rejected --


def test_score_pair_model_conflict_caps_score_regardless_of_title_similarity() -> None:
    # Titles are ~98% similar (single model-number digit differs) -- without
    # the attribute-conflict rule this would score high enough to confirm.
    our = _attrs(
        "our-1", "Sony WH-1000XM5 Wireless Noise Cancelling Headphones", "Sony", model="xm5"
    )
    comp = _attrs(
        "comp-1", "Sony WH-1000XM4 Wireless Noise Cancelling Headphones", "Sony", model="xm4"
    )
    result = score_pair(our, comp)

    assert result.title_similarity == pytest.approx(0.9807692307692306, abs=1e-9)
    assert result.attribute_conflict is True
    assert result.conflicting_attributes == ("model",)
    assert result.score == ATTRIBUTE_CONFLICT_CEILING
    assert result.score == 0.45
    assert classify_score(result.score) == "rejected"


def test_score_pair_pack_size_conflict_is_rejected_despite_similar_title() -> None:
    our = _attrs("our-1", "Coca-Cola Bottle 2L", "Coca-Cola", pack_size="2l")
    comp = _attrs("comp-1", "Coca-Cola Bottle 500ml", "Coca-Cola", pack_size="500ml")
    result = score_pair(our, comp)

    assert result.attribute_conflict is True
    assert result.score == ATTRIBUTE_CONFLICT_CEILING
    assert classify_score(result.score) == "rejected"


def test_score_pair_ignores_attribute_keys_present_on_only_one_side() -> None:
    # our has a pack_size, competitor doesn't record one at all -- no shared
    # decisive key means no evidence either way, not a guessed conflict.
    our = _attrs("our-1", "Widget Foo", "Acme", pack_size="500ml")
    comp = _attrs("comp-1", "Widget Foo", "Acme")
    result = score_pair(our, comp)

    assert result.attribute_conflict is False
    assert result.attribute_similarity == 1.0


# -- worked example 3: genuinely ambiguous -> suspect, never auto-confirmed --


def test_score_pair_ambiguous_tier_variant_lands_in_suspect_not_confirmed() -> None:
    # Same brand, near-identical wording, but "S23" vs "S23 Ultra" is a real
    # product-tier difference, not a rewording -- and neither side records a
    # decisive attribute that could settle it algorithmically.
    our = _attrs("our-1", "Samsung Galaxy S23 Smartphone", "Samsung")
    comp = _attrs("comp-1", "Samsung Galaxy S23 Ultra Smartphone", "Samsung")
    result = score_pair(our, comp)

    assert result.title_similarity == pytest.approx(0.90625)
    assert result.score == pytest.approx(0.9484375)
    assert SUSPECT_THRESHOLD <= result.score < CONFIRM_THRESHOLD
    status = classify_score(result.score)
    assert status == "suspect"
    assert status != "confirmed"


def test_score_pair_clearly_unrelated_products_is_rejected() -> None:
    our = _attrs("our-1", "Dell XPS 13 Laptop", "Dell")
    comp = _attrs("comp-1", "Razer DeathAdder V3 Mouse", "Razer")
    result = score_pair(our, comp)
    assert classify_score(result.score) == "rejected"


# -- classify_score boundaries --------------------------------------------------


def test_classify_score_boundaries_are_inclusive_on_the_lower_edge() -> None:
    assert classify_score(CONFIRM_THRESHOLD) == "confirmed"
    assert classify_score(CONFIRM_THRESHOLD - 1e-9) == "suspect"
    assert classify_score(SUSPECT_THRESHOLD) == "suspect"
    assert classify_score(SUSPECT_THRESHOLD - 1e-9) == "rejected"


def test_classify_score_rejects_out_of_range_input() -> None:
    with pytest.raises(ValueError):
        classify_score(1.5)
    with pytest.raises(ValueError):
        classify_score(-0.1)


# -- score_to_match_candidate ---------------------------------------------------


def test_score_to_match_candidate_confirmed_sets_auto_confirmed_by() -> None:
    our = _attrs("our-1", "Coca-Cola Bottle 2L", "Coca-Cola", pack_size="2l")
    comp = _attrs("comp-1", "Coca-Cola 2L Bottle", "Coca-Cola", pack_size="2l")
    result = score_pair(our, comp)
    candidate = score_to_match_candidate(
        result, site="example-retailer.test", competitor_sku_ref="https://example-retailer.test/p/1", now=NOW
    )

    assert candidate.status == "confirmed"
    assert candidate.method == "probabilistic"
    assert candidate.confirmed_by == "auto"
    assert candidate.confirmed_at == NOW
    assert candidate.our_product_id == "our-1"


def test_score_to_match_candidate_suspect_leaves_confirmed_by_none() -> None:
    our = _attrs("our-1", "Samsung Galaxy S23 Smartphone", "Samsung")
    comp = _attrs("comp-1", "Samsung Galaxy S23 Ultra Smartphone", "Samsung")
    result = score_pair(our, comp)
    candidate = score_to_match_candidate(
        result, site="example-retailer.test", competitor_sku_ref="https://example-retailer.test/p/2"
    )

    assert candidate.status == "suspect"
    assert candidate.confirmed_by is None
    assert candidate.confirmed_at is None


def test_score_to_match_candidate_rejected_reports_conflicting_attribute() -> None:
    our = _attrs(
        "our-1", "Sony WH-1000XM5 Wireless Noise Cancelling Headphones", "Sony", model="xm5"
    )
    comp = _attrs(
        "comp-1", "Sony WH-1000XM4 Wireless Noise Cancelling Headphones", "Sony", model="xm4"
    )
    result = score_pair(our, comp)
    candidate = score_to_match_candidate(
        result, site="example-retailer.test", competitor_sku_ref="https://example-retailer.test/p/3"
    )

    assert candidate.status == "rejected"
    assert "model" in candidate.reason
    assert candidate.confirmed_by is None
