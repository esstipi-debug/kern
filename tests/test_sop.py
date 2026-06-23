"""Tests for the S&OP / IBP cadence engine (capability gap #2).

The monthly demand -> supply -> reconciliation -> exec workflow. Given a demand
plan over a horizon, it projects the inventory balance under the three classic
aggregate-planning strategies (chase / level / hybrid), evaluates each one's cost
and service trade-offs, and (via the decision-options engine) emits a protected,
ranked set of executable option-packages with a recommended default.

Math hand-checked here so the engine stays auditable for the QA gate. Reference:
Chopra & Meindl, *Supply Chain Management*, "Sales & Operations Planning / Aggregate
Planning"; Heizer & Render, "Aggregate Planning and S&OP"; Oliver Wight 5-step cadence.
"""

import pytest

from src.decision_options import Objective
from src.guided import OPTIONS, OWNER_HUMAN, passed_guided, recommend
from src.sop import (
    DEFAULT_OBJECTIVES,
    CostModel,
    SopReview,
    build_scenarios,
    chase_plan,
    hybrid_plan,
    level_plan,
    project_plan,
    run_sop_cycle,
)

# Reference horizon used across the math checks (opening 50, per-period target 20).
_DEMAND = [100.0, 120.0, 80.0, 100.0]
_OPENING = 50.0
_TARGET = 20.0


# -- strategy generators ------------------------------------------------------


def test_chase_plan_produces_to_hold_inventory_at_target():
    # Chase tracks demand so closing inventory equals the target every period.
    plan = chase_plan(_DEMAND, opening_inventory=_OPENING, target=_TARGET)

    assert plan == pytest.approx([70.0, 120.0, 80.0, 100.0])


def test_level_plan_is_a_constant_rate_that_lands_at_target():
    # Constant rate = (total demand + ending target - opening) / horizon.
    plan = level_plan(_DEMAND, opening_inventory=_OPENING, target=_TARGET)

    assert plan == pytest.approx([92.5, 92.5, 92.5, 92.5])


def test_chase_and_level_produce_the_same_total_over_the_horizon():
    # Both must land on the same ending inventory, so total production is equal
    # (and the per-unit production cost therefore carries no ranking signal).
    chase = chase_plan(_DEMAND, opening_inventory=_OPENING, target=_TARGET)
    level = level_plan(_DEMAND, opening_inventory=_OPENING, target=_TARGET)

    assert sum(chase) == pytest.approx(sum(level)) == pytest.approx(370.0)


def test_hybrid_plan_is_the_blend_of_level_and_chase():
    chase = chase_plan(_DEMAND, opening_inventory=_OPENING, target=_TARGET)
    level = level_plan(_DEMAND, opening_inventory=_OPENING, target=_TARGET)
    hybrid = hybrid_plan(_DEMAND, opening_inventory=_OPENING, target=_TARGET, mix=0.5)

    expected = [(c + lv) / 2 for c, lv in zip(chase, level)]
    assert hybrid == pytest.approx(expected)


def test_hybrid_mix_one_equals_chase_and_mix_zero_equals_level():
    chase = chase_plan(_DEMAND, opening_inventory=_OPENING, target=_TARGET)
    level = level_plan(_DEMAND, opening_inventory=_OPENING, target=_TARGET)

    assert hybrid_plan(_DEMAND, opening_inventory=_OPENING, target=_TARGET, mix=1.0) == pytest.approx(chase)
    assert hybrid_plan(_DEMAND, opening_inventory=_OPENING, target=_TARGET, mix=0.0) == pytest.approx(level)


# -- balance projection -------------------------------------------------------


def test_project_plan_tracks_the_inventory_balance():
    level = level_plan(_DEMAND, opening_inventory=_OPENING, target=_TARGET)
    ev = project_plan(_DEMAND, level, opening_inventory=_OPENING, name="Level")

    closings = [p.closing_inventory for p in ev.periods]
    assert closings == pytest.approx([42.5, 15.0, 27.5, 20.0])
    assert ev.periods[-1].closing_inventory == pytest.approx(_TARGET)


def test_project_plan_reports_peak_and_average_on_hand():
    level = level_plan(_DEMAND, opening_inventory=_OPENING, target=_TARGET)
    ev = project_plan(_DEMAND, level, opening_inventory=_OPENING, name="Level")

    assert ev.peak_inventory == pytest.approx(42.5)
    assert ev.average_inventory == pytest.approx(26.25)  # (42.5+15+27.5+20)/4


def test_chase_keeps_inventory_flat_at_target_with_full_service():
    chase = chase_plan(_DEMAND, opening_inventory=_OPENING, target=_TARGET)
    ev = project_plan(_DEMAND, chase, opening_inventory=_OPENING, name="Chase")

    assert ev.peak_inventory == pytest.approx(_TARGET)
    assert ev.total_shortfall == pytest.approx(0.0)
    assert ev.fill_rate == pytest.approx(1.0)


def test_shortfall_is_recorded_when_supply_lags_demand():
    # Level rate 100/period but front-loaded demand -> a 50-unit shortfall in M1.
    demand = [150.0, 50.0]
    plan = level_plan(demand, opening_inventory=0.0, target=0.0)  # constant 100
    ev = project_plan(demand, plan, opening_inventory=0.0, name="Level")

    assert plan == pytest.approx([100.0, 100.0])
    assert ev.periods[0].shortfall == pytest.approx(50.0)
    assert ev.periods[0].on_hand == pytest.approx(0.0)        # never negative on-hand
    assert ev.total_shortfall == pytest.approx(50.0)
    assert ev.fill_rate == pytest.approx(0.75)               # 1 - 50/200


# -- cost evaluation ----------------------------------------------------------


def test_cost_components_are_summed_from_the_model():
    cost = CostModel(
        holding_per_unit_per_period=1.0,
        shortage_per_unit_per_period=5.0,
        capacity_change_per_unit=2.0,
        production_per_unit=0.0,
    )
    level = level_plan(_DEMAND, opening_inventory=_OPENING, target=_TARGET)
    ev = project_plan(_DEMAND, level, opening_inventory=_OPENING, name="Level", cost=cost)

    # Holding = 1 * sum(on_hand) = 42.5+15+27.5+20 = 105; no shortage; level => no flex.
    assert ev.holding_cost == pytest.approx(105.0)
    assert ev.shortage_cost == pytest.approx(0.0)
    assert ev.capacity_change_cost == pytest.approx(0.0)
    assert ev.total_cost == pytest.approx(105.0)


def test_capacity_change_cost_penalizes_a_chase_plan():
    cost = CostModel(capacity_change_per_unit=2.0, holding_per_unit_per_period=1.0)
    chase = chase_plan(_DEMAND, opening_inventory=_OPENING, target=_TARGET)
    ev = project_plan(_DEMAND, chase, opening_inventory=_OPENING, name="Chase", cost=cost)

    # |120-70| + |80-120| + |100-80| = 50 + 40 + 20 = 110 units of capacity change.
    assert ev.capacity_changes == pytest.approx(110.0)
    assert ev.capacity_change_cost == pytest.approx(220.0)


# -- validation ---------------------------------------------------------------


def test_project_plan_rejects_mismatched_lengths():
    with pytest.raises(ValueError):
        project_plan([100.0, 120.0], [100.0], opening_inventory=0.0)


def test_empty_demand_is_rejected():
    with pytest.raises(ValueError):
        level_plan([], opening_inventory=0.0, target=0.0)


# -- scenario assembly --------------------------------------------------------


def test_build_scenarios_returns_the_three_named_strategies():
    evals = build_scenarios(_DEMAND, opening_inventory=_OPENING, target=_TARGET)

    assert set(evals) == {"Chase", "Level", "Hybrid"}
    assert evals["Chase"].fill_rate == pytest.approx(1.0)
    assert evals["Level"].peak_inventory >= evals["Chase"].peak_inventory


# -- the full cadence: ranked, protected option-package -----------------------


def test_run_sop_cycle_emits_a_protected_options_outcome():
    review = run_sop_cycle(_DEMAND, opening_inventory=_OPENING, target=_TARGET, confidence=0.8)

    assert isinstance(review, SopReview)
    assert review.outcome.status == OPTIONS
    assert passed_guided(review.outcome)                      # never a dead end
    assert review.outcome.confidence == pytest.approx(0.8)
    assert sum(1 for o in review.outcome.options if o.recommended) == 1


def test_run_sop_cycle_offers_all_three_strategies_as_options():
    review = run_sop_cycle(_DEMAND, opening_inventory=_OPENING, target=_TARGET)

    assert {o.label for o in review.outcome.options} == {"Chase", "Level", "Hybrid"}


def test_recommended_plan_matches_the_top_ranked_option():
    review = run_sop_cycle(_DEMAND, opening_inventory=_OPENING, target=_TARGET)

    assert review.recommended.name == recommend(review.outcome.options).label
    assert review.recommended.name in {"Chase", "Level", "Hybrid"}


def test_weighting_service_picks_the_full_service_plan_under_shortage_risk():
    # Front-loaded demand: a level/hybrid plan stocks out early, only chase serves it all.
    demand = [200.0, 40.0, 40.0, 40.0]
    review = run_sop_cycle(
        demand,
        opening_inventory=0.0,
        target=0.0,
        objectives=[Objective("fill_rate", weight=5.0, maximize=True)],
    )

    assert review.recommended.name == "Chase"
    assert review.recommended.fill_rate == pytest.approx(1.0)


def test_cycle_carries_a_human_residual_so_the_contract_holds():
    review = run_sop_cycle(_DEMAND, opening_inventory=_OPENING, target=_TARGET)

    assert review.outcome.residuals, "S&OP must flag the exec sign-off residual"
    res = review.outcome.residuals[0]
    assert res.owner == OWNER_HUMAN
    assert res.risk_if_skipped.strip()                        # required by verify_guided


def test_default_objectives_balance_cost_inventory_and_service():
    metrics = {o.metric for o in DEFAULT_OBJECTIVES}

    assert metrics == {"total_cost", "peak_inventory", "fill_rate"}
