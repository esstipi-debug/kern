"""Tests for multi-sourcing LP allocation in src/mcdm.py."""

import pytest

from src.mcdm import MultiSourcingPlan, SupplierAllocation, solve_multi_sourcing


def test_dual_sourcing_split_70_30():
    # Demand = 1000 units
    # Supplier A: $10/unit, topsis score 0.90
    # Supplier B: $12/unit, topsis score 0.80
    # Max share = 70%
    plan = solve_multi_sourcing(
        demand=1000.0,
        suppliers=["A", "B"],
        unit_costs={"A": 10.0, "B": 12.0},
        topsis_scores={"A": 0.90, "B": 0.80},
        max_share_pct=0.70,
    )

    assert plan.total_units == 1000.0
    assert len(plan.allocations) == 2

    alloc_a = next(a for a in plan.allocations if a.supplier == "A")
    alloc_b = next(a for a in plan.allocations if a.supplier == "B")

    # A gets capped at 70% (700 units)
    assert alloc_a.units == pytest.approx(700.0)
    assert alloc_a.share_pct == pytest.approx(0.70)
    # B gets remaining 300 units (30%)
    assert alloc_b.units == pytest.approx(300.0)
    assert alloc_b.share_pct == pytest.approx(0.30)

    # Total cost: 700 * 10 + 300 * 12 = 7000 + 3600 = 10600
    assert plan.total_cost == pytest.approx(10600.0)
    # Single source cost (100% to A): 1000 * 10 = 10000
    assert plan.single_source_cost == pytest.approx(10000.0)
    # Diversification premium: 10600 - 10000 = $600 (+6%)
    assert plan.diversification_premium_usd == pytest.approx(600.0)
    assert plan.diversification_premium_pct == pytest.approx(0.06)


def test_capacity_constraints_bind_allocation():
    # Demand = 1000 units
    # Supplier A is cheaper ($8/u) but capacity is only 400
    # Supplier B is $10/u with capacity 800
    plan = solve_multi_sourcing(
        demand=1000.0,
        suppliers=["A", "B"],
        unit_costs={"A": 8.0, "B": 10.0},
        topsis_scores={"A": 0.85, "B": 0.75},
        capacities={"A": 400.0, "B": 800.0},
        max_share_pct=0.90,
    )

    alloc_a = next(a for a in plan.allocations if a.supplier == "A")
    alloc_b = next(a for a in plan.allocations if a.supplier == "B")

    assert alloc_a.units == pytest.approx(400.0)
    assert alloc_b.units == pytest.approx(600.0)


def test_infeasible_allocation_raises():
    # 2 suppliers with max_share 40% each -> max total is 80% < 100% demand
    with pytest.raises(ValueError, match="infeasible allocation"):
        solve_multi_sourcing(
            demand=1000.0,
            suppliers=["A", "B"],
            unit_costs={"A": 10.0, "B": 12.0},
            topsis_scores={"A": 0.90, "B": 0.80},
            max_share_pct=0.40,
        )


def test_tradeoff_lambda_favors_quality_over_cheap_cost():
    # Supplier A: $10/unit, topsis score 0.50 (cheap but low quality)
    # Supplier B: $11/unit, topsis score 0.95 (slightly higher price, much higher quality)
    # With lambda = 5.0:
    # Effective cost A: 10 - 5 * 0.50 = 7.5
    # Effective cost B: 11 - 5 * 0.95 = 6.25  <-- B is now more attractive!
    plan = solve_multi_sourcing(
        demand=1000.0,
        suppliers=["A", "B"],
        unit_costs={"A": 10.0, "B": 11.0},
        topsis_scores={"A": 0.50, "B": 0.95},
        max_share_pct=0.70,
        tradeoff_lambda=5.0,
    )

    alloc_b = next(a for a in plan.allocations if a.supplier == "B")
    # B gets the primary 70% allocation because its high score beats the $1 price difference
    assert alloc_b.units == pytest.approx(700.0)
    assert alloc_b.share_pct == pytest.approx(0.70)
