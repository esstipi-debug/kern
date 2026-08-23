"""Tests for multi-criteria supplier selection (M8, plan §2.6).

BWM (Best-Worst Method) weights via scipy linprog + classic vector-normalized TOPSIS
ranking, both on base deps (numpy/scipy) - auditable, no optional libraries. The award
result maps to a protected, never-dead-end options outcome (Guided Execution Layer).
"""

import pytest
from nosilo_core.guided import OPTIONS, passed_guided, recommend

from src.mcdm import (
    BWMResult,
    Criterion,
    RankingResult,
    award_outcome,
    bwm_weights,
    topsis_rank,
)

# A perfectly consistent BWM judgement: best=C1, worst=C3, ratios multiply through.
_CRIT = ["C1", "C2", "C3"]
_BEST_TO_OTHERS = {"C1": 1.0, "C2": 2.0, "C3": 4.0}   # a_BW = 4
_OTHERS_TO_WORST = {"C1": 4.0, "C2": 2.0, "C3": 1.0}


def test_bwm_consistent_weights_are_exact():
    res = bwm_weights("C1", "C3", _BEST_TO_OTHERS, _OTHERS_TO_WORST, criteria=_CRIT)

    assert isinstance(res, BWMResult)
    assert res.weights["C1"] == pytest.approx(4 / 7, abs=1e-4)
    assert res.weights["C2"] == pytest.approx(2 / 7, abs=1e-4)
    assert res.weights["C3"] == pytest.approx(1 / 7, abs=1e-4)
    assert res.xi == pytest.approx(0.0, abs=1e-6)
    assert res.consistency_ratio == pytest.approx(0.0, abs=1e-6)


def test_bwm_weights_sum_to_one_and_best_dominates():
    res = bwm_weights("C1", "C3", _BEST_TO_OTHERS, _OTHERS_TO_WORST, criteria=_CRIT)

    assert sum(res.weights.values()) == pytest.approx(1.0)
    assert res.weights["C1"] == max(res.weights.values())


def test_bwm_inconsistent_judgement_has_positive_xi_and_cr():
    res = bwm_weights(
        "C1", "C3",
        {"C1": 1.0, "C2": 3.0, "C3": 8.0},
        {"C1": 8.0, "C2": 4.0, "C3": 1.0},  # 3*4=12 != 8 -> inconsistent
        criteria=_CRIT,
    )

    assert res.xi > 1e-6
    assert res.consistency_ratio > 0.0


def test_bwm_rejects_best_not_in_criteria():
    with pytest.raises(ValueError):
        bwm_weights("ZZ", "C3", _BEST_TO_OTHERS, _OTHERS_TO_WORST, criteria=_CRIT)


def test_bwm_rejects_incomplete_comparison_vectors():
    with pytest.raises(ValueError):
        bwm_weights("C1", "C3", {"C1": 1.0, "C2": 2.0}, _OTHERS_TO_WORST, criteria=_CRIT)


# --- TOPSIS --------------------------------------------------------------------------

_BENEFIT = [Criterion("q", benefit=True)]


def test_single_criterion_best_is_one_worst_is_zero():
    alts = {"A": {"q": 10.0}, "B": {"q": 5.0}}

    res = topsis_rank(alts, _BENEFIT, {"q": 1.0})

    assert isinstance(res, RankingResult)
    assert res.scores["A"] == pytest.approx(1.0)
    assert res.scores["B"] == pytest.approx(0.0)
    assert res.best == "A"


def test_dominating_alternative_wins():
    crit = [Criterion("quality", True), Criterion("service", True)]
    alts = {
        "Good": {"quality": 9.0, "service": 9.0},   # dominates on both
        "Mid": {"quality": 6.0, "service": 5.0},
        "Low": {"quality": 3.0, "service": 4.0},
    }

    res = topsis_rank(alts, crit, {"quality": 1.0, "service": 1.0})

    assert res.best == "Good"
    assert res.scores["Good"] == pytest.approx(1.0, abs=1e-9)
    assert res.ranking == ["Good", "Mid", "Low"]


def test_cost_criterion_prefers_lower():
    crit = [Criterion("price", benefit=False)]
    alts = {"Cheap": {"price": 100.0}, "Pricey": {"price": 200.0}}

    res = topsis_rank(alts, crit, {"price": 1.0})

    assert res.best == "Cheap"


def test_weights_change_the_winner():
    crit = [Criterion("quality", True), Criterion("price", benefit=False)]
    alts = {"HiQ": {"quality": 9.0, "price": 200.0}, "Cheap": {"quality": 5.0, "price": 80.0}}

    q_heavy = topsis_rank(alts, crit, {"quality": 5.0, "price": 1.0})
    p_heavy = topsis_rank(alts, crit, {"quality": 1.0, "price": 5.0})

    assert q_heavy.best == "HiQ"
    assert p_heavy.best == "Cheap"


def test_scores_in_unit_interval_and_ranking_is_descending():
    crit = [Criterion("a", True), Criterion("b", True)]
    alts = {"x": {"a": 7.0, "b": 3.0}, "y": {"a": 4.0, "b": 8.0}, "z": {"a": 6.0, "b": 6.0}}

    res = topsis_rank(alts, crit, {"a": 1.0, "b": 1.0})

    assert all(0.0 - 1e-9 <= s <= 1.0 + 1e-9 for s in res.scores.values())
    ordered = [res.scores[a] for a in res.ranking]
    assert ordered == sorted(ordered, reverse=True)


def test_topsis_missing_value_raises():
    with pytest.raises(KeyError):
        topsis_rank({"A": {"q": 1.0}, "B": {}}, _BENEFIT, {"q": 1.0})


def test_topsis_no_alternatives_raises():
    with pytest.raises(ValueError):
        topsis_rank({}, _BENEFIT, {"q": 1.0})


def test_topsis_non_positive_weights_raise():
    with pytest.raises(ValueError):
        topsis_rank({"A": {"q": 1.0}, "B": {"q": 2.0}}, _BENEFIT, {"q": 0.0})


# --- award outcome (guided) ----------------------------------------------------------


def test_award_outcome_is_protected_options_with_best_recommended():
    crit = [Criterion("quality", True)]
    alts = {"S1": {"quality": 9.0}, "S2": {"quality": 4.0}}
    res = topsis_rank(alts, crit, {"quality": 1.0})

    outcome = award_outcome(res, summary="Award the contract")

    assert outcome.status == OPTIONS
    assert passed_guided(outcome)
    assert len(outcome.options) == 2
    assert recommend(outcome.options).label == "S1"
    assert any("S1" in o.action for o in outcome.options)  # staged award action carried


def test_award_outcome_charges_for_a_criterion_that_was_never_measured():
    """`topsis_rank` only ever sees the criteria that survived, so the ones the data
    never carried have to be handed in -- and they have to cost. Passing only the ties
    is how the same award reported the same confidence on three criteria and on five."""
    crit = [Criterion("quality", True)]
    alts = {"S1": {"quality": 9.0}, "S2": {"quality": 4.0}}
    res = topsis_rank(alts, crit, {"quality": 1.0})

    clean = award_outcome(res, summary="Award")
    degraded = award_outcome(res, summary="Award", unmeasured=("otif", "lead_time"))

    assert not res.ignored_criteria, "control: no tie may be doing the work here"
    assert clean.confidence == 1.0
    assert degraded.confidence < clean.confidence
    assert passed_guided(degraded)


def test_award_outcome_stacks_the_two_coverage_holes():
    """A tie and an unmeasured criterion are different gaps; both count."""
    crit = [Criterion("quality", True), Criterion("flat", True)]
    alts = {"S1": {"quality": 9.0, "flat": 5.0}, "S2": {"quality": 4.0, "flat": 5.0}}
    res = topsis_rank(alts, crit, {"quality": 0.5, "flat": 0.5})

    assert res.ignored_criteria, "control: 'flat' must be dropped as a tie"
    solo_tie = award_outcome(res, summary="Award")
    both = award_outcome(res, summary="Award", unmeasured=("otif",))

    assert both.confidence < solo_tie.confidence < 1.0
