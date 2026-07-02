"""Tests for volume discount EOQ — Vandeput (2020), Section 2.5.3."""

import pytest

from src.eoq import PriceBreak, compute_eoq_volume_discount


def test_volume_discount_picks_cheaper_tier():
    breaks = [
        PriceBreak(0, 10.0),
        PriceBreak(200, 9.0),
        PriceBreak(500, 8.5),
    ]
    result = compute_eoq_volume_discount(
        annual_demand=1200,
        holding_cost_rate=0.25,
        fixed_order_cost=100,
        price_breaks=breaks,
    )
    # Hand-verified: the deepest tier's forced minimum (500 @ $8.50) beats both
    # tier 0's own EOQ (~310 @ $10) and tier 1's own EOQ (~327 @ $9) once the
    # annual purchase cost (D*c) is counted for all three.
    assert result.price_break_index == 2
    assert result.order_quantity == pytest.approx(500.0)
    assert result.optimal_total_cost == pytest.approx(10971.25, rel=1e-4)


def test_single_break_matches_eoq():
    breaks = [PriceBreak(0, 5.0)]
    vd = compute_eoq_volume_discount(1000, 0.2, 50, breaks)
    from src.eoq import compute_eoq

    plain = compute_eoq(1000, 0.2 * 5.0, 50)
    assert vd.order_quantity == pytest.approx(plain.order_quantity, rel=0.01)


def test_volume_discount_credits_the_purchase_cost_across_tiers():
    """Regression: the decision cost used to omit the annual purchase cost (D*c),
    so it could never credit a cheaper tier for the money saved on every unit
    bought - only for its holding/ordering cost, which discounts rarely improve.
    Hand-verified (D=1000, S=10, rate=0.2): true annual totals are $10,200 at the
    tier-0 EOQ (100 @ $10) vs $9,995 forcing the minimum to reach tier 1 (500 @
    $9.50) - the discount tier must win."""
    result = compute_eoq_volume_discount(
        annual_demand=1000,
        holding_cost_rate=0.2,
        fixed_order_cost=10,
        price_breaks=[PriceBreak(0, 10.0), PriceBreak(500, 9.5)],
    )
    assert result.price_break_index == 1
    assert result.order_quantity == pytest.approx(500.0)
    assert result.optimal_total_cost == pytest.approx(9995.0)
    assert result.unit_cost == pytest.approx(9.5)


def test_volume_discount_rejects_a_tier_not_worth_the_minimum_order():
    """A deep tier whose minimum order quantity is punitively large relative to
    its per-unit saving must lose to the cheaper-to-run base tier - the fix must
    not blindly favor a lower unit price regardless of the quantity it forces."""
    result = compute_eoq_volume_discount(
        annual_demand=1000,
        holding_cost_rate=0.2,
        fixed_order_cost=10,
        price_breaks=[PriceBreak(0, 10.0), PriceBreak(5000, 9.99)],
    )
    assert result.price_break_index == 0
    assert result.unit_cost == pytest.approx(10.0)
    assert result.order_quantity == pytest.approx(100.0)  # tier 0's own unconstrained EOQ
