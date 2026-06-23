"""Tests for affinity slotting (plan §2.5).

Complements COI class-based slotting (space.py) with the co-occurrence dimension:
SKUs frequently ordered together should sit near each other. Measures pairwise lift
(association strength vs chance) and groups strongly-linked SKUs for co-location.
Pure python - no deps.
"""

from src.slotting_affinity import AffinityPair, affinity_pairs, co_location_groups, partners

# milk/bread/butter/eggs cluster together; beer/chips/salsa form a separate cluster.
_ORDERS = [
    ["milk", "bread", "butter"],
    ["milk", "bread"],
    ["milk", "bread", "eggs"],
    ["beer", "chips"],
    ["beer", "chips", "salsa"],
]


def _pair(pairs, a, b):
    a, b = sorted([a, b])
    return next(p for p in pairs if p.sku_a == a and p.sku_b == b)


def test_lift_co_count_and_support_are_computed():
    pairs = affinity_pairs(_ORDERS)

    mb = _pair(pairs, "milk", "bread")
    assert isinstance(mb, AffinityPair)
    assert mb.co_count == 3
    assert mb.support == 3 / 5
    # lift = co*n / (count_a * count_b) = 3*5 / (3*3) = 1.667
    assert round(mb.lift, 3) == 1.667


def test_pairs_are_canonical_and_sorted_by_lift_desc():
    pairs = affinity_pairs(_ORDERS)

    assert all(p.sku_a < p.sku_b for p in pairs)         # canonical ordering
    lifts = [p.lift for p in pairs]
    assert lifts == sorted(lifts, reverse=True)
    # beer-chips has the highest lift (2.5) and the most co-occurrences among the 2.5s
    assert (pairs[0].sku_a, pairs[0].sku_b) == ("beer", "chips")


def test_min_co_count_filters_weak_pairs():
    pairs = affinity_pairs(_ORDERS, min_co_count=2)

    keys = {(p.sku_a, p.sku_b) for p in pairs}
    assert keys == {("bread", "milk"), ("beer", "chips")}


def test_partners_returns_only_pairs_for_the_sku_best_first():
    ps = partners(_ORDERS, "milk")

    assert all("milk" in (p.sku_a, p.sku_b) for p in ps)
    assert (ps[0].sku_a, ps[0].sku_b) == ("bread", "milk")   # strongest milk partner


def test_co_location_groups_finds_the_two_clusters():
    groups = co_location_groups(_ORDERS, min_lift=1.5)

    assert len(groups) == 2
    flat = {frozenset(g) for g in groups}
    assert {"milk", "bread", "butter", "eggs"} in flat
    assert {"beer", "chips", "salsa"} in flat
    assert all(g == sorted(g) for g in groups)              # each group sorted


def test_high_threshold_yields_no_groups():
    assert co_location_groups(_ORDERS, min_lift=3.0) == []   # max lift is 2.5


def test_orders_are_deduped_within_an_order():
    # a repeated SKU in one order must not inflate counts.
    pairs = affinity_pairs([["a", "a", "b"], ["a", "b"]])
    ab = _pair(pairs, "a", "b")
    assert ab.co_count == 2


def test_empty_and_singleton_orders_have_no_pairs():
    assert affinity_pairs([]) == []
    assert affinity_pairs([["solo"], ["alone"]]) == []
    assert co_location_groups([]) == []
