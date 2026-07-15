"""Tests for safety stock."""

import pytest
from scipy.stats import norm

from src.safety_stock import (
    achieved_service_level,
    cycle_service_level_from_inventory,
    safety_stock,
    service_level_factor,
)


def test_service_level_factor_95():
    assert service_level_factor(0.95) == pytest.approx(1.645, rel=0.01)


def test_safety_stock_table_4_1():
    """Table 4.1: mu=100, sigma=25, alpha=0.95 -> inv=141."""
    mu, sigma, alpha = 100, 25, 0.95
    inv = mu + service_level_factor(alpha) * sigma
    assert inv == pytest.approx(141, abs=1)


def test_safety_stock_sqrt_tau():
    ss1 = safety_stock(25, 0.95, risk_periods=1).safety_stock
    ss4 = safety_stock(25, 0.95, risk_periods=4).safety_stock
    assert ss4 == pytest.approx(ss1 * 2, rel=1e-9)


def test_safety_stock_exact_signed_value_table_4_1():
    """Table 4.1: mu=100, sigma=25, alpha=0.95 -> Ss = inv - mu = 141 - 100 = 41.

    Anchored to the book's own worked example and checked through safety_stock()
    itself (not re-derived by hand like test_safety_stock_table_4_1 above), so a
    sign error in the core formula fails here. test_safety_stock_sqrt_tau does NOT
    catch a sign flip on z_alpha: it only checks proportionality, and a
    flipped-sign result is still exactly 2x itself at risk_periods=4. This test
    pins the actual signed value.
    """
    result = safety_stock(25, 0.95, risk_periods=1)
    assert result.safety_stock == pytest.approx(41, abs=1)
    assert result.safety_stock > 0


def test_cycle_service_level_inverse():
    mu, sigma = 100, 25
    alpha = 0.9
    inv = float(norm.ppf(alpha, mu, sigma))
    assert cycle_service_level_from_inventory(inv, mu, sigma) == pytest.approx(alpha, rel=1e-3)


def test_achieved_service_level_independent_formula():
    """Cross-check via a direct norm.cdf call, avoiding tautology with the
    function under test — same pattern as test_cycle_service_level_inverse."""
    expected = float(norm.cdf(20 / 25))
    assert achieved_service_level(20, 25, 1) == pytest.approx(expected)


def test_achieved_service_level_naive_20pct_rule():
    """The common 'hold 20% of average demand as safety stock' heuristic,
    mu=100 sigma=25 (the Table 4.1 scenario) -- only ~79% service level
    against a 95% target, despite looking like a reasonable buffer."""
    result = achieved_service_level(20, 25, 1)
    assert result == pytest.approx(0.7881, abs=0.001)


def test_achieved_service_level_round_trips_with_safety_stock():
    """Round-trips with safety_stock()/service_level_factor() the same way
    test_cycle_service_level_inverse round-trips cycle_service_level_from_inventory()."""
    target_sl = 0.95
    ss = safety_stock(25, target_sl, 1).safety_stock
    assert achieved_service_level(ss, 25, 1) == pytest.approx(target_sl, abs=0.001)


def test_achieved_service_level_zero_std():
    assert achieved_service_level(5, 0, 1) == 1.0
    assert achieved_service_level(-5, 0, 1) == 0.0


def test_achieved_service_level_scales_with_risk_periods():
    """The sqrt(tau) term must actually do something -- a bug here (e.g. dropping
    the sqrt, or inverting risk_periods) would not be caught by any test that
    only exercises risk_periods=1."""
    # Same safety stock quantity covers a SHORTER risk period better than a longer one.
    short = achieved_service_level(50, 25, risk_periods=1)
    long = achieved_service_level(50, 25, risk_periods=4)
    assert short > long
    # Cross-check against the independent norm.cdf formula for risk_periods=4.
    expected = float(norm.cdf(50 / (25 * (4**0.5))))
    assert long == pytest.approx(expected)


def test_achieved_service_level_rejects_nonpositive_risk_periods():
    """Matches safety_stock()'s own validation for the same parameter."""
    with pytest.raises(ValueError, match="risk_periods must be > 0"):
        achieved_service_level(20, 25, risk_periods=-1)
    with pytest.raises(ValueError, match="risk_periods must be > 0"):
        achieved_service_level(20, 25, risk_periods=0)
