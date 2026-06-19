"""Tests for multi-echelon GSM — Vandeput (2020), Chapter 10."""

import pytest

from src.multi_echelon import (
    evaluate_serial_allocation,
    optimize_serial_gsm,
    serial_gsm_cases,
)


def test_serial_gsm_four_cases():
    cases = serial_gsm_cases([4, 3, 2], review_period=1.0)
    assert len(cases) == 4
    assert (4, 3, 3) in cases
    assert (0, 7, 3) in cases
    assert (0, 0, 10) in cases


def test_gsm_optimal_allocation_section_10_4():
    """Case [4,0,6] minimizes holding cost (~485)."""
    lead_times = [4, 3, 2]
    holding = [1, 2, 4]
    best = optimize_serial_gsm(
        lead_times=lead_times,
        mean_demand_per_period=100,
        demand_std_per_period=25,
        holding_costs=holding,
        cycle_service_level=0.95,
        review_period=1.0,
    )
    assert best.risk_periods == (4, 0, 6)
    assert best.total_holding_cost == pytest.approx(485, abs=15)


def test_gsm_case4_all_downstream_cost():
    """All SS at demand node: cost ~520."""
    lead_times = [4, 3, 2]
    holding = [1, 2, 4]
    case4 = evaluate_serial_allocation(
        (0, 0, 10),
        lead_times,
        100,
        25,
        holding,
        0.95,
        1.0,
        case_id=4,
    )
    assert case4.total_holding_cost == pytest.approx(520, abs=15)


def test_gsm_case1_higher_cost_than_optimal():
    lead_times = [4, 3, 2]
    holding = [1, 2, 4]
    case1 = evaluate_serial_allocation(
        (4, 3, 3),
        lead_times,
        100,
        25,
        holding,
        0.95,
        1.0,
        case_id=1,
    )
    optimal = evaluate_serial_allocation(
        (4, 0, 6),
        lead_times,
        100,
        25,
        holding,
        0.95,
        1.0,
        case_id=3,
    )
    assert case1.total_holding_cost > optimal.total_holding_cost


def test_gsm_simulation_runs():
    from src.multi_echelon import optimize_serial_gsm, simulate_serial_gsm

    alloc = optimize_serial_gsm([4, 3, 2], 100, 25, [1, 2, 4], 0.95, 1.0)
    result = simulate_serial_gsm(alloc, [4, 3, 2], periods=2000, seed=1)
    assert 0 <= result.fill_rate <= 1
    assert len(result.mean_echelon_inventory) == 3


def test_gsm_backorders_improve_fill_rate():
    from src.multi_echelon import optimize_serial_gsm, simulate_serial_gsm

    alloc = optimize_serial_gsm([4, 3, 2], 100, 25, [1, 2, 4], 0.95, 1.0)
    with_bo = simulate_serial_gsm(alloc, [4, 3, 2], periods=3000, seed=5, backorders=True)
    lost = simulate_serial_gsm(alloc, [4, 3, 2], periods=3000, seed=5, backorders=False)
    assert with_bo.fill_rate >= lost.fill_rate


