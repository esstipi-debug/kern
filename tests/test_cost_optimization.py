"""Tests for cost optimization — Vandeput (2020), Chapter 8."""

import pytest
from scipy.stats import norm

from src.cost_optimization import (
    optimal_cycle_service_level_rs,
    optimize_rs_policy,
    rs_cost_per_period,
)
from src.fill_rate import normal_loss_standard
from src.risk_period import demand_over_risk_period


def test_optimal_cycle_service_level_book_example():
    """Section 8.2: h=1.25, R=1, b=50 -> alpha*=97.5%."""
    alpha = optimal_cycle_service_level_rs(1.25, 1.0, 50.0)
    assert alpha == pytest.approx(0.975, abs=0.001)
    assert norm.ppf(alpha) == pytest.approx(1.960, abs=0.01)


def test_rs_cost_book_example():
    """Section 8.2: optimal cost ~1165.82 at z=1.960."""
    h, d_mu, d_std, r, lead, k, b = 1.25, 100, 25, 1, 1, 1000, 50
    risk = demand_over_risk_period(d_mu, d_std, lead, 0, r)
    z = 1.960
    cost = rs_cost_per_period(h, d_mu, r, z, risk.demand_std, k, b)
    assert cost.total == pytest.approx(1165.82, abs=2.0)
    assert cost.holding == pytest.approx(149.12, abs=1.0)
    assert float(normal_loss_standard(z)) == pytest.approx(0.0094, abs=0.001)


def test_review_period_optimization_finds_minimum():
    """Section 8.2: optimal R=4 for book parameters with d_mu=100."""
    result = optimize_rs_policy(
        mean_demand_per_period=100,
        demand_std_per_period=25,
        mean_lead_time=1,
        holding_cost_per_period=1.25,
        fixed_order_cost=1000,
        backorder_cost=50,
        review_periods=[1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0],
    )
    assert result.review_period == 4.0
    assert result.cost.total == pytest.approx(622.63, abs=5.0)
    assert result.cost.fill_rate > 0.99
