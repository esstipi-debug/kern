"""Tests for inventory financial KPIs (capability M13 / methods §4.5).

Standards-anchored closed-form metrics computed from COGS / average inventory /
margin. SCOR DS (cash-to-cash), retail-math GMROI, GAAP DIO.
"""


import pytest

from src.financial_kpis import (
    cash_to_cash,
    days_inventory_outstanding,
    gmroi,
    inventory_to_sales,
    inventory_turns,
    sell_through,
    stockout_rate,
    weeks_of_supply,
)


def test_inventory_turns():
    assert inventory_turns(cogs=1_000_000, average_inventory_value=250_000) == pytest.approx(4.0)


def test_turns_with_zero_inventory_is_not_measurable():
    """"Infinite turns" is the best score on the sheet - never report it from no data."""
    assert inventory_turns(cogs=100, average_inventory_value=0) is None


def test_days_inventory_outstanding():
    assert days_inventory_outstanding(250_000, 1_000_000) == pytest.approx(91.25)


def test_dio_custom_period():
    assert days_inventory_outstanding(250_000, 1_000_000, period_days=360) == pytest.approx(90.0)


def test_gmroi_above_one_means_inventory_earns_margin():
    assert gmroi(gross_margin_value=500_000, average_inventory_cost=250_000) == pytest.approx(2.0)


def test_sell_through_rate():
    assert sell_through(units_sold=80, units_on_hand=20) == pytest.approx(0.8)


def test_sell_through_with_nothing_observed_is_not_measurable():
    """No units sold and none on hand is an empty observation, not a 0% sell-through."""
    assert sell_through(units_sold=0, units_on_hand=0) is None


def test_weeks_of_supply():
    assert weeks_of_supply(units_on_hand=100, avg_weekly_demand=25) == pytest.approx(4.0)


def test_inventory_to_sales_ratio():
    assert inventory_to_sales(250_000, 1_000_000) == pytest.approx(0.25)


def test_cash_to_cash_cycle():
    # C2C = DIO + DSO - DPO
    assert cash_to_cash(dio=91.25, dso=30.0, dpo=45.0) == pytest.approx(76.25)


def test_stockout_rate():
    assert stockout_rate(stockout_periods=3, total_periods=100) == pytest.approx(0.03)
