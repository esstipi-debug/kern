"""Edge-case / branch coverage for src.safety_stock (validation + helper functions)."""
from __future__ import annotations

import pytest

from src.safety_stock import (
    cycle_service_level_from_inventory,
    inventory_for_service_level,
    safety_stock,
    service_level_factor,
)


@pytest.mark.parametrize("bad", [0.0, 1.0, -0.1, 1.5])
def test_service_level_factor_rejects_out_of_range(bad):
    with pytest.raises(ValueError):
        service_level_factor(bad)


def test_safety_stock_rejects_negative_std():
    with pytest.raises(ValueError):
        safety_stock(-1.0, 0.95)


def test_safety_stock_rejects_nonpositive_risk_periods():
    with pytest.raises(ValueError):
        safety_stock(10.0, 0.95, risk_periods=0.0)


def test_inventory_for_service_level_at_median_equals_mean():
    # z(0.5) == 0, so inventory target collapses to the mean demand.
    assert abs(inventory_for_service_level(100.0, 20.0, 0.5) - 100.0) < 1e-6


def test_inventory_for_service_level_adds_buffer_above_median():
    assert inventory_for_service_level(100.0, 20.0, 0.95) > 100.0


def test_cycle_service_level_from_inventory_zero_std_is_step():
    assert cycle_service_level_from_inventory(100.0, 50.0, 0.0) == 1.0
    assert cycle_service_level_from_inventory(40.0, 50.0, 0.0) == 0.0


def test_cycle_service_level_from_inventory_at_mean_is_half():
    assert abs(cycle_service_level_from_inventory(50.0, 50.0, 10.0) - 0.5) < 1e-6
