"""Tests for src/elasticity_batch.py (Linchpin 3.0 PR-16, P2 price_optimization).

The primary fixture below uses ``price = exp(k)``, ``quantity = exp(m)`` for
small integer ``k``/``m`` so that ``ln(price) == k`` and ``ln(quantity) == m``
exactly (to floating-point precision) -- the log-log OLS this module runs
then reduces to ordinary least squares on the small integers ``k``/``m``
directly, which is hand-computable with the textbook slope formula
``b = sum((x-xbar)(y-ybar)) / sum((x-xbar)^2)``. See
``test_hand_verified_reference_example`` for the full worked arithmetic.
"""

from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from src.elasticity_batch import (
    DEFAULT_CATEGORY,
    MIN_POINTS_FOR_SE,
    estimate_portfolio_elasticities,
    fit_sku_elasticity_ci,
    shrink_toward_category,
    shrinkage_weight,
    statsmodels_available,
)


def _portfolio_frame() -> pd.DataFrame:
    """The category "cat_A" fixture used across this file's tests.

    SKU_RICH1: k=[0,1,2,3], m=[6,4,2,1]   (a near-elasticity-(-2) power law
      with one point nudged by +1 so the fit has finite, computable noise)
    SKU_RICH2: k=[0,1,2,3], m=[5,3,2,1]   (built with the mirror-image nudge
      so its SSE and se come out identical to SKU_RICH1's -- see comment
      below)
    SKU_THIN:  k=[0,1],     m=[3,3]       (flat demand, only 2 points -- no
      residual degrees of freedom, no computable standard error)
    """
    rows: list[dict] = []
    for k, m in zip([0, 1, 2, 3], [6, 4, 2, 1]):
        rows.append({"product_id": "RICH1", "category": "cat_A", "price": math.exp(k), "quantity": math.exp(m)})
    for k, m in zip([0, 1, 2, 3], [5, 3, 2, 1]):
        rows.append({"product_id": "RICH2", "category": "cat_A", "price": math.exp(k), "quantity": math.exp(m)})
    for k, m in zip([0, 1], [3, 3]):
        rows.append({"product_id": "THIN", "category": "cat_A", "price": math.exp(k), "quantity": math.exp(m)})
    return pd.DataFrame(rows)


def test_hand_verified_reference_example():
    """Full worked arithmetic for the fixture above (all in log-space, since
    price=exp(k)/quantity=exp(m) makes ln(price)=k, ln(quantity)=m exactly).

    -- SKU_RICH1: k=[0,1,2,3], m=[6,4,2,1] --
    kbar=1.5, mbar=13/4=3.25
    k-kbar=[-1.5,-0.5,0.5,1.5]; m-mbar=[2.75,0.75,-1.25,-2.25]
    Sxy = (-1.5*2.75)+(-0.5*0.75)+(0.5*-1.25)+(1.5*-2.25)
        = -4.125-0.375-0.625-3.375 = -8.5
    Sxx = 1.5^2+0.5^2+0.5^2+1.5^2 = 5.0
    slope = Sxy/Sxx = -8.5/5.0 = -1.7                        <- own elasticity
    intercept = mbar - slope*kbar = 3.25 - (-1.7*1.5) = 5.8
    predicted = [5.8, 4.1, 2.4, 0.7]; residuals = [0.2,-0.1,-0.4,0.3]
    SSE = 0.04+0.01+0.16+0.09 = 0.30; df=2; MSE=0.15
    se = sqrt(MSE/Sxx) = sqrt(0.15/5.0) = sqrt(0.03) = 0.1732050808   <- se
    t_crit(df=2, 97.5%) = 4.302652729911275 (standard value)
    margin = t_crit*se = 0.745245...
    CI = (-1.7-0.745245, -1.7+0.745245) = (-2.445, -0.955)          <- excludes 0

    -- SKU_RICH2: k=[0,1,2,3], m=[5,3,2,1] (mirror-image nudge) --
    kbar=1.5, mbar=11/4=2.75
    k-kbar=[-1.5,-0.5,0.5,1.5]; m-mbar=[2.25,0.25,-0.75,-1.75]
    Sxy = -3.375-0.125-0.375-2.625 = -6.5; Sxx=5.0
    slope = -6.5/5.0 = -1.3                                          <- own elasticity
    intercept = 2.75-(-1.3*1.5) = 4.7
    predicted=[4.7,3.4,2.1,0.8]; residuals=[0.3,-0.4,-0.1,0.2]
    SSE = 0.09+0.16+0.01+0.04 = 0.30  (same SSE as RICH1 by construction)
    se = sqrt(0.15/5.0) = 0.1732050808                                <- same se as RICH1

    -- category "cat_A" (RICH1 + RICH2 are the only contributing SKUs;
       THIN has n=2 < MIN_POINTS_FOR_SE, no se, does not contribute) --
    Since se_RICH1 == se_RICH2, the precision (inverse-variance) weights are
    equal, so the weighted mean is the plain average:
    category_elasticity = (-1.7 + -1.3)/2 = -1.5

    tau^2 (simplified DerSimonian-Laird, se^2=0.03 for both, weight w=1/0.03
    for both, mu_FE=-1.5 by the same equal-weight argument):
    Q = (( -1.7-(-1.5))^2)/0.03 + ((-1.3-(-1.5))^2)/0.03
      = (0.04/0.03) + (0.04/0.03) = 8/3
    k_studies=2; sum_w=2/0.03=200/3; sum_w2=2*(1/0.03)^2=20000/9
    C = sum_w - sum_w2/sum_w = 200/3 - (20000/9)/(200/3) = 200/3-100/3 = 100/3
    tau2 = (Q-(k_studies-1))/C = (8/3-1)/(100/3) = (5/3)/(100/3) = 5/100 = 0.05

    shrinkage weight (same for both RICH SKUs, same se):
    w = tau2/(tau2+se^2) = 0.05/(0.05+0.03) = 0.05/0.08 = 0.625

    shrunk_RICH1 = 0.625*(-1.7) + 0.375*(-1.5) = -1.0625 + -0.5625 = -1.625
    shrunk_RICH2 = 0.625*(-1.3) + 0.375*(-1.5) = -0.8125 + -0.5625 = -1.375

    -- SKU_THIN: k=[0,1], m=[3,3] --
    own slope = (3-3)/(1-0) = 0.0 (own OLS estimate, n=2 -> identified=True
    per src.pricing.estimate_elasticity, but df=n-2=0 -> se is undefined)
    shrinkage weight = 0.0 (se is None -> fully deferred to category)
    shrunk_THIN = 0.0*0.0 + 1.0*(-1.5) = -1.5     <- fully inherits category
    """
    result = estimate_portfolio_elasticities(_portfolio_frame(), category_col="category")

    rich1, rich2, thin = result["RICH1"], result["RICH2"], result["THIN"]

    # Own OLS coefficient (exact match -- these are the log-log slopes, not
    # merely close approximations, since price=exp(k)/quantity=exp(m)).
    assert rich1.elasticity == pytest.approx(-1.7, abs=1e-9)
    assert rich2.elasticity == pytest.approx(-1.3, abs=1e-9)
    assert rich1.se == pytest.approx(math.sqrt(0.03), abs=1e-9)
    assert rich2.se == pytest.approx(math.sqrt(0.03), abs=1e-9)
    assert rich1.ci_low == pytest.approx(-2.445245, abs=1e-5)
    assert rich1.ci_high == pytest.approx(-0.954755, abs=1e-5)
    assert rich1.ci_excludes_zero is True
    assert rich2.ci_excludes_zero is True

    # Category-level pooled elasticity + tau^2-implied shrinkage weight.
    assert rich1.category_elasticity == pytest.approx(-1.5, abs=1e-9)
    assert rich2.category_elasticity == pytest.approx(-1.5, abs=1e-9)
    assert rich1.category_n_contributors == 2
    assert rich1.shrinkage_weight == pytest.approx(0.625, abs=1e-9)
    assert rich2.shrinkage_weight == pytest.approx(0.625, abs=1e-9)

    # The shrunk value -- exact match to the hand computation above.
    assert rich1.shrunk_elasticity == pytest.approx(-1.625, abs=1e-9)
    assert rich2.shrunk_elasticity == pytest.approx(-1.375, abs=1e-9)

    # SKU_THIN: identified (2 distinct prices) but no computable own se --
    # fully deferred (weight 0.0) to the category mean.
    assert thin.identified is True
    assert thin.se is None
    assert thin.ci_low is None and thin.ci_high is None
    assert thin.ci_excludes_zero is False
    assert thin.shrinkage_weight == pytest.approx(0.0, abs=1e-12)
    assert thin.shrunk_elasticity == pytest.approx(-1.5, abs=1e-9)


def test_thin_sku_pulled_toward_category_rich_sku_stays_close_to_own_estimate():
    """Shrinkage is demonstrated, not a no-op: the thin SKU's shrunk value
    moves ALL the way to the category mean (weight 0), while a data-rich SKU
    with a computable, nonzero se moves only PARTWAY from its own estimate
    (weight 0.625, strictly between 0 and 1)."""
    result = estimate_portfolio_elasticities(_portfolio_frame(), category_col="category")
    rich1, thin = result["RICH1"], result["THIN"]

    # Rich SKU: shrunk stays close to its own estimate, not equal to it (real
    # shrinkage occurred) and clearly closer to -1.7 than to the category -1.5.
    assert rich1.shrunk_elasticity != pytest.approx(rich1.elasticity, abs=1e-6)
    assert abs(rich1.shrunk_elasticity - rich1.elasticity) < abs(rich1.shrunk_elasticity - rich1.category_elasticity)

    # Thin SKU: shrunk value equals the category mean exactly (full pull),
    # and is far from its own (near-zero, unreliable) own-OLS estimate.
    assert thin.shrunk_elasticity == pytest.approx(thin.category_elasticity, abs=1e-9)
    assert abs(thin.shrunk_elasticity - thin.elasticity) > abs(rich1.shrunk_elasticity - rich1.elasticity)


def test_ci_crossing_zero_is_not_excludes_zero():
    """A SKU with weak/noisy price sensitivity (elasticity indistinguishable
    from zero) is identified (real price variation exists) but its CI must
    straddle zero -- this is elasticity_batch's half of the "needs_data,
    never a fabricated number" contract; src/price_optimizer.py gates on it."""
    prices = [5.0, 10.0, 15.0, 20.0, 25.0, 30.0]
    quantities = [100.0, 92.0, 105.0, 89.0, 98.0, 94.0]  # flat/noisy, no real trend
    ci = fit_sku_elasticity_ci(prices, quantities)
    assert ci.fit.identified is True
    assert ci.se is not None
    assert ci.ci_low < 0 < ci.ci_high  # crosses zero
    assert ci.ci_excludes_zero is False


def test_unidentified_sku_propagates_from_elasticity_fit():
    """No price variation at all -> identified=False (ElasticityFit's own
    signal, propagated verbatim, not rebuilt) -> no shrunk number either."""
    df = pd.DataFrame(
        {
            "product_id": ["A", "A", "A"],
            "price": [10.0, 10.0, 10.0],
            "quantity": [50.0, 48.0, 52.0],
        }
    )
    result = estimate_portfolio_elasticities(df)
    fit = result["A"]
    assert fit.identified is False
    assert fit.shrinkage_weight is None
    assert fit.shrunk_elasticity is None
    assert fit.category == DEFAULT_CATEGORY  # no category_col given -> documented default


def test_statsmodels_and_fallback_paths_agree():
    """The statsmodels path and the closed-form numpy+scipy fallback solve
    the same normal equations -- they must agree to floating precision. This
    only proves something when statsmodels is actually installed (this repo
    pins it as a low-risk core-numpy extra -- see pyproject's `elasticity`
    group); skip cleanly if it is not available in this environment."""
    if not statsmodels_available():
        pytest.skip("statsmodels not installed")

    import src.elasticity_batch as eb

    prices = np.linspace(5, 25, 12)
    rng = np.random.default_rng(7)
    quantities = 5000 * prices ** (-1.8) * rng.normal(1.0, 0.05, size=prices.size)

    with_sm = eb.fit_sku_elasticity_ci(prices, quantities)

    original_flag = eb.statsmodels_available
    eb.statsmodels_available = lambda: False
    try:
        without_sm = eb.fit_sku_elasticity_ci(prices, quantities)
    finally:
        eb.statsmodels_available = original_flag

    assert with_sm.fit.elasticity == pytest.approx(without_sm.fit.elasticity, abs=1e-9)
    assert with_sm.se == pytest.approx(without_sm.se, rel=1e-6)
    assert with_sm.ci_low == pytest.approx(without_sm.ci_low, rel=1e-4)
    assert with_sm.ci_high == pytest.approx(without_sm.ci_high, rel=1e-4)


def test_shrinkage_weight_edge_cases():
    assert shrinkage_weight(None, tau2=0.05) == 0.0  # thin -- no own se
    assert shrinkage_weight(0.0, tau2=0.05) == 1.0  # perfect fit -- trust own value
    assert shrinkage_weight(0.2, tau2=0.0) == 0.0  # no between-SKU heterogeneity -> pool fully
    assert shrinkage_weight(0.2, tau2=0.04) == pytest.approx(0.5)  # se^2 == tau2 -> 50/50


def test_shrink_toward_category_is_a_convex_combination():
    assert shrink_toward_category(-2.0, weight=1.0, category_elasticity=-1.0) == pytest.approx(-2.0)
    assert shrink_toward_category(-2.0, weight=0.0, category_elasticity=-1.0) == pytest.approx(-1.0)
    assert shrink_toward_category(-2.0, weight=0.5, category_elasticity=-1.0) == pytest.approx(-1.5)


def test_default_category_when_no_category_col_given():
    df = pd.DataFrame(
        {
            "product_id": ["X", "X", "X", "X", "Y", "Y", "Y", "Y"],
            "price": [1.0, 2.0, 4.0, 8.0, 1.0, 2.0, 4.0, 8.0],
            "quantity": [64.0, 16.0, 4.0, 1.0, 60.0, 15.0, 4.5, 1.0],
        }
    )
    result = estimate_portfolio_elasticities(df)
    assert result["X"].category == DEFAULT_CATEGORY
    assert result["Y"].category == DEFAULT_CATEGORY
    assert result["X"].category_n_contributors == 2  # both X and Y contribute to the pool


def test_inconsistent_category_within_a_sku_raises():
    df = pd.DataFrame(
        {
            "product_id": ["A", "A"],
            "category": ["cat_1", "cat_2"],
            "price": [10.0, 20.0],
            "quantity": [50.0, 30.0],
        }
    )
    with pytest.raises(ValueError, match="inconsistent"):
        estimate_portfolio_elasticities(df, category_col="category")


def test_missing_columns_raises():
    df = pd.DataFrame({"product_id": ["A"], "price": [10.0]})
    with pytest.raises(ValueError, match="missing columns"):
        estimate_portfolio_elasticities(df)


def test_min_points_for_se_constant_is_three():
    # Sanity guard: MIN_POINTS_FOR_SE must stay 3 (df = n - 2 >= 1) for the
    # rest of this file's hand-verified arithmetic to hold.
    assert MIN_POINTS_FOR_SE == 3
