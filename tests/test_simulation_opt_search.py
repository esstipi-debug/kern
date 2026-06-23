"""Coverage for the simulation-based (R,S) optimizers in src.simulation_opt.

Uses short horizons (periods small) and tight bounds/grids to stay fast while
exercising the default-start branch and the two scipy/grid search functions.
"""
from __future__ import annotations

from src.simulation_opt import (
    SimulationCostResult,
    find_best_safety_stock,
    optimize_rs_simulation,
    optimize_rs_simulation_grid,
)

_BASE = dict(
    mean_demand=100.0,
    std_demand=20.0,
    lead_time_periods=2,
    review_period=1,
    holding_cost_per_period=0.2,
    fixed_order_cost=50.0,
    backorder_cost=5.0,
)


def test_find_best_safety_stock_uses_default_start_when_none():
    # start_ss left as None exercises the analytical default-start branch.
    res = find_best_safety_stock(**_BASE, step_size=20.0, search_radius=40.0, periods=300, seed=1)
    assert isinstance(res, SimulationCostResult)
    assert res.total_cost >= 0
    assert res.safety_stock >= 0


def test_optimize_rs_simulation_returns_result():
    res = optimize_rs_simulation(**_BASE, bounds_ss=(0.0, 80.0), periods=300, seed=1)
    assert isinstance(res, SimulationCostResult)
    assert res.total_cost >= 0


def test_optimize_rs_simulation_grid_searches_review_periods():
    grid_base = {k: v for k, v in _BASE.items() if k != "review_period"}
    best, best_r, best_ss = optimize_rs_simulation_grid(
        **grid_base, review_periods=[1, 2], bounds_ss=(0.0, 80.0), periods=300, seed=1
    )
    assert isinstance(best, SimulationCostResult)
    assert best_r in (1, 2)
    assert best_ss >= 0
