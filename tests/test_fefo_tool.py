"""Tests for the FEFO / lot-expiry agent tool.

Wires src.lots into the orchestrator: a lots CSV -> shelf-life aging, FEFO issue order, the
at-risk quantity demand can't sell before expiry, and a markdown-vs-scrap disposition, with
ranked options on success.
"""

from pathlib import Path

import pandas as pd
import pytest

from jobs import fefo_job as fj
from scm_agent import intent, llm, tools
from scm_agent.orchestrator import Orchestrator
from src.deliverable import Deliverable
from src.guided import OPTIONS


def _lots_df() -> pd.DataFrame:
    return pd.DataFrame({
        "product_id": ["P", "P", "P"],
        "lot_id": ["fresh", "soon", "dead"],
        "quantity": [10, 10, 4],
        "days_to_expiry": [40, 5, -1],
        "unit_cost": [5, 5, 5],
        "unit_price": [12, 12, 12],
        "daily_demand": [1, 1, 1],
    })


def test_prepare_reads_lots_and_demand(tmp_path):
    csv = tmp_path / "lots.csv"
    _lots_df().to_csv(csv, index=False)
    payload = fj.prepare(str(csv), {})
    assert len(payload["lots"]) == 3
    assert payload["demand_rate_by_product"]["P"] == 1.0


def test_prepare_errors_without_expiry_info(tmp_path):
    df = pd.DataFrame({"product_id": ["P"], "lot_id": ["L"], "quantity": [5]})
    csv = tmp_path / "noexp.csv"
    df.to_csv(csv, index=False)
    with pytest.raises(ValueError, match="days_to_expiry|expiry_date"):
        fj.prepare(str(csv), {})


def test_prepare_computes_days_from_expiry_date(tmp_path):
    df = pd.DataFrame({"product_id": ["P"], "lot_id": ["L"], "quantity": [5],
                       "expiry_date": ["2026-07-10"]})
    csv = tmp_path / "dated.csv"
    df.to_csv(csv, index=False)
    payload = fj.prepare(str(csv), {"as_of": "2026-06-30"})
    assert payload["lots"][0].days_to_expiry == 10.0


def test_run_flags_aging_at_risk_and_disposition():
    report = fj.run(fj.prepare_records(_lots_df()))
    assert report.n_lots == 3
    assert report.expired_quantity == 4.0            # the "dead" lot
    assert report.at_risk_units > 0                  # rate 1/day can't clear it all before expiry
    assert report.disposition.recommended == "markdown"   # 0.5*price beats 0 scrap
    assert fj.verify(report) == []


def test_build_deck_is_ascii_deliverable():
    report = fj.run(fj.prepare_records(_lots_df()))
    deck = fj.build_deck(report, client="Acme", citations=("GS1 lot/expiry",), confidence=0.85)
    assert isinstance(deck, Deliverable)
    md = deck.to_markdown()
    assert md.isascii()
    assert "FEFO" in md and "## Coverage & handoff" in md


def test_brief_routes_to_fefo():
    reg = tools.build_default_registry()
    res = intent.classify(
        "FEFO first-expired: which lots are at expiry risk and need markdown before expiry by shelf life",
        reg, llm.RulesFallback(),
    )
    assert res.job_type == "fefo"


def test_orchestrator_runs_fefo_with_ranked_options(tmp_path):
    csv = tmp_path / "lots.csv"
    _lots_df().to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())
    res = orch.run("lot expiry risk and FEFO disposition with markdown before expiry",
                   data_path=str(csv), client="Acme", out_dir=tmp_path)
    assert res.status == "ok" and res.tool == "fefo"
    assert Path(res.deliverables["deck_report"]).exists()
    assert Path(res.deliverables["csv"]).exists()
    assert res.guided is not None and res.guided.status == OPTIONS
    assert sum(1 for o in res.guided.options if o.recommended) == 1
