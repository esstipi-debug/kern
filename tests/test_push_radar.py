"""Tests for src/push_radar.py (Push Inventory Radar & Clearance Pricing)."""

import pytest
from src.push_radar import (
    InventoryItemProfile,
    evaluate_push_opportunities,
)


def test_perishable_spoilage_risk_trigger():
    """Verify that perishable products with inventory exceeding shelf life trigger push."""
    # On hand: 100 units. Daily demand: 2 units/day -> DOH = 50 days.
    # Total shelf life: 30 days, channel gating: 0.20 (6 days), transit LT: 4 days -> Effective SL = 20 days.
    # Max sellable in 20 days = 2 * 20 = 40 units.
    # 60 units will spoil!
    item = InventoryItemProfile(
        sku="PERISHABLE_YOGURT",
        on_hand=100.0,
        daily_demand=2.0,
        current_price=5.0,
        unit_cost=3.0,
        shelf_life_days=30.0,
        channel_gating_pct=0.20,
        transit_lead_time_days=4.0,
        disposal_cost_per_unit=0.50,
        price_elasticity=1.5,
    )

    opps = evaluate_push_opportunities([item])
    assert len(opps) == 1
    opp = opps[0]

    assert opp.sku == "PERISHABLE_YOGURT"
    assert opp.trigger == "PERISHABLE_SPOILAGE_RISK"
    assert opp.current_doh == 50.0
    assert opp.at_risk_units == 60.0
    assert opp.recommended_discount_pct > 0.0
    assert opp.recommended_clearance_price < 5.0
    assert opp.net_cash_benefit > 0.0


def test_excess_capital_doh_trigger():
    """Verify that non-perishable items with DOH > 120 trigger excess capital clearance."""
    # On hand: 1500 units. Daily demand: 10 units/day -> DOH = 150 days > 120 threshold.
    item = InventoryItemProfile(
        sku="INDUSTRIAL_BEARING",
        on_hand=1500.0,
        daily_demand=10.0,
        current_price=20.0,
        unit_cost=10.0,
        shelf_life_days=None,  # non-perishable
        price_elasticity=1.2,
    )

    opps = evaluate_push_opportunities([item], excess_doh_threshold=120.0, target_healthy_doh=60.0)
    assert len(opps) == 1
    opp = opps[0]

    assert opp.sku == "INDUSTRIAL_BEARING"
    assert opp.trigger == "EXCESS_CAPITAL_DOH"
    assert opp.current_doh == 150.0
    assert opp.at_risk_units == 900.0  # 1500 - (10 * 60)
    assert opp.recommended_discount_pct > 0.0
    assert opp.recommended_clearance_price < 20.0


def test_healthy_inventory_ignored():
    """Verify that healthy items (low DOH, ample shelf life) generate no alerts."""
    # On hand: 30 units, daily demand: 2 units/day -> DOH = 15 days. Shelf life 60 days.
    item = InventoryItemProfile(
        sku="HEALTHY_SNACK",
        on_hand=30.0,
        daily_demand=2.0,
        current_price=3.0,
        unit_cost=1.5,
        shelf_life_days=60.0,
    )

    opps = evaluate_push_opportunities([item])
    assert len(opps) == 0


def test_push_discount_clamped_to_ceiling():
    """Verify that extreme overstock does not discount beyond max_allowed_discount_pct."""
    # On hand: 10,000 units, demand: 1 unit/day -> DOH = 10,000 days.
    item = InventoryItemProfile(
        sku="OBSOLETE_WIDGET",
        on_hand=10000.0,
        daily_demand=1.0,
        current_price=50.0,
        unit_cost=20.0,
        shelf_life_days=None,
    )

    max_discount = 0.50
    opps = evaluate_push_opportunities([item], max_allowed_discount_pct=max_discount)
    assert len(opps) == 1
    assert opps[0].recommended_discount_pct <= max_discount
    assert opps[0].recommended_clearance_price >= 50.0 * (1.0 - max_discount)
