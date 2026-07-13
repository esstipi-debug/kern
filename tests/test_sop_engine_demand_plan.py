"""Tests for src/sop_engine/demand_plan.py (Linchpin 3.0 PR-20, A5 steps 1-2)."""

from __future__ import annotations

import math

import pytest

from src.liquidation import LiquidationLine
from src.price_optimizer import PriceOptimizationResult
from src.sop_engine.demand_plan import (
    LIQUIDATION_SOURCE,
    NO_SHIFT_SOURCE,
    PRICE_OPTIMIZER_SOURCE,
    build_demand_plan,
    price_cut_lift_ratio,
)


def _price_result(**overrides) -> PriceOptimizationResult:
    base = dict(
        product_id="SKU1", status="ok", reason=None, current_price=10.0, proposed_price=5.0,
        landed_cost=3.0, elasticity_used=-2.0, shrinkage_weight=1.0, category="cat_A",
        floor_applied=False, price_capped=False, competitor_context=None,
    )
    base.update(overrides)
    return PriceOptimizationResult(**base)


def _liq_line(**overrides) -> LiquidationLine:
    base = dict(
        product_id="SKU1", classification="excess", units_to_clear=50.0, at_risk_value=500.0,
        method="elasticity", clearance_price=8.0, weeks_to_clear=5.0, recovered_value=400.0,
        recovery_pct=0.8,
    )
    base.update(overrides)
    return LiquidationLine(**base)


# ---- price_cut_lift_ratio: hand-verified against q = scale * price**elasticity ----

def test_price_cut_lift_ratio_halved_price_at_elasticity_minus2():
    # (5/10)**-2 - 1 = (0.5)**-2 - 1 = 4 - 1 = 3.0 -> demand quadruples
    assert price_cut_lift_ratio(10.0, 5.0, -2.0) == pytest.approx(3.0)


def test_price_cut_lift_ratio_price_increase_shrinks_demand():
    # (20/10)**-2 - 1 = 2**-2 - 1 = 0.25 - 1 = -0.75 -> demand drops 75%
    assert price_cut_lift_ratio(10.0, 20.0, -2.0) == pytest.approx(-0.75)


def test_price_cut_lift_ratio_no_change_is_zero():
    assert price_cut_lift_ratio(10.0, 10.0, -2.0) == pytest.approx(0.0)


def test_price_cut_lift_ratio_rejects_nonpositive_prices():
    with pytest.raises(ValueError):
        price_cut_lift_ratio(0.0, 5.0, -2.0)
    with pytest.raises(ValueError):
        price_cut_lift_ratio(10.0, -1.0, -2.0)


# ---- build_demand_plan: price-optimizer source ----

def test_price_optimizer_shift_quadruples_shaped_demand():
    forecast = {"SKU1": 100.0}
    price_shifts = {"SKU1": _price_result(current_price=10.0, proposed_price=5.0, elasticity_used=-2.0)}
    plan = build_demand_plan(forecast, price_shifts=price_shifts)
    assert len(plan) == 1
    line = plan[0]
    assert line.source == PRICE_OPTIMIZER_SOURCE
    assert line.base_forecast == pytest.approx(100.0)
    assert line.demand_shift_pct == pytest.approx(300.0)
    assert line.shaped_demand == pytest.approx(400.0)


def test_price_optimizer_needs_data_result_produces_no_shift():
    forecast = {"SKU1": 100.0}
    price_shifts = {"SKU1": _price_result(status="needs_data", proposed_price=None, elasticity_used=None)}
    plan = build_demand_plan(forecast, price_shifts=price_shifts)
    line = plan[0]
    assert line.source == NO_SHIFT_SOURCE
    assert line.demand_shift_pct == pytest.approx(0.0)
    assert line.shaped_demand == pytest.approx(100.0)


# ---- build_demand_plan: liquidation source ----

def test_liquidation_line_adds_units_to_clear_as_absolute_lift():
    forecast = {"SKU1": 20.0}
    liq = {"SKU1": _liq_line(units_to_clear=50.0, clearance_price=8.0)}
    plan = build_demand_plan(forecast, liquidation_lines=liq)
    line = plan[0]
    assert line.source == LIQUIDATION_SOURCE
    assert line.shaped_demand == pytest.approx(70.0)  # 20 base + 50 units_to_clear
    assert line.demand_shift_pct == pytest.approx(250.0)  # (70/20 - 1) * 100


def test_liquidation_salvage_line_has_no_price_so_no_shift():
    forecast = {"SKU1": 20.0}
    liq = {"SKU1": _liq_line(clearance_price=None, method="salvage_heuristic", units_to_clear=999.0)}
    plan = build_demand_plan(forecast, liquidation_lines=liq)
    line = plan[0]
    assert line.source == NO_SHIFT_SOURCE
    assert line.shaped_demand == pytest.approx(20.0)


def test_liquidation_lift_from_zero_baseline_is_infinite_not_fabricated():
    forecast = {"SKU1": 0.0}
    liq = {"SKU1": _liq_line(units_to_clear=30.0, clearance_price=8.0)}
    plan = build_demand_plan(forecast, liquidation_lines=liq)
    line = plan[0]
    assert line.shaped_demand == pytest.approx(30.0)
    assert math.isinf(line.demand_shift_pct)


def test_zero_baseline_and_zero_lift_is_finite_zero_shift():
    forecast = {"SKU1": 0.0}
    liq = {"SKU1": _liq_line(units_to_clear=0.0, clearance_price=8.0)}
    plan = build_demand_plan(forecast, liquidation_lines=liq)
    line = plan[0]
    assert line.source == NO_SHIFT_SOURCE  # units_to_clear <= 0 -> no shift signal at all
    assert line.demand_shift_pct == pytest.approx(0.0)


# ---- priority: price optimizer wins over liquidation ----

def test_price_optimizer_takes_priority_over_liquidation_when_both_present():
    forecast = {"SKU1": 100.0}
    price_shifts = {"SKU1": _price_result(current_price=10.0, proposed_price=5.0, elasticity_used=-2.0)}
    liq = {"SKU1": _liq_line(units_to_clear=999.0, clearance_price=8.0)}
    plan = build_demand_plan(forecast, price_shifts=price_shifts, liquidation_lines=liq)
    assert plan[0].source == PRICE_OPTIMIZER_SOURCE
    assert plan[0].shaped_demand == pytest.approx(400.0)


def test_falls_through_to_liquidation_when_price_optimizer_has_no_signal():
    forecast = {"SKU1": 20.0}
    price_shifts = {"SKU1": _price_result(status="needs_data", proposed_price=None, elasticity_used=None)}
    liq = {"SKU1": _liq_line(units_to_clear=50.0, clearance_price=8.0)}
    plan = build_demand_plan(forecast, price_shifts=price_shifts, liquidation_lines=liq)
    assert plan[0].source == LIQUIDATION_SOURCE
    assert plan[0].shaped_demand == pytest.approx(70.0)


# ---- portfolio behavior ----

def test_plan_is_sorted_by_product_id_and_covers_every_forecast_sku():
    forecast = {"SKU3": 1.0, "SKU1": 2.0, "SKU2": 3.0}
    plan = build_demand_plan(forecast)
    assert [line.product_id for line in plan] == ["SKU1", "SKU2", "SKU3"]
    assert all(line.source == NO_SHIFT_SOURCE for line in plan)


def test_sku_absent_from_forecast_is_never_invented_even_with_a_signal():
    forecast = {"SKU1": 10.0}
    price_shifts = {"SKU2": _price_result(product_id="SKU2")}
    plan = build_demand_plan(forecast, price_shifts=price_shifts)
    assert [line.product_id for line in plan] == ["SKU1"]
