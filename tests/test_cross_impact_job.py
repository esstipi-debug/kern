"""prepare()'s validation contract (spec sec 5.1). Every gap is fatal."""

import csv
import math
from dataclasses import replace

import pandas as pd
import pytest
from nosilo_core.guided import OPTIONS, ExecutionOption, GuidedOutcome, verify_guided

from jobs import cross_impact_job
from src.hats import HAT_COMPRADOR, HAT_KEYS, Candidate
from src.objection import NO_EVALUABLE, OBJETA, Verdict


@pytest.fixture
def demand_csv(tmp_path):
    path = tmp_path / "demanda.csv"
    pd.DataFrame({
        "product_id": ["SKU-1"] * 30 + ["SKU-2"] * 30,
        "quantity": ([100, 110, 95, 105, 120] * 6)[:30] * 2,
        "unit_cost": [10.0] * 60,
        "lead_time_days": [14.0] * 60,
    }).to_csv(path, index=False)
    return str(path)


def _params(**over):
    base = {
        "sku": "SKU-1",
        "current": {"q": 200.0, "sl": 0.95},
        "proposed": {"q": 400.0, "sl": 0.95},
        "proposed_by": HAT_COMPRADOR,
        "objection_thresholds": {"comprador": 0.2, "planner": 5_000.0,
                                 "cfo": 3_000.0, "comercial": 0.03},
    }
    base.update(over)
    return base


def test_happy_path_builds_the_payload(demand_csv):
    p = cross_impact_job.prepare(demand_csv, _params())
    assert p["inputs"].sku == "SKU-1"
    assert p["current"] == Candidate(200.0, 0.95)
    assert p["proposed"] == Candidate(400.0, 0.95)
    assert p["proposed_by"] == HAT_COMPRADOR
    assert p["thresholds"]["cfo"] == 3_000.0


@pytest.mark.parametrize("missing", ["sku", "current", "proposed", "proposed_by"])
def test_each_required_param_is_fatal_and_named(demand_csv, missing):
    params = _params()
    del params[missing]
    with pytest.raises(ValueError, match=missing):
        cross_impact_job.prepare(demand_csv, params)


def test_missing_current_does_not_fall_back_to_baseline(demand_csv):
    params = _params()
    del params["current"]
    with pytest.raises(ValueError) as exc:
        cross_impact_job.prepare(demand_csv, params)
    msg = str(exc.value).lower()
    assert "baseline" not in msg          # never silently assumes the theoretical (s,Q)
    assert "current" in msg


def test_unknown_proposer_is_fatal(demand_csv):
    with pytest.raises(ValueError, match="proposed_by"):
        cross_impact_job.prepare(demand_csv, _params(proposed_by="almacen"))


@pytest.mark.parametrize("bad", [{"q": 0.0, "sl": 0.95}, {"q": 200.0, "sl": 1.0},
                                 {"q": 200.0, "sl": 0.0}, {"q": -5.0, "sl": 0.95}])
def test_out_of_range_points_are_fatal(demand_csv, bad):
    with pytest.raises(ValueError):
        cross_impact_job.prepare(demand_csv, _params(proposed=bad))


def test_incomplete_point_is_fatal(demand_csv):
    with pytest.raises(ValueError, match="proposed"):
        cross_impact_job.prepare(demand_csv, _params(proposed={"q": 400.0}))


def test_identical_points_are_fatal(demand_csv):
    with pytest.raises(ValueError, match="cambio"):
        cross_impact_job.prepare(demand_csv, _params(proposed={"q": 200.0, "sl": 0.95}))


def test_sku_not_in_csv_is_fatal(demand_csv):
    with pytest.raises(ValueError):
        cross_impact_job.prepare(demand_csv, _params(sku="SKU-404"))


def test_absent_thresholds_are_not_fatal(demand_csv):
    # honesty does not depend on strict_params: the run proceeds and the
    # verdict comes out indeterminado (Task 5).
    params = _params()
    del params["objection_thresholds"]
    p = cross_impact_job.prepare(demand_csv, params)
    assert p["thresholds"] == {}


def test_partial_thresholds_survive(demand_csv):
    p = cross_impact_job.prepare(demand_csv, _params(objection_thresholds={"cfo": 1.0}))
    assert p["thresholds"] == {"cfo": 1.0}


def test_bad_threshold_key_is_fatal(demand_csv):
    with pytest.raises(ValueError, match="objection_thresholds"):
        cross_impact_job.prepare(demand_csv, _params(objection_thresholds={"almacen": 1.0}))


# -- run(): verdict, counterproposal, options (spec sec 7) --------------------


def _payload(demand_csv, **over):
    return cross_impact_job.prepare(demand_csv, _params(**over))


def test_zero_objections_verdict_is_ejecutar(demand_csv):
    # generous thresholds: nobody objects to anything
    huge = {k: 1e9 for k in HAT_KEYS}
    r = cross_impact_job.run(_payload(demand_csv, objection_thresholds=huge))
    assert r.n_objections == 0
    assert r.n_unevaluable == 0
    assert r.verdict == cross_impact_job.EJECUTAR


def test_one_objection_verdict_is_bilateral(demand_csv):
    thr = {k: 1e9 for k in HAT_KEYS}
    thr["cfo"] = 1e-9                      # only the CFO can object
    r = cross_impact_job.run(_payload(demand_csv, objection_thresholds=thr))
    assert r.n_objections == 1
    assert r.verdict == cross_impact_job.BILATERAL


def test_two_objections_verdict_is_reunion(demand_csv):
    # Q must OVERSHOOT the EOQ for two areas to worsen together. With the
    # fixture (D=5200, K=75, h=0.25, c=10) the EOQ is ~559: at Q=400 the
    # planner's policy_cost actually FALLS (400 is closer to the EOQ than the
    # current 200), so only the CFO objects. At Q=2000 both the planner and the
    # CFO worsen. Verified by arithmetic during the plan's pre-flight scan.
    thr = {k: 1e-9 for k in HAT_KEYS}      # everyone objects to any deterioration
    r = cross_impact_job.run(_payload(demand_csv, proposed={"q": 2000.0, "sl": 0.95},
                                      objection_thresholds=thr))
    assert r.n_objections >= 2
    assert r.verdict == cross_impact_job.REUNION


def test_unevaluable_beats_zero_objections(demand_csv):
    # THE honesty rule: a declared gap outranks an optimistic verdict
    r = cross_impact_job.run(_payload(demand_csv, objection_thresholds={"cfo": 1e9}))
    assert r.n_unevaluable == 3
    assert r.n_objections == 0
    assert r.verdict == cross_impact_job.INDETERMINADO


def test_unevaluable_verdicts_have_empty_fields(demand_csv):
    r = cross_impact_job.run(_payload(demand_csv, objection_thresholds={"cfo": 1e9}))
    for v in r.verdicts:
        if v.status == NO_EVALUABLE:
            assert v.kpi_current is None
            assert v.kpi_proposed is None
            assert v.deterioration is None
            assert v.threshold is None


def test_one_verdict_per_hat_in_order(demand_csv):
    r = cross_impact_job.run(_payload(demand_csv))
    assert tuple(v.hat_key for v in r.verdicts) == HAT_KEYS


def test_planner_objects_when_proposal_breaches_sl_target(demand_csv):
    # config default sl_target = 0.95; proposing 0.90 breaches it while making
    # the planner's raw policy_cost CHEAPER (spec 9.1)
    thr = {k: 1e9 for k in HAT_KEYS}
    r = cross_impact_job.run(_payload(demand_csv, proposed={"q": 200.0, "sl": 0.90},
                                      objection_thresholds=thr))
    planner = next(v for v in r.verdicts if v.hat_key == "planner")
    assert planner.status == OBJETA
    assert planner.deterioration < 0          # the KPI actually improved
    assert r.verdict == cross_impact_job.BILATERAL


def test_counterproposal_absent_when_no_grid_point_is_clean(demand_csv):
    thr = {k: 1e-9 for k in HAT_KEYS}
    r = cross_impact_job.run(_payload(demand_csv, objection_thresholds=thr))
    assert r.counterproposal is None
    assert len(r.outcome.options) == 2        # never fabricated to fill the slot


def test_counterproposal_when_unevaluable_is_never_searched(demand_csv):
    r = cross_impact_job.run(_payload(demand_csv, objection_thresholds={"cfo": 1e9}))
    assert r.counterproposal is None


def test_counterproposal_really_has_zero_objections(demand_csv):
    # The default fixture's current (Q=200) sits BELOW the whole grid (~280-700
    # around the ~559 EOQ), so every grid point raises the CFO's capital charge
    # and no clean point can exist - a counterproposal assertion there would
    # pass vacuously forever. Put `current` INSIDE the grid and give the CFO
    # real headroom so a clean point genuinely exists, then assert it is found.
    thr = {k: 1e9 for k in HAT_KEYS}
    thr["cfo"] = 5_000.0
    r = cross_impact_job.run(_payload(demand_csv,
                                      current={"q": 560.0, "sl": 0.95},
                                      proposed={"q": 700.0, "sl": 0.99},
                                      objection_thresholds=thr))
    assert r.counterproposal is not None      # never vacuous
    assert len(r.outcome.options) == 3


def test_outcome_is_protected_options_with_one_recommendation(demand_csv):
    r = cross_impact_job.run(_payload(demand_csv))
    assert r.outcome.status == OPTIONS
    assert verify_guided(r.outcome) == []
    assert len(r.outcome.options) >= 2
    assert sum(1 for o in r.outcome.options if o.recommended) == 1


def test_confidence_drops_with_unevaluable_areas(demand_csv):
    full = cross_impact_job.run(_payload(demand_csv))
    partial = cross_impact_job.run(_payload(demand_csv, objection_thresholds={"cfo": 1e9}))
    assert full.outcome.confidence == 1.0
    assert partial.outcome.confidence < full.outcome.confidence


def test_summary_names_the_verdict(demand_csv):
    r = cross_impact_job.run(_payload(demand_csv, objection_thresholds={"cfo": 1e9}))
    assert cross_impact_job.INDETERMINADO in r.summary


# -- verify(): the QA gate (spec sec 8) --------------------------------------


def _good_report(demand_csv):
    return cross_impact_job.run(_payload(demand_csv))


def test_honest_report_passes(demand_csv):
    assert cross_impact_job.verify(_good_report(demand_csv)) == []


def test_indeterminado_with_gaps_passes(demand_csv):
    # a declared gap is a valid delivery, NOT a failure
    r = cross_impact_job.run(_payload(demand_csv, objection_thresholds={"cfo": 1e9}))
    assert r.verdict == cross_impact_job.INDETERMINADO
    assert cross_impact_job.verify(r) == []


def test_veto_optimistic_verdict_with_unevaluable_areas(demand_csv):
    r = cross_impact_job.run(_payload(demand_csv, objection_thresholds={"cfo": 1e9}))
    bad = replace(r, verdict=cross_impact_job.EJECUTAR)
    issues = cross_impact_job.verify(bad)
    assert any("no_evaluable" in i or "indeterminado" in i for i in issues)


def test_veto_judged_verdict_with_empty_fields(demand_csv):
    r = _good_report(demand_csv)
    broken = replace(r.verdicts[0], kpi_current=None)
    bad = replace(r, verdicts=(broken,) + r.verdicts[1:])
    assert cross_impact_job.verify(bad)


def test_veto_unevaluable_verdict_carrying_numbers(demand_csv):
    r = _good_report(demand_csv)
    fake = Verdict(hat_key=r.verdicts[0].hat_key, status=NO_EVALUABLE,
                   kpi_name=r.verdicts[0].kpi_name, kpi_current=1.0,
                   kpi_proposed=2.0, deterioration=1.0, threshold=None,
                   reason="fabricado")
    bad = replace(r, verdicts=(fake,) + r.verdicts[1:],
                  n_unevaluable=1, verdict=cross_impact_job.INDETERMINADO)
    assert cross_impact_job.verify(bad)


def test_veto_wrong_verdict_count_or_keys(demand_csv):
    r = _good_report(demand_csv)
    assert cross_impact_job.verify(replace(r, verdicts=r.verdicts[:3]))


def test_veto_non_finite_number(demand_csv):
    r = _good_report(demand_csv)
    broken = replace(r.impact, judge_cost_proposed=math.nan)
    assert cross_impact_job.verify(replace(r, impact=broken))


def test_veto_unprotected_outcome(demand_csv):
    r = _good_report(demand_csv)
    dead = GuidedOutcome(status="options", summary="x", options=[])
    assert cross_impact_job.verify(replace(r, outcome=dead))


def test_real_counterproposal_passes_the_self_check(demand_csv):
    # the positive side of the self-check: a genuinely clean counterproposal
    # (same setup as test_counterproposal_really_has_zero_objections) must not
    # be flagged. Without this, only the negative path is covered.
    thr = {k: 1e9 for k in HAT_KEYS}
    thr["cfo"] = 5_000.0
    r = cross_impact_job.run(_payload(demand_csv,
                                      current={"q": 560.0, "sl": 0.95},
                                      proposed={"q": 700.0, "sl": 0.99},
                                      objection_thresholds=thr))
    assert r.counterproposal is not None
    assert cross_impact_job.verify(r) == []


def test_veto_counterproposal_that_still_draws_objections(demand_csv):
    thr = {k: 1e-9 for k in HAT_KEYS}
    r = cross_impact_job.run(_payload(demand_csv, objection_thresholds=thr))
    assert r.counterproposal is None
    liar = replace(r, counterproposal=Candidate(999.0, 0.99))
    assert cross_impact_job.verify(liar)


def test_veto_unknown_verdict_string(demand_csv):
    r = _good_report(demand_csv)
    assert cross_impact_job.verify(replace(r, verdict="quiza"))


def test_veto_option_without_recommendation(demand_csv):
    r = _good_report(demand_csv)
    flat = replace(r.outcome, options=[
        ExecutionOption(label="a", summary="a", score=1.0),
        ExecutionOption(label="b", summary="b", score=0.0),
    ])
    assert cross_impact_job.verify(replace(r, outcome=flat))


# -- write(): the deliverables (spec sec 7.4) --------------------------------


def test_write_emits_both_csvs(demand_csv, tmp_path):
    paths = cross_impact_job.write(_good_report(demand_csv), tmp_path, "Acme")
    assert set(paths) == {"csv", "verdict_csv"}
    assert all(p.exists() for p in paths.values())


def test_unevaluable_cells_are_empty_not_zero(demand_csv, tmp_path):
    r = cross_impact_job.run(_payload(demand_csv, objection_thresholds={"cfo": 1e9}))
    paths = cross_impact_job.write(r, tmp_path, "Acme")
    rows = list(csv.DictReader(paths["csv"].open(encoding="utf-8")))
    unevaluable = [row for row in rows if row["status"] == NO_EVALUABLE]
    assert len(unevaluable) == 3
    for row in unevaluable:
        assert row["value_current"] == ""
        assert row["value_proposed"] == ""
        assert row["delta"] == ""
        assert row["deterioration"] == ""
        assert row["threshold"] == ""


def test_verdict_csv_is_one_countable_row(demand_csv, tmp_path):
    r = _good_report(demand_csv)
    paths = cross_impact_job.write(r, tmp_path, "Acme")
    rows = list(csv.DictReader(paths["verdict_csv"].open(encoding="utf-8")))
    assert len(rows) == 1
    assert rows[0]["verdict"] == r.verdict
    assert int(rows[0]["n_objections"]) == r.n_objections
    assert int(rows[0]["n_unevaluable"]) == r.n_unevaluable


def test_deck_carries_lang_and_options(demand_csv):
    r = _good_report(demand_csv)
    deck = cross_impact_job.build_deck(r, client="Acme", citations=("c1", "c2"),
                                       confidence=0.9, lang="en")
    assert deck.lang == "en"
    assert deck.citations == ("c1", "c2")
    assert deck.confidence == 0.9
