"""Tests for src/price_optimizer.py (Linchpin 3.0 PR-16, P2 price_optimization).

Uses hand-built ``SkuElasticityFit`` fixtures directly (rather than always
routing through ``estimate_portfolio_elasticities``) to isolate the
optimizer's own logic -- the fit's own numbers are covered by
``tests/test_elasticity_batch.py``.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.elasticity_batch import SkuElasticityFit
from src.price_optimizer import (
    CompetitorPriceContext,
    optimize_portfolio_prices,
    optimize_sku_price,
)


def _fit(**overrides) -> SkuElasticityFit:
    base = dict(
        product_id="SKU1",
        category="cat_A",
        elasticity=-3.0,
        se=0.2,
        ci_low=-3.5,
        ci_high=-2.5,
        r_squared=0.9,
        n_points=10,
        identified=True,
        ci_excludes_zero=True,
        category_elasticity=-3.0,
        category_n_contributors=1,
        shrinkage_weight=1.0,
        shrunk_elasticity=-3.0,
    )
    base.update(overrides)
    return SkuElasticityFit(**base)


def test_a_floor_resolved_from_the_vertical_pack_binds_the_proposed_price():
    """End to end for the per-vertical price floor: pack -> merge_params ->
    ``optimize_sku_price``. The engine default is 0.0 (no floor at all), so the
    number has to travel the whole resolution chain to bind anything."""
    from src.client_profile import merge_params
    from src.vertical_profile import VerticalEngineParams, VerticalProfile

    pack = VerticalProfile(
        vertical="prueba", display_name="Prueba",
        engine_params=VerticalEngineParams(min_margin_pct=0.60),
    )
    resolved = merge_params({}, None, pack)["min_margin_pct"]
    # elasticity=-3 alone would propose 15.0; the pack's floor lifts it to 16.0.
    result = optimize_sku_price(
        _fit(), landed_cost=10.0, min_margin_pct=resolved, max_move_pct=None,
    )
    assert result.floor_applied is True
    assert result.proposed_price == pytest.approx(16.0)


def test_proposed_price_matches_constant_elasticity_formula():
    # elasticity=-3 -> p* = c*eps/(eps+1) = 10*(-3)/(-2) = 15.0
    fit = _fit(elasticity=-3.0, shrunk_elasticity=-3.0)
    result = optimize_sku_price(fit, landed_cost=10.0)
    assert result.status == "ok"
    assert result.proposed_price == pytest.approx(15.0)
    assert result.elasticity_used == pytest.approx(-3.0)
    assert result.floor_applied is False


def test_proposed_price_never_goes_below_landed_cost_floor():
    """elasticity=-3 -> unconstrained p* = 10*1.5 = 15.0. A required 80%
    margin pushes the floor to 10*1.8 = 18.0, which is ABOVE the elasticity-
    implied optimum -- the floor must win, not the elasticity math."""
    fit = _fit(elasticity=-3.0, shrunk_elasticity=-3.0)
    result = optimize_sku_price(fit, landed_cost=10.0, min_margin_pct=0.8)
    assert result.status == "ok"
    assert result.proposed_price == pytest.approx(18.0)
    assert result.floor_applied is True
    assert result.proposed_price >= 10.0 * 1.8


def test_proposed_price_at_least_covers_bare_landed_cost_even_with_extreme_elasticity():
    """Even a very elastic SKU (large negative elasticity -> small natural
    markup) with a punitive required margin never proposes below cost."""
    fit = _fit(elasticity=-20.0, shrunk_elasticity=-20.0)  # p* = c*20/19 ~ 1.0526c
    result = optimize_sku_price(fit, landed_cost=50.0, min_margin_pct=0.5)
    assert result.proposed_price >= 50.0
    assert result.proposed_price == pytest.approx(75.0)  # floor = 50*1.5
    assert result.floor_applied is True


def test_needs_data_when_not_identified_no_price_returned():
    fit = _fit(
        identified=False, elasticity=0.0, se=None, ci_low=None, ci_high=None,
        ci_excludes_zero=False, category_elasticity=None, category_n_contributors=0,
        shrinkage_weight=None, shrunk_elasticity=None,
    )
    result = optimize_sku_price(fit, landed_cost=10.0)
    assert result.status == "needs_data"
    assert result.proposed_price is None
    assert "identified" in result.reason.lower() or "variation" in result.reason.lower()


def test_needs_data_when_ci_crosses_zero_no_price_returned():
    """Elasticity CI crossing zero must never justify a price move -- this is
    the QA invariant from Linchpin 3.0 plan section 7 (P2's QA row)."""
    fit = _fit(
        elasticity=-0.2, ci_low=-0.9, ci_high=0.5, ci_excludes_zero=False,
        shrunk_elasticity=-0.4,
    )
    result = optimize_sku_price(fit, landed_cost=10.0)
    assert result.status == "needs_data"
    assert result.proposed_price is None
    assert "CI" in result.reason


def test_needs_data_when_shrunk_elasticity_is_inelastic():
    fit = _fit(elasticity=-0.7, ci_low=-0.9, ci_high=-0.5, ci_excludes_zero=True, shrunk_elasticity=-0.7)
    result = optimize_sku_price(fit, landed_cost=10.0)
    assert result.status == "needs_data"
    assert result.proposed_price is None
    assert "inelastic" in result.reason


def test_needs_data_when_ci_does_not_exclude_minus_one():
    """The audit case: eps = -1.05 with a CI that excludes zero but NOT -1
    used to ship p* = 10 * (-1.05)/(-0.05) = 210 (21x cost) with status 'ok'.
    The markup formula p* = c*eps/(eps+1) explodes as eps -> -1 from below,
    so the statistical gate must require the CI to exclude -1 (ci_high < -1),
    not merely exclude zero."""
    fit = _fit(elasticity=-1.05, ci_low=-2.0, ci_high=-0.1, ci_excludes_zero=True, shrunk_elasticity=-1.05)
    result = optimize_sku_price(fit, landed_cost=10.0)
    assert result.status == "needs_data"
    assert result.proposed_price is None
    assert "-1" in result.reason and "exclude" in result.reason.lower()


def test_ci_safely_below_minus_one_still_prices():
    """A CI wholly inside the elastic region (ci_high < -1) must keep pricing
    -- the new gate blocks the unstable-near--1 case, not healthy fits."""
    fit = _fit(elasticity=-3.0, ci_low=-3.5, ci_high=-2.5, shrunk_elasticity=-3.0)
    result = optimize_sku_price(fit, landed_cost=10.0)
    assert result.status == "ok"
    assert result.proposed_price == pytest.approx(15.0)


def test_uses_shrunk_elasticity_not_raw_own_elasticity():
    """When both are present, the (category-shrunk) elasticity drives the
    price, not the SKU's raw own-OLS estimate."""
    fit = _fit(elasticity=-10.0, shrunk_elasticity=-3.0)  # very different p* for each
    result = optimize_sku_price(fit, landed_cost=10.0)
    assert result.elasticity_used == pytest.approx(-3.0)
    assert result.proposed_price == pytest.approx(15.0)  # from -3.0, not -10.0


def test_price_increment_rounds_up_to_nearest_tick():
    fit = _fit(elasticity=-3.0, shrunk_elasticity=-3.0)  # p* = 15.0 exactly
    result = optimize_sku_price(fit, landed_cost=10.0, price_increment=0.0)
    assert result.proposed_price == pytest.approx(15.0)

    fit2 = _fit(elasticity=-4.0, shrunk_elasticity=-4.0)  # p* = 10*4/3 = 13.333...
    result2 = optimize_sku_price(fit2, landed_cost=10.0, price_increment=0.5)
    assert result2.proposed_price == pytest.approx(13.5)  # rounded up to nearest 0.5


def test_max_price_caps_proposal_and_flags_it():
    fit = _fit(elasticity=-3.0, shrunk_elasticity=-3.0)  # p* = 15.0
    result = optimize_sku_price(fit, landed_cost=10.0, max_price=12.0)
    assert result.proposed_price == pytest.approx(12.0)
    assert result.price_capped is True


# ---- move-size clamp vs current_price (audit fix 2) ------------------------------


def _near_minus_one_fit() -> SkuElasticityFit:
    """eps = -1.5 -> unconstrained p* = 10 * 3 = 30.0, with a CI safely
    inside the elastic region so only the clamps are under test."""
    return _fit(elasticity=-1.5, ci_low=-2.0, ci_high=-1.2, shrunk_elasticity=-1.5)


def test_move_clamp_limits_proposal_to_default_band_vs_current_price():
    """eps = -1.5 -> p* = 30.0, but from a current price of 16.0 the default
    +/-20% band caps the step at 16 * 1.2 = 19.2 -- and the result says so."""
    result = optimize_sku_price(_near_minus_one_fit(), landed_cost=10.0, current_price=16.0)
    assert result.status == "ok"
    assert result.proposed_price == pytest.approx(19.2)
    assert result.move_clamped is True


def test_move_clamp_band_is_configurable():
    result = optimize_sku_price(
        _near_minus_one_fit(), landed_cost=10.0, current_price=16.0, max_move_pct=0.5,
    )
    assert result.proposed_price == pytest.approx(24.0)  # 16 * 1.5
    assert result.move_clamped is True


def test_move_clamp_disabled_with_none():
    result = optimize_sku_price(
        _near_minus_one_fit(), landed_cost=10.0, current_price=16.0, max_move_pct=None,
    )
    assert result.proposed_price == pytest.approx(30.0)
    assert result.move_clamped is False


def test_no_move_clamp_without_a_current_price():
    result = optimize_sku_price(_near_minus_one_fit(), landed_cost=10.0)
    assert result.proposed_price == pytest.approx(30.0)
    assert result.move_clamped is False


def test_move_clamp_also_limits_downward_moves():
    """eps = -3 -> p* = 15.0; from a current price of 30.0 the default band
    floor is 30 * 0.8 = 24.0 -- steps down are clamped too, not just raises."""
    fit = _fit(elasticity=-3.0, shrunk_elasticity=-3.0)
    result = optimize_sku_price(fit, landed_cost=10.0, current_price=30.0)
    assert result.proposed_price == pytest.approx(24.0)
    assert result.move_clamped is True


def test_max_move_pct_must_be_positive_when_given():
    with pytest.raises(ValueError):
        optimize_sku_price(_near_minus_one_fit(), landed_cost=10.0, current_price=16.0, max_move_pct=0.0)
    with pytest.raises(ValueError):
        optimize_sku_price(_near_minus_one_fit(), landed_cost=10.0, current_price=16.0, max_move_pct=-0.2)


def test_cost_floor_wins_over_the_move_clamp():
    """A current price below cost must never trap the proposal below cost:
    the landed-cost floor overrides the move band (and both are flagged)."""
    fit = _fit(elasticity=-3.0, shrunk_elasticity=-3.0)  # p* = 15.0
    result = optimize_sku_price(fit, landed_cost=10.0, current_price=5.0)
    # move band would cap at 5 * 1.2 = 6.0, but that is below landed cost 10.0
    assert result.proposed_price == pytest.approx(10.0)
    assert result.floor_applied is True
    assert result.move_clamped is True


# ---- observed-price-range clamp (ported from src.pricing.recommend_price) --------


def test_range_clamp_limits_extrapolation_beyond_observed_prices():
    """The fit only saw prices in [12, 18]; the proposal is clamped to at
    most 1.3x the observed range (18 * 1.3 = 23.4) -- beyond that the curve
    is extrapolation, not interpolation (same guard as recommend_price)."""
    fit = _fit(
        elasticity=-1.5, ci_low=-2.0, ci_high=-1.2, shrunk_elasticity=-1.5,
        price_low=12.0, price_high=18.0,
    )
    result = optimize_sku_price(fit, landed_cost=10.0)  # unconstrained p* = 30.0
    assert result.proposed_price == pytest.approx(23.4)
    assert result.range_clamped is True


def test_no_range_clamp_when_fit_carries_no_observed_range():
    result = optimize_sku_price(_near_minus_one_fit(), landed_cost=10.0)
    assert result.proposed_price == pytest.approx(30.0)
    assert result.range_clamped is False


def test_move_clamp_binds_after_the_range_clamp():
    """Both clamps engage: range trims 30.0 -> 23.4, then the +/-20% band vs
    current price 16.0 trims further to 19.2 -- the tighter, operational
    constraint wins, and both are disclosed."""
    fit = _fit(
        elasticity=-1.5, ci_low=-2.0, ci_high=-1.2, shrunk_elasticity=-1.5,
        price_low=12.0, price_high=18.0,
    )
    result = optimize_sku_price(fit, landed_cost=10.0, current_price=16.0)
    assert result.proposed_price == pytest.approx(19.2)
    assert result.range_clamped is True
    assert result.move_clamped is True


def test_competitor_context_surfaced_never_overrides_price():
    """A competitor price is context only -- it must be echoed verbatim with
    its provenance (tier + timestamp) but never silently change the
    elasticity-driven proposed price (plan rule 7)."""
    ts = datetime(2026, 7, 10, tzinfo=timezone.utc)
    ctx = CompetitorPriceContext(site="example.com", competitor_price=9.99, acquisition_tier="L1", observed_at=ts)
    fit = _fit(elasticity=-3.0, shrunk_elasticity=-3.0)
    result = optimize_sku_price(fit, landed_cost=10.0, competitor_context=ctx)
    assert result.proposed_price == pytest.approx(15.0)  # unchanged by the competitor signal
    assert result.competitor_context is ctx
    assert result.competitor_context.acquisition_tier == "L1"
    assert result.competitor_context.observed_at == ts


def test_competitor_context_still_present_on_needs_data_result():
    ts = datetime(2026, 7, 10, tzinfo=timezone.utc)
    ctx = CompetitorPriceContext(site="example.com", competitor_price=9.99, acquisition_tier="L1", observed_at=ts)
    fit = _fit(identified=False, ci_excludes_zero=False, shrunk_elasticity=None, shrinkage_weight=None)
    result = optimize_sku_price(fit, landed_cost=10.0, competitor_context=ctx)
    assert result.status == "needs_data"
    assert result.competitor_context is ctx  # context survives even without a price


def test_landed_cost_must_be_positive():
    fit = _fit()
    with pytest.raises(ValueError):
        optimize_sku_price(fit, landed_cost=0.0)
    with pytest.raises(ValueError):
        optimize_sku_price(fit, landed_cost=-5.0)


def test_portfolio_mixes_ok_and_needs_data_results_in_one_call():
    """Some SKUs return real prices, others needs_data, within one batch."""
    fits = {
        "OK_SKU": _fit(product_id="OK_SKU", elasticity=-3.0, shrunk_elasticity=-3.0),
        "NO_SIGNAL_SKU": _fit(
            product_id="NO_SIGNAL_SKU", identified=False, ci_excludes_zero=False,
            shrunk_elasticity=None, shrinkage_weight=None,
        ),
        "NO_COST_SKU": _fit(product_id="NO_COST_SKU", elasticity=-3.0, shrunk_elasticity=-3.0),
    }
    results = optimize_portfolio_prices(
        fits,
        landed_costs={"OK_SKU": 10.0, "NO_SIGNAL_SKU": 10.0},  # NO_COST_SKU deliberately omitted
    )
    assert results["OK_SKU"].status == "ok"
    assert results["OK_SKU"].proposed_price == pytest.approx(15.0)
    assert results["NO_SIGNAL_SKU"].status == "needs_data"
    assert results["NO_COST_SKU"].status == "needs_data"
    assert "landed cost" in results["NO_COST_SKU"].reason


def test_portfolio_applies_the_move_clamp_per_sku():
    """current_prices flowing into the portfolio call engage the same
    per-step band as the single-SKU path (default +/-20%)."""
    fits = {"SKU1": _fit(product_id="SKU1", elasticity=-3.0, shrunk_elasticity=-3.0)}  # p* = 15.0
    results = optimize_portfolio_prices(
        fits, landed_costs={"SKU1": 10.0}, current_prices={"SKU1": 10.0},
    )
    assert results["SKU1"].proposed_price == pytest.approx(12.0)  # 10 * 1.2, not 15.0
    assert results["SKU1"].move_clamped is True

    unclamped = optimize_portfolio_prices(
        fits, landed_costs={"SKU1": 10.0}, current_prices={"SKU1": 10.0}, max_move_pct=None,
    )
    assert unclamped["SKU1"].proposed_price == pytest.approx(15.0)
    assert unclamped["SKU1"].move_clamped is False
