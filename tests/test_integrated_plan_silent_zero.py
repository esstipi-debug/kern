"""The silent zero in jobs/integrated_plan.py: a purchase input NOBODY SUPPLIED
was handed to the coherence engine as ``0.0``, and the engine -- which cannot
tell a stated zero from a missing one -- signed the plan off as coherent.

Reproduced before the fix, against a stock CSV with no ``reorder_point`` column::

    reorder_point handed to the engine : 0.0
    engine counts: n_checks=3 passed=3 failed=0
      [PASS] reorder_point_service_level :: SKU-A: projected position 50.0 on-hand
             + 0.0 incoming (committed 0.0 + recommended 0.0) - 10.0 shaped demand
             = 40.0, at or above its 0.0-unit reorder buffer.
    DECK SUMMARY : ... 3/3 coherence check(s) passed.
    DECK FINDING : Coherence checks (step 4) | all 3 check(s) passed.
                   | impact=no cross-domain conflict detected this cycle

and, in its second shape, against a CSV where the column IS there and the CELL is
empty -- the check tested the column NAME, so it read ``nan`` and manufactured a
citable-looking stockout instead::

      [FAIL] reorder_point_service_level :: SKU-A: ... below its nan-unit reorder
             buffer by nan unit(s) -- stockout risk before the next replenishment
             cycle.

Both shapes are the same defect: a check that passes (or fails) BY THE ABSENCE of
the number it exists to compare against is worse than no check at all -- it signs
the coherence of a plan nobody verified. The rule these tests hold the module to:
the absence is declared with a motive and never filled, the count distinguishes
evaluated results from non-evaluable ones, and ``verify()`` vetoes a clean verdict
whenever the base for one is missing.
"""

from __future__ import annotations

import dataclasses

import pandas as pd
import pytest

from jobs import integrated_plan as ip
from jobs.qa import verify_integrated_plan
from src.sop_engine.coherence import (
    CHECK_BUDGET_FEASIBILITY,
    CHECK_PROMO_COVERAGE,
    CHECK_SERVICE_LEVEL,
    CoherenceResult,
)
from src.sop_engine.demand_plan import LIQUIDATION_SOURCE

_CLEAN_VERDICT_PHRASE = "no cross-domain conflict detected"


def _demand_csv(tmp_path, series: dict[str, list[float]], name: str = "demand.csv") -> str:
    rows = [
        {"sku": sku, "period": t, "demand": q}
        for sku, vals in series.items()
        for t, q in enumerate(vals)
    ]
    pd.DataFrame(rows).to_csv(tmp_path / name, index=False)
    return str(tmp_path / name)


def _stock_csv(tmp_path, rows: list[dict], name: str = "stock.csv") -> str:
    pd.DataFrame(rows).to_csv(tmp_path / name, index=False)
    return str(tmp_path / name)


def _bundle(tmp_path, stock_rows: list[dict], *, stock_name: str = "stock.csv", **params):
    """One flat-demand SKU (forecast 10.0/period) against the given stock file."""
    demand = _demand_csv(tmp_path, {"SKU-A": [10.0] * 12})
    stock = _stock_csv(tmp_path, stock_rows, name=stock_name)
    return ip.run(ip.prepare(demand, {"stock_path": stock, **params}))


# The exact file that reproduced the defect: on_hand and unit_cost are there,
# the reorder point is not mentioned anywhere.
_NO_REORDER_COLUMN = [{"product_id": "SKU-A", "on_hand": 50.0, "unit_cost": 5.0}]
# Same file, second shape: the column exists and the cell is empty.
_BLANK_REORDER_CELL = [
    {"product_id": "SKU-A", "on_hand": 50.0, "unit_cost": 5.0, "reorder_point": None,
     "incoming_po": 0.0},
]
# The control: every optional input actually stated by the client, reorder point
# deliberately stated as a real 0.0 (a policy, not a gap).
_REORDER_STATED_AS_ZERO = [
    {"product_id": "SKU-A", "on_hand": 50.0, "unit_cost": 5.0, "reorder_point": 0.0,
     "incoming_po": 0.0},
]


# ---- the defect itself -------------------------------------------------------

def test_absent_reorder_point_never_reports_a_clean_coherence_verdict(tmp_path):
    """The headline claim the client acted on: "3/3 passed" + "no cross-domain
    conflict detected", both derived from a reorder point nobody supplied."""
    bundle = _bundle(tmp_path, _NO_REORDER_COLUMN)
    deck = ip.build_deck(bundle, client="Acme Co")

    assert "3/3" not in deck.summary
    assert "NOT EVALUABLE" in deck.summary
    for finding in deck.findings:
        assert _CLEAN_VERDICT_PHRASE not in (finding.impact or "")
        assert "all 3 check(s) passed" not in finding.detail


def test_absent_reorder_point_makes_the_service_level_check_not_evaluable(tmp_path):
    """The reorder-point check is the one that reads the missing number, so it is
    the one that loses its verdict -- the other two keep theirs."""
    bundle = _bundle(tmp_path, _NO_REORDER_COLUMN)

    unevaluable = bundle.tally.unevaluable
    assert [u.check for u in unevaluable] == [CHECK_SERVICE_LEVEL]
    assert unevaluable[0].product_id == "SKU-A"
    assert unevaluable[0].column == "reorder_point"
    assert "reorder_point" in unevaluable[0].reason

    assert {c.check for c in bundle.tally.passed} == {CHECK_PROMO_COVERAGE, CHECK_BUDGET_FEASIBILITY}
    assert bundle.tally.failed == ()
    assert bundle.verdict == ip.INDETERMINATE


def test_the_count_distinguishes_evaluated_results_from_non_evaluable_ones(tmp_path):
    """"N/M passed" must count M as EVALUATED, never as TOTAL: a check nobody
    could evaluate may not inflate either side of the fraction."""
    bundle = _bundle(tmp_path, _NO_REORDER_COLUMN)
    tally = bundle.tally

    assert (tally.n_passed, tally.n_evaluated, tally.n_unevaluable) == (2, 2, 1)
    assert tally.n_total == len(bundle.plan.checks) == 3
    assert tally.headline == (
        "2/2 evaluated coherence check(s) passed, 1 NOT EVALUABLE "
        "(reorder_point not supplied)"
    )
    assert "NOT EVALUABLE" in bundle.summary


def test_an_empty_cell_is_the_same_absence_as_a_missing_column(tmp_path):
    """The defect's mechanism was testing the column NAME instead of its CONTENT:
    a present-but-blank cell used to reach the check as ``nan`` and manufacture a
    stockout finding. Absence is absence, whichever way the CSV spells it."""
    bundle = _bundle(tmp_path, _BLANK_REORDER_CELL)

    assert [u.check for u in bundle.tally.unevaluable] == [CHECK_SERVICE_LEVEL]
    assert bundle.tally.failed == ()
    assert "nan" not in bundle.summary
    for check in bundle.plan.checks:
        assert "nan" not in check.message


def test_a_reorder_point_the_client_states_as_zero_is_a_real_verdict(tmp_path):
    """The whole distinction: a stated 0.0 is a policy and must still be judged.
    Refusing to judge it would be the mirror-image dishonesty."""
    bundle = _bundle(tmp_path, _REORDER_STATED_AS_ZERO)

    assert bundle.tally.unevaluable == ()
    assert bundle.tally.n_passed == 3
    assert bundle.verdict == ip.COHERENT
    assert ip.verify(bundle) == []

    deck = ip.build_deck(bundle)
    assert "3/3 evaluated coherence check(s) passed" in deck.summary
    assert any(_CLEAN_VERDICT_PHRASE in (f.impact or "") for f in deck.findings)


def test_absent_incoming_po_costs_the_promo_check_its_verdict_only_where_it_read_it(tmp_path):
    """Every coherence message is CITABLE by contract (it names a number from a
    named upstream step). "already-committed incoming PO 0.0" is a fabricated
    citation when no PO position was ever supplied -- so the lifted SKU's promo
    check loses its verdict. The portfolio-level results never read it and keep
    theirs."""
    demand = _demand_csv(tmp_path, {"SKU-CLR": [1.0] * 12})
    stock = _stock_csv(tmp_path, [
        # daily_demand + a huge on_hand makes P4 shape a liquidation-implied lift,
        # which is what makes the promo-coverage check read a position at all.
        {"product_id": "SKU-CLR", "on_hand": 2000.0, "unit_cost": 5.0, "daily_demand": 1.0,
         "reorder_point": 5.0},
    ])
    # P4 only implies a LIFT off an announced clearance price: with no price history
    # it classifies the SKU salvage_heuristic (clearance_price None) and
    # src.sop_engine.demand_plan._shift_from_liquidation deliberately reads no lift
    # from a salvage line. A FLAT series is what P4 needs and P2 cannot use -- P2
    # returns needs_data, so the liquidation signal is the one that shapes demand
    # (the same fixture shape tests/test_integrated_plan_job.py:168 already uses).
    prices = pd.DataFrame([
        {"date": d, "product_id": "SKU-CLR", "price": 10.0, "quantity": 5}
        for d in ("2026-01-05", "2026-01-12", "2026-01-19", "2026-01-26")
    ])
    prices.to_csv(tmp_path / "prices.csv", index=False)
    bundle = ip.run(ip.prepare(
        demand, {"stock_path": stock, "price_history_path": str(tmp_path / "prices.csv")}))
    assert bundle.plan.demand_plan[0].source == LIQUIDATION_SOURCE

    unevaluable = bundle.tally.unevaluable
    assert [(u.check, u.column) for u in unevaluable] == [(CHECK_PROMO_COVERAGE, "incoming_po")]
    assert unevaluable[0].product_id == "SKU-CLR"
    # reorder_point WAS supplied, so the service-level check keeps its verdict.
    assert any(c.check == CHECK_SERVICE_LEVEL for c in bundle.tally.passed + bundle.tally.failed)


def test_no_lift_means_the_promo_check_never_read_a_po_position(tmp_path):
    """Its "no SKU carries a demand lift -- check not applicable" result is a real,
    self-declared verdict about the demand plan; it reads no PO position, so an
    unsupplied incoming_po does not touch it."""
    bundle = _bundle(tmp_path, [
        {"product_id": "SKU-A", "on_hand": 50.0, "unit_cost": 5.0, "reorder_point": 4.0},
    ])
    assert bundle.tally.unevaluable == ()
    assert any(c.check == CHECK_PROMO_COVERAGE for c in bundle.tally.passed)


# ---- what the deliverable is allowed to say ---------------------------------

def test_the_deck_declares_the_gap_with_its_motive_and_the_ask(tmp_path):
    bundle = _bundle(tmp_path, _NO_REORDER_COLUMN)
    deck = ip.build_deck(bundle, client="Acme Co")

    gap = next(f for f in deck.findings if "NOT EVALUABLE" in f.title)
    assert CHECK_SERVICE_LEVEL in gap.title
    assert "SKU-A" in gap.detail
    assert "reorder_point" in (gap.impact or "")

    kpi = next(k for k in deck.kpis if "NOT EVALUABLE" in k.name)
    assert kpi.value == "1"
    passed_kpi = next(k for k in deck.kpis if k.name == "Coherence checks passed")
    assert (passed_kpi.value, passed_kpi.target) == ("2/2", "3/3")


def test_deck_confidence_is_derived_down_by_the_unsupplied_column(tmp_path):
    """A plan with an unmeasured check must not claim the certainty of one whose
    checks were all measured (jobs/intake_scan_job.py's rule, same helper)."""
    measured = ip.build_deck(_bundle(tmp_path, _REORDER_STATED_AS_ZERO, stock_name="ok.csv"))
    unmeasured = ip.build_deck(_bundle(tmp_path, _NO_REORDER_COLUMN, stock_name="gap.csv"))

    assert measured.confidence == ip._DECK_BASE_CONFIDENCE
    assert unmeasured.confidence < measured.confidence


def test_the_operational_csv_writes_an_empty_verdict_not_a_pass(tmp_path):
    """The gap has to look like a gap in the spreadsheet too (the rule
    jobs/cross_impact_job.write already applies to an unevaluable area)."""
    bundle = _bundle(tmp_path, _NO_REORDER_COLUMN)
    written = ip.write_operational(bundle, tmp_path / "out")

    df = pd.read_csv(written["coherence_checks"], keep_default_na=False)
    row = df[df["check"] == CHECK_SERVICE_LEVEL].iloc[0]
    assert row["passed"] == ""
    assert row["status"] == ip.NOT_EVALUABLE
    assert "reorder_point" in row["message"]
    assert set(df[df["check"] != CHECK_SERVICE_LEVEL]["status"]) == {"passed"}


# ---- verify(): the veto -----------------------------------------------------

def test_verify_vetoes_a_clean_verdict_while_a_check_is_not_evaluable(tmp_path):
    bundle = _bundle(tmp_path, _NO_REORDER_COLUMN)
    assert ip.verify(bundle) == []  # the honest combination ships

    lying = dataclasses.replace(bundle, verdict=ip.COHERENT)
    issues = ip.verify(lying)
    assert any("clean verdict" in i for i in issues), issues


def test_verify_vetoes_counting_a_verdict_built_on_an_unsupplied_number(tmp_path):
    """The veto that matters: re-labelling the not-evaluable result as a pass must
    not get past QA just because the counts still add up."""
    bundle = _bundle(tmp_path, _NO_REORDER_COLUMN)
    smuggled = CoherenceResult(CHECK_SERVICE_LEVEL, True, "SKU-A", "looks fine to me")
    lying = dataclasses.replace(
        bundle,
        verdict=ip.COHERENT,
        tally=ip.CoherenceTally(
            passed=bundle.tally.passed + (smuggled,), failed=(), unevaluable=(),
        ),
    )
    issues = ip.verify(lying)
    assert any("rests on a 0.0 nobody stated" in i for i in issues), issues


def test_verify_vetoes_a_fabricated_gap_too(tmp_path):
    """Symmetry: declaring a gap that is not there is as dishonest as hiding one."""
    bundle = _bundle(tmp_path, _REORDER_STATED_AS_ZERO)
    invented = ip.UnevaluableCheck(CHECK_SERVICE_LEVEL, "SKU-A", "reorder_point", "made up")
    lying = dataclasses.replace(
        bundle,
        verdict=ip.INDETERMINATE,
        tally=ip.CoherenceTally(
            passed=bundle.tally.passed[:-1], failed=(), unevaluable=(invented,),
        ),
    )
    assert any("fabricated gap" in i for i in ip.verify(lying)), ip.verify(lying)


def test_verify_vetoes_a_summary_that_hides_the_gap(tmp_path):
    bundle = _bundle(tmp_path, _NO_REORDER_COLUMN)
    quiet = dataclasses.replace(bundle, summary="Integrated plan over 1 SKU(s): all good.")
    assert any("without disclosing" in i for i in ip.verify(quiet)), ip.verify(quiet)


def test_a_coherence_check_nobody_classified_raises_instead_of_passing(tmp_path):
    """A fourth check added to the engine must not default to "evaluable" -- that
    is exactly how a verdict built on a filled-in number ships unnoticed."""
    with pytest.raises(KeyError, match="no declared grounding column"):
        ip._grounding_column(CoherenceResult("brand_new_check", True, "SKU-A", "msg"))


def test_run_refuses_a_payload_that_cannot_tell_absence_from_zero(tmp_path):
    """``run`` will not fall back to "everything was supplied" -- the default that
    would quietly restore the defect for any caller not using ``prepare``."""
    demand = _demand_csv(tmp_path, {"SKU-A": [10.0] * 12})
    stock = _stock_csv(tmp_path, _REORDER_STATED_AS_ZERO)
    payload = ip.prepare(demand, {"stock_path": stock})
    payload.pop("unsupplied_inputs")
    with pytest.raises(KeyError, match="unsupplied_inputs"):
        ip.run(payload)


# ---- the structural QA gate keeps working ------------------------------------

def test_the_structural_qa_gate_rejects_a_tally_that_loses_a_check(tmp_path):
    """Written after a surviving mutation: deleting jobs/qa.py's reconciliation of
    the tally against ``plan.checks`` broke no test. Both dishonest shapes below
    keep ``ip.verify`` happy -- every result they DO carry is honestly grounded --
    so the structural gate is the only thing standing between them and a delivery,
    and a check dropped from all three buckets shrinks the "N/M evaluated"
    denominator the whole fix rests on."""
    bundle = _bundle(tmp_path, _REORDER_STATED_AS_ZERO)
    assert verify_integrated_plan(bundle) == []

    dropped = dataclasses.replace(
        bundle, tally=ip.CoherenceTally(bundle.tally.passed[:-1], (), ()),
    )
    assert dropped.tally.headline == "2/2 evaluated coherence check(s) passed"
    assert ip.verify(dropped) == []  # nothing it claims is ungrounded; it just claims less
    assert any("tally accounts for 2" in i for i in verify_integrated_plan(dropped))

    kept = bundle.tally.passed[0]
    swapped = dataclasses.replace(
        bundle, tally=ip.CoherenceTally((kept,) * len(bundle.plan.checks), (), ()),
    )
    assert ip.verify(swapped) == []
    assert any("not a partition" in i for i in verify_integrated_plan(swapped))


def test_the_structural_qa_gate_still_passes_on_a_declared_gap(tmp_path):
    """A declared gap is a valid delivery, not a QA failure -- jobs.qa checks
    structural soundness, ip.verify checks what may be claimed."""
    bundle = _bundle(tmp_path, _NO_REORDER_COLUMN)
    assert verify_integrated_plan(bundle) == []
    written = ip.write_deliverable(bundle, out_dir=tmp_path / "deck", client="Acme Co")
    report = written["report"].read_text(encoding="utf-8")
    assert "NOT EVALUABLE" in report
    assert _CLEAN_VERDICT_PHRASE not in report
