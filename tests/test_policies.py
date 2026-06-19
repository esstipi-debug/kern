"""Tests for inventory policies (s,Q) and (R,S) (Ch. 5-6)."""

import pytest

from src.eoq import compute_eoq
from src.policies import continuous_review_sq, periodic_review_rs

# Shared scenario: ~100 units/period, weekly data.
COMMON = dict(
    annual_demand=5200,
    mean_demand_per_period=100,
    demand_std_per_period=20,
    holding_cost_per_unit=2.0,
    fixed_order_cost=50.0,
    lead_time_periods=2,
    cycle_service_level=0.95,
)


def test_sq_policy_uses_eoq_order_quantity():
    """(s,Q): Q* comes from EOQ, s = mu_x + Ss."""
    result = continuous_review_sq(**COMMON)
    eoq = compute_eoq(COMMON["annual_demand"], COMMON["holding_cost_per_unit"], COMMON["fixed_order_cost"])
    assert result.policy == "(s, Q)"
    assert result.order_quantity == pytest.approx(eoq.order_quantity)
    assert result.order_up_to_level is None
    assert result.review_period is None
    # reorder point sits above mean risk-period demand by the safety stock
    assert result.reorder_point == pytest.approx(result.mean_demand_risk_period + result.safety_stock.safety_stock)
    assert result.reorder_point > result.mean_demand_risk_period


def test_rs_policy_sets_order_up_to_level():
    """(R,S): S = mu_x + Ss over tau = R + L."""
    result = periodic_review_rs(review_period=4, **COMMON)
    assert result.policy == "(R, S)"
    assert result.review_period == 4
    assert result.order_quantity is None
    assert result.reorder_point is None
    assert result.order_up_to_level == pytest.approx(result.mean_demand_risk_period + result.safety_stock.safety_stock)
    # tau = L + R = 6, so mean risk demand = 600
    assert result.mean_demand_risk_period == pytest.approx(600)


def test_higher_service_level_raises_reorder_point():
    low = continuous_review_sq(**{**COMMON, "cycle_service_level": 0.90})
    high = continuous_review_sq(**{**COMMON, "cycle_service_level": 0.99})
    assert high.reorder_point > low.reorder_point
