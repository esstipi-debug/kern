"""Sourcing must not award on, or report, a criterion it never measured.

Companion to tests/test_supplier_scorecard_evaluability.py, on the job side: a
delivery column that the sniffer could not find becomes ``None`` (absent) rather
than ``False``/``0.0``, an unmeasured criterion is dropped from the TOPSIS instead
of scoring every supplier a tied zero, and the deck says "no medido" instead of
printing a confident 0%.
"""

import pandas as pd
import pytest

from jobs import sourcing_job as srcj
from src.deliverable import not_measured

_FULL = pd.DataFrame({
    "supplier": ["Alpha"] * 4 + ["Beta"] * 4,
    "on_time": [1, 1, 1, 0, 1, 0, 0, 1],
    "in_full": [1, 1, 1, 1, 1, 1, 0, 1],
    "lead_time_days": [7, 7, 8, 7, 12, 11, 13, 12],
    "units": [100, 100, 100, 100, 100, 100, 100, 100],
    "defects": [0, 1, 0, 0, 2, 3, 1, 2],
})

# What the autopsy actually fed it: a real PO export with no service columns at all.
_LEAD_ONLY = pd.DataFrame({
    "supplier": ["Alpha"] * 3 + ["Beta"] * 3,
    "lead_time_days": [7, 8, 7, 12, 11, 13],
})


def _cards(df: pd.DataFrame, **cols):
    """The permissive policy: score what this export can support, name what it cannot.

    The default (`require_all_criteria=True`, what `prepare` uses) refuses instead - that
    contract is covered by tests/test_sourcing_degenerate_inputs.py and asserted below.
    """
    return srcj.score_suppliers(df, supplier_col="supplier", require_all_criteria=False, **cols)


def test_the_strict_default_still_refuses_a_panel_missing_a_decisive_column():
    with pytest.raises(ValueError, match="columns that decide the award"):
        srcj.score_suppliers(_LEAD_ONLY, supplier_col="supplier", lead_col="lead_time_days")


def test_the_refusal_points_at_the_permissive_mode():
    """A dead end is not an answer: say what the caller can do instead."""
    with pytest.raises(ValueError, match="require_all_criteria=False"):
        srcj.score_suppliers(_LEAD_ONLY, supplier_col="supplier", lead_col="lead_time_days")


def test_a_present_column_is_typed_strictly_even_in_permissive_mode():
    """Permissive about absence is not permissive about junk."""
    df = _LEAD_ONLY.assign(on_time=["Delivered", "Cancelled", "Delivered"] * 2)

    with pytest.raises(ValueError, match="free text|yes/no"):
        srcj.score_suppliers(df, supplier_col="supplier", require_all_criteria=False,
                             on_time_col="on_time", lead_col="lead_time_days")


# ---- absence propagates out of prepare --------------------------------------

def test_an_unfound_column_scores_as_absent_not_as_a_failed_delivery():
    cards, _ = _cards(_LEAD_ONLY, lead_col="lead_time_days")

    for card in cards:
        assert card.otif is None
        assert card.on_time_rate is None
        assert card.in_full_rate is None
        assert card.ppm is None
        assert card.avg_lead_time is not None


def test_prepare_keeps_the_strict_policy_on_an_export_without_service_columns(tmp_path):
    """`prepare` is the client-facing path and stays strict: no award without the columns."""
    csv = tmp_path / "po_export.csv"
    _LEAD_ONLY.to_csv(csv, index=False)

    with pytest.raises(ValueError, match="on_time|quantity|columns that decide"):
        srcj.prepare(str(csv), {})


# ---- the award may only use measured criteria -------------------------------

def test_an_unmeasured_criterion_is_dropped_from_the_ranking():
    """With everything tied at zero, TOPSIS awards alphabetically. That is not a decision."""
    cards, _ = _cards(_LEAD_ONLY, lead_col="lead_time_days")

    report = srcj.run(cards)

    assert [c.name for c in report.criteria] == ["lead_time"]
    assert "otif" not in report.weights
    assert report.recommended == "Alpha"        # genuinely the shorter lead time
    assert srcj.verify(report) == []


def test_the_report_names_the_criteria_it_could_not_measure():
    cards, _ = _cards(_LEAD_ONLY, lead_col="lead_time_days")

    report = srcj.run(cards)

    assert set(report.unmeasured) == {"otif", "ppm"}


def test_nothing_measured_means_no_award_and_a_blocked_qa_gate():
    cards, _ = _cards(pd.DataFrame({"supplier": ["Alpha", "Beta"]}))

    report = srcj.run(cards)

    assert report.recommended == ""
    assert srcj.verify(report) != []
    assert any("measured" in issue for issue in srcj.verify(report))


def test_a_fully_measured_panel_still_ranks_on_every_criterion():
    cards, prices = _cards(
        _FULL, on_time_col="on_time", in_full_col="in_full", lead_col="lead_time_days",
        units_col="units", defects_col="defects",
    )

    report = srcj.run(cards, prices)

    assert {c.name for c in report.criteria} == {"otif", "lead_time", "ppm"}
    assert report.unmeasured == ()
    assert report.recommended == "Alpha"


# ---- rendering ---------------------------------------------------------------

def test_the_deck_says_not_measured_instead_of_printing_zero_percent():
    cards, _ = _cards(_LEAD_ONLY, lead_col="lead_time_days")
    report = srcj.run(cards)

    md = srcj.build_deck(report, client="Acme").to_markdown()

    assert not_measured() in md
    assert "OTIF 0%" not in md
    assert "0% PPM" not in md
    assert md.isascii()


def test_the_same_deck_in_spanish_says_no_medido():
    cards, _ = _cards(_LEAD_ONLY, lead_col="lead_time_days")
    report = srcj.run(cards)

    md = srcj.build_deck(report, client="Acme", lang="es").to_markdown()

    assert "no medido" in md
    assert "OTIF 0%" not in md


def test_the_deck_does_not_claim_an_otif_spread_it_never_observed():
    cards, _ = _cards(_LEAD_ONLY, lead_col="lead_time_days")
    report = srcj.run(cards)

    md = srcj.build_deck(report, client="Acme").to_markdown()

    assert "OTIF spread" not in md


def test_the_operational_csv_leaves_an_unmeasured_cell_empty_not_zero(tmp_path):
    cards, _ = _cards(_LEAD_ONLY, lead_col="lead_time_days")
    report = srcj.run(cards)

    paths = srcj.write_operational(report, tmp_path)
    rows = pd.read_csv(paths["csv"], keep_default_na=False)

    assert set(rows["otif"]) == {""}
    assert set(rows["ppm"]) == {""}
    assert all(float(v) > 0 for v in rows["avg_lead_time"])


def test_a_measured_zero_is_still_printed_as_zero_percent():
    """Honesty cuts both ways: an observed 0% OTIF is a fact and must show as 0%."""
    df = pd.DataFrame({
        "supplier": ["Alpha"] * 2 + ["Beta"] * 2,
        "on_time": [0, 0, 1, 1],
        "in_full": [1, 1, 1, 1],
        "lead_time_days": [7, 8, 12, 11],
        "units": [100, 100, 100, 100],
        "defects": [0, 0, 0, 0],
    })
    cards, _ = _cards(
        df, on_time_col="on_time", in_full_col="in_full", lead_col="lead_time_days",
        units_col="units", defects_col="defects",
    )
    report = srcj.run(cards)

    alpha = next(c for c in report.scorecards if c.supplier == "Alpha")
    assert alpha.otif == pytest.approx(0.0)
    assert alpha.evaluable.otif == 2

    md = srcj.build_deck(report, client="Acme").to_markdown()
    assert not_measured() not in md
    assert "OTIF 0%" in md          # an observed zero, stated as one


def test_an_unrankable_panel_refuses_to_compose_a_deck():
    """The QA gate blocks it first; build_deck must not invent an award behind it."""
    cards, _ = _cards(pd.DataFrame({"supplier": ["Alpha", "Beta"]}))
    report = srcj.run(cards)

    with pytest.raises(ValueError, match="no award to present"):
        srcj.build_deck(report, client="Acme")


def test_the_recommendations_do_not_prescribe_a_target_on_an_unmeasured_criterion():
    """"Hold them to an OTIF target" is advice you cannot give without an OTIF."""
    cards, _ = _cards(_LEAD_ONLY, lead_col="lead_time_days")
    report = srcj.run(cards)

    recs = " ".join(srcj.build_deck(report, client="Acme").recommendations)

    assert "OTIF target" not in recs
    assert "lead" in recs.lower()


# --- the confidence has to MOVE when the coverage moves -------------------------------
#
# Everything above asserts that an unmeasured criterion is dropped, named, and printed
# as "no medido". None of it asserted the number the client reads at the top of the
# deck. So the deck disclosed the hole in prose and reported the SAME confidence either
# way: 0.85 on three of five criteria and 0.85 on all five, verify() clean both times.
# Measured 2026-08-22 against 47b7e1a, on the 777-PO Kaggle export and on the fixtures
# below alike.
#
# A single-input test cannot catch that. A classifier that always returns 0.85 and a
# correct one are indistinguishable from one observation, which is why both cases below
# run the same panel through the same call and compare.


def _report(df, **cols):
    cards, prices = _cards(df, **cols)
    return srcj.run(cards, prices)


def test_the_award_confidence_drops_when_criteria_were_never_measured():
    full = _report(_FULL, on_time_col="on_time", in_full_col="in_full",
                   lead_col="lead_time_days", units_col="units", defects_col="defects")
    partial = _report(_FULL, units_col="units", defects_col="defects")

    assert not full.unmeasured, "the full panel is the control: it must measure everything"
    assert partial.unmeasured, "the degraded panel must actually leave something unmeasured"
    # Neither side may have a tie doing the work: that is the OTHER coverage hole, and if
    # it appeared here the comparison would be measuring two things at once.
    assert not full.ranking.ignored_criteria
    assert not partial.ranking.ignored_criteria

    assert partial.outcome.confidence < full.outcome.confidence


def test_the_deck_confidence_drops_when_criteria_were_never_measured():
    full = _report(_FULL, on_time_col="on_time", in_full_col="in_full",
                   lead_col="lead_time_days", units_col="units", defects_col="defects")
    partial = _report(_FULL, units_col="units", defects_col="defects")

    full_deck = srcj.build_deck(full, client="Acme")
    partial_deck = srcj.build_deck(partial, client="Acme")

    assert partial_deck.confidence < full_deck.confidence


def test_each_unmeasured_criterion_costs_again():
    """Two holes cost more than one -- otherwise the penalty is a flag, not a measure."""
    one = _report(_FULL, on_time_col="on_time", in_full_col="in_full",
                  units_col="units", defects_col="defects")     # lead_time missing
    two = _report(_FULL, units_col="units", defects_col="defects")  # + otif missing

    assert len(two.unmeasured) > len(one.unmeasured)
    assert two.outcome.confidence < one.outcome.confidence


def test_full_coverage_is_left_exactly_where_it_was():
    """The fix must not quietly move the honest case: no hole, no penalty."""
    full = _report(_FULL, on_time_col="on_time", in_full_col="in_full",
                   lead_col="lead_time_days", units_col="units", defects_col="defects")

    assert full.outcome.confidence == 1.0
    assert srcj.build_deck(full, client="Acme").confidence == srcj._DECK_BASE_CONFIDENCE


def test_an_explicit_confidence_still_wins_over_the_derived_one():
    partial = _report(_FULL, units_col="units", defects_col="defects")

    assert srcj.build_deck(partial, client="Acme", confidence=0.42).confidence == 0.42
