"""Tests for risk-period safety stock under normal / gamma demand (Ch. 4, 9)."""

import pytest

from src.demand_variability import safety_stock_risk_period
from src.safety_stock import service_level_factor


def test_normal_safety_stock_is_z_times_std():
    """Normal: Ss = z_alpha * sigma_x."""
    result = safety_stock_risk_period(200, 50, 0.95, risk_periods=2, distribution="normal")
    z = service_level_factor(0.95)
    assert result.safety_stock == pytest.approx(z * 50)
    assert result.risk_periods == 2
    assert result.cycle_service_level == 0.95


def test_gamma_safety_stock_is_positive_and_differs_from_normal():
    normal = safety_stock_risk_period(200, 50, 0.95, 2, distribution="normal")
    gamma = safety_stock_risk_period(200, 50, 0.95, 2, distribution="gamma")
    assert gamma.safety_stock > 0
    assert gamma.safety_stock != pytest.approx(normal.safety_stock)


def test_auto_picks_gamma_when_skew_exceeds_cv():
    """auto uses gamma when observed skewness > std/mean."""
    # std/mean = 50/200 = 0.25; skew 0.8 > 0.25 -> gamma
    auto = safety_stock_risk_period(200, 50, 0.95, 2, distribution="auto", observed_skewness=0.8)
    gamma = safety_stock_risk_period(200, 50, 0.95, 2, distribution="gamma")
    assert auto.safety_stock == pytest.approx(gamma.safety_stock)


def test_auto_picks_normal_when_skew_below_cv():
    auto = safety_stock_risk_period(200, 50, 0.95, 2, distribution="auto", observed_skewness=0.1)
    normal = safety_stock_risk_period(200, 50, 0.95, 2, distribution="normal")
    assert auto.safety_stock == pytest.approx(normal.safety_stock)
