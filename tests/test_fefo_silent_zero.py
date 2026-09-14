"""The FEFO job must never price stock it has no prices for.

``src.lots.fefo.Lot`` types ``unit_cost``/``unit_price`` as plain floats, so a lot whose
money the client never sent enters the engine as ``0.0`` - indistinguishable from a lot
that really is free. ``markdown_vs_scrap`` then multiplies that filler through the whole
money half of the study and still returns a ``recommended`` string, because ``0 >= 0``.

Measured before the fix, on a lots file carrying no unit_cost and no unit_price column::

    Lot expiry over 3 lot(s): 4 expired + 10 expiring soon; 9 unit(s) at risk (0 cost),
    recommend markdown (recover 0).
    verify() -> []

and, worse, on a file carrying unit_cost but no unit_price (scrap_value_pct=0.2)::

    ... 9 unit(s) at risk (45 cost), recommend scrap (recover 9).
    verify() -> []

A recommendation to DESTROY stock, issued because a price column was absent, shipped
past a clean QA gate. These tests pin the opposite: absent money is ``None`` plus a
legible note, the text recommends nothing, and ``verify()`` vetoes.
"""

from dataclasses import replace

import pandas as pd
import pytest

from jobs import fefo_job as fj

# ---------------------------------------------------------------- fixtures


def _df(**overrides: object) -> pd.DataFrame:
    """Three lots of one product: one fresh, one expiring, one already expired."""
    base: dict[str, object] = {
        "product_id": ["P", "P", "P"],
        "lot_id": ["fresh", "soon", "dead"],
        "quantity": [10, 10, 4],
        "days_to_expiry": [40, 5, -1],
        "unit_cost": [5, 5, 5],
        "unit_price": [12, 12, 12],
        "daily_demand": [1, 1, 1],
    }
    base.update(overrides)
    return pd.DataFrame({k: v for k, v in base.items() if v is not None})


def _run(params: dict | None = None, **overrides: object) -> fj.FefoReport:
    return fj.run(fj.prepare_records(_df(**overrides), params or {}))


def _priced() -> fj.FefoReport:
    """The happy path: every lot carries both a cost and a price."""
    return _run()


def _unpriced() -> fj.FefoReport:
    """No unit_cost and no unit_price column at all - the measured defect."""
    return _run(unit_cost=None, unit_price=None)


# ------------------------------------------- the money half is absent, not zero


def test_no_cost_or_price_column_leaves_every_money_field_none() -> None:
    report = _unpriced()

    assert report.total_value is None
    assert report.at_risk_value is None
    assert report.scrap_recovery is None
    assert report.markdown_recovery is None
    assert report.recovered_value is None
    assert report.recommended is None
    # the QUANTITY half is genuinely measured and must survive intact
    assert report.at_risk_units == 9.0
    assert report.expired_quantity == 4.0


def test_the_absence_carries_a_legible_reason_naming_the_missing_columns() -> None:
    notes = " ".join(_unpriced().notes)

    assert "unit-cost" in notes and "unit_cost" in notes
    assert "unit-price" in notes and "unit_price" in notes


def test_the_summary_recommends_nothing_and_never_prints_a_zero_recovery() -> None:
    summary = _unpriced().summary

    assert "recover 0" not in summary
    assert "recommend markdown" not in summary
    assert "recommend scrap" not in summary
    assert "not determinable" in summary


def test_verify_vetoes_the_clean_verdict_when_the_basis_is_missing() -> None:
    issues = fj.verify(_unpriced())

    assert issues != []
    assert any("cost" in i for i in issues), issues


def test_a_missing_unit_price_alone_still_blocks_a_scrap_recommendation() -> None:
    """The costliest variant: cost present, price absent -> markdown_recovery is
    0.5 * 0 and the engine recommends DESTROYING the stock."""
    report = _run({"scrap_value_pct": 0.2}, unit_price=None)

    assert report.disposition.recommended == "scrap"   # what the raw engine still says
    assert report.recommended is None                  # what the job is allowed to report
    assert report.markdown_recovery is None
    assert report.recovered_value is None
    assert "scrap" not in report.summary.split("Disposition")[0]
    assert fj.verify(report) != []


def test_a_missing_unit_cost_alone_blocks_the_recommendation_too() -> None:
    report = _run(unit_cost=None)

    assert report.recommended is None
    assert report.at_risk_value is None
    assert report.scrap_recovery is None
    assert report.markdown_recovery is not None   # price WAS supplied; that half is real
    assert fj.verify(report) != []


def test_a_cost_column_present_but_empty_is_the_same_as_no_column() -> None:
    report = _run(unit_cost=[None, None, None])

    assert report.basis.cost_column == "unit_cost"
    assert report.total_value is None
    assert report.recommended is None
    assert any("empty" in n for n in report.notes), report.notes


def test_one_uncosted_at_risk_lot_is_enough_to_block_the_disposition() -> None:
    """The disposition is a decision about the AT-RISK lots. A hole in any one of
    them biases the markdown-vs-scrap comparison, so it is not issued."""
    report = _run(unit_cost=[5, None, 5])

    assert "soon" in report.at_risk_by_lot          # the uncosted lot IS at risk
    assert report.recommended is None
    assert report.total_value is None
    assert fj.verify(report) != []


# ---------------------------------------------- the honest path must stay honest


def test_a_complete_basis_still_recommends_and_passes_verify() -> None:
    report = _priced()

    assert report.recommended == "markdown"
    assert report.recovered_value == pytest.approx(0.5 * 9.0 * 12.0)
    assert report.at_risk_value == pytest.approx(9.0 * 5.0)
    assert report.total_value == pytest.approx(24.0 * 5.0)
    assert report.notes == ()
    assert fj.verify(report) == []
    assert "recommend markdown" in report.summary


def test_nothing_at_risk_is_a_measured_zero_not_an_absence() -> None:
    """Demand clears every lot before it expires: the zero recovery is a
    measurement and must not be suppressed as if it were a gap."""
    report = _run(quantity=[1, 1, 0], days_to_expiry=[40, 30, 20], daily_demand=[100, 100, 100])

    assert report.at_risk_units == 0.0
    assert report.recovered_value == 0.0
    assert report.recommended is not None
    assert fj.verify(report) == []


# ------------------------------------------------------- verify() bites both ways


def test_verify_vetoes_money_fields_populated_without_a_basis() -> None:
    """The pre-fix behaviour, replayed: a zero where there is no basis is a veto."""
    issues = fj.verify(replace(_unpriced(), recovered_value=0.0, at_risk_value=0.0))

    assert any("no basis" in i or "without" in i for i in issues), issues


def test_verify_vetoes_a_recommendation_smuggled_in_without_a_basis() -> None:
    issues = fj.verify(replace(_unpriced(), recommended="markdown"))

    assert issues != []
    assert any("recommend" in i for i in issues), issues


def test_verify_vetoes_a_measured_basis_reported_as_absent() -> None:
    """The inverse guard, so 'return None always' is not a passing fix."""
    issues = fj.verify(replace(_priced(), recommended=None, recovered_value=None))

    assert issues != []


def test_verify_re_derives_the_summary_and_rejects_a_stale_one() -> None:
    stale = "Lot expiry over 3 lot(s): 4 expired + 10 expiring soon; 9 unit(s) at risk (0 cost), recommend markdown (recover 0)."
    issues = fj.verify(replace(_unpriced(), summary=stale))

    assert any("summary" in i for i in issues), issues


# -------------------------------------- the second silent zero: unassessed demand


def test_products_without_a_demand_rate_are_named_not_silently_dropped() -> None:
    df = pd.DataFrame({
        "product_id": ["P", "Q"],
        "lot_id": ["p1", "q1"],
        "quantity": [10, 1000],
        "days_to_expiry": [5, 5],
        "unit_cost": [5, 5],
        "unit_price": [12, 12],
        "daily_demand": [1, None],       # Q's forward risk cannot be assessed at all
    })
    report = fj.run(fj.prepare_records(df))

    assert report.products_without_demand_rate == ("Q",)
    assert any("Q" in n and "demand" in n for n in report.notes), report.notes
    assert "Q" in report.summary


# ------------------------------------------------------------------- the deck


def test_the_deck_prints_not_measured_instead_of_a_zero() -> None:
    deck = fj.build_deck(_unpriced(), client="Acme")
    md = deck.to_markdown()

    assert md.isascii()
    assert "not measured" in md
    assert "recover 0" not in md
    assert "Recommended disposition: markdown" not in md
    kpis = {k.name: k.value for k in deck.kpis}
    assert kpis["At-risk cost"] == "not measured"
    assert kpis["Recoverable value"] == "not measured"


def test_the_deck_confidence_is_capped_when_the_basis_is_missing() -> None:
    assert fj.build_deck(_unpriced(), confidence=0.85).confidence < 0.85
    assert fj.build_deck(_priced(), confidence=0.85).confidence == 0.85


def test_the_deck_asks_for_the_missing_columns_instead_of_recommending() -> None:
    recs = " ".join(fj.build_deck(_unpriced()).recommendations)

    assert "unit_cost" in recs or "unit cost" in recs
    assert "recover 0" not in recs


# ------------------------------------------------- a payload must declare its basis


def test_run_refuses_a_payload_that_never_declared_a_valuation_basis() -> None:
    """A hand-built payload cannot opt out of the declaration by omitting it."""
    payload = fj.prepare_records(_df())
    del payload["basis"]

    with pytest.raises(KeyError):
        fj.run(payload)
