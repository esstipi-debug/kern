"""Tests for the supplier-sourcing agent job + tool (7th tool).

Aggregates supplier delivery records into OTIF/lead/PPM scorecards (pandas directly,
not the parallel loop's intake.py), ranks suppliers by TOPSIS over those criteria, and
the tool wires it into the orchestrator so "select the best supplier" produces the deck.
"""

from pathlib import Path

import pandas as pd
import pytest

from jobs import sourcing_job as srcj
from scm_agent import intent, llm, tools
from scm_agent.orchestrator import Orchestrator
from src.deliverable import Deliverable


def _deliveries_df() -> pd.DataFrame:
    return pd.DataFrame({
        "supplier": ["Alpha"] * 4 + ["Beta"] * 4,
        "on_time": [1, 1, 1, 0, 1, 0, 0, 1],
        "in_full": [1, 1, 1, 1, 1, 1, 0, 1],
        "lead_time_days": [7, 7, 8, 7, 12, 11, 13, 12],
        "units": [100, 100, 100, 100, 100, 100, 100, 100],
        "defects": [0, 1, 0, 0, 2, 3, 1, 2],
        "unit_price": [10.0, 10.0, 10.0, 10.0, 9.0, 9.0, 9.0, 9.0],
    })


# -- scoring ------------------------------------------------------------------


def test_score_suppliers_aggregates_otif_and_ppm():
    cards, prices = srcj.score_suppliers(
        _deliveries_df(), supplier_col="supplier", on_time_col="on_time", in_full_col="in_full",
        lead_col="lead_time_days", units_col="units", defects_col="defects", price_col="unit_price",
    )

    by = {c.supplier: c for c in cards}
    assert by["Alpha"].otif == pytest.approx(0.75)        # 3 of 4 on-time-in-full
    assert by["Alpha"].ppm == pytest.approx(2500.0)       # 1 defect / 400 units * 1e6
    assert by["Beta"].otif == pytest.approx(0.5)
    assert prices["Alpha"] == pytest.approx(10.0)


# -- prepare ------------------------------------------------------------------


def test_prepare_reads_a_delivery_csv(tmp_path):
    csv = tmp_path / "deliv.csv"
    _deliveries_df().to_csv(csv, index=False)

    payload = srcj.prepare(str(csv), {})

    assert {c.supplier for c in payload["scorecards"]} == {"Alpha", "Beta"}


def test_prepare_errors_without_a_supplier_column(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1], "on_time": [1]}).to_csv(csv, index=False)

    with pytest.raises(ValueError, match="supplier_col"):
        srcj.prepare(str(csv), {})


# -- run + qa -----------------------------------------------------------------


def test_run_ranks_the_stronger_supplier_first():
    cards, prices = srcj.score_suppliers(
        _deliveries_df(), supplier_col="supplier", on_time_col="on_time", in_full_col="in_full",
        lead_col="lead_time_days", units_col="units", defects_col="defects", price_col="unit_price",
    )

    report = srcj.run(cards, prices)

    assert report.recommended == "Alpha"                  # better OTIF, lead, quality
    assert report.outcome.status == "options"
    assert srcj.verify(report) == []


# -- deck ---------------------------------------------------------------------


def test_build_deck_is_an_ascii_deliverable_with_the_award():
    cards, prices = srcj.score_suppliers(
        _deliveries_df(), supplier_col="supplier", on_time_col="on_time", in_full_col="in_full",
        lead_col="lead_time_days", units_col="units", defects_col="defects", price_col="unit_price",
    )
    report = srcj.run(cards, prices)

    deck = srcj.build_deck(report, client="Acme", citations=("Rezaei - BWM",), confidence=0.85)

    assert isinstance(deck, Deliverable)
    md = deck.to_markdown()
    assert md.isascii()
    assert "Alpha" in md and "## Coverage & handoff" in md


# -- routing ------------------------------------------------------------------


def test_brief_routes_to_sourcing():
    reg = tools.build_default_registry()
    res = intent.classify("select the best supplier / sourcing award by OTIF and price",
                          reg, llm.RulesFallback())
    assert res.job_type == "sourcing"


def test_sourcing_keywords_do_not_steal_other_briefs():
    reg = tools.build_default_registry()
    p = llm.RulesFallback()
    assert intent.classify("set up reorder points and safety stock", reg, p).job_type == "inventory_optimization"
    assert intent.classify("classify our inventory ABC-XYZ", reg, p).job_type == "abc_xyz"
    assert intent.classify("cost to serve by segment", reg, p).job_type == "cost_to_serve"


# -- end-to-end ---------------------------------------------------------------


def test_orchestrator_runs_sourcing_and_emits_the_deck(tmp_path):
    csv = tmp_path / "deliv.csv"
    _deliveries_df().to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())

    res = orch.run("select the best supplier by OTIF, lead time and price", data_path=str(csv),
                   client="Acme", out_dir=tmp_path)

    assert res.status == "ok"
    assert res.tool == "sourcing"
    assert "csv" in res.deliverables
    deck = Path(res.deliverables["deck_report"])
    assert deck.exists()
    assert "Alpha" in deck.read_text(encoding="utf-8")
