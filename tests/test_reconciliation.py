"""Tests for inventory reconciliation / IRA / cycle-count plan (capability M6)."""

import pytest

from src.reconciliation import (
    CountResult,
    cycle_count_plan,
    inventory_record_accuracy,
    reconcile,
    total_variance_value,
)


def _records():
    return [
        {"product_id": "A", "system_qty": 100, "physical_qty": 98, "unit_cost": 5.0},
        {"product_id": "B", "system_qty": 50, "physical_qty": 50, "unit_cost": 2.0},
        {"product_id": "C", "system_qty": 200, "physical_qty": 150, "unit_cost": 1.0},
    ]


def test_reconcile_computes_variance():
    results = reconcile(_records())
    by_id = {r.product_id: r for r in results}
    assert isinstance(results[0], CountResult)
    assert by_id["A"].variance == pytest.approx(-2.0)
    assert by_id["A"].variance_pct == pytest.approx(-0.02)


def test_exact_match_is_within_tolerance_even_at_zero():
    results = reconcile(_records())
    by_id = {r.product_id: r for r in results}
    assert by_id["B"].within_tolerance is True


def test_tolerance_pct_widens_acceptance():
    strict = {r.product_id: r for r in reconcile(_records(), tolerance_pct=0.0)}
    loose = {r.product_id: r for r in reconcile(_records(), tolerance_pct=0.05)}
    assert strict["A"].within_tolerance is False   # 2% off, zero tol
    assert loose["A"].within_tolerance is True      # within 5%


def test_inventory_record_accuracy():
    # at zero tolerance: only B matches -> 1/3
    assert inventory_record_accuracy(reconcile(_records())) == pytest.approx(1 / 3)


def test_total_variance_value_is_absolute_dollar_impact():
    # |-2|*5 + 0*2 + |-50|*1 = 10 + 0 + 50 = 60
    assert total_variance_value(reconcile(_records())) == pytest.approx(60.0)


def test_cycle_count_plan_counts_a_more_often():
    plan = cycle_count_plan([
        {"product_id": "A", "abc": "A"},
        {"product_id": "C", "abc": "C"},
    ])
    by_id = {p["product_id"]: p for p in plan}
    assert by_id["A"]["counts_per_year"] > by_id["C"]["counts_per_year"]
