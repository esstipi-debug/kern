"""Unit tests for src/perishables.py — perishable replenishment and shelf-life bounds."""

import pytest
from src.perishables import (
    PerishableLimit,
    bound_order_quantity_by_expiry,
    effective_shelf_life,
)


def test_effective_shelf_life_pure():
    # 30 days total, no channel gating, no transit
    assert effective_shelf_life(30.0) == 30.0

    # 30 days total, 70% supermarket gating, 2 days transit:
    # 30 * (1 - 0.70) - 2 = 9 - 2 = 7 days
    assert effective_shelf_life(30.0, channel_gating_pct=0.70, transit_lead_time_days=2.0) == pytest.approx(7.0)

    # If transit + gating exceeds total life, effective life is 0.0
    assert effective_shelf_life(10.0, channel_gating_pct=0.90, transit_lead_time_days=3.0) == 0.0


def test_effective_shelf_life_validation():
    with pytest.raises(ValueError, match="channel_gating_pct"):
        effective_shelf_life(30.0, channel_gating_pct=1.0)
    with pytest.raises(ValueError, match="channel_gating_pct"):
        effective_shelf_life(30.0, channel_gating_pct=-0.1)
    with pytest.raises(ValueError, match="transit_lead_time_days"):
        effective_shelf_life(30.0, transit_lead_time_days=-1.0)


def test_bound_order_quantity_fits_inside_shelf_life():
    # Demand = 10 u/day, Shelf life = 20 days -> Absorption = 200 units
    # Reorder point s = 50 -> Max absorbable for new order Q = 150 units
    # Proposed Q = 100 <= 150 -> ok, no capping needed
    lim = bound_order_quantity_by_expiry(
        unconstrained_q=100.0,
        reorder_point=50.0,
        daily_demand=10.0,
        shelf_life_days=20.0,
        unit_cost=5.0,
    )
    assert lim.verdict == "ok"
    assert lim.constrained_q == 100.0
    assert lim.max_absorbable_units == 150.0
    assert lim.spoilage_risk_units == 0.0
    assert not lim.is_constrained
    assert not lim.generates_spoilage


def test_bound_order_quantity_capped_by_shelf_life():
    # Demand = 10 u/day, Shelf life = 20 days -> Absorption = 200 units
    # Reorder point s = 50 -> Max absorbable for Q = 150 units
    # Proposed Q = 250 > 150 -> must cap Q to 150
    lim = bound_order_quantity_by_expiry(
        unconstrained_q=250.0,
        reorder_point=50.0,
        daily_demand=10.0,
        shelf_life_days=20.0,
        unit_cost=5.0,
    )
    assert lim.verdict == "shelf_life_constrained"
    assert lim.constrained_q == 150.0
    assert lim.max_absorbable_units == 150.0
    assert lim.spoilage_risk_units == 0.0
    assert lim.is_constrained
    assert not lim.generates_spoilage


def test_bound_order_quantity_with_order_multiple():
    # Max absorbable = 150 units. Supplier sells by multiples of 40.
    # Snaps down to 3 * 40 = 120 units (does not overshoot 150)
    lim = bound_order_quantity_by_expiry(
        unconstrained_q=250.0,
        reorder_point=50.0,
        daily_demand=10.0,
        shelf_life_days=20.0,
        order_multiple=40.0,
        unit_cost=10.0,
    )
    assert lim.verdict == "shelf_life_constrained"
    assert lim.constrained_q == 120.0
    assert lim.is_constrained


def test_bound_order_quantity_moq_forces_spoilage():
    # Max absorbable = 150 units. Supplier has hard MOQ = 200 units.
    # The supplier forces buying 200, so 50 units will structurally spoil.
    lim = bound_order_quantity_by_expiry(
        unconstrained_q=100.0,
        reorder_point=50.0,
        daily_demand=10.0,
        shelf_life_days=20.0,
        moq=200.0,
        unit_cost=10.0,
    )
    assert lim.verdict == "moq_forces_spoilage"
    assert lim.constrained_q == 200.0
    assert lim.spoilage_risk_units == 50.0
    assert lim.spoilage_risk_usd == 500.0
    assert lim.forced_moq_penalty_usd == 500.0
    assert lim.generates_spoilage


def test_bound_order_quantity_unviable_turnover():
    # Demand = 2 u/day, Shelf life = 10 days -> Absorption = 20 units
    # Reorder point s = 25 units (> 20 absorption capacity).
    # Product cannot even sustain the reorder point without spoiling.
    lim = bound_order_quantity_by_expiry(
        unconstrained_q=50.0,
        reorder_point=25.0,
        daily_demand=2.0,
        shelf_life_days=10.0,
        unit_cost=10.0,
    )
    assert lim.verdict == "unviable_turnover_too_slow"
    assert lim.constrained_q == 0.0
    assert lim.max_absorbable_units == 0.0
    assert lim.spoilage_risk_units == 50.0
