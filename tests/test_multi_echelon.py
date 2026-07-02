"""Tests for multi-echelon GSM — Vandeput (2020), Chapter 10."""

import pytest

from src.multi_echelon import (
    EchelonNode,
    GSMAllocation,
    evaluate_serial_allocation,
    optimize_serial_gsm,
    serial_gsm_cases,
    simulate_serial_gsm,
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
    assert result.fill_rate > 0.9  # a correctly-sized 95%-CSL allocation should serve well


def test_gsm_backorders_improve_fill_rate():
    from src.multi_echelon import optimize_serial_gsm, simulate_serial_gsm

    alloc = optimize_serial_gsm([4, 3, 2], 100, 25, [1, 2, 4], 0.95, 1.0)
    with_bo = simulate_serial_gsm(alloc, [4, 3, 2], periods=3000, seed=5, backorders=True)
    lost = simulate_serial_gsm(alloc, [4, 3, 2], periods=3000, seed=5, backorders=False)
    assert with_bo.fill_rate >= lost.fill_rate


# -- material actually has to flow between echelons ---------------------------


def _two_node_alloc(*, node0_target: float) -> tuple[GSMAllocation, list[int]]:
    """A 2-node chain: node 0 (upstream, 20-period lead time) feeds node 1
    (customer-facing, 2-period lead time, sized normally for ~100 units/period
    demand). ``node0_target`` controls whether node 0 can actually keep up."""
    lead_times = [20, 2]
    nodes = (
        EchelonNode(index=0, lead_time=20, holding_cost=1.0, risk_period=1.0,
                    safety_stock=0.0, order_up_to=node0_target),
        EchelonNode(index=1, lead_time=2, holding_cost=4.0, risk_period=3.0,
                    safety_stock=200.0, order_up_to=500.0),
    )
    alloc = GSMAllocation(
        case_id=0, risk_periods=(1.0, 3.0), nodes=nodes, total_holding_cost=0.0,
        echelon_order_up_to=(node0_target + 500.0, 500.0),
    )
    return alloc, lead_times


def test_starved_upstream_node_now_causes_downstream_stockouts():
    """Regression: the simulation used to treat every node's pipeline as fed by
    an infinite source - on_hand[i-1] was incremented but never decremented or
    even read when shipping to node i, so an upstream shortage could never
    reach (let alone stock out) a downstream node. Give the upstream node a
    target (50) far below what sustaining ~100 units/period downstream
    requires: the chain must now visibly fail."""
    alloc, lead_times = _two_node_alloc(node0_target=50.0)

    result = simulate_serial_gsm(alloc, lead_times, periods=500, mean_demand=100.0, std_demand=15.0, seed=3)

    assert result.stockout_periods > 100  # far from the rare, well-provisioned case
    assert result.fill_rate < 0.99


def test_healthy_upstream_node_keeps_the_same_downstream_target_fully_served():
    """Same downstream node, same demand and seed - only node 0's own target
    changes, now large enough to keep pace. Isolates that the previous test's
    failure comes from the upstream/downstream coupling, not from node 1 alone."""
    alloc, lead_times = _two_node_alloc(node0_target=3000.0)

    result = simulate_serial_gsm(alloc, lead_times, periods=500, mean_demand=100.0, std_demand=15.0, seed=3)

    assert result.stockout_periods == 0
    assert result.fill_rate == pytest.approx(1.0)


