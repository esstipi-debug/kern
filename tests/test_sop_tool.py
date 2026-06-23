"""Tests for the S&OP agent job + tool (closes more of the 3-tools gap).

The job periodizes a demand history into a monthly horizon (pandas directly, NOT via the
parallel loop's intake.py), runs the S&OP cadence, and the tool wires it into the
orchestrator so a brief like "build the monthly S&OP plan" produces the deck end-to-end.
"""

from pathlib import Path

import pandas as pd
import pytest

from jobs import sop_job as sopj
from scm_agent import intent, llm, tools
from scm_agent.orchestrator import Orchestrator


def _history() -> pd.DataFrame:
    return pd.DataFrame({
        "Order Date": ["2026-01-05", "2026-01-20", "2026-02-10", "2026-02-15", "2026-03-01"],
        "Quantity": [10, 20, 15, 5, 30],
    })


# -- periodization ------------------------------------------------------------


def test_prepare_periodizes_demand_into_a_monthly_horizon(tmp_path):
    csv = tmp_path / "hist.csv"
    _history().to_csv(csv, index=False)

    payload = sopj.prepare(str(csv), {})

    assert payload["demand"] == pytest.approx([30.0, 20.0, 30.0])   # Jan 30, Feb 20, Mar 30
    assert payload["labels"] == ["2026-01", "2026-02", "2026-03"]


def test_prepare_needs_at_least_two_periods(tmp_path):
    csv = tmp_path / "hist.csv"
    pd.DataFrame({"Order Date": ["2026-01-05"], "Quantity": [10]}).to_csv(csv, index=False)

    with pytest.raises(ValueError, match="two periods"):
        sopj.prepare(str(csv), {})


def test_prepare_errors_without_date_or_demand_column(tmp_path):
    csv = tmp_path / "hist.csv"
    pd.DataFrame({"foo": [1], "bar": [2]}).to_csv(csv, index=False)

    with pytest.raises(ValueError, match="date_col|demand_col"):
        sopj.prepare(str(csv), {})


# -- run + qa -----------------------------------------------------------------


def test_run_produces_a_protected_review_that_passes_qa():
    payload = {"demand": [100.0, 120.0, 80.0, 100.0], "labels": ["M1", "M2", "M3", "M4"]}

    review = sopj.run(payload, opening_inventory=50.0, target=20.0)

    assert review.outcome.status == "options"
    assert review.recommended.name in {"Chase", "Level", "Hybrid"}
    assert sopj.verify(review) == []


# -- routing ------------------------------------------------------------------


def test_brief_routes_to_sop():
    reg = tools.build_default_registry()
    res = intent.classify("build the monthly S&OP plan / sales and operations cadence",
                          reg, llm.RulesFallback())
    assert res.job_type == "sop"


def test_sop_keywords_do_not_steal_existing_briefs():
    reg = tools.build_default_registry()
    p = llm.RulesFallback()
    assert intent.classify("set up reorder points and safety stock", reg, p).job_type == "inventory_optimization"
    assert intent.classify("what price maximizes profit", reg, p).job_type == "pricing"
    assert intent.classify("cost to serve and working capital by segment", reg, p).job_type == "cost_to_serve"


# -- end-to-end through the orchestrator --------------------------------------


def test_orchestrator_runs_sop_and_emits_the_deck(tmp_path):
    csv = tmp_path / "hist.csv"
    _history().to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())

    res = orch.run("build the monthly sales and operations (S&OP) plan", data_path=str(csv),
                   client="Acme", out_dir=tmp_path)

    assert res.status == "ok"
    assert res.tool == "sop"
    assert "csv" in res.deliverables                      # operational per-period plan
    deck = Path(res.deliverables["deck_report"])
    assert deck.exists()
    assert "S&OP" in deck.read_text(encoding="utf-8")
