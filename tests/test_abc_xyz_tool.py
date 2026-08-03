"""Tests for the ABC-XYZ classification agent job + tool (6th tool).

The job aggregates a per-SKU demand history into the classifier's item shape (pandas
directly - not the parallel loop's intake.py), runs the ABC-XYZ matrix, and the tool
wires it into the orchestrator so "classify our inventory ABC-XYZ" produces the deck.
"""

from pathlib import Path

import pandas as pd
import pytest

from jobs import abc_xyz_job as axj
from nosilo_core import llm
from scm_agent import intent, tools
from scm_agent.orchestrator import Orchestrator
from src.deliverable import Deliverable

PORTFOLIO = "data/sample_demand_portfolio.csv"


def _portfolio_df() -> pd.DataFrame:
    return pd.DataFrame({
        "product_id": ["A", "A", "A", "B", "B"],
        "quantity": [100, 100, 100, 10, 10],
        "unit_cost": [50.0, 50.0, 50.0, 5.0, 5.0],
    })


# -- aggregation --------------------------------------------------------------


def test_aggregate_builds_one_item_per_sku_with_its_demand_series():
    items = axj.aggregate_skus(_portfolio_df(), product_col="product_id",
                               demand_col="quantity", cost_col="unit_cost")

    by = {it["product_id"]: it for it in items}
    assert by["A"]["demand"] == [100.0, 100.0, 100.0]
    assert by["A"]["unit_cost"] == pytest.approx(50.0)
    assert by["B"]["demand"] == [10.0, 10.0]


# -- prepare ------------------------------------------------------------------


def test_prepare_reads_a_portfolio_csv(tmp_path):
    csv = tmp_path / "p.csv"
    _portfolio_df().to_csv(csv, index=False)

    payload = axj.prepare(str(csv), {})

    assert {it["product_id"] for it in payload["items"]} == {"A", "B"}


def test_prepare_errors_without_product_or_cost_column(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1], "quantity": [2]}).to_csv(csv, index=False)

    with pytest.raises(ValueError, match="product_col|cost_col"):
        axj.prepare(str(csv), {})


# -- run + qa -----------------------------------------------------------------


def test_run_classifies_high_value_sku_as_a_and_long_tail_as_c():
    items = axj.aggregate_skus(_portfolio_df(), product_col="product_id",
                               demand_col="quantity", cost_col="unit_cost")

    report = axj.run(items)

    by = {c.product_id: c for c in report.classifications}
    assert by["A"].abc == "A"
    assert by["A"].cell == "AX"            # stable demand -> X
    assert by["B"].abc == "C"
    assert report.n_a == 1
    assert report.a_value_share > 0.9      # A drives nearly all the value
    assert axj.verify(report) == []


def test_verify_flags_an_empty_portfolio():
    report = axj.run([])
    assert axj.verify(report)              # non-empty issues list


# -- deck ---------------------------------------------------------------------


def test_build_deck_is_a_deliverable_grounded_with_citations():
    items = axj.aggregate_skus(_portfolio_df(), product_col="product_id",
                               demand_col="quantity", cost_col="unit_cost")
    report = axj.run(items)

    deck = axj.build_deck(report, client="Acme", citations=("Silver Pyke Thomas - ABC",), confidence=0.9)

    assert isinstance(deck, Deliverable)
    md = deck.to_markdown()
    assert md.isascii()                    # cp1252-safe
    assert "ABC" in md and "## Coverage & handoff" in md


# -- routing ------------------------------------------------------------------


def test_brief_routes_to_abc_xyz():
    reg = tools.build_default_registry()
    res = intent.classify("run an ABC-XYZ classification of our inventory portfolio",
                          reg, llm.RulesFallback())
    assert res.job_type == "abc_xyz"


def test_abc_keywords_do_not_steal_other_briefs():
    reg = tools.build_default_registry()
    p = llm.RulesFallback()
    assert intent.classify("set up reorder points and safety stock", reg, p).job_type == "inventory_optimization"
    assert intent.classify("what price maximizes profit", reg, p).job_type == "pricing"
    assert intent.classify("build the monthly S&OP plan", reg, p).job_type == "sop"


# -- end-to-end ---------------------------------------------------------------


def test_orchestrator_runs_abc_xyz_and_emits_the_deck(tmp_path):
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())

    res = orch.run("classify the inventory portfolio with ABC-XYZ analysis", data_path=PORTFOLIO,
                   client="Acme", out_dir=tmp_path)

    assert res.status == "ok"
    assert res.tool == "abc_xyz"
    assert "csv" in res.deliverables
    deck = Path(res.deliverables["deck_report"])
    assert deck.exists()
    assert "ABC" in deck.read_text(encoding="utf-8")
