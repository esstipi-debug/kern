"""Tests for the learning-curve (cost-down) agent tool."""

from pathlib import Path

import pandas as pd
import pytest

from jobs import learning_curve_job as lcj
from scm_agent import intent, llm, tools
from scm_agent.orchestrator import Orchestrator
from src.guided import OPTIONS


def _products_df() -> pd.DataFrame:
    return pd.DataFrame({
        "product": ["P1"],
        "first_unit_cost": [100.0],
        "learning_rate": [0.8],
        "planned_volume": [4],
    })


def test_prepare_reads_products(tmp_path):
    csv = tmp_path / "p.csv"
    _products_df().to_csv(csv, index=False)
    records = lcj.prepare(str(csv), {})
    assert records[0]["first_unit_cost"] == 100.0 and records[0]["planned_volume"] == 4


def test_run_projects_cost_down():
    report = lcj.run(lcj.prepare_records(_products_df()))
    p = report.products[0]
    assert p.projected_unit_cost == pytest.approx(64.0)         # 100 * 4^(ln0.8/ln2)
    assert p.total_cost == pytest.approx(314.21, abs=0.5)       # 100 + 80 + 70.21 + 64
    assert p.savings == pytest.approx(400 - 314.21, abs=0.5) and report.total_savings > 0
    assert lcj.verify(report) == []


def test_build_deck_ascii():
    report = lcj.run(lcj.prepare_records(_products_df()))
    md = lcj.build_deck(report, client="Acme", citations=("Jacobs & Chase ch.6",), confidence=0.85).to_markdown()
    assert md.isascii() and "Cost-Down" in md and "## Coverage & handoff" in md


def test_routes_and_runs_end_to_end(tmp_path):
    csv = tmp_path / "p.csv"
    _products_df().to_csv(csv, index=False)
    reg = tools.build_default_registry()
    assert intent.classify("project the cost-down via the learning curve at volume",
                           reg, llm.RulesFallback()).job_type == "learning_curve"
    orch = Orchestrator(registry=reg, provider=llm.RulesFallback())
    res = orch.run("learning curve cost reduction with volume", data_path=str(csv), client="Acme", out_dir=tmp_path)
    assert res.status == "ok" and res.tool == "learning_curve"
    assert Path(res.deliverables["deck_report"]).exists()
    assert res.guided is not None and res.guided.status == OPTIONS
