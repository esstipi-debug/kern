"""A count sheet with no unit costs must not report a zero dollar impact.

``jobs/reconciliation_job.py`` used to default a missing ``unit_cost`` to ``0.0`` and
then print the sum of those zeros as a finding: "Dollar impact of the discrepancies:
0 total absolute variance value across 2 mismatched line(s)". Nothing in the deck
distinguished that from a real, measured zero, so a client reading it concluded the
variances were free. Two lines of the same sheet with a cost and one without produced
a *ranking* with the same flaw: the 20-unit discrepancy nobody could price sorted
below the 2-unit one that had a price, and the deck named the wrong SKU as the
biggest problem.

The same shape hides in IRA. A row with a blank ``physical_qty`` was never counted,
yet it landed in the IRA denominator as a failure and poisoned the variance total to
``nan``; drop it and the mirror image appears - "accuracy 100%" read off the single
line anyone actually counted.

The contract these tests pin: an unmeasurable figure is ``None`` plus a legible
motive, the deck prose never asserts anything derived from it, an IRA computed over
part of the sheet always says so, and ``verify()`` refuses a report that states a
number it had no basis for. A genuine measured zero stays a zero - that distinction
is the entire point.
"""

from __future__ import annotations

import pandas as pd
import pytest

from jobs import reconciliation_job as rcj

# -- fixtures -----------------------------------------------------------------


def _priced_df() -> pd.DataFrame:
    """Every line carries a real unit cost: the fully measurable baseline."""
    return pd.DataFrame({
        "product_id": ["SKU-A", "SKU-B", "SKU-C"],
        "system_qty": [100.0, 200.0, 50.0],
        "physical_qty": [100.0, 180.0, 52.0],
        "unit_cost": [5.0, 10.0, 2.0],
    })


def _unpriced_df() -> pd.DataFrame:
    """The same count, off a sheet that never carried a cost column."""
    return _priced_df().drop(columns=["unit_cost"])


def _partly_priced_df() -> pd.DataFrame:
    """SKU-B - the 20-unit discrepancy, by far the biggest - has no cost."""
    return _priced_df().assign(unit_cost=[5.0, None, 2.0])


def _partly_counted_df() -> pd.DataFrame:
    """Only SKU-A was physically counted; the other three lines are blank."""
    return pd.DataFrame({
        "product_id": ["SKU-A", "SKU-B", "SKU-C", "SKU-D"],
        "system_qty": [100.0, 200.0, 50.0, 70.0],
        "physical_qty": [100.0, None, None, None],
        "unit_cost": [5.0, 10.0, 2.0, 1.0],
    })


def _run(df: pd.DataFrame, **kwargs) -> rcj.IRAReport:
    return rcj.run(rcj.prepare_records(df), **kwargs)


# -- the dollar impact --------------------------------------------------------


def test_a_sheet_without_unit_costs_cannot_state_a_dollar_impact():
    report = _run(_unpriced_df())

    assert report.total_variance_value is None
    assert report.variance_value_note


def test_the_absence_names_the_lines_that_caused_it():
    """A motive that does not say *which* lines are unpriced is not actionable."""
    report = _run(_unpriced_df())

    assert set(report.unpriced) == {"SKU-B", "SKU-C"}
    assert "SKU-B" in report.variance_value_note or "2" in report.variance_value_note


def test_a_line_with_no_variance_does_not_make_the_total_unmeasurable():
    """SKU-A matched exactly: it contributes 0 whatever its cost, so its missing
    cost cannot corrupt a sum. Only a *nonzero* variance without a price can."""
    report = _run(_unpriced_df())

    assert "SKU-A" not in report.unpriced


def test_a_partly_priced_sheet_is_not_totalled_as_if_it_were_complete():
    """204 minus the line nobody could price is 4, and 4 is a lie with a decimal point."""
    report = _run(_partly_priced_df())

    assert report.total_variance_value is None
    assert report.unpriced == ("SKU-B",)


def test_a_measured_zero_is_still_a_measurement():
    """The overcorrection to guard against: a sheet that was counted, priced, and
    simply had no variance must report $0.00, not "not measured"."""
    exact = _priced_df().assign(physical_qty=[100.0, 200.0, 50.0])

    report = _run(exact)

    assert report.total_variance_value == 0.0
    assert report.variance_value_note is None
    assert report.unpriced == ()


def test_a_fully_priced_sheet_still_totals_exactly_as_before():
    report = _run(_priced_df())

    assert report.total_variance_value == pytest.approx(204.0)   # 20*10 + 2*2
    assert report.variance_value_note is None
    assert rcj.verify(report) == []


# -- the ranking --------------------------------------------------------------


def test_an_unpriced_discrepancy_is_kept_out_of_the_dollar_ranking():
    """Sorting it by ``|variance| * 0.0`` puts the biggest problem last."""
    report = _run(_partly_priced_df())

    assert [r.product_id for r in report.worst] == ["SKU-C"]
    assert [r.product_id for r in report.unvalued] == ["SKU-B"]


def test_the_deck_does_not_crown_the_cheap_line_as_the_biggest_discrepancy():
    md = rcj.build_deck(_run(_partly_priced_df())).to_markdown()

    assert "Biggest discrepancy: SKU-C" not in md
    assert "SKU-B" in md


# -- the deck prose -----------------------------------------------------------


def test_the_deck_never_prints_a_dollar_impact_it_could_not_compute():
    md = rcj.build_deck(_run(_unpriced_df())).to_markdown()

    assert "0 total absolute variance value" not in md
    assert "not measured" in md
    assert md.isascii()


def test_the_deck_says_why_the_impact_is_missing_and_what_to_send():
    md = rcj.build_deck(_run(_unpriced_df())).to_markdown()

    assert "unit cost" in md.lower()


def test_the_deck_confidence_drops_when_the_money_could_not_be_measured():
    priced = rcj.build_deck(_run(_priced_df()))
    unpriced = rcj.build_deck(_run(_unpriced_df()))

    assert unpriced.confidence < priced.confidence


def test_a_caller_supplied_confidence_cannot_restore_what_the_data_cannot_support():
    """``scm_agent`` passes 0.85 in unconditionally; it is a ceiling, not an override."""
    deck = rcj.build_deck(_run(_unpriced_df()), confidence=0.95)

    assert deck.confidence < 0.85


# -- IRA over lines nobody counted --------------------------------------------


def test_a_line_nobody_counted_is_not_an_inaccurate_line():
    """A blank physical qty is an absence, not a variance of ``nan``."""
    report = _run(_partly_counted_df())

    assert report.n_lines == 4
    assert report.n_counted == 1
    assert set(report.uncounted) == {"SKU-B", "SKU-C", "SKU-D"}


def test_the_variance_total_is_never_nan():
    report = _run(_partly_counted_df())

    assert report.total_variance_value == 0.0     # the one counted line matched
    assert "nan" not in rcj.build_deck(report).to_markdown().lower()


def test_an_accuracy_read_off_one_line_of_four_says_so():
    """100% is arithmetically true and editorially a lie without the coverage."""
    report = _run(_partly_counted_df())

    assert report.ira == 1.0
    assert report.ira_note
    assert "3" in report.ira_note

    md = rcj.build_deck(report).to_markdown()
    assert "3" in md and "not counted" in md.lower()


def test_a_sheet_where_nothing_was_counted_has_no_accuracy_at_all():
    never = _partly_counted_df().assign(physical_qty=[None] * 4)

    report = _run(never)

    assert report.ira is None
    assert report.ira_note
    assert rcj.verify(report) != []


def test_the_deck_prints_no_accuracy_percentage_when_nothing_was_counted():
    never = _partly_counted_df().assign(physical_qty=[None] * 4)

    deck = rcj.build_deck(_run(never))
    md = deck.to_markdown()

    # Asserted against the accuracy figure specifically, not against every "%" in the
    # document: the deck's own "Confidence: **60%**" line contains "0%" as a substring,
    # and a check that trips on it would be dodged by tuning the penalty rather than by
    # keeping the accuracy honest.
    assert {k.name: k.value for k in deck.kpis}["Inventory record accuracy"] == "not measured"
    assert "IRA 0%" not in md and "IRA 100%" not in md
    assert "accuracy 0%" not in md and "accuracy 100%" not in md
    assert "not measured" in md


# -- inconstructibility -------------------------------------------------------


def test_a_report_claiming_an_impact_it_has_no_basis_for_cannot_be_built():
    base = _run(_unpriced_df())

    with pytest.raises(ValueError, match="cost basis"):
        rcj.IRAReport(
            n_lines=base.n_lines, n_counted=base.n_counted, n_within=base.n_within,
            ira=base.ira, ira_note=base.ira_note,
            total_variance_value=0.0, variance_value_note=None,
            tolerance_pct=0.0, tolerance_units=0.0,
            worst=base.worst, unvalued=base.unvalued,
            unpriced=base.unpriced, uncounted=base.uncounted, results=base.results,
        )


def test_an_absence_without_a_motive_cannot_be_built():
    base = _run(_unpriced_df())

    with pytest.raises(ValueError, match="motive|reason|note"):
        rcj.IRAReport(
            n_lines=base.n_lines, n_counted=base.n_counted, n_within=base.n_within,
            ira=base.ira, ira_note=base.ira_note,
            total_variance_value=None, variance_value_note=None,
            tolerance_pct=0.0, tolerance_units=0.0,
            worst=base.worst, unvalued=base.unvalued,
            unpriced=base.unpriced, uncounted=base.uncounted, results=base.results,
        )


# -- verify() as independent defence ------------------------------------------


def test_verify_vetoes_a_report_that_states_an_impact_without_the_cost_basis():
    """``__post_init__`` guards construction; ``verify()`` is the gate that still
    holds when a caller mutates a frozen report past it."""
    report = _run(_unpriced_df())
    object.__setattr__(report, "total_variance_value", 0.0)
    object.__setattr__(report, "variance_value_note", None)

    issues = rcj.verify(report)

    assert issues
    assert any("cost basis" in i for i in issues)


def test_verify_vetoes_an_accuracy_that_hides_the_lines_nobody_counted():
    report = _run(_partly_counted_df())
    object.__setattr__(report, "ira_note", None)

    assert any("counted" in i for i in rcj.verify(report))


def test_verify_recomputes_the_stated_impact_instead_of_trusting_it():
    report = _run(_priced_df())
    object.__setattr__(report, "total_variance_value", 9999.0)

    assert any("204" in i or "recompute" in i.lower() for i in rcj.verify(report))


def test_verify_passes_a_declared_gap_it_is_the_undeclared_one_that_is_vetoed():
    """The gap is a legal delivery; the dishonest combination is not."""
    assert rcj.verify(_run(_unpriced_df())) == []
    assert rcj.verify(_run(_partly_counted_df())) == []


# -- the operational CSV ------------------------------------------------------


def test_the_csv_leaves_an_unvalued_variance_blank_never_zero(tmp_path):
    report = _run(_partly_priced_df())

    path = rcj.write_operational(report, tmp_path)["csv"]
    rows = pd.read_csv(path).set_index("product_id")

    assert pd.isna(rows.loc["SKU-B", "variance_value"])
    assert rows.loc["SKU-C", "variance_value"] == 4.0
