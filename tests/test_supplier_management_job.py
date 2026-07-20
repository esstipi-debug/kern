"""Kraljic supplier-segmentation job: suppliers CSV -> normalized drivers -> deck."""

import math

import pandas as pd
import pytest

from jobs import supplier_management_job as smj
from src.deliverable import Deliverable


def _suppliers_df() -> pd.DataFrame:
    return pd.DataFrame({
        "supplier": ["A", "B", "C", "D"],
        "annual_spend": [500.0, 300.0, 120.0, 80.0],
        "lead_time_days": [40, 8, 34, 5],       # min 5, max 40 -> min-max normalized
        "single_source": [1, 0, 1, 0],
        "defect_ppm": [3000, 100, 2500, 50],
    })


def test_normalize_drivers_min_max_scales_to_unit_interval():
    df = _suppliers_df()
    norm = smj.normalize_drivers(
        df, driver_cols={"lead": "lead_time_days", "single": "single_source", "ppm": "defect_ppm"}
    )
    # lead: A=40 -> 1.0 (max), D=5 -> 0.0 (min)
    assert norm["A"]["lead"] == pytest.approx(1.0)
    assert norm["D"]["lead"] == pytest.approx(0.0)
    # boolean single-source passes through as 0/1
    assert norm["A"]["single"] == pytest.approx(1.0)
    assert norm["B"]["single"] == pytest.approx(0.0)


def test_normalize_constant_column_is_zero_risk():
    df = pd.DataFrame({"supplier": ["X", "Y"], "annual_spend": [1.0, 1.0], "geo": [3, 3]})
    norm = smj.normalize_drivers(df, driver_cols={"geo": "geo"})
    assert norm["X"]["geo"] == pytest.approx(0.0)
    assert norm["Y"]["geo"] == pytest.approx(0.0)


def test_prepare_reads_csv_and_builds_supplier_inputs(tmp_path):
    csv = tmp_path / "sup.csv"
    _suppliers_df().to_csv(csv, index=False)
    payload = smj.prepare(str(csv), {})
    assert {s.supplier for s in payload["suppliers"]} == {"A", "B", "C", "D"}
    assert {d.name for d in payload["drivers"]}  # at least one driver detected


def test_prepare_errors_without_a_supplier_column(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1], "annual_spend": [10]}).to_csv(csv, index=False)
    with pytest.raises(ValueError, match="supplier"):
        smj.prepare(str(csv), {})


def test_prepare_errors_without_a_spend_column(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"supplier": ["A"], "lead_time_days": [10]}).to_csv(csv, index=False)
    with pytest.raises(ValueError, match="spend"):
        smj.prepare(str(csv), {})


def test_prepare_threads_supplier_col_override_into_normalize_drivers(tmp_path):
    """Regression: prepare's supplier_col override must reach normalize_drivers.

    Before the fix, normalize_drivers re-sniffed the supplier column internally
    with a hardcoded None override, ignoring params["supplier_col"]. For a CSV
    whose supplier column has a non-standard name (e.g. a Spanish/LATAM client's
    "proveedor"), that internal sniff found no standard-named column and fell
    back to keying its output by positional index -- so prepare's
    ``normed.get(str(row[supplier_col]), {})`` lookup always missed, and every
    supplier silently got risk_scores={} (supply_risk 0.0 for everyone), masking
    real risk under a wrong-but-plausible "all low risk" Kraljic answer.
    """
    csv = tmp_path / "sup_es.csv"
    pd.DataFrame({
        "proveedor": ["Acme", "Globex", "Initech"],
        "annual_spend": [500.0, 300.0, 120.0],
        "lead_time_days": [40, 8, 34],
    }).to_csv(csv, index=False)

    payload = smj.prepare(str(csv), {"supplier_col": "proveedor"})
    assert {s.supplier for s in payload["suppliers"]} == {"Acme", "Globex", "Initech"}
    assert any(s.risk_scores for s in payload["suppliers"]), (
        "every supplier's risk_scores is empty -- normalize_drivers isn't keyed "
        "by the resolved supplier_col"
    )

    report = smj.run(payload["suppliers"], payload["drivers"])
    assert any(s.supply_risk > 0.0 for s in report.segments), (
        "every supplier's supply_risk is 0.0 -- the risk axis is silently dead"
    )


def test_run_places_each_supplier_and_counts_quadrants(tmp_path):
    csv = tmp_path / "sup.csv"
    _suppliers_df().to_csv(csv, index=False)
    payload = smj.prepare(str(csv), {})
    report = smj.run(payload["suppliers"], payload["drivers"])

    by = {s.supplier: s for s in report.segments}
    assert by["A"].quadrant == "strategic"      # top spend + long lead + single-source
    assert by["D"].quadrant == "non_critical"   # low spend + low risk
    assert sum(report.quadrant_counts.values()) == 4
    assert smj.verify(report) == []


def test_write_operational_emits_one_row_per_supplier(tmp_path):
    csv = tmp_path / "sup.csv"
    _suppliers_df().to_csv(csv, index=False)
    payload = smj.prepare(str(csv), {})
    report = smj.run(payload["suppliers"], payload["drivers"])
    out = smj.write_operational(report, tmp_path, client="Acme")
    df = pd.read_csv(out["csv"])
    assert len(df) == 4
    assert {"supplier", "quadrant", "spend_share", "supply_risk", "strategy"} <= set(df.columns)


def test_build_deck_is_an_ascii_deliverable_naming_the_quadrants(tmp_path):
    csv = tmp_path / "sup.csv"
    _suppliers_df().to_csv(csv, index=False)
    payload = smj.prepare(str(csv), {})
    report = smj.run(payload["suppliers"], payload["drivers"])
    deck = smj.build_deck(report, client="Acme", citations=("Kraljic - purchasing portfolio",))
    assert isinstance(deck, Deliverable)
    md = deck.to_markdown()
    assert md.isascii()
    assert "strategic" in md and "## Coverage & handoff" in md


def test_prepare_treats_unparseable_spend_as_zero_not_nan(tmp_path):
    """One bad spend cell must not poison every supplier's spend_share.

    ``pd.to_numeric(..., errors="coerce")`` turns an unparseable cell into NaN,
    and NaN is truthy in Python (``bool(float('nan'))`` is True), so a naive
    ``... or 0.0`` fallback never catches it. Because ``segment_suppliers``
    sums ``annual_value`` across the whole batch once and divides every
    supplier's spend_share by that shared total, a single NaN would corrupt
    ALL suppliers' spend_share -- not just the bad row's.
    """
    csv = tmp_path / "sup_bad_spend.csv"
    pd.DataFrame({
        "supplier": ["A", "B", "C"],
        "annual_spend": ["500.0", "N/A", "120.0"],
        "lead_time_days": [40, 8, 34],
    }).to_csv(csv, index=False)

    payload = smj.prepare(str(csv), {})
    by_name = {s.supplier: s.annual_value for s in payload["suppliers"]}
    assert by_name["B"] == 0.0
    assert not math.isnan(by_name["B"])

    report = smj.run(payload["suppliers"], payload["drivers"])
    assert len(report.segments) == 3
    for s in report.segments:
        assert math.isfinite(s.spend_share), f"{s.supplier} has non-finite spend_share: {s.spend_share}"
        assert not math.isnan(s.spend_share)
