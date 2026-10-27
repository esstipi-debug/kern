"""Integration test: perishable / shelf-life bounding in inventory_optimization."""

import pandas as pd
import pytest

from jobs.inventory_optimization import run


def test_inventory_optimization_bounds_q_by_shelf_life(tmp_path):
    # 20 weeks of demand for a perishable SKU
    # 10 units per week = ~1.43 units/day
    # Mean weekly = 10, std = 1
    # Lead time = 2 weeks
    # Unit cost = $10, Order cost = $50, Holding rate = 0.25 -> h = $2.5/yr
    # Unconstrained EOQ = sqrt(2 * 500 * 50 / 2.5) = sqrt(20000) ~= 141.4 units
    #
    # With days_to_expiry = 21 days (3 weeks):
    # Daily demand = 10 * 52 / 365 = 1.4246 u/day
    # Total absorption capacity in 21 days = 1.4246 * 21 ~= 29.9 units
    # Reorder point s ~= 20 units (mu * L = 20)
    # Max absorbable for new order Q = 29.9 - 20 ~= 9.9 units
    #
    # Unconstrained EOQ (~141) would spoil >130 units!
    # The perishable engine MUST cap Q to <= ~10 units.

    dates = pd.date_range("2026-01-01", periods=20, freq="W")
    df = pd.DataFrame({
        "date": dates,
        "product_id": ["PERISH-01"] * 20,
        "quantity": [10.0] * 20,
        "unit_cost": [10.0] * 20,
        "lead_time_days": [14.0] * 20,     # 2 weeks
        "days_to_expiry": [21.0] * 20,     # 21 days shelf life
    })

    report = run(
        df,
        holding_rate=0.25,
        order_cost=50.0,
        service_level=0.95,
        periods_per_year=52.0,
    )

    assert report.n_skus == 1
    rec = report.recommendations[0]

    assert rec.product_id == "PERISH-01"
    assert rec.days_to_expiry == 21.0
    assert rec.perishable_verdict == "shelf_life_constrained"
    assert rec.order_quantity_before_shelf_life is not None
    assert rec.order_quantity_before_shelf_life > 100.0   # Unconstrained EOQ was ~141
    assert rec.order_quantity < 15.0                     # Bounded to absorption capacity (~10)
    assert rec.spoilage_risk_units == 0.0                # No spoilage because Q was capped


def test_inventory_optimization_detects_moq_forced_spoilage(tmp_path):
    # Same SKU, but supplier has hard MOQ = 50 units.
    # Max absorbable is ~10 units. MOQ forces buying 50.
    # Must report moq_forces_spoilage and ~40 units at risk.

    dates = pd.date_range("2026-01-01", periods=20, freq="W")
    df = pd.DataFrame({
        "date": dates,
        "product_id": ["PERISH-MOQ"] * 20,
        "quantity": [10.0] * 20,
        "unit_cost": [10.0] * 20,
        "lead_time_days": [14.0] * 20,
        "days_to_expiry": [21.0] * 20,
        "moq": [50.0] * 20,
    })

    report = run(
        df,
        holding_rate=0.25,
        order_cost=50.0,
        service_level=0.95,
        periods_per_year=52.0,
    )

    rec = report.recommendations[0]
    assert rec.perishable_verdict == "moq_forces_spoilage"
    assert rec.order_quantity == 50.0                    # Supplier accepts nothing less than 50
    assert rec.spoilage_risk_units > 35.0                # ~40 units will spoil
    assert rec.spoilage_risk_usd > 350.0                 # Measured at cost ($10/u)


def test_inventory_optimization_leaves_non_perishable_unaffected():
    # Standard SKU without days_to_expiry column: behaves exactly as before
    dates = pd.date_range("2026-01-01", periods=20, freq="W")
    df = pd.DataFrame({
        "date": dates,
        "product_id": ["DRY-GOOD"] * 20,
        "quantity": [10.0] * 20,
        "unit_cost": [10.0] * 20,
        "lead_time_days": [14.0] * 20,
    })

    report = run(
        df,
        holding_rate=0.25,
        order_cost=50.0,
        service_level=0.95,
        periods_per_year=52.0,
    )

    rec = report.recommendations[0]
    assert rec.days_to_expiry is None
    assert rec.perishable_verdict is None
    assert rec.order_quantity_before_shelf_life is None
    assert rec.order_quantity > 100.0                    # Normal EOQ (~141)
