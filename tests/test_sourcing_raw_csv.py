"""Sourcing entered through a RAW ERP export - the path the pre-mapped fixture hides.

``tests/test_sourcing_tool.py`` feeds ``score_suppliers`` a dataframe that is already
mapped (``on_time``/``in_full`` as 0/1, ``lead_time_days`` as a number). Real client data
never looks like that: it carries ``Order_Date``/``Delivery_Date`` as unparsed strings and
ERP column names, and has no boolean OTIF columns at all. That gap is why a scorecard of
all zeros could ship with a clean QA gate and an adjudicated winner.

These tests enter only through ``prepare()`` on a raw CSV.
"""

from __future__ import annotations

import pandas as pd
import pytest

from jobs import sourcing_job as srcj

SLA = 14


def _raw_erp_csv(tmp_path, **overrides):
    """An ERP purchase-order export: dates as strings, no boolean OTIF columns."""
    frame = {
        "Supplier": ["Alpha", "Alpha", "Alpha", "Beta", "Beta"],
        "Order_Date": ["2024-01-01", "2024-01-02", "2024-01-03", "2024-01-01", "2024-01-02"],
        "Delivery_Date": ["2024-01-08", "2024-01-09", "2024-01-25", "2024-01-20", "2024-01-22"],
        "Order_Status": ["Delivered", "Delivered", "Delivered", "Delivered", "Partial"],
        "Quantity": [100, 100, 100, 100, 100],
        "Unit_Price": [10.0, 10.0, 10.0, 9.0, 9.0],
        "Defective_Units": [0.0, 1.0, 0.0, 2.0, 3.0],
    }
    frame.update(overrides)
    csv = tmp_path / "erp_export.csv"
    pd.DataFrame(frame).to_csv(csv, index=False)
    return csv


def _params(**extra):
    base = {"sla_days": SLA, "in_full_status_col": "Order_Status", "in_full_ok_values": ["Delivered"]}
    base.update(extra)
    return base


# -- the headline: real numbers out of a raw export ---------------------------


def test_prepare_derives_real_scorecards_from_an_unmapped_erp_export(tmp_path):
    payload = srcj.prepare(str(_raw_erp_csv(tmp_path)), _params())

    by = {c.supplier: c for c in payload["scorecards"]}
    # Alpha: leads 7/7/22 days -> avg 12.0; 2 of 3 within the 14-day SLA; all 3 delivered in full.
    assert by["Alpha"].avg_lead_time == pytest.approx(12.0)
    assert by["Alpha"].on_time_rate == pytest.approx(2 / 3)
    assert by["Alpha"].in_full_rate == pytest.approx(1.0)
    assert by["Alpha"].otif == pytest.approx(2 / 3)
    assert by["Alpha"].ppm == pytest.approx(1 / 300 * 1_000_000)
    # Beta: leads 19/20 -> both late; one partial delivery.
    assert by["Beta"].on_time_rate == pytest.approx(0.0)
    assert by["Beta"].in_full_rate == pytest.approx(0.5)
    assert by["Beta"].otif == pytest.approx(0.0)
    assert payload["prices"]["Alpha"] == pytest.approx(10.0)


def test_the_award_is_decided_by_real_performance_not_alphabetical_order(tmp_path):
    payload = srcj.prepare(str(_raw_erp_csv(tmp_path)), _params())

    report = srcj.run(payload["scorecards"], payload["prices"])

    assert report.recommended == "Alpha"
    assert report.ranking.scores["Alpha"] > 0.0
    assert report.ranking.scores["Alpha"] != report.ranking.scores["Beta"]


# -- the bug that shipped: truthiness on a free-text column -------------------


def test_a_free_text_status_column_is_refused_as_on_time_not_read_as_true(tmp_path):
    """``bool('Cancelled') is True`` scored every cancelled PO as a perfect delivery."""
    csv = _raw_erp_csv(tmp_path, Order_Status=["Cancelled"] * 5)

    with pytest.raises(ValueError, match="on_time"):
        srcj.prepare(str(csv), _params(on_time_col="Order_Status"))


def test_an_order_status_column_is_refused_as_in_full_not_silently_scored(tmp_path):
    """'Delivered' is a status, not a yes/no token.

    Read as a flag it is ambiguous in both directions - it says nothing about timing, and
    'delivered' vs 'partially delivered' is a substring trap. The supported path for a status
    column is the explicit ``in_full_status_col`` + ``in_full_ok_values`` mapping, so a status
    handed straight to ``in_full_col`` must refuse and say so.
    """
    with pytest.raises(ValueError, match="in_full_status_col|free text"):
        srcj.prepare(str(_raw_erp_csv(tmp_path)), {"sla_days": SLA, "in_full_col": "Order_Status"})


def test_a_rate_column_is_refused_as_a_per_delivery_flag(tmp_path):
    """One row per supplier holding an on-time RATE, not one row per delivery holding a flag.

    ``bool(0.95)`` is True, so a rates table scored every supplier a perfect 100% OTIF. This
    is not hypothetical: the capability-smoke fixture itself was shaped this way, which is how
    a 'PASS' smoke run reported three flawless suppliers.
    """
    csv = tmp_path / "rates.csv"
    pd.DataFrame({
        "supplier": ["Alpha", "Beta", "Gamma"],
        "on_time": [0.95, 0.80, 0.99],
        "in_full": [0.90, 0.75, 0.97],
        "lead_time_days": [12, 20, 8],
        "units": [1000, 800, 1200],
        "defects": [5, 30, 2],
    }).to_csv(csv, index=False)

    with pytest.raises(ValueError, match="not 0/1|measurement"):
        srcj.prepare(str(csv), {})


# -- missing decision columns must refuse, never default to zero --------------


def test_junk_in_a_numeric_column_is_refused_not_coerced_to_zero(tmp_path):
    """A quantity of 'TBD' must not become 0 units. (Note: 'n/a' is a pandas NA token, not junk.)"""
    csv = _raw_erp_csv(tmp_path, Quantity=[100, "TBD", 100, 100, 100])

    with pytest.raises(ValueError, match="not numeric"):
        srcj.prepare(str(csv), _params())


def test_a_blank_quantity_is_refused_because_it_silently_means_flawless_quality(tmp_path):
    """PPM divides by units; ``NaN > 0`` is False, so a blank quantity scored 0 PPM - perfect."""
    csv = _raw_erp_csv(tmp_path, Quantity=[100, None, 100, 100, 100])

    with pytest.raises(ValueError, match="quantity|units"):
        srcj.prepare(str(csv), _params())


def test_missing_lead_time_and_dates_refuses_instead_of_scoring_zero(tmp_path):
    csv = tmp_path / "no_dates.csv"
    pd.DataFrame({
        "Supplier": ["Alpha", "Beta"],
        "Order_Status": ["Delivered", "Delivered"],
        "Quantity": [100, 100],
    }).to_csv(csv, index=False)

    with pytest.raises(ValueError, match="lead_time"):
        srcj.prepare(str(csv), _params())


def test_no_promised_date_and_no_declared_sla_refuses_instead_of_assuming_one(tmp_path):
    """On-time against a silent default SLA is a fabricated number - make the caller declare it."""
    with pytest.raises(ValueError, match="sla_days"):
        srcj.prepare(str(_raw_erp_csv(tmp_path)), {"in_full_status_col": "Order_Status",
                                                   "in_full_ok_values": ["Delivered"]})


def test_missing_in_full_source_refuses_instead_of_scoring_zero(tmp_path):
    with pytest.raises(ValueError, match="in_full"):
        srcj.prepare(str(_raw_erp_csv(tmp_path)), {"sla_days": SLA})


# -- ERP-cased quality/price columns are found, not silently skipped ----------


def test_snake_case_erp_defect_and_price_columns_are_recognised(tmp_path):
    """``Defective_Units``/``Unit_Price`` are the real Kaggle/ERP spellings."""
    payload = srcj.prepare(str(_raw_erp_csv(tmp_path)), _params())

    by = {c.supplier: c for c in payload["scorecards"]}
    assert by["Beta"].ppm > 0.0
    assert payload["prices"]["Beta"] == pytest.approx(9.0)


# -- the promised-date path, when the ERP actually carries one ----------------


def test_a_promised_date_column_beats_the_declared_sla(tmp_path):
    csv = _raw_erp_csv(
        tmp_path,
        Promised_Date=["2024-01-06", "2024-01-15", "2024-01-30", "2024-01-15", "2024-01-15"],
    )

    payload = srcj.prepare(str(csv), _params(promised_date_col="Promised_Date"))

    by = {c.supplier: c for c in payload["scorecards"]}
    # Alpha delivered 01-08 (late vs 01-06), 01-09 (early vs 01-15), 01-25 (early vs 01-30).
    assert by["Alpha"].on_time_rate == pytest.approx(2 / 3)
    # Beta delivered 01-20 and 01-22, both past the 01-15 promise.
    assert by["Beta"].on_time_rate == pytest.approx(0.0)
