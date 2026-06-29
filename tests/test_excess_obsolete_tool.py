"""Tests for the excess & obsolete (E&O) / dead-stock agent tool.

Wires src.excess_obsolete into the orchestrator: a stock CSV -> dead / excess / healthy
classification + cash at risk per SKU, with ranked release options on success.
"""

from pathlib import Path

import pandas as pd
import pytest

from jobs import excess_obsolete_job as eoj
from scm_agent import intent, llm, tools
from scm_agent.orchestrator import Orchestrator
from src.deliverable import Deliverable
from src.guided import OPTIONS


def _stock_df() -> pd.DataFrame:
    return pd.DataFrame({
        "product_id": ["dead", "excess", "healthy"],
        "on_hand": [100, 1000, 100],
        "daily_demand": [5, 5, 5],
        "unit_cost": [2, 2, 2],
        "days_since_last_sale": [200, 5, 5],
    })


def test_prepare_reads_stock(tmp_path):
    csv = tmp_path / "stock.csv"
    _stock_df().to_csv(csv, index=False)
    payload = eoj.prepare(str(csv), {})
    assert len(payload["stocks"]) == 3
    assert payload["target_cover_days"] == 90.0


def test_prepare_errors_without_required_columns(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1]}).to_csv(csv, index=False)
    with pytest.raises(ValueError, match="product_id|on_hand|daily_demand"):
        eoj.prepare(str(csv), {})


def test_run_classifies_and_sizes_cash_at_risk():
    report = eoj.run(eoj.prepare_records(_stock_df()))
    assert report.n_skus == 3
    assert report.n_dead == 1 and report.n_excess == 1 and report.n_healthy == 1
    assert report.dead_value == 200.0           # 100 * 2
    assert report.excess_value == 1100.0        # (1000 - 90*5) * 2
    assert report.eo_value == 1300.0
    assert eoj.verify(report) == []


def test_build_deck_is_ascii_deliverable():
    report = eoj.run(eoj.prepare_records(_stock_df()))
    deck = eoj.build_deck(report, client="Acme", citations=("APICS E&O",), confidence=0.85)
    assert isinstance(deck, Deliverable)
    md = deck.to_markdown()
    assert md.isascii()
    assert "Excess & Obsolete" in md and "## Coverage & handoff" in md


def test_brief_routes_to_excess_obsolete():
    reg = tools.build_default_registry()
    res = intent.classify(
        "find excess and obsolete inventory: dead stock and overstock to liquidate",
        reg, llm.RulesFallback(),
    )
    assert res.job_type == "excess_obsolete"


def test_orchestrator_runs_excess_obsolete_with_ranked_options(tmp_path):
    csv = tmp_path / "stock.csv"
    _stock_df().to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())
    res = orch.run("excess and obsolete dead stock analysis", data_path=str(csv),
                   client="Acme", out_dir=tmp_path)
    assert res.status == "ok" and res.tool == "excess_obsolete"
    assert Path(res.deliverables["deck_report"]).exists()
    assert Path(res.deliverables["csv"]).exists()
    assert res.guided is not None and res.guided.status == OPTIONS
    assert sum(1 for o in res.guided.options if o.recommended) == 1
