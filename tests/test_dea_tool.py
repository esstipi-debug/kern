"""Tests for the DEA efficiency-benchmarking agent tool."""

from pathlib import Path

import pandas as pd

from jobs import dea_job as dj
from scm_agent import intent, llm, tools
from scm_agent.orchestrator import Orchestrator
from src.guided import OPTIONS


def _units_df() -> pd.DataFrame:
    return pd.DataFrame({
        "unit": ["A", "B", "C"],
        "input_cost": [1.0, 2.0, 1.0],
        "output_revenue": [1.0, 1.0, 2.0],
    })


def test_prepare_sniffs_input_output_columns(tmp_path):
    csv = tmp_path / "u.csv"
    _units_df().to_csv(csv, index=False)
    payload = dj.prepare(str(csv), {})
    assert payload["input_cols"] == ["input_cost"] and payload["output_cols"] == ["output_revenue"]
    assert payload["names"] == ["A", "B", "C"]


def test_run_scores_the_frontier():
    report = dj.run(dj.prepare_records(_units_df()))
    by = {u.name: u for u in report.units}
    assert by["C"].is_efficient and by["C"].efficiency == 1.0   # on the frontier
    assert report.worst_unit == "B"                             # 0.25, least efficient
    assert report.n_efficient == 1
    assert dj.verify(report) == []


def test_build_deck_ascii():
    report = dj.run(dj.prepare_records(_units_df()))
    md = dj.build_deck(report, client="Acme", citations=("Jacobs & Chase ch.25",), confidence=0.85).to_markdown()
    assert md.isascii() and "DEA" in md and "## Coverage & handoff" in md


def test_routes_and_runs_end_to_end(tmp_path):
    csv = tmp_path / "u.csv"
    _units_df().to_csv(csv, index=False)
    reg = tools.build_default_registry()
    assert intent.classify("benchmark the relative efficiency of our warehouses (data envelopment)",
                           reg, llm.RulesFallback()).job_type == "dea"
    orch = Orchestrator(registry=reg, provider=llm.RulesFallback())
    res = orch.run("efficiency benchmarking of the units (DEA frontier)", data_path=str(csv), client="Acme", out_dir=tmp_path)
    assert res.status == "ok" and res.tool == "dea"
    assert Path(res.deliverables["deck_report"]).exists()
    assert res.guided is not None and res.guided.status == OPTIONS
    assert sum(1 for o in res.guided.options if o.recommended) == 1
