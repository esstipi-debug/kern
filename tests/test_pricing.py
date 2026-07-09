"""Tests for the price-optimization module."""

import numpy as np
import pytest

from src.pricing import (
    demand_at,
    estimate_elasticity,
    fit_linear_demand,
    markdown_price,
    optimal_price_constant_elasticity,
    optimal_price_linear,
    recommend_price,
)


def test_estimate_elasticity_recovers_known_value():
    rng = np.random.default_rng(0)
    prices = np.linspace(5, 25, 40)
    quantities = 5000 * prices ** (-2.0) * rng.normal(1.0, 0.03, size=prices.size)
    fit = estimate_elasticity(prices, quantities)
    assert fit.identified
    assert fit.elasticity == pytest.approx(-2.0, abs=0.15)
    assert fit.r_squared > 0.9


def test_estimate_elasticity_unidentified_without_variation():
    fit = estimate_elasticity([10, 10, 10], [100, 98, 102])
    assert fit.identified is False


def test_optimal_price_constant_elasticity():
    assert optimal_price_constant_elasticity(10, -2) == pytest.approx(20)
    assert optimal_price_constant_elasticity(10, -3) == pytest.approx(15)
    assert optimal_price_constant_elasticity(10, -0.5) is None  # inelastic
    with pytest.raises(ValueError):
        optimal_price_constant_elasticity(0, -2)


def test_linear_demand_fit_and_optimal_price():
    prices = np.array([10, 20, 30, 40, 50], dtype=float)
    quantities = 1000 - 8 * prices  # a=1000, b=8
    a, b = fit_linear_demand(prices, quantities)
    assert a == pytest.approx(1000, rel=1e-6)
    assert b == pytest.approx(8, rel=1e-6)
    # p* = (a/b + c)/2 = (125 + 25)/2 = 75
    assert optimal_price_linear(a, b, 25) == pytest.approx(75)


def test_demand_at():
    fit = estimate_elasticity(np.linspace(5, 25, 20), 1000 * np.linspace(5, 25, 20) ** -1.5)
    assert demand_at(fit, 10) > demand_at(fit, 20)  # downward sloping
    with pytest.raises(ValueError):
        demand_at(fit, 0)


def test_markdown_price_lowers_to_clear_stock():
    fit = estimate_elasticity(np.linspace(5, 25, 20), 2000 * np.linspace(5, 25, 20) ** -2.0)
    p = markdown_price(remaining_units=400, periods_left=4, fit=fit, current_price=20.0)
    assert p <= 20.0  # markdown never raises
    assert p > 0


def test_markdown_price_returns_current_price_unchanged_for_a_non_negative_slope():
    """A flat or upward-sloping fitted demand curve (elasticity >= 0) is not a usable
    clearance curve: cutting price would model FEWER units sold, the opposite of a
    markdown's purpose. Regression for a bug where only `elasticity == 0` was guarded,
    letting a positive slope solve an economically inverted 'markdown' that recommends
    cutting price to sell less and recover less cash."""
    prices = [8.0, 10.0, 12.0, 14.0, 16.0, 18.0]
    quantities = [20.0, 26.0, 33.0, 41.0, 50.0, 60.0]  # co-moving with price -> elasticity > 0
    fit = estimate_elasticity(prices, quantities)
    assert fit.identified and fit.elasticity > 0  # sanity: this is the failure-mode shape
    assert markdown_price(remaining_units=200, periods_left=13, fit=fit, current_price=12.0) == 12.0


def test_recommend_price_elastic_data():
    rng = np.random.default_rng(1)
    prices = np.linspace(8, 24, 30)
    quantities = 8000 * prices ** (-2.0) * rng.normal(1.0, 0.02, size=prices.size)
    rec = recommend_price(prices, quantities, unit_cost=10.0)
    assert rec.optimal_price is not None
    assert rec.optimal_price > 10.0  # above cost
    assert rec.action in {"raise", "lower", "hold"}
    assert rec.elasticity < -1


def test_recommend_price_inelastic_and_insufficient():
    rng = np.random.default_rng(2)
    prices = np.linspace(8, 24, 30)
    inelastic_q = 500 * prices ** (-0.4) * rng.normal(1.0, 0.02, size=prices.size)
    assert recommend_price(prices, inelastic_q, 5.0).action == "inelastic"
    assert recommend_price([10, 10, 10], [100, 100, 100], 5.0).action == "insufficient_data"


# -- confidence must not survive extrapolation far outside the observed data --


def test_recommend_price_is_not_confident_when_it_extrapolates_past_observed_prices():
    """Regression: confidence used to be checked only against the current/median
    price (up to 5x), not against what the curve was actually fit on. A price
    history observed only between $18-$22 combined with a low unit cost pushes
    the theoretical optimum to ~$6 - a 3x move from the median, well inside the
    old 5x guard, but nowhere near data the elasticity curve ever saw."""
    rng = np.random.default_rng(4)
    prices = np.linspace(18, 22, 12)
    quantities = 20000 * prices ** (-2.0) * rng.normal(1.0, 0.01, size=prices.size)

    rec = recommend_price(prices, quantities, unit_cost=3.0)

    assert rec.optimal_price < 18.0 / 1.3  # meaningfully below the observed range
    assert rec.confident is False


def test_recommend_price_stays_confident_within_the_observed_range():
    """Same fitted curve, same cost regime - just a unit_cost consistent with the
    prices actually observed - must NOT be penalized by the tightened check."""
    rng = np.random.default_rng(4)
    prices = np.linspace(18, 22, 12)
    quantities = 20000 * prices ** (-2.0) * rng.normal(1.0, 0.01, size=prices.size)

    rec = recommend_price(prices, quantities, unit_cost=9.0)

    assert 18.0 <= rec.optimal_price <= 22.0
    assert rec.confident is True
