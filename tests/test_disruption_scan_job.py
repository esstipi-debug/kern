"""Tests for the disruption scan job (jobs/disruption_scan_job.py). No network."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from jobs import disruption_scan_job as J
from nosilo_core.guided import EXECUTED, HANDOFF, OPTIONS
from src import disruption as D

FIXTURES = Path(__file__).parent / "fixtures" / "gdelt"
_ACME = (FIXTURES / "acme_electronics.json").read_text(encoding="utf-8")
_EMPTY = (FIXTURES / "empty.json").read_text(encoding="utf-8")
_NOW = datetime(2026, 7, 24, tzinfo=timezone.utc)


def _suppliers_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "supplier": ["Acme Electronics", "Calm Supplier"],
            "country": ["Brazil", "New Zealand"],
            "annual_spend": [4_200_000, 500_000],
        }
    )


def _acme_only_fetcher(url: str) -> str:
    return _ACME if "Acme" in url else _EMPTY


# -- prepare ----------------------------------------------------------------

def test_prepare_sniffs_supplier_country_spend():
    rows = J.prepare_records(_suppliers_df())
    assert len(rows) == 2
    assert rows[0].supplier == "Acme Electronics"
    assert rows[0].country == "Brazil"
    assert rows[0].annual_spend == 4_200_000


def test_prepare_needs_a_supplier_column():
    with pytest.raises(ValueError, match="supplier column"):
        J.prepare_records(pd.DataFrame({"foo": [1], "bar": [2]}))


def test_prepare_tolerates_missing_country_and_spend():
    rows = J.prepare_records(pd.DataFrame({"vendor": ["Solo Corp"]}))
    assert rows[0].supplier == "Solo Corp"
    assert rows[0].country == ""
    assert rows[0].annual_spend == 0.0


def test_prepare_coerces_messy_spend_instead_of_aborting():
    # a messy-but-present spend cell must not sink the whole file
    df = pd.DataFrame({
        "supplier": ["Clean Co", "Messy Co", "Currency Co", "NA Co"],
        "annual_spend": ["1000000", "1,200,000", "$4.2M", "N/A"],
    })
    rows = J.prepare_records(df)
    assert len(rows) == 4
    by = {r.supplier: r.annual_spend for r in rows}
    assert by["Clean Co"] == 1_000_000
    assert by["Messy Co"] == 0.0       # unparseable -> unknown, not a crash
    assert by["Currency Co"] == 0.0
    assert by["NA Co"] == 0.0


def test_prepare_treats_negative_spend_as_unknown():
    rows = J.prepare_records(pd.DataFrame({"supplier": ["Neg Co"], "annual_spend": [-5000]}))
    assert rows[0].annual_spend == 0.0


def test_prepare_reads_the_fixture_csv():
    rows = J.prepare(str(Path(__file__).parent / "fixtures" / "disruption" / "suppliers.csv"))
    assert len(rows) == 5
    assert any(r.supplier == "Pacific Freight Co" for r in rows)


# -- run: the three guided outcomes -----------------------------------------

def test_run_with_signal_yields_ranked_options():
    rows = J.prepare_records(_suppliers_df())
    report = J.run(rows, fetcher=_acme_only_fetcher, now=_NOW)
    assert report.outcome.status == OPTIONS
    assert report.n_signalled == 1
    assert report.signals[0].supplier == "Acme Electronics"   # ranked first
    assert report.outcome.options[0].label == "Acme Electronics"
    assert report.outcome.options[0].recommended is True
    # the signal really flowed through the risk engine
    assert report.risk_report.assessments[0].name == "Disruption exposure: Acme Electronics"
    assert not J.verify(report)


def test_run_all_clean_is_executed_and_passes_qa():
    rows = J.prepare_records(_suppliers_df())
    report = J.run(rows, fetcher=lambda url: _EMPTY, now=_NOW)
    assert report.outcome.status == EXECUTED
    assert report.n_signalled == 0
    assert report.n_failed == 0
    assert not J.verify(report)


def test_run_all_failed_is_handoff_and_fails_qa():
    def broken(url: str) -> str:
        raise D.GdeltUnavailable("down")

    rows = J.prepare_records(_suppliers_df())
    report = J.run(rows, fetcher=broken, now=_NOW)
    assert report.outcome.status == HANDOFF
    assert report.n_failed == 2
    issues = J.verify(report)
    assert any("every GDELT query failed" in i for i in issues)


def test_run_partial_failure_no_signal_discloses_unknown_not_clear():
    # one supplier unreachable, the other clean -> EXECUTED but the summary must
    # NOT claim the unreachable supplier is clear.
    def flaky(url: str) -> str:
        if "Acme" in url:
            raise D.GdeltUnavailable("down")
        return _EMPTY

    rows = J.prepare_records(_suppliers_df())
    report = J.run(rows, fetcher=flaky, now=_NOW)
    assert report.outcome.status == EXECUTED
    assert report.n_failed == 1
    assert "unknown, not clear" in report.summary
    assert report.outcome.confidence < 0.6  # partial coverage lowers confidence
    assert not J.verify(report)  # partial is disclosed, not a QA failure


def test_run_partial_failure_with_signal_discloses_gap_in_summary():
    def flaky(url: str) -> str:
        if "Acme" in url:
            return _ACME
        raise D.GdeltUnavailable("down")

    rows = J.prepare_records(_suppliers_df())
    report = J.run(rows, fetcher=flaky, now=_NOW)
    assert report.outcome.status == OPTIONS
    assert report.n_failed == 1
    assert "could not be retrieved" in report.summary


# -- write_operational ------------------------------------------------------

def test_write_operational_one_row_per_supplier(tmp_path):
    rows = J.prepare_records(_suppliers_df())
    report = J.run(rows, fetcher=_acme_only_fetcher, now=_NOW)
    out = J.write_operational(report, tmp_path)
    df = pd.read_csv(out["csv"])
    assert len(df) == 2
    assert set(df["supplier"]) == {"Acme Electronics", "Calm Supplier"}
    acme = df[df["supplier"] == "Acme Electronics"].iloc[0]
    assert acme["signal"] == "flag"
    assert acme["articles"] == 5
    calm = df[df["supplier"] == "Calm Supplier"].iloc[0]
    assert calm["signal"] == "clear"


# -- build_deck -------------------------------------------------------------

def test_build_deck_is_ascii_and_cites_gdelt():
    rows = J.prepare_records(_suppliers_df())
    report = J.run(rows, fetcher=_acme_only_fetcher, now=_NOW)
    deck = J.build_deck(report, client="TestCo")
    md = deck.to_markdown()
    assert md.isascii()
    assert "Supplier Disruption Exposure Scan" in md
    assert "GDELT" in md
    assert "Acme Electronics" in md


def test_deck_labels_the_category_as_inferred_not_asserted_fact():
    # honesty moat: the headline-keyword category must never be stated as fact
    rows = J.prepare_records(_suppliers_df())
    report = J.run(rows, fetcher=_acme_only_fetcher, now=_NOW)
    deck = J.build_deck(report)
    md = deck.to_markdown().lower()
    assert "inferred from headlines" in md
    # the top supplier's category is qualified, never a bare "dominant signal: logistics"
    top_cat = report.signals[0].dominant_category
    assert f"dominant signal: {top_cat}." not in md
    assert f"likely {top_cat}" in md


def test_build_deck_clean_scan_still_renders():
    rows = J.prepare_records(_suppliers_df())
    report = J.run(rows, fetcher=lambda url: _EMPTY, now=_NOW)
    deck = J.build_deck(report)
    md = deck.to_markdown()
    assert md.isascii()
    assert "No disruption" in md or "screen clear" in md.lower()
