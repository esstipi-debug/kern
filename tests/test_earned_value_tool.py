"""Tests for the earned-value (project control) agent tool."""

from pathlib import Path

import pandas as pd
import pytest

from jobs import earned_value_job as evj
from scm_agent import intent, llm, tools
from scm_agent.orchestrator import Orchestrator
from src.guided import OPTIONS


def _tasks_df() -> pd.DataFrame:
    return pd.DataFrame({
        "task": ["T1", "T2"],
        "planned": [1000.0, 500.0],
        "earned": [800.0, 500.0],
        "actual": [900.0, 450.0],
    })


def test_prepare_reads_tasks(tmp_path):
    csv = tmp_path / "t.csv"
    _tasks_df().to_csv(csv, index=False)
    records = evj.prepare(str(csv), {})
    by = {r["task"]: r for r in records}
    assert by["T1"]["planned"] == 1000.0 and by["T2"]["actual"] == 450.0


def test_run_rolls_up_the_portfolio():
    report = evj.run(evj.prepare_records(_tasks_df()))
    assert report.n_tasks == 2
    assert report.portfolio.spi == pytest.approx(1300 / 1500)   # behind schedule
    assert report.portfolio.cpi == pytest.approx(1300 / 1350)   # over budget
    assert report.tasks[0].task == "T1"                          # worst CPI first
    assert evj.verify(report) == []


def test_build_deck_ascii():
    report = evj.run(evj.prepare_records(_tasks_df()))
    md = evj.build_deck(report, client="Acme", citations=("Jacobs & Chase ch.4",), confidence=0.85).to_markdown()
    assert md.isascii() and "Earned Value" in md and "## Coverage & handoff" in md


def test_routes_and_runs_end_to_end(tmp_path):
    csv = tmp_path / "t.csv"
    _tasks_df().to_csv(csv, index=False)
    reg = tools.build_default_registry()
    assert intent.classify("earned value project control - SPI and CPI on the tasks",
                           reg, llm.RulesFallback()).job_type == "earned_value"
    orch = Orchestrator(registry=reg, provider=llm.RulesFallback())
    res = orch.run("project earned value (schedule variance, cost variance)", data_path=str(csv), client="Acme", out_dir=tmp_path)
    assert res.status == "ok" and res.tool == "earned_value"
    assert Path(res.deliverables["deck_report"]).exists()
    assert res.guided is not None and res.guided.status == OPTIONS
