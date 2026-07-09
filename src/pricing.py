"""Price optimization — elasticity, profit-maximizing price, markdown.

Extends the engine's economics (newsvendor critical ratio, EOQ volume discounts)
to the price-setting side: estimate demand elasticity from price/quantity history
and recommend the margin-maximizing price.

Constant-elasticity demand model: q(p) = A * p**ε  (ε < 0 for normal goods).
Profit π(p) = (p − c) · q(p) is maximized at p* = c · ε/(ε+1), valid when the
demand is elastic (ε < −1); for inelastic demand (−1 ≤ ε < 0) there is no
interior optimum.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

# How far the recommended price may fall outside the OBSERVED price range (the
# min/max the elasticity curve was actually fit on) and still be called
# "confident". The curve is only validated where there is data; beyond this
# margin it is extrapolation, not interpolation, no matter how good the R^2
# looked over the observed range. Replaces a looser check against only the
# current/median price (up to 5x), which said nothing about what data the fit
# had actually seen and let a recommendation double the observed max price -
# well outside the fitted range - still be reported as confident.
_MAX_EXTRAPOLATION = 1.3


@dataclass(frozen=True)
class ElasticityFit:
    """Fitted constant-elasticity demand curve q = scale * price**elasticity."""

    elasticity: float
    scale: float
    r_squared: float
    n_points: int
    identified: bool  # enough price variation to estimate


def estimate_elasticity(prices: object, quantities: object) -> ElasticityFit:
    """Estimate price elasticity by log-log regression of quantity on price."""
    p = np.asarray(list(prices), dtype=float)
    q = np.asarray(list(quantities), dtype=float)
    if p.shape != q.shape:
        raise ValueError("prices and quantities must have the same length")
    mask = (p > 0) & (q > 0)
    p, q = p[mask], q[mask]
    if len(p) < 2 or np.allclose(p, p[0]):
        return ElasticityFit(0.0, float(np.mean(q)) if len(q) else 0.0, 0.0, int(len(p)), False)

    lp, lq = np.log(p), np.log(q)
    elasticity, intercept = np.polyfit(lp, lq, 1)
    pred = intercept + elasticity * lp
    ss_res = float(np.sum((lq - pred) ** 2))
    ss_tot = float(np.sum((lq - np.mean(lq)) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0
    return ElasticityFit(float(elasticity), float(np.exp(intercept)), r2, int(len(p)), True)


def demand_at(fit: ElasticityFit, price: float) -> float:
    """Predicted quantity at a price under the fitted curve."""
    if price <= 0:
        raise ValueError("price must be > 0")
    return fit.scale * price**fit.elasticity


def optimal_price_constant_elasticity(unit_cost: float, elasticity: float) -> float | None:
    """p* = c·ε/(ε+1); None when demand is inelastic (ε ≥ −1)."""
    if unit_cost <= 0:
        raise ValueError("unit_cost must be > 0")
    if elasticity >= -1:
        return None
    return unit_cost * elasticity / (elasticity + 1)


def fit_linear_demand(prices: object, quantities: object) -> tuple[float, float]:
    """Fit q = a − b·p; returns (a, b)."""
    p = np.asarray(list(prices), dtype=float)
    q = np.asarray(list(quantities), dtype=float)
    if len(p) < 2 or np.allclose(p, p[0]):
        raise ValueError("need at least two distinct prices")
    slope, intercept = np.polyfit(p, q, 1)
    return float(intercept), float(-slope)


def optimal_price_linear(intercept_a: float, slope_b: float, unit_cost: float) -> float:
    """Profit-max price for linear demand q = a − b·p:  p* = (a/b + c)/2."""
    if slope_b <= 0:
        raise ValueError("slope_b must be > 0")
    return (intercept_a / slope_b + unit_cost) / 2.0


def markdown_price(
    remaining_units: float,
    periods_left: float,
    fit: ElasticityFit,
    current_price: float,
    *,
    floor: float | None = None,
) -> float:
    """Markdown price that clears remaining stock over the periods left.

    Solves demand_at(p)·periods_left = remaining_units for p, never raising above
    the current price; clamped to ``floor`` when given.
    """
    if remaining_units <= 0 or periods_left <= 0:
        raise ValueError("remaining_units and periods_left must be > 0")
    # A non-negative fitted elasticity (flat or upward-sloping demand) means cutting
    # price would not increase - or would even decrease - modeled demand; there is no
    # markdown that clears stock faster under this curve, so return the current price
    # unchanged rather than solve an economically inverted "markdown". Mirrors how
    # optimal_price_constant_elasticity/recommend_price already reject elasticity >= -1
    # as not optimizable.
    if not fit.identified or fit.elasticity >= 0 or fit.scale <= 0:
        return current_price
    target_rate = remaining_units / periods_left
    price = (target_rate / fit.scale) ** (1.0 / fit.elasticity)
    price = min(price, current_price)
    if floor is not None:
        price = max(price, floor)
    return float(price)


@dataclass(frozen=True)
class PriceRecommendation:
    """A price recommendation derived from price/quantity history."""

    current_price: float
    optimal_price: float | None
    elasticity: float
    r_squared: float
    n_points: int
    demand_change_pct: float | None
    profit_uplift_pct: float | None
    action: str  # raise | lower | hold | inelastic | insufficient_data
    confident: bool


def recommend_price(prices: object, quantities: object, unit_cost: float) -> PriceRecommendation:
    """Recommend a margin-maximizing price from observed price/quantity points."""
    if unit_cost <= 0:
        raise ValueError("unit_cost must be > 0")
    p = np.asarray(list(prices), dtype=float)
    positive = p[p > 0]
    current = float(np.median(positive)) if len(positive) else 0.0
    price_low = float(positive.min()) if len(positive) else 0.0
    price_high = float(positive.max()) if len(positive) else 0.0

    fit = estimate_elasticity(prices, quantities)
    base = PriceRecommendation(
        current_price=current, optimal_price=None, elasticity=fit.elasticity,
        r_squared=fit.r_squared, n_points=fit.n_points, demand_change_pct=None,
        profit_uplift_pct=None, action="insufficient_data", confident=False,
    )
    if not fit.identified:
        return base
    if fit.elasticity >= -1:
        return PriceRecommendation(**{**base.__dict__, "action": "inelastic"})

    p_star = optimal_price_constant_elasticity(unit_cost, fit.elasticity)
    assert p_star is not None  # elasticity < -1 guaranteed above
    q_cur, q_opt = demand_at(fit, current) if current > 0 else 0.0, demand_at(fit, p_star)
    demand_change = (q_opt / q_cur - 1.0) * 100.0 if q_cur > 0 else None
    profit_cur = (current - unit_cost) * q_cur
    profit_opt = (p_star - unit_cost) * q_opt
    uplift = (profit_opt / profit_cur - 1.0) * 100.0 if profit_cur > 0 else None

    if current <= 0:
        action = "raise"
    elif p_star > current * 1.01:
        action = "raise"
    elif p_star < current * 0.99:
        action = "lower"
    else:
        action = "hold"

    within_observed_range = price_low > 0 and (price_low / _MAX_EXTRAPOLATION) <= p_star <= (price_high * _MAX_EXTRAPOLATION)
    confident = (
        fit.r_squared >= 0.5
        and fit.n_points >= 4
        and current > 0
        and within_observed_range
    )

    return PriceRecommendation(
        current_price=current, optimal_price=float(p_star), elasticity=fit.elasticity,
        r_squared=fit.r_squared, n_points=fit.n_points, demand_change_pct=demand_change,
        profit_uplift_pct=uplift, action=action, confident=confident,
    )
