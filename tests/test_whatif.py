"""Tests for the what-if / sensitivity engine (efficacy roadmap #4).

Where ``decision_options`` ranks a fixed set of executable plans, this engine asks the
orthogonal question: *how do the outputs move when the assumptions move?* One-way
sensitivity (tornado), best/worst corner bundles, and break-even thresholds over any
pure ``inputs -> outputs`` model. Pure - no external deps.
"""

import math

import pytest

from src.whatif import (
    Driver,
    ScenarioCase,
    break_even,
    evaluate_cases,
    one_way,
    optimistic_case,
    pessimistic_case,
    tornado,
)


def _inventory_model(inp: dict) -> dict:
    """Toy (R,S)-flavoured model: cost rises with demand and holding; fill rate falls."""
    demand = inp["demand"]
    lead_time = inp["lead_time"]
    holding = inp["holding"]
    safety_stock = 0.5 * demand * (lead_time ** 0.5)
    reorder_point = demand * lead_time + safety_stock
    total_cost = holding * (safety_stock + demand)
    fill_rate = 1.0 / (1.0 + 0.001 * demand)  # strictly decreasing in demand, in (0, 1)
    return {
        "total_cost": total_cost,
        "reorder_point": reorder_point,
        "safety_stock": safety_stock,
        "fill_rate": fill_rate,
    }


_BASE = {"demand": 100.0, "lead_time": 4.0, "holding": 2.0}
_DEMAND = Driver("demand", base=100.0, low=80.0, high=120.0, unit="u/wk")
_LEAD_TIME = Driver("lead_time", base=4.0, low=2.0, high=6.0, unit="wk")
_HOLDING = Driver("holding", base=2.0, low=1.0, high=3.0, unit="$/u")


# ── one-way sensitivity ───────────────────────────────────────────────────────

def test_one_way_reports_base_low_high_outputs_and_swing():
    ow = one_way(_inventory_model, _BASE, _DEMAND, "total_cost")

    # cost = holding*(0.5*demand*sqrt(lt) + demand); holding=2, lt=4 -> cost = 4*demand
    assert ow.base_output == pytest.approx(400.0)
    assert ow.low_output == pytest.approx(320.0)
    assert ow.high_output == pytest.approx(480.0)
    assert ow.swing == pytest.approx(160.0)
    assert ow.low_delta == pytest.approx(-80.0)
    assert ow.high_delta == pytest.approx(80.0)


def test_one_way_unknown_metric_raises():
    with pytest.raises(KeyError):
        one_way(_inventory_model, _BASE, _DEMAND, "nonexistent")


def test_one_way_does_not_mutate_base_inputs():
    snapshot = dict(_BASE)
    one_way(_inventory_model, _BASE, _DEMAND, "total_cost")
    assert _BASE == snapshot


# ── tornado (ranked one-way sweep) ────────────────────────────────────────────

def test_tornado_orders_drivers_by_swing_descending():
    rows = tornado(_inventory_model, _BASE, [_DEMAND, _LEAD_TIME, _HOLDING], "total_cost")

    # holding swing = |600-200| = 400 (biggest), demand = 160, lead_time ~ 103.5
    assert [r.driver for r in rows] == ["holding", "demand", "lead_time"]
    swings = [r.swing for r in rows]
    assert swings == sorted(swings, reverse=True)
    assert rows[0].swing == pytest.approx(400.0)


def test_tornado_with_no_drivers_is_empty():
    assert tornado(_inventory_model, _BASE, [], "total_cost") == []


# ── scenario cases (named override bundles) ───────────────────────────────────

def test_evaluate_cases_merges_overrides_onto_base():
    cases = [ScenarioCase("spike", {"demand": 200.0}), ScenarioCase("flat", {})]

    results = evaluate_cases(_inventory_model, _BASE, cases)

    spike = next(r for r in results if r.label == "spike")
    assert spike.inputs["demand"] == 200.0
    assert spike.outputs["total_cost"] == pytest.approx(800.0)  # 4*200
    # untouched driver carried from base
    assert spike.inputs["holding"] == 2.0
    flat = next(r for r in results if r.label == "flat")
    assert flat.outputs["total_cost"] == pytest.approx(400.0)


def test_evaluate_cases_does_not_mutate_base_inputs():
    snapshot = dict(_BASE)
    evaluate_cases(_inventory_model, _BASE, [ScenarioCase("spike", {"demand": 999.0})])
    assert _BASE == snapshot


# ── optimistic / pessimistic corner bundles ───────────────────────────────────

def test_optimistic_minimizes_a_cost_metric_pessimistic_maximizes_it():
    drivers = [_DEMAND, _LEAD_TIME, _HOLDING]
    opt = optimistic_case(_inventory_model, _BASE, drivers, "total_cost")
    pes = pessimistic_case(_inventory_model, _BASE, drivers, "total_cost")

    # favourable-for-low-cost endpoints: every driver at its low
    assert opt.inputs["demand"] == 80.0
    assert opt.inputs["holding"] == 1.0
    assert opt.inputs["lead_time"] == 2.0
    assert pes.inputs["demand"] == 120.0
    assert pes.inputs["holding"] == 3.0
    assert opt.outputs["total_cost"] < 400.0 < pes.outputs["total_cost"]


def test_optimistic_respects_a_maximize_metric():
    # fill_rate is maximised and only demand moves it -> low demand is best.
    opt = optimistic_case(
        _inventory_model, _BASE, [_DEMAND, _LEAD_TIME, _HOLDING], "fill_rate", maximize=True
    )

    assert opt.inputs["demand"] == 80.0
    # drivers that do not move the metric stay at base (not perturbed arbitrarily)
    assert opt.inputs["holding"] == 2.0
    assert opt.inputs["lead_time"] == 4.0
    assert opt.outputs["fill_rate"] == pytest.approx(1.0 / (1.0 + 0.001 * 80.0))


# ── break-even threshold ──────────────────────────────────────────────────────

def test_break_even_finds_input_value_hitting_target():
    be = break_even(_inventory_model, _BASE, _DEMAND, "total_cost", target=440.0)

    assert be.found
    assert be.value == pytest.approx(110.0, abs=1e-3)  # cost = 4*demand -> 110
    hit = _inventory_model({**_BASE, "demand": be.value})
    assert hit["total_cost"] == pytest.approx(440.0, abs=1e-2)


def test_break_even_not_found_when_target_outside_range():
    be = break_even(_inventory_model, _BASE, _DEMAND, "total_cost", target=10_000.0)

    assert not be.found
    assert be.value is None
    assert be.bracket == (80.0, 120.0)


# ── driver validation ─────────────────────────────────────────────────────────

def test_driver_rejects_inverted_bounds():
    with pytest.raises(ValueError):
        Driver("bad", base=5.0, low=10.0, high=2.0)


def test_break_even_handles_decreasing_metric():
    # fill_rate decreases in demand; target between endpoints is still bracketed.
    lo = _inventory_model({**_BASE, "demand": 80.0})["fill_rate"]
    hi = _inventory_model({**_BASE, "demand": 120.0})["fill_rate"]
    target = (lo + hi) / 2.0

    be = break_even(_inventory_model, _BASE, _DEMAND, "fill_rate", target=target)

    assert be.found
    assert 80.0 < be.value < 120.0
    assert _inventory_model({**_BASE, "demand": be.value})["fill_rate"] == pytest.approx(
        target, abs=1e-4
    )
    assert not math.isnan(be.value)
