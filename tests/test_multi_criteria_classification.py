"""Tests for multi-criteria ABC classification (plan §2.2, the MCDM upgrade).

Ranks SKUs by several weighted criteria (usage value, criticality, lead time, margin)
into A/B/C bands - reusing the TOPSIS engine from src/mcdm.py. Distinct from the
single-criterion ABC-XYZ in classification.py; this is the Flores/Ng multi-criteria
variant. Pure (numpy/scipy), no optional deps.
"""

import pytest

from src.mcdm import Criterion, bwm_weights
from src.multi_criteria_classification import (
    MultiCriteriaClass,
    classify_multicriteria,
)

_CRIT = [
    Criterion("usage_value", benefit=True),
    Criterion("criticality", benefit=True),
    Criterion("lead_time", benefit=False),   # shorter is better
]
_WEIGHTS = {"usage_value": 0.5, "criticality": 0.3, "lead_time": 0.2}


def _items(n):
    # SKU i is strictly more important as i grows (higher value/criticality, lower lead time).
    return {
        f"S{i}": {"usage_value": float(i), "criticality": float(i), "lead_time": float(n - i + 1)}
        for i in range(1, n + 1)
    }


def test_returns_a_class_per_sku_with_unique_ranks():
    out = classify_multicriteria(_items(5), _CRIT, _WEIGHTS)

    assert len(out) == 5
    assert all(isinstance(c, MultiCriteriaClass) for c in out)
    assert sorted(c.rank for c in out) == [1, 2, 3, 4, 5]


def test_most_important_sku_is_rank_one_class_a():
    out = classify_multicriteria(_items(5), _CRIT, _WEIGHTS)
    top = min(out, key=lambda c: c.rank)

    assert top.sku == "S5"            # highest value/criticality, lowest lead time
    assert top.abc_class == "A"


def test_bands_split_by_cumulative_share():
    out = classify_multicriteria(_items(10), _CRIT, _WEIGHTS, a_share=0.2, b_share=0.3)

    counts = {band: sum(1 for c in out if c.abc_class == band) for band in "ABC"}
    assert counts == {"A": 2, "B": 3, "C": 5}


def test_cost_criterion_makes_lower_lead_time_rank_better():
    # two SKUs identical except lead time; the shorter lead time must rank higher.
    items = {"fast": {"usage_value": 5.0, "criticality": 5.0, "lead_time": 1.0},
             "slow": {"usage_value": 5.0, "criticality": 5.0, "lead_time": 9.0}}

    out = {c.sku: c for c in classify_multicriteria(items, _CRIT, _WEIGHTS)}

    assert out["fast"].rank < out["slow"].rank


def test_weights_can_come_from_bwm():
    weights = bwm_weights(
        "usage_value", "lead_time",
        {"usage_value": 1.0, "criticality": 2.0, "lead_time": 4.0},
        {"usage_value": 4.0, "criticality": 2.0, "lead_time": 1.0},
        criteria=["usage_value", "criticality", "lead_time"],
    ).weights

    out = classify_multicriteria(_items(6), _CRIT, weights)

    assert min(out, key=lambda c: c.rank).sku == "S6"


def test_empty_items_return_empty():
    assert classify_multicriteria({}, _CRIT, _WEIGHTS) == []


def test_invalid_shares_raise():
    with pytest.raises(ValueError):
        classify_multicriteria(_items(4), _CRIT, _WEIGHTS, a_share=0.7, b_share=0.5)
