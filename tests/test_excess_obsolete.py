"""Tests for the offline excess & obsolete (E&O) / dead-stock engine."""

from src.excess_obsolete import (
    DEAD,
    EXCESS,
    HEALTHY,
    SkuStock,
    classify_excess_obsolete,
    classify_sku,
)


def test_dead_when_no_recent_sale():
    eo = classify_sku(SkuStock("d", on_hand=100, daily_demand=5, unit_cost=2, days_since_last_sale=200))
    assert eo.classification == DEAD
    assert eo.excess_units == 100 and eo.excess_value == 200


def test_dead_when_no_demand():
    eo = classify_sku(SkuStock("z", on_hand=50, daily_demand=0, unit_cost=3, days_since_last_sale=10))
    assert eo.classification == DEAD
    assert eo.days_of_cover == float("inf")
    assert eo.excess_value == 150


def test_excess_beyond_cover_target():
    eo = classify_sku(SkuStock("x", on_hand=1000, daily_demand=5, unit_cost=2, days_since_last_sale=5))
    assert eo.classification == EXCESS
    assert eo.days_of_cover == 200.0
    assert eo.excess_units == 550.0          # 1000 - 90*5
    assert eo.excess_value == 1100.0


def test_healthy_within_cover():
    eo = classify_sku(SkuStock("h", on_hand=100, daily_demand=5, unit_cost=2, days_since_last_sale=5))
    assert eo.classification == HEALTHY
    assert eo.excess_units == 0.0


def test_classify_ranks_by_at_risk_value():
    stocks = [
        SkuStock("dead", 100, 5, 2, 200),
        SkuStock("excess", 1000, 5, 2, 5),
        SkuStock("healthy", 100, 5, 2, 5),
    ]
    ranked = classify_excess_obsolete(stocks)
    assert [e.product_id for e in ranked] == ["excess", "dead", "healthy"]   # by excess_value desc
    assert ranked[-1].classification == HEALTHY
