"""Tests for the inventory-reconciliation / IRA agent job + tool (12th tool).

Reads a count CSV (system vs physical qty + unit cost), reconciles against a tolerance band,
and reports inventory record accuracy (IRA), the dollar impact of variances, and the worst
discrepancies. The tool wires it into the orchestrator so "reconcile the physical count and
report inventory accuracy" produces the IRA study deck.
"""

from pathlib import Path

import pandas as pd
import pytest

from jobs import reconciliation_job as rcj
from scm_agent import intent, llm, tools
from scm_agent.orchestrator import Orchestrator
from src.deliverable import Deliverable


def _counts_df() -> pd.DataFrame:
    return pd.DataFrame({
        "product_id": ["SKU-A", "SKU-B", "SKU-C"],
        "system_qty": [100.0, 200.0, 50.0],
        "physical_qty": [100.0, 180.0, 52.0],
        "unit_cost": [5.0, 10.0, 2.0],
    })


# -- prepare ------------------------------------------------------------------


def test_prepare_reads_count_lines(tmp_path):
    csv = tmp_path / "counts.csv"
    _counts_df().to_csv(csv, index=False)

    records = rcj.prepare(str(csv), {})

    by = {r["product_id"]: r for r in records}
    assert by["SKU-B"]["system_qty"] == 200.0 and by["SKU-B"]["physical_qty"] == 180.0


def test_prepare_errors_without_count_columns(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1]}).to_csv(csv, index=False)

    with pytest.raises(ValueError, match="system|physical|product"):
        rcj.prepare(str(csv), {})


# -- run + qa -----------------------------------------------------------------


def test_run_computes_ira_and_variance_value():
    report = rcj.run(rcj.prepare_records(_counts_df()), tolerance_units=0.0)

    assert report.n_counted == 3
    assert report.n_within == 1                       # only SKU-A is exact
    assert report.ira == pytest.approx(1 / 3)
    assert report.total_variance_value == pytest.approx(204.0)  # 20*10 + 2*2
    assert report.worst[0].product_id == "SKU-B"      # biggest $ impact
    assert rcj.verify(report) == []


def test_tolerance_widens_what_counts_as_accurate():
    report = rcj.run(rcj.prepare_records(_counts_df()), tolerance_units=2.0)
    # SKU-A (0) and SKU-C (2) now within tolerance; SKU-B (20) still out.
    assert report.n_within == 2
    assert report.ira == pytest.approx(2 / 3)


# -- deck ---------------------------------------------------------------------


def test_build_deck_is_an_ascii_deliverable():
    report = rcj.run(rcj.prepare_records(_counts_df()))

    deck = rcj.build_deck(report, client="Acme", citations=("Piasecki - Inventory Accuracy",), confidence=0.85)

    assert isinstance(deck, Deliverable)
    md = deck.to_markdown()
    assert md.isascii()
    assert "accuracy" in md.lower() and "## Coverage & handoff" in md


# -- routing ------------------------------------------------------------------


def test_brief_routes_to_reconciliation():
    reg = tools.build_default_registry()
    res = intent.classify("reconcile the physical count vs system and report inventory record accuracy",
                          reg, llm.RulesFallback())
    assert res.job_type == "reconciliation"


def test_accuracy_keywords_do_not_steal_other_briefs():
    reg = tools.build_default_registry()
    p = llm.RulesFallback()
    assert intent.classify("set up reorder points and safety stock", reg, p).job_type == "inventory_optimization"
    assert intent.classify("run a what-if sensitivity tornado analysis", reg, p).job_type == "whatif"


# -- end-to-end ---------------------------------------------------------------


def test_orchestrator_runs_reconciliation_and_emits_the_deck(tmp_path):
    csv = tmp_path / "counts.csv"
    _counts_df().to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())

    res = orch.run("reconcile our physical count vs the system and report inventory record accuracy",
                   data_path=str(csv), client="Acme", out_dir=tmp_path)

    assert res.status == "ok"
    assert res.tool == "reconciliation"
    assert "csv" in res.deliverables
    assert Path(res.deliverables["deck_report"]).exists()
