"""Guardrails for the simulation grid search.

The grid runs a full multi-thousand-period simulation per candidate, so an
over-fine grid (tiny step / wide radius) can silently grind. These tests pin the
fail-fast cap and input validation.
"""

from __future__ import annotations

import pytest

from src.simulation_opt import find_best_safety_stock, find_best_safety_stock_smart_start

_BASE = dict(
    mean_demand=20.0,
    std_demand=5.0,
    lead_time_periods=2,
    review_period=1,
    holding_cost_per_period=1.0,
    fixed_order_cost=50.0,
    backorder_cost=5.0,
)


def test_oversized_grid_fails_fast():
    # step 0.1 over radius 50 -> ~1000 points, far above the default cap.
    with pytest.raises(ValueError, match="cap|evaluat"):
        find_best_safety_stock(**_BASE, step_size=0.1, search_radius=50.0, periods=100)


def test_nonpositive_step_rejected():
    with pytest.raises(ValueError, match="step_size"):
        find_best_safety_stock(**_BASE, step_size=0.0, periods=100)


def test_grid_within_cap_runs():
    res = find_best_safety_stock(
        **_BASE, step_size=10.0, search_radius=40.0, periods=200, max_evaluations=200
    )
    assert res.total_cost >= 0.0


def test_smart_start_enforces_the_cap():
    with pytest.raises(ValueError, match="cap|evaluat"):
        find_best_safety_stock_smart_start(
            **_BASE, step_size=0.05, search_radius=50.0, periods=100, max_evaluations=50
        )
