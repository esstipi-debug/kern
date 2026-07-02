"""Tests for newsvendor — Vandeput (2020), Chapter 11."""

import numpy as np
import pytest

from src.discrete_demand import DiscretePMF
from src.newsvendor import (
    critical_ratio,
    muffin_pmf,
    optimal_newsvendor_continuous_normal,
    optimal_newsvendor_discrete,
)


def test_muffin_critical_ratio():
    # price=6, cost=2, salvage=1 -> cu=4, co=1 -> cr=0.8
    cr = critical_ratio(4, 1)
    assert cr == pytest.approx(0.8)


def test_muffin_optimal_quantity():
    pmf = muffin_pmf()
    result = optimal_newsvendor_discrete(
        pmf,
        price=6.0,
        unit_cost=2.0,
        salvage_value=1.0,
    )
    assert result.optimal_quantity == 4
    assert result.critical_ratio == pytest.approx(0.8)
    assert result.expected_profit == pytest.approx(6.0, abs=0.1)


def test_muffin_profit_at_q4():
    pmf = muffin_pmf()
    from src.newsvendor import expected_profit_discrete

    p4 = expected_profit_discrete(4, pmf, 6, 2, salvage_value=1)
    p6 = expected_profit_discrete(6, pmf, 6, 2, salvage_value=1)
    assert p4 == pytest.approx(6.0, abs=0.1)
    assert p6 == pytest.approx(6.0, abs=0.1)


# -- discrete: non-integer PMF support must not be truncated ------------------


def test_discrete_optimum_is_not_truncated_to_an_integer():
    """Regression: candidates used to go through int(v), so a PMF support of
    {0, 2.5, 5} was silently collapsed to {0, 2, 5} - losing the true optimum.
    Hand-verified: expected profit is 0.0 / 3.2 / 4.0 / 2.0 at Q = 0 / 2 / 2.5 /
    5 - the true best (2.5, profit 4.0) beats the wrongly-truncated candidate
    (2, profit 3.2), so a truncating implementation would report Q*=2, not 2.5."""
    pmf = DiscretePMF(values=np.array([0, 2.5, 5]), probabilities=np.array([0.3, 0.3, 0.4]))

    result = optimal_newsvendor_discrete(pmf, price=10.0, unit_cost=6.0, salvage_value=2.0)

    assert result.optimal_quantity == pytest.approx(2.5)
    assert result.expected_profit == pytest.approx(4.0)


# -- continuous: zero/negative demand std is deterministic, not undefined -----


def test_continuous_zero_std_orders_exactly_the_known_demand():
    """Regression: std_demand<=0 made norm.ppf return NaN, which max(0, nan)
    silently collapsed to Q*=0 - understocking by the entire mean demand -
    alongside a hardcoded, never-computed expected_profit=0.0. With demand
    known exactly, the right order quantity is the demand itself, with zero
    expected shortage/excess and profit equal to the full margin."""
    result = optimal_newsvendor_continuous_normal(
        mean_demand=100.0, std_demand=0.0, price=10.0, unit_cost=6.0, salvage_value=2.0
    )
    assert result.optimal_quantity == pytest.approx(100.0)
    assert result.expected_profit == pytest.approx((10.0 - 6.0) * 100.0)
    assert result.expected_cost == pytest.approx(0.0)


def test_continuous_negative_std_is_treated_like_zero():
    result = optimal_newsvendor_continuous_normal(
        mean_demand=50.0, std_demand=-5.0, price=10.0, unit_cost=6.0
    )
    assert result.optimal_quantity == pytest.approx(50.0)
    assert result.expected_profit == pytest.approx((10.0 - 6.0) * 50.0)


def test_continuous_normal_case_computes_real_profit_and_cost_not_zero():
    """expected_profit/expected_cost used to be hardcoded to 0.0 unconditionally,
    even when std_demand was perfectly normal and well-behaved."""
    result = optimal_newsvendor_continuous_normal(
        mean_demand=100.0, std_demand=20.0, price=10.0, unit_cost=6.0, salvage_value=2.0
    )
    assert result.expected_profit > 0.0
    assert result.expected_cost > 0.0
    # Symmetric costs (cu == co here) put the critical ratio at 0.5, so Q* == mean.
    assert result.critical_ratio == pytest.approx(0.5)
    assert result.optimal_quantity == pytest.approx(100.0)
    # Sanity cross-check against the closed-form components directly.
    from src.fill_rate import normal_loss

    shortage = normal_loss(result.optimal_quantity, 100.0, 20.0)
    excess = shortage + (result.optimal_quantity - 100.0)
    assert result.expected_cost == pytest.approx((6.0 - 2.0) * excess + (10.0 - 6.0) * shortage)
