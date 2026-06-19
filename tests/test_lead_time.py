"""Tests for stochastic lead time safety stock — Chapter 6."""

import pytest

from src.policies import periodic_review_rs
from src.risk_period import demand_over_risk_period


def test_bicycle_shop_example_section_6_3():
    """R=4, L=13, sigma_L=3, mu=350, sigma=100, alpha=95% -> Ss~1855."""
    risk = demand_over_risk_period(350, 100, 13, 3, 4)
    z = 1.645
    ss = z * risk.demand_std
    assert ss == pytest.approx(1855, abs=10)


def test_stochastic_lt_increases_safety_stock():
    base = demand_over_risk_period(100, 25, 4, 0, 1)
    stoch = demand_over_risk_period(100, 25, 4, 2, 1)
    assert stoch.demand_std > base.demand_std


def test_policy_includes_risk_period_stats():
    rs = periodic_review_rs(
        annual_demand=5200,
        mean_demand_per_period=100,
        demand_std_per_period=25,
        holding_cost_per_unit=1.75,
        fixed_order_cost=50,
        lead_time_periods=4,
        review_period=1,
        cycle_service_level=0.95,
        lead_time_std=2,
    )
    assert rs.demand_std_risk_period > 0
    assert rs.order_up_to_level > rs.mean_demand_risk_period
