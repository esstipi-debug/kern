"""Tests for demand over the risk period (Ch. 5-6)."""

import math

import pytest

from src.risk_period import demand_over_risk_period


def test_sq_risk_period_is_lead_time_only():
    """(s,Q): tau = L, review_period = 0."""
    stats = demand_over_risk_period(100, 20, mean_lead_time=2, review_period=0)
    assert stats.risk_periods == 2
    assert stats.mean_demand == pytest.approx(200)
    assert stats.demand_std == pytest.approx(20 * math.sqrt(2))


def test_rs_risk_period_adds_review_period():
    """(R,S): tau = R + L."""
    stats = demand_over_risk_period(100, 20, mean_lead_time=2, review_period=3)
    assert stats.risk_periods == 5
    assert stats.mean_demand == pytest.approx(500)
    assert stats.demand_std == pytest.approx(20 * math.sqrt(5))


def test_stochastic_lead_time_inflates_std():
    """sigma_x = sqrt(tau*sigma_d^2 + sigma_L^2*mu_d^2) (eq. 6.4-6.5)."""
    deterministic = demand_over_risk_period(100, 20, 2, lead_time_std=0.0)
    stochastic = demand_over_risk_period(100, 20, 2, lead_time_std=0.5)
    assert stochastic.demand_std > deterministic.demand_std
    expected = math.sqrt(2 * 20**2 + 0.5**2 * 100**2)
    assert stochastic.demand_std == pytest.approx(expected)


def test_negative_parameters_raise():
    with pytest.raises(ValueError):
        demand_over_risk_period(-1, 20, 2)
    with pytest.raises(ValueError):
        demand_over_risk_period(100, 20, -2)
