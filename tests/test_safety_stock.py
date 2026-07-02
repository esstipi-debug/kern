"""Tests for safety stock."""

import pytest
from scipy.stats import norm

from src.safety_stock import (
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
