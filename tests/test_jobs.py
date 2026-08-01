"""Tests for the job-fulfillment layer (intake, playbook, QA, deliverables)."""

import numpy as np
import pandas as pd
import pytest
from openpyxl import load_workbook

from jobs import deliverables, qa
from jobs.intake import (
    _parse_quantities,
    detect_columns,
    normalize,
    normalize_tracked,
    prepare,
    prepare_tracked,
)
from jobs.inventory_optimization import run
from jobs.pricing import prepare_pricing
from jobs.pricing import run as run_pricing

PORTFOLIO = "data/sample_demand_portfolio.csv"
PRICING = "data/sample_pricing.csv"


def test_detect_columns_from_arbitrary_headers():
    df = pd.DataFrame(columns=["Order Date", "SKU", "Qty", "Unit Price", "Lead Time"])
    m = detect_columns(df)
    assert m.ok
    assert m.mapping["date"] == "Order Date"
    assert m.mapping["product_id"] == "SKU"
    assert m.mapping["quantity"] == "Qty"
    assert m.mapping["unit_cost"] == "Unit Price"
    assert m.mapping["lead_time_days"] == "Lead Time"


def test_detect_columns_reports_missing_required():
    df = pd.DataFrame(columns=["foo", "bar"])
    m = detect_columns(df)
    assert not m.ok
    assert set(m.unmatched_required) == {"date", "product_id", "quantity"}


def test_normalize_aggregates_transactions_to_weekly():
    # two same-week transactions for one SKU must sum into one weekly row
    raw = pd.DataFrame(
        {
            "InvoiceDate": ["2024-01-01", "2024-01-03", "2024-01-08"],
            "StockCode": ["A", "A", "A"],
            "Quantity": [10, 5, 20],
            "UnitPrice": [2.0, 2.0, 2.5],
        }
    )
    canon = normalize(raw, detect_columns(raw), period="W")
    assert list(canon.columns) == [
        "date", "product_id", "quantity", "unit_cost", "lead_time_days", "lead_time_std",
    ]
    assert len(canon) == 2  # week 1 (10+5) and week 2 (20)
    assert canon.iloc[0]["quantity"] == 15
    assert "lead_time_days" in canon.columns  # defaulted when absent
    assert canon["lead_time_std"].eq(0.0).all()  # no dispersion known -> deterministic lead time


def test_normalize_rejects_undetectable_data():
    raw = pd.DataFrame({"foo": [1], "bar": [2]})
    with pytest.raises(ValueError):
        normalize(raw, detect_columns(raw))


# -- normalize_tracked / IntakeQuality: plausibility signal, not just a silent drop ---


def test_normalize_tracked_returns_same_frame_as_normalize():
    """The tracked variant must not change the actual cleaned data - only add
    visibility into what got dropped and why."""
    raw = pd.DataFrame(
        {
            "InvoiceDate": ["2024-01-01", "2024-01-03", "2024-01-08"],
            "StockCode": ["A", "A", "A"],
            "Quantity": [10, 5, 20],
            "UnitPrice": [2.0, 2.0, 2.5],
        }
    )
    mapping = detect_columns(raw)
    plain = normalize(raw, mapping, period="W")
    tracked, quality = normalize_tracked(raw, mapping, period="W")
    pd.testing.assert_frame_equal(plain, tracked)
    assert quality.n_raw == 3
    assert quality.n_dropped == 0
    assert quality.dropped_fraction == 0.0


def test_normalize_tracked_counts_bad_dates_separately_from_bad_quantities():
    raw = pd.DataFrame(
        {
            "InvoiceDate": ["2024-01-01", "not-a-date", "2024-01-08", "2024-01-09"],
            "StockCode": ["A", "A", "A", "A"],
            "Quantity": [10, 5, None, -3],
        }
    )
    mapping = detect_columns(raw)
    _, quality = normalize_tracked(raw, mapping, period="W")
    assert quality.n_raw == 4
    assert quality.n_dropped_bad_date == 1
    assert quality.n_dropped_bad_quantity == 1
    assert quality.n_dropped_negative_quantity == 1
    assert quality.n_dropped == 3
    assert quality.dropped_fraction == pytest.approx(0.75)


def test_normalize_tracked_handles_mixed_date_formats_without_dropping():
    """A column mixing ISO ('2025-01-06') and day-first-slash ('13/01/2025')
    dates is a real, common messy-client-export failure mode -- pandas locks
    onto whichever format it infers first and turns every row in the other
    format into NaT. Both rows here must survive, and the ambiguous ISO date
    must land on the correct calendar day (Jan 6, not Jun 1), proving
    dayfirst=True is actually applied to the retried rows."""
    raw = pd.DataFrame(
        {
            "InvoiceDate": ["2025-01-06", "13/01/2025"],
            "StockCode": ["A", "A"],
            "Quantity": [10, 5],
        }
    )
    mapping = detect_columns(raw)
    canon, quality = normalize_tracked(raw, mapping, period="D")
    assert quality.n_dropped_bad_date == 0
    assert quality.n_raw == 2
    dates = sorted(canon["date"].tolist())
    assert dates[0] == pd.Timestamp("2025-01-06")
    assert dates[1] == pd.Timestamp("2025-01-13")


def test_normalize_tracked_still_drops_genuinely_bad_dates_amid_mixed_formats():
    """Mixing ISO, day-first-slash, AND a garbage string in the same column
    must only drop the garbage row -- proving the mixed-format fix doesn't
    accidentally rescue truly unparseable data."""
    raw = pd.DataFrame(
        {
            "InvoiceDate": ["2025-01-06", "13/01/2025", "not-a-date"],
            "StockCode": ["A", "A", "A"],
            "Quantity": [10, 5, 7],
        }
    )
    mapping = detect_columns(raw)
    _, quality = normalize_tracked(raw, mapping, period="D")
    assert quality.n_dropped_bad_date == 1
    assert quality.n_raw == 3


def test_normalize_tracked_handles_mixed_naive_and_timezone_aware_dates():
    """A column mixing a naive date ('2025-01-06') with a timezone-aware one
    ('2025-01-07T21:00:00-05:00') is a real messy-export shape (e.g. a file
    that switches from date-only rows to full timestamps mid-export). pandas
    treats timezone heterogeneity as a structural failure rather than a
    per-value parse failure: format="mixed" either raises ValueError outright
    (pandas >=3, observed) or silently returns an object-dtype column that
    crashes the very next `.dt` access downstream (pandas 2.x) -- neither is
    a normal per-row 'bad date' that IntakeQuality can count, so both used to
    escape all the way to the orchestrator's generic error status. Both rows
    must survive here, with the aware value converted to its UTC-equivalent
    instant: 21:00-05:00 on Jan 7 is 02:00 UTC on Jan 8, so it must land in
    the Jan 8 bucket -- proving the conversion actually ran rather than the
    value merely surviving with its local wall-clock day intact."""
    raw = pd.DataFrame(
        {
            "InvoiceDate": ["2025-01-06", "2025-01-07T21:00:00-05:00"],
            "StockCode": ["A", "A"],
            "Quantity": [10, 5],
        }
    )
    mapping = detect_columns(raw)
    canon, quality = normalize_tracked(raw, mapping, period="D")
    assert quality.n_dropped_bad_date == 0
    assert quality.n_raw == 2
    dates = sorted(canon["date"].tolist())
    assert dates[0] == pd.Timestamp("2025-01-06")
    assert dates[1] == pd.Timestamp("2025-01-08")  # 21:00-05:00 Jan 7 -> 02:00 UTC Jan 8


def test_normalize_tracked_drops_garbage_amid_mixed_timezone_dates():
    """Mixing a naive date, a timezone-aware date, AND a genuinely
    unparseable string in the same column must only drop the garbage row as
    a normal bad date -- proving the timezone-mixing recovery path still
    runs errors="coerce" per value instead of failing (or corrupting) the
    whole column."""
    raw = pd.DataFrame(
        {
            "InvoiceDate": ["2025-01-06", "2025-01-07T10:00:00-05:00", "not-a-date"],
            "StockCode": ["A", "A", "A"],
            "Quantity": [10, 5, 7],
        }
    )
    mapping = detect_columns(raw)
    _, quality = normalize_tracked(raw, mapping, period="D")
    assert quality.n_dropped_bad_date == 1
    assert quality.n_raw == 3


def test_normalize_tracked_handles_all_timezone_aware_dates_with_differing_offsets():
    """No naive value at all here -- both rows are timezone-aware but at
    DIFFERENT fixed offsets (-05:00 and +02:00). This is the other shape that
    trips pandas's timezone-heterogeneity failure (it's about offset
    consistency, not naive-vs-aware specifically), so it must be recovered
    the same way. The two offsets are chosen so each value's UTC-bucketed day
    differs from its local wall-clock day, in OPPOSITE directions -- proving
    both rows were actually converted to their UTC instant rather than one
    surviving by coincidence."""
    raw = pd.DataFrame(
        {
            # 23:00-05:00 Jan 6 -> 04:00 UTC Jan 7
            # 01:00+02:00 Jan 7 -> 23:00 UTC Jan 6
            "InvoiceDate": ["2025-01-06T23:00:00-05:00", "2025-01-07T01:00:00+02:00"],
            "StockCode": ["A", "A"],
            "Quantity": [10, 5],
        }
    )
    mapping = detect_columns(raw)
    canon, quality = normalize_tracked(raw, mapping, period="D")
    assert quality.n_dropped_bad_date == 0
    assert quality.n_raw == 2
    dates = sorted(canon["date"].tolist())
    assert dates == [pd.Timestamp("2025-01-06"), pd.Timestamp("2025-01-07")]


def test_normalize_tracked_single_iso_format_column_unaffected():
    """The common real-world case -- a column with only ISO dates, no mixing
    -- must behave exactly as before: nothing dropped, dates parsed as-is."""
    raw = pd.DataFrame(
        {
            "InvoiceDate": ["2024-01-01", "2024-01-08", "2024-01-15"],
            "StockCode": ["A", "A", "A"],
            "Quantity": [10, 5, 20],
        }
    )
    mapping = detect_columns(raw)
    canon, quality = normalize_tracked(raw, mapping, period="D")
    assert quality.n_dropped_bad_date == 0
    assert quality.n_raw == 3
    dates = sorted(canon["date"].tolist())
    assert dates == [
        pd.Timestamp("2024-01-01"),
        pd.Timestamp("2024-01-08"),
        pd.Timestamp("2024-01-15"),
    ]


def test_normalize_tracked_does_not_corrupt_iso_dates_when_a_slash_date_leads():
    """Regression test for a reverted two-pass dayfirst=True retry: that fix
    made pandas lock its FIRST pass onto whichever format led the column, so
    an ISO date landing anywhere after a slash-formatted date got sent into
    the dayfirst retry and silently swapped month/day (measured: ~39% of ISO
    dates corrupted this way). Here the slash date leads the column -- the
    exact trigger condition -- and both ISO dates must still parse to their
    correct, unswapped calendar day."""
    raw = pd.DataFrame(
        {
            "InvoiceDate": ["13/01/2025", "2025-01-06", "2025-02-10"],
            "StockCode": ["A", "A", "A"],
            "Quantity": [5, 10, 7],
        }
    )
    mapping = detect_columns(raw)
    canon, quality = normalize_tracked(raw, mapping, period="D")
    assert quality.n_dropped_bad_date == 0
    dates = sorted(canon["date"].tolist())
    assert pd.Timestamp("2025-01-06") in dates, "ISO date Jan 6 was corrupted"
    assert pd.Timestamp("2025-02-10") in dates, "ISO date Feb 10 was corrupted"
    assert pd.Timestamp("2025-06-01") not in dates, "Jan 6 was swapped to Jun 1"


def test_normalize_tracked_does_not_corrupt_us_format_dates():
    """Regression test for the same reverted dayfirst=True retry corrupting
    genuine US-format (MM/DD/YYYY) dates, not just ISO ones. A slash date
    leads the column (the exact trigger condition -- see the sibling ISO
    regression test above) followed by an ISO date, a clearly-US-only-valid
    date ('03/25/2025', day=25 rules out day-first), and an ambiguous-looking
    '03/04/2025'. All must resolve correctly: 03/25 stays March 25, and
    03/04 reads month-first as March 4, not April 3."""
    raw = pd.DataFrame(
        {
            "InvoiceDate": ["13/01/2025", "2025-01-06", "03/25/2025", "03/04/2025"],
            "StockCode": ["A", "A", "A", "A"],
            "Quantity": [3, 10, 5, 7],
        }
    )
    mapping = detect_columns(raw)
    canon, quality = normalize_tracked(raw, mapping, period="D")
    assert quality.n_dropped_bad_date == 0
    dates = sorted(canon["date"].tolist())
    assert pd.Timestamp("2025-03-25") in dates, "US-only-valid date corrupted"
    assert pd.Timestamp("2025-03-04") in dates, "03/04/2025 not read month-first (March 4)"
    assert pd.Timestamp("2025-04-03") not in dates, "03/04/2025 misread as April 3rd"


def test_normalize_tracked_ambiguous_slash_date_defaults_month_first():
    """Documents the one honest, accepted limitation of format="mixed": a
    slash-delimited date where BOTH components are <= 12 (e.g. '06/01/2025')
    is genuinely ambiguous without locale context, and pandas defaults to
    month-first (the international/US convention) absent one. This is NOT a
    bug to fix here -- it never drops a row and never corrupts an
    unambiguous date (ISO, or any slash date with a component > 12), unlike
    the reverted dayfirst=True retry. A day-first client exporting this exact
    shape would get this one narrow case read as June 1st instead of the
    intended January 6th; that tradeoff is deliberate and documented in
    jobs.intake._parse_dates."""
    raw = pd.DataFrame(
        {
            "InvoiceDate": ["06/01/2025"],
            "StockCode": ["A"],
            "Quantity": [10],
        }
    )
    mapping = detect_columns(raw)
    canon, quality = normalize_tracked(raw, mapping, period="D")
    assert quality.n_dropped_bad_date == 0
    assert canon["date"].iloc[0] == pd.Timestamp("2025-06-01")  # June 1st, not Jan 6


# -- _parse_quantities: locale-aware thousands/decimal separators -----------


def test_normalize_tracked_handles_latam_formatted_quantities_without_dropping():
    """A quantity column mixing plain values and LATAM-formatted values
    (period thousands separator, comma decimal separator, e.g. '1.234,56')
    is a real, common export convention -- Kern's stated ICP explicitly
    includes LATAM. ``pd.to_numeric`` alone understands only a bare decimal
    point and silently drops every LATAM-formatted row as invalid quantity.
    Confirmed on a real 120-SKU production run: a single 'cosmetic' dirt
    injector that reformats quantities into LATAM convention alone drops
    31.67% of rows (152/480), all attributed to bad quantity. Both rows must
    survive here, and the parsed values must be the correct floats, not just
    'not dropped'."""
    raw = pd.DataFrame(
        {
            "InvoiceDate": ["2024-01-01", "2024-01-08"],
            "StockCode": ["A", "A"],
            "Quantity": ["45.5", "1.234,56"],
        }
    )
    mapping = detect_columns(raw)
    canon, quality = normalize_tracked(raw, mapping, period="D")
    assert quality.n_dropped_bad_quantity == 0
    quantities = sorted(canon["quantity"].tolist())
    assert quantities == [45.5, 1234.56]


def test_normalize_tracked_handles_us_thousands_formatted_quantities():
    """US/international format (comma thousands separator, period decimal
    separator, e.g. '1,234.56') also fails under plain ``pd.to_numeric`` --
    this is a real improvement over the old (pre-fix) behavior, not just a
    LATAM-specific patch."""
    raw = pd.DataFrame(
        {
            "InvoiceDate": ["2024-01-01", "2024-01-08"],
            "StockCode": ["A", "A"],
            "Quantity": ["45.5", "1,234.56"],
        }
    )
    mapping = detect_columns(raw)
    canon, quality = normalize_tracked(raw, mapping, period="D")
    assert quality.n_dropped_bad_quantity == 0
    quantities = sorted(canon["quantity"].tolist())
    assert quantities == [45.5, 1234.56]


def test_normalize_tracked_ambiguous_thousands_quantity_still_drops():
    """Documents the one honest, accepted limitation of ``_parse_quantities``:
    a comma-separated value with exactly three trailing digits and no other
    separator (e.g. '1,234') is genuinely ambiguous without locale context --
    it could be a US thousands-separated integer (1234) or a LATAM decimal
    (1.234) -- and this must stay dropped as bad quantity rather than guessed
    either way, mirroring the discipline ``_parse_dates`` applies to its own
    ambiguous dates. This is the exact shape a prior regex attempt at this
    fix got wrong (a zero-thousands-groups degenerate case that silently
    accepted '1,234' as LATAM 1.234); this test would have caught it.

    The "good" values here are deliberately plain (already parseable by bare
    ``pd.to_numeric``, no locale ambiguity at all) rather than LATAM/US
    formatted -- there is nothing to discriminate against the old naive code
    in this test, since the one point of the fix under test (leaving '1,234'
    unresolved) is a no-op relative to old behavior: naive ``pd.to_numeric``
    already turns '1,234' into NaN too, just for the wrong reason (it doesn't
    understand comma separators at all, not because it recognizes the
    ambiguity)."""
    raw = pd.DataFrame(
        {
            "InvoiceDate": ["2024-01-01", "2024-01-08", "2024-01-15"],
            "StockCode": ["A", "B", "C"],
            "Quantity": ["45.5", "1,234", "100"],
        }
    )
    mapping = detect_columns(raw)
    canon, quality = normalize_tracked(raw, mapping, period="D")
    assert quality.n_dropped_bad_quantity == 1
    # exactly the ambiguous row (product B) dropped -- A and C survive with
    # their correctly-parsed plain values.
    assert set(canon["product_id"]) == {"A", "C"}
    surviving = dict(zip(canon["product_id"], canon["quantity"]))
    assert surviving == {"A": 45.5, "C": 100.0}


def test_normalize_tracked_drops_garbage_amid_mixed_locale_quantities():
    """Mixing a LATAM-formatted value, a US-thousands-formatted value, AND a
    genuinely unparseable string in the same column must only drop the
    garbage row -- proving the locale-recovery retry doesn't rescue actual
    garbage."""
    raw = pd.DataFrame(
        {
            "InvoiceDate": ["2024-01-01", "2024-01-08", "2024-01-15"],
            "StockCode": ["A", "B", "C"],
            "Quantity": ["1.234,56", "1,234.56", "not-a-number"],
        }
    )
    mapping = detect_columns(raw)
    canon, quality = normalize_tracked(raw, mapping, period="D")
    assert quality.n_dropped_bad_quantity == 1
    assert set(canon["product_id"]) == {"A", "B"}
    surviving = dict(zip(canon["product_id"], canon["quantity"]))
    assert surviving == {"A": 1234.56, "B": 1234.56}


def test_parse_quantities_ambiguous_thousands_shape_stays_nan_in_isolation():
    """Regression test for the specific prior-attempt regex bug: a
    zero-thousands-groups degenerate pattern (``(?:\\.\\d{3})*`` instead of
    ``+``) silently accepted '1,234' as LATAM (parsing it to 1.234) instead
    of leaving it unresolved. Calls ``_parse_quantities`` directly (not
    through the full pipeline) as cheap insurance against that exact defect
    reappearing silently."""
    result = _parse_quantities(pd.Series(["1,234"]))
    assert pd.isna(result.iloc[0])


def test_normalize_tracked_clean_data_has_zero_dropped_fraction():
    raw = pd.DataFrame(
        {
            "InvoiceDate": ["2024-01-01", "2024-01-08"],
            "StockCode": ["A", "A"],
            "Quantity": [10, 5],
        }
    )
    mapping = detect_columns(raw)
    _, quality = normalize_tracked(raw, mapping, period="W")
    assert quality.n_dropped == 0
    assert quality.dropped_fraction == 0.0


def test_prepare_tracked_matches_prepare_and_reports_clean_sample_data():
    demand = prepare(PORTFOLIO)
    tracked, quality = prepare_tracked(PORTFOLIO)
    pd.testing.assert_frame_equal(demand, tracked)
    assert quality.n_raw > 0
    assert quality.dropped_fraction == 0.0  # the bundled sample data is clean


def test_playbook_runs_and_passes_qa():
    demand = prepare(PORTFOLIO)
    report = run(demand, service_level=0.95, holding_rate=0.25, order_cost=75.0)
    assert report.n_skus == 8
    assert qa.verify(report) == []  # internally consistent
    # at least one (s,Q) and one intermittent (R,S)
    kinds = {r.policy_kind for r in report.recommendations}
    assert "(s, Q)" in kinds and "(R, S)" in kinds
    for r in report.recommendations:
        assert r.investment == pytest.approx(r.cycle_investment + r.ss_investment)


def test_budget_scaling_and_feasibility():
    demand = prepare(PORTFOLIO)
    full = run(demand)
    tight = run(demand, budget=full.requested_investment * 0.7)
    assert tight.safety_stock_scale < 1.0
    assert tight.final_investment <= tight.requested_investment + 1e-6
    assert qa.verify(tight) == []

    infeasible = run(demand, budget=1.0)
    assert infeasible.feasible is False
    assert qa.verify(infeasible) == []  # consistent even when infeasible


def test_qa_catches_tampered_report():
    demand = prepare(PORTFOLIO)
    report = run(demand)
    bad = report.recommendations[0]
    object.__setattr__(bad, "investment", bad.investment + 999)  # corrupt the math
    issues = qa.verify(report)
    assert any("investment" in i for i in issues)


# -- Zero-demand SKU isolation: one degenerate SKU must not kill the batch -----
#
# compute_eoq (src/eoq.py) raises ValueError when annual_demand <= 0. Before this
# fix, that exception propagated straight out of run()'s per-SKU loop and killed
# the ENTIRE portfolio's run - one brand-new/seasonal SKU with no sales, or a
# messy export that split one SKU's demand across two spellings leaving one at
# zero, blocked every other SKU's perfectly good recommendation too.


def _clean_portfolio_df(n_periods: int = 20) -> pd.DataFrame:
    """Two ordinary SKUs, no degenerate demand anywhere - the baseline used to
    prove the good SKUs' recommendations are byte-identical whether or not a
    degenerate SKU is present elsewhere in the same portfolio."""
    dates = pd.date_range("2026-01-01", periods=n_periods, freq="W")
    rows = []
    for d in dates:
        rows.append({"date": d, "product_id": "GOOD-A", "quantity": 100.0, "unit_cost": 10.0})
        rows.append({"date": d, "product_id": "GOOD-B", "quantity": 40.0, "unit_cost": 25.0})
    return pd.DataFrame(rows)


def _zero_demand_portfolio_df(n_periods: int = 20, extra_zero_skus: tuple = ()) -> pd.DataFrame:
    """The clean portfolio plus one or more all-zero-quantity SKUs (e.g. ZERO-C)."""
    dates = pd.date_range("2026-01-01", periods=n_periods, freq="W")
    rows = []
    for d in dates:
        rows.append({"date": d, "product_id": "GOOD-A", "quantity": 100.0, "unit_cost": 10.0})
        rows.append({"date": d, "product_id": "GOOD-B", "quantity": 40.0, "unit_cost": 25.0})
        rows.append({"date": d, "product_id": "ZERO-C", "quantity": 0.0, "unit_cost": 8.0})
        for sku in extra_zero_skus:
            rows.append({"date": d, "product_id": sku, "quantity": 0.0, "unit_cost": 5.0})
    return pd.DataFrame(rows)


def test_zero_demand_sku_is_skipped_not_fatal_to_batch():
    """The bug this fixes, end to end: before the fix this call raised ValueError
    and lost every SKU's recommendation, not just the degenerate one."""
    report = run(_zero_demand_portfolio_df())
    baseline = run(_clean_portfolio_df())

    assert report.n_skus == 3
    assert len(report.recommendations) == report.n_skus

    by = {r.product_id: r for r in report.recommendations}
    base_by = {r.product_id: r for r in baseline.recommendations}

    zero_rec = by["ZERO-C"]
    assert zero_rec.policy_kind == "skipped_zero_demand"
    assert zero_rec.order_quantity is None
    assert zero_rec.order_up_to is None
    assert zero_rec.investment == 0.0
    assert zero_rec.status == "review"

    # the good SKUs get normal, correct recommendations, completely unaffected
    # by the degenerate SKU's presence elsewhere in the same portfolio
    for pid in ("GOOD-A", "GOOD-B"):
        good, base = by[pid], base_by[pid]
        assert good.policy_kind == base.policy_kind == "(s, Q)"
        assert good.status == base.status == "ok"
        assert good.order_quantity == pytest.approx(base.order_quantity)
        assert good.reorder_point == pytest.approx(base.reorder_point)
        assert good.safety_stock == pytest.approx(base.safety_stock)
        assert good.investment == pytest.approx(base.investment)
        assert good.investment == pytest.approx(good.cycle_investment + good.ss_investment)


def test_qa_passes_on_report_with_skipped_sku():
    """This is the test that would catch a wrong policy_kind choice (e.g. reusing
    "(s, Q)" or "(R, S)" for a skipped rec) reintroducing the same crash-the-whole-
    batch bug in QA-failure form instead of exception form."""
    report = run(_zero_demand_portfolio_df())
    assert qa.verify(report) == []


def test_n_skipped_zero_demand_counts_correctly():
    report = run(_zero_demand_portfolio_df())
    assert report.n_skipped_zero_demand == 1

    clean_report = run(_clean_portfolio_df())
    assert clean_report.n_skipped_zero_demand == 0


def test_multiple_zero_demand_skus_are_all_skipped_independently():
    """The loop must not stop at the first degenerate SKU - every zero-demand SKU
    is skipped and recorded, every normal SKU still gets a real recommendation."""
    df = _zero_demand_portfolio_df(extra_zero_skus=("ZERO-D", "ZERO-E"))
    report = run(df)

    assert report.n_skus == 5
    assert report.n_skipped_zero_demand == 3

    by = {r.product_id: r for r in report.recommendations}
    for pid in ("ZERO-C", "ZERO-D", "ZERO-E"):
        assert by[pid].policy_kind == "skipped_zero_demand"
        assert by[pid].investment == 0.0
        assert by[pid].status == "review"
    for pid in ("GOOD-A", "GOOD-B"):
        assert by[pid].policy_kind == "(s, Q)"
        assert by[pid].order_quantity is not None and by[pid].order_quantity > 0
        assert by[pid].status == "ok"

    assert qa.verify(report) == []


def test_deliverables_written(tmp_path):
    demand = prepare(PORTFOLIO)
    report = run(demand, budget=40000)
    written = deliverables.write_all(report, tmp_path, client="Acme")
    assert written["excel"].exists()
    assert written["report"].exists()
    assert written["csv"].exists()
    md = written["report"].read_text(encoding="utf-8")
    assert "Inventory Optimization — Acme" in md
    assert "Methodology" in md


def test_deliverables_defuse_formula_injection_in_product_id(tmp_path):
    """A CSV-uploaded product_id like '=cmd|...' must not survive into the
    client's .xlsx as a live formula (OWASP CSV/Excel injection)."""
    payload = '=cmd|" /C calc"!A0'
    demand = prepare(PORTFOLIO)
    demand.loc[demand["product_id"] == "SKU-A", "product_id"] = payload
    report = run(demand, budget=40000)
    written = deliverables.write_all(report, tmp_path, client="Acme")

    wb = load_workbook(written["excel"])
    ws = wb["Recommendations"]
    col = [c.value for c in ws[1]].index("product id") + 1
    values = [ws.cell(row=r, column=col).value for r in range(2, ws.max_row + 1)]
    assert payload not in values
    assert any(v == "'" + payload for v in values)

    df = pd.read_csv(written["csv"])
    assert payload not in set(df["product_id"])
    assert "'" + payload in set(df["product_id"])


# ---- pricing playbook --------------------------------------------------------

def test_pricing_playbook_runs_and_passes_qa():
    demand = prepare_pricing(PRICING)
    assert "price" in demand.columns
    report = run_pricing(demand)
    assert report.n_skus == 6
    assert qa.verify_pricing(report) == []
    # elastic SKUs yield a confident move; inelastic ones are flagged
    assert report.n_actionable >= 1
    assert report.n_inelastic >= 1


def test_pricing_optimal_above_cost_for_actionable():
    report = run_pricing(prepare_pricing(PRICING))
    for r in report.recommendations:
        if r.action in {"raise", "lower"}:
            assert r.optimal_price is not None and r.optimal_price > r.unit_cost
            assert r.elasticity < -1


def test_pricing_qa_catches_tampered_rec():
    report = run_pricing(prepare_pricing(PRICING))
    actionable = next(r for r in report.recommendations if r.action in {"raise", "lower"})
    object.__setattr__(actionable, "optimal_price", actionable.unit_cost * 0.5)  # below cost
    assert any("cost" in i for i in qa.verify_pricing(report))


def test_pricing_with_no_cost_column_is_never_reported_confident():
    """Regression: a recommendation built on an IMPUTED cost (no real cost column,
    so unit_cost = cost_ratio * price - a guess) could previously still be
    reported at full confidence, e.g. "raise 18%, +22% profit" built on a
    fabricated cost. Elastic, otherwise clearly-actionable data must still come
    back not-confident once the cost itself is not real."""
    rng = np.random.default_rng(9)
    prices = np.linspace(8, 24, 30)
    quantities = 8000 * prices ** (-2.0) * rng.normal(1.0, 0.02, size=prices.size)
    demand = pd.DataFrame({
        "product_id": ["SKU-X"] * len(prices),
        "price": prices,
        "quantity": quantities,
        # deliberately no "cost" column
    })

    report = run_pricing(demand)

    rec = report.recommendations[0]
    assert rec.cost_is_imputed is True
    assert rec.action in {"raise", "lower"}  # the underlying signal is real...
    assert rec.confident is False  # ...but must not be reported as trustworthy


def test_pricing_with_a_real_cost_column_can_be_confident():
    """Same elastic signal, but with a genuine cost column - confidence is
    governed by fit quality/extrapolation only, not suppressed by imputation."""
    rng = np.random.default_rng(9)
    prices = np.linspace(8, 24, 30)
    quantities = 8000 * prices ** (-2.0) * rng.normal(1.0, 0.02, size=prices.size)
    demand = pd.DataFrame({
        "product_id": ["SKU-X"] * len(prices),
        "price": prices,
        "quantity": quantities,
        "cost": [5.0] * len(prices),
    })

    report = run_pricing(demand)

    rec = report.recommendations[0]
    assert rec.cost_is_imputed is False
    assert rec.confident is True


def test_pricing_deliverables_written(tmp_path):
    report = run_pricing(prepare_pricing(PRICING))
    written = deliverables.write_pricing_all(report, tmp_path, client="Acme")
    assert all(p.exists() for p in written.values())
    md = written["report"].read_text(encoding="utf-8")
    assert "Price Optimization — Acme" in md
    assert "Elasticity" in md


def test_pricing_deliverables_defuse_formula_injection_in_product_id(tmp_path):
    """Same OWASP CSV/Excel injection guard, for the pricing deliverable path."""
    payload = '=cmd|" /C calc"!A0'
    rng = np.random.default_rng(9)
    prices = np.linspace(8, 24, 30)
    quantities = 8000 * prices ** (-2.0) * rng.normal(1.0, 0.02, size=prices.size)
    demand = pd.DataFrame({
        "product_id": [payload] * len(prices),
        "price": prices,
        "quantity": quantities,
        "cost": [5.0] * len(prices),
    })
    report = run_pricing(demand)
    written = deliverables.write_pricing_all(report, tmp_path, client="Acme")

    wb = load_workbook(written["excel"])
    ws = wb["Price recommendations"]
    col = [c.value for c in ws[1]].index("product id") + 1
    values = [ws.cell(row=r, column=col).value for r in range(2, ws.max_row + 1)]
    assert payload not in values
    assert any(v == "'" + payload for v in values)

    df = pd.read_csv(written["csv"])
    assert payload not in set(df["product_id"])
    assert "'" + payload in set(df["product_id"])
