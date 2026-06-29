"""Tests for the simulation (Monte-Carlo (R,S) optimization) agent tool.

Wires src.simulation_opt into the orchestrator: a per-SKU demand + cost CSV -> the safety
stock / order-up-to level that minimizes simulated total cost, vs the analytical optimum,
with ranked roll-out options on success. Uses small period counts to stay fast.
"""

from pathlib import Path

import pandas as pd
import pytest

from jobs import simulation_job as sj
from scm_agent import intent, llm, tools
from scm_agent.orchestrator import Orchestrator
from src.deliverable import Deliverable
from src.guided import OPTIONS


def _sku_df() -> pd.DataFrame:
    return pd.DataFrame({
        "product_id": ["P1"],
        "mean_demand": [100.0],
        "std_demand": [20.0],
        "lead_time": [2],
        "holding_cost": [1.0],
        "order_cost": [50.0],
        "backorder_cost": [5.0],
    })


def test_prepare_reads_records_and_periods(tmp_path):
    csv = tmp_path / "skus.csv"
    _sku_df().to_csv(csv, index=False)
    payload = sj.prepare(str(csv), {"periods": 500})
    assert payload["periods"] == 500
    rec = payload["records"][0]
    assert rec["lead_time_periods"] == 2 and rec["review_period"] == 1


def test_prepare_errors_without_required_columns(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1]}).to_csv(csv, index=False)
    with pytest.raises(ValueError, match="mean_demand|std_demand|lead_time|product_id"):
        sj.prepare(str(csv), {})


def test_run_optimizes_policy_and_reports_fill():
    report = sj.run(sj.prepare_records(_sku_df(), {"periods": 500}))
    assert report.n_skus == 1
    line = report.lines[0]
    assert line.total_cost > 0
    assert 0.0 <= line.fill_rate <= 1.0
    assert line.order_up_to_level > 0
    assert sj.verify(report) == []


def test_build_deck_is_ascii_deliverable():
    report = sj.run(sj.prepare_records(_sku_df(), {"periods": 500}))
    deck = sj.build_deck(report, client="Acme", citations=("Vandeput (2020) ch.13",), confidence=0.85)
    assert isinstance(deck, Deliverable)
    md = deck.to_markdown()
    assert md.isascii()
    assert "Simulation" in md and "## Coverage & handoff" in md


def test_brief_routes_to_simulation():
    reg = tools.build_default_registry()
    res = intent.classify(
        "monte carlo simulation optimization of the (R,S) policy safety stock",
        reg, llm.RulesFallback(),
    )
    assert res.job_type == "simulation"


def test_orchestrator_runs_simulation_with_ranked_options(tmp_path):
    csv = tmp_path / "skus.csv"
    _sku_df().to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())
    res = orch.run("monte carlo simulation optimization of the inventory policy",
                   data_path=str(csv), overrides={"periods": 400}, client="Acme", out_dir=tmp_path)
    assert res.status == "ok" and res.tool == "simulation"
    assert Path(res.deliverables["deck_report"]).exists()
    assert Path(res.deliverables["csv"]).exists()
    assert res.guided is not None and res.guided.status == OPTIONS
    assert sum(1 for o in res.guided.options if o.recommended) == 1
