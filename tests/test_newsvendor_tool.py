"""Tests for the single-period (newsvendor) agent tool.

Wires src.newsvendor into the orchestrator: a per-SKU single-period CSV -> the
critical-ratio-optimal one-shot order quantity + expected profit, with ranked
stocking options on success.
"""

from pathlib import Path

import pandas as pd
import pytest

from jobs import newsvendor_job as nv
from scm_agent import intent, llm, tools
from scm_agent.orchestrator import Orchestrator
from src.deliverable import Deliverable
from src.guided import OPTIONS


def _df() -> pd.DataFrame:
    return pd.DataFrame({
        "product_id": ["P-perish", "P-atcost"],
        "mean_demand": [100.0, 50.0],
        "std_demand": [20.0, 10.0],
        "price": [12.0, 5.0],
        "unit_cost": [5.0, 5.0],
        "salvage_value": [1.0, 0.0],
    })


def test_prepare_reads_records(tmp_path):
    csv = tmp_path / "nv.csv"
    _df().to_csv(csv, index=False)
    records = nv.prepare(str(csv), {})
    by = {r["product_id"]: r for r in records}
    assert by["P-perish"]["mean_demand"] == 100.0
    assert by["P-perish"]["price"] == 12.0


def test_prepare_errors_without_required_columns(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1]}).to_csv(csv, index=False)
    with pytest.raises(ValueError, match="product_id|mean_demand|price|unit_cost"):
        nv.prepare(str(csv), {})


def test_run_sizes_order_and_critical_ratio():
    report = nv.run(nv.prepare_records(_df()))
    by = {ln.product_id: ln for ln in report.lines}
    assert report.n_skus == 2
    # high-margin perishable: cu=7, co=4 -> critical ratio 7/11 ~ 0.64, orders > 0
    assert 0.0 < by["P-perish"].critical_ratio < 1.0
    assert by["P-perish"].optimal_quantity > 0
    # sells at cost -> no underage incentive -> order nothing
    assert by["P-atcost"].optimal_quantity == 0
    assert nv.verify(report) == []


def test_build_deck_is_ascii_deliverable():
    report = nv.run(nv.prepare_records(_df()))
    deck = nv.build_deck(report, client="Acme", citations=("Vandeput (2020) ch.11",), confidence=0.85)
    assert isinstance(deck, Deliverable)
    md = deck.to_markdown()
    assert md.isascii()
    assert "Newsvendor" in md and "## Coverage & handoff" in md


def test_brief_routes_to_newsvendor():
    reg = tools.build_default_registry()
    res = intent.classify(
        "newsvendor: how many perishable units to order for a single-period selling window",
        reg, llm.RulesFallback(),
    )
    assert res.job_type == "newsvendor"


def test_orchestrator_runs_newsvendor_with_ranked_options(tmp_path):
    csv = tmp_path / "nv.csv"
    _df().to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())
    res = orch.run("single-period newsvendor order for perishables", data_path=str(csv),
                   client="Acme", out_dir=tmp_path)
    assert res.status == "ok" and res.tool == "newsvendor"
    assert Path(res.deliverables["deck_report"]).exists()
    assert Path(res.deliverables["csv"]).exists()
    assert res.guided is not None and res.guided.status == OPTIONS
    assert sum(1 for o in res.guided.options if o.recommended) == 1
