"""Tests for src/campaign_simulator.py (Marketing Campaign Stockout & Dual LP)."""

import pytest
from src.campaign_simulator import (
    ScheduledReplenishment,
    simulate_campaign_feasibility,
)


def test_feasible_campaign_no_stockout():
    """Verify that an amply stocked SKU returns FEASIBLE with zero expedite cost."""
    result = simulate_campaign_feasibility(
        sku="SKU_AMPLE",
        initial_on_hand=500.0,
        daily_base_demand=10.0,
        campaign_start_day=5,
        campaign_duration_days=10,
        campaign_lift_pct=0.50,  # +50% -> 15 units/day during days 5..14
        scheduled_replenishments=[],
        horizon_days=30,
    )

    assert result.is_feasible is True
    assert result.stockout_day is None
    assert result.delay_days == 0
    assert result.unmet_demand_units == 0.0
    assert result.optimal_expedite_units == 0.0
    assert result.total_expedite_cost == 0.0
    assert result.verdict == "FEASIBLE"


def test_campaign_stockout_and_delay_calculation():
    """Verify exact stockout runout date and delay days calculation.
    
    Setup:
    Initial stock: 60 units
    Base demand: 10 units/day
    Campaign starts day 3, duration 5 days, lift +100% -> demand = 20 units/day.
    Days 1..2: 10 * 2 = 20 units consumed. Stock left at start of day 3 = 40.
    Day 3: demand 20 -> stock 20
    Day 4: demand 20 -> stock 0
    Day 5: demand 20 -> stockout! (day 5 is stockout_day)
    Replenishment arrives day 12 (100 units).
    Stockout duration: days 5..11 = 7 days.
    """
    replenishments = [
        ScheduledReplenishment(arrival_day=12, quantity=100.0)
    ]

    result = simulate_campaign_feasibility(
        sku="SKU_TIGHT",
        initial_on_hand=60.0,
        daily_base_demand=10.0,
        campaign_start_day=3,
        campaign_duration_days=5,
        campaign_lift_pct=1.0,  # 20 units/day during days 3..7
        scheduled_replenishments=replenishments,
        horizon_days=20,
        regular_freight_unit_cost=2.0,
        expedite_freight_unit_cost=7.0,  # surcharge = 5.0 / unit
        unserved_demand_penalty_unit=25.0,
    )

    assert result.is_feasible is False
    assert result.stockout_day == 5
    assert result.next_replenishment_day == 12
    assert result.delay_days >= 7
    assert result.verdict == "EXPEDITE_REQUIRED"
    assert result.optimal_expedite_units > 0.0
    assert result.total_expedite_cost > 0.0


def test_dual_shadow_price_kkt_consistency():
    """Verify that LP dual shadow price matches the marginal cost of expediting.
    
    When stock is in deficit and penalty (25.0) > expedite surcharge (5.0),
    the marginal cost of satisfying an additional unit of demand is exactly the
    expedite surcharge (5.0).
    """
    replenishments = [
        ScheduledReplenishment(arrival_day=10, quantity=50.0)
    ]

    result = simulate_campaign_feasibility(
        sku="SKU_DUAL_TEST",
        initial_on_hand=30.0,
        daily_base_demand=10.0,
        campaign_start_day=2,
        campaign_duration_days=4,
        campaign_lift_pct=0.5,
        scheduled_replenishments=replenishments,
        horizon_days=15,
        regular_freight_unit_cost=2.0,
        expedite_freight_unit_cost=7.0,  # delta = 5.0
        unserved_demand_penalty_unit=20.0,
    )

    assert result.is_feasible is False
    # Max shadow price must equal expedite surcharge (5.0) within floating point tolerance
    assert pytest.approx(result.max_shadow_price, abs=0.05) == 5.0


def test_campaign_lift_monotonicity():
    """Invariance: Higher campaign lift cannot decrease expedite volume or delay."""
    r_low = simulate_campaign_feasibility(
        sku="SKU_MONO",
        initial_on_hand=40.0,
        daily_base_demand=10.0,
        campaign_start_day=2,
        campaign_duration_days=5,
        campaign_lift_pct=0.2,
        scheduled_replenishments=[],
        horizon_days=15,
    )

    r_high = simulate_campaign_feasibility(
        sku="SKU_MONO",
        initial_on_hand=40.0,
        daily_base_demand=10.0,
        campaign_start_day=2,
        campaign_duration_days=5,
        campaign_lift_pct=0.8,
        scheduled_replenishments=[],
        horizon_days=15,
    )

    assert r_high.optimal_expedite_units >= r_low.optimal_expedite_units
    assert r_high.delay_days >= r_low.delay_days
