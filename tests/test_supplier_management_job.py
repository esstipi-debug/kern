"""Kraljic supplier-segmentation job: suppliers CSV -> normalized drivers -> deck."""

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
