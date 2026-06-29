"""Tests for the cycle-count program agent tool.

Wires src.cycle_count into the orchestrator: a SKU list (ABC class explicit or derived
from value) -> a balanced count schedule (frequency per class, spread across the year),
with ranked program options on success. Plans the count cadence (M6); ``reconciliation``
measures the resulting record accuracy.
"""

from pathlib import Path

import pandas as pd
import pytest

from jobs import cycle_count_job as cc
from scm_agent import intent, llm, tools
from scm_agent.orchestrator import Orchestrator
from src.deliverable import Deliverable
from src.guided import OPTIONS


def _df_abc() -> pd.DataFrame:
    return pd.DataFrame({
        "product_id": [f"S{i}" for i in range(6)],
        "abc": ["A", "A", "B", "B", "C", "C"],
    })


def test_prepare_reads_explicit_abc(tmp_path):
    csv = tmp_path / "cc.csv"
    _df_abc().to_csv(csv, index=False)
    items = cc.prepare(str(csv), {})
    assert len(items) == 6
    assert sorted({it.abc for it in items}) == ["A", "B", "C"]


def test_prepare_classifies_from_value_when_no_abc(tmp_path):
    df = pd.DataFrame({"product_id": ["hi", "mid", "lo"], "annual_value": [1000.0, 100.0, 5.0]})
    csv = tmp_path / "v.csv"
    df.to_csv(csv, index=False)
    by = {it.product_id: it.abc for it in cc.prepare(str(csv), {})}
    assert by["hi"] == "A"     # top value share -> A
    assert by["lo"] == "C"     # tail -> C


def test_prepare_errors_without_class_or_value(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"product_id": ["x"], "foo": [1]}).to_csv(csv, index=False)
    with pytest.raises(ValueError, match="abc|value|classify"):
        cc.prepare(str(csv), {})


def test_run_builds_balanced_schedule():
    report = cc.run(cc.prepare_records(_df_abc()))
    assert report.n_items == 6
    # A x12, B x4, C x1 per year: 2*12 + 2*4 + 2*1 = 34 counts/year
    assert report.total_counts == 34
    assert len(report.schedule) == 34
    assert report.peak_daily_load >= 1
    assert cc.verify(report) == []


def test_build_deck_is_ascii_deliverable():
    report = cc.run(cc.prepare_records(_df_abc()))
    deck = cc.build_deck(report, client="Acme", citations=("Vandeput (2020) cycle counting",), confidence=0.85)
    assert isinstance(deck, Deliverable)
    md = deck.to_markdown()
    assert md.isascii()
    assert "Cycle-Count Program" in md and "## Coverage & handoff" in md


def test_brief_routes_to_cycle_count():
    reg = tools.build_default_registry()
    res = intent.classify(
        "build a cycle count program with a count schedule and count cadence by ABC class",
        reg, llm.RulesFallback(),
    )
    assert res.job_type == "cycle_count"


def test_orchestrator_runs_cycle_count_with_ranked_options(tmp_path):
    csv = tmp_path / "cc.csv"
    _df_abc().to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())
    res = orch.run("set up a cycle count program with a count schedule and count cadence by abc class",
                   data_path=str(csv), client="Acme", out_dir=tmp_path)
    assert res.status == "ok" and res.tool == "cycle_count"
    assert Path(res.deliverables["deck_report"]).exists()
    assert Path(res.deliverables["csv"]).exists()
    assert res.guided is not None and res.guided.status == OPTIONS
    assert sum(1 for o in res.guided.options if o.recommended) == 1
