"""Tests for the multi-echelon (serial GSM) agent tool.

Wires src.multi_echelon into the orchestrator: a serial-chain CSV (stage, lead time,
holding cost + end-customer demand) -> the cost-minimizing safety-stock placement across
the echelons, with ranked stocking-strategy options on success.
"""

from pathlib import Path

import pandas as pd
import pytest

from jobs import multi_echelon_job as me
from scm_agent import intent, llm, tools
from scm_agent.orchestrator import Orchestrator
from src.deliverable import Deliverable
from src.guided import OPTIONS


def _chain_df() -> pd.DataFrame:
    return pd.DataFrame({
        "stage": ["Supplier", "DC", "Store"],
        "lead_time": [2, 1, 1],
        "holding_cost": [0.5, 1.0, 2.0],
        "mean_demand": [100.0, 100.0, 100.0],
        "demand_std": [25.0, 25.0, 25.0],
    })


def test_prepare_reads_chain_and_demand(tmp_path):
    csv = tmp_path / "chain.csv"
    _chain_df().to_csv(csv, index=False)
    payload = me.prepare(str(csv), {})
    assert [s["name"] for s in payload["stages"]] == ["Supplier", "DC", "Store"]
    assert payload["mean_demand"] == 100.0 and payload["demand_std"] == 25.0


def test_prepare_errors_without_chain_columns(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1]}).to_csv(csv, index=False)
    with pytest.raises(ValueError, match="stage|lead_time|holding_cost"):
        me.prepare(str(csv), {})


def test_prepare_errors_without_demand(tmp_path):
    df = pd.DataFrame({"stage": ["A", "B"], "lead_time": [1, 1], "holding_cost": [1.0, 2.0]})
    csv = tmp_path / "nodemand.csv"
    df.to_csv(csv, index=False)
    with pytest.raises(ValueError, match="demand"):
        me.prepare(str(csv), {})


def test_run_places_safety_stock_and_levels():
    report = me.run(me.prepare_records(_chain_df()))
    assert report.n_stages == 3
    assert report.total_holding_cost > 0          # demand_std > 0 -> some safety stock held
    assert report.n_stocking >= 1
    # echelon order-up-to is non-increasing downstream (cumulative from the demand node up)
    echelons = [ln.echelon_order_up_to for ln in report.stages]
    assert echelons[0] >= echelons[-1]
    # integer lead times -> the optional fill-rate simulation runs
    assert report.achieved_fill_rate is not None
    assert 0.0 <= report.achieved_fill_rate <= 1.0
    assert me.verify(report) == []


def test_build_deck_is_ascii_deliverable():
    report = me.run(me.prepare_records(_chain_df()))
    deck = me.build_deck(report, client="Acme", citations=("Vandeput (2020) ch.10",), confidence=0.85)
    assert isinstance(deck, Deliverable)
    md = deck.to_markdown()
    assert md.isascii()
    assert "Multi-Echelon" in md and "## Coverage & handoff" in md


def test_brief_routes_to_multi_echelon():
    reg = tools.build_default_registry()
    res = intent.classify(
        "multi-echelon network inventory: optimize safety stock placement and inventory "
        "positioning across echelons",
        reg, llm.RulesFallback(),
    )
    assert res.job_type == "multi_echelon"


def test_orchestrator_runs_multi_echelon_with_ranked_options(tmp_path):
    csv = tmp_path / "chain.csv"
    _chain_df().to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())
    res = orch.run("multi-echelon safety stock placement across the network", data_path=str(csv),
                   client="Acme", out_dir=tmp_path)
    assert res.status == "ok" and res.tool == "multi_echelon"
    assert Path(res.deliverables["deck_report"]).exists()
    assert Path(res.deliverables["csv"]).exists()
    assert res.guided is not None and res.guided.status == OPTIONS
    assert sum(1 for o in res.guided.options if o.recommended) == 1
