"""Tests for the decision-options engine (Guided Execution Layer, plan §2.14).

The scenario engine that sits over the analytic engines: takes N executable scenarios
(e.g. 3 reorder plans, 3 supplier awards) with competing metrics, scores their
trade-offs multi-objectively, and emits a protected `as_options` outcome with the
recommended default. Pure - no external deps.
"""

import pytest

from src.decision_options import Objective, Scenario, decide, rank, weighted_scores
from src.guided import OPTIONS, passed_guided, recommend

# investment (minimize) vs service level (maximize): A cheap/low, B balanced, C rich/high.
_A = Scenario("A", "lean reorder", {"investment": 100.0, "service": 0.90}, action="stage:A")
_B = Scenario("B", "balanced reorder", {"investment": 150.0, "service": 0.95}, action="stage:B")
_C = Scenario("C", "rich reorder", {"investment": 200.0, "service": 0.99}, action="stage:C")
_COST_MIN = Objective("investment", weight=1.0, maximize=False)
_SERVICE_MAX = Objective("service", weight=1.0, maximize=True)


def test_balanced_scenario_wins_with_equal_weights():
    options = rank([_A, _B, _C], [_COST_MIN, _SERVICE_MAX])

    assert options[0].label == "B"          # best trade-off
    assert options[0].recommended
    assert [o.label for o in options[1:]] == ["A", "C"] or [o.label for o in options[1:]] == ["C", "A"]


def test_weighting_service_higher_picks_the_rich_plan():
    options = rank([_A, _B, _C], [_COST_MIN, Objective("service", weight=4.0, maximize=True)])

    assert options[0].label == "C"
    assert recommend(options).label == "C"


def test_single_minimize_objective_picks_lowest():
    options = rank([_A, _B, _C], [_COST_MIN])

    assert options[0].label == "A"          # cheapest


def test_constant_metric_contributes_no_signal():
    # same investment -> the service objective alone decides.
    s1 = Scenario("s1", "", {"investment": 100.0, "service": 0.90})
    s2 = Scenario("s2", "", {"investment": 100.0, "service": 0.95})

    options = rank([s1, s2], [_COST_MIN, _SERVICE_MAX])

    assert options[0].label == "s2"


def test_scores_are_descending_and_carry_action_and_tradeoffs():
    sc = Scenario("X", "x", {"investment": 100.0}, action="stage:X", tradeoffs="cheap but risky")
    options = rank([sc, _B], [_COST_MIN])

    scores = [o.score for o in options]
    assert scores == sorted(scores, reverse=True)
    top = next(o for o in options if o.label == "X")
    assert top.action == "stage:X"
    assert top.tradeoffs == "cheap but risky"


def test_missing_metric_raises():
    bad = Scenario("bad", "", {"service": 0.9})  # no 'investment'
    with pytest.raises(KeyError):
        rank([bad, _B], [_COST_MIN])


def test_decide_returns_protected_options_outcome():
    outcome = decide("Choose a reorder plan", [_A, _B, _C], [_COST_MIN, _SERVICE_MAX])

    assert outcome.status == OPTIONS
    assert passed_guided(outcome)
    assert sum(1 for o in outcome.options if o.recommended) == 1
    assert recommend(outcome.options).label == "B"


def test_decide_with_no_scenarios_raises():
    with pytest.raises(ValueError):
        decide("nothing to decide", [], [_COST_MIN])


def test_weighted_scores_normalize_to_unit_contributions():
    scores = weighted_scores([_A, _C], [_COST_MIN, _SERVICE_MAX])

    # Two scenarios, two opposed objectives weight 1: each endpoint scores 1.0
    # (wins one objective 1.0, loses the other 0.0).
    assert scores == pytest.approx([1.0, 1.0])
