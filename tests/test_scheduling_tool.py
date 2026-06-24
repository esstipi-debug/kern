"""Tests for the job-sequencing agent tool (16th tool).

Wires src.scheduling into the orchestrator: a jobs CSV -> recommended run order (SPT/EDD)
+ flow-time/lateness, with the dispatching rule as ranked options on success.
"""

from pathlib import Path

import pandas as pd

from jobs import scheduling_job as sj
from scm_agent import intent, llm, tools
from scm_agent.orchestrator import Orchestrator
from src.deliverable import Deliverable
from src.guided import OPTIONS


def _jobs_df() -> pd.DataFrame:
    return pd.DataFrame({
        "job": ["A", "B", "C"],
        "processing_time": [3.0, 1.0, 2.0],
        "due_date": [5.0, 3.0, 7.0],
    })


def test_prepare_reads_jobs(tmp_path):
    csv = tmp_path / "jobs.csv"
    _jobs_df().to_csv(csv, index=False)
    jobs = sj.prepare(str(csv), {})
    by = {j.id: j for j in jobs}
    assert by["A"].processing == 3.0 and by["B"].due == 3.0


def test_prepare_errors_without_required_columns(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1]}).to_csv(csv, index=False)
    import pytest
    with pytest.raises(ValueError, match="job|processing"):
        sj.prepare(str(csv), {})


def test_run_evaluates_rules_and_recommends():
    report = sj.run(sj.prepare_records(_jobs_df()))
    assert report.n_jobs == 3
    assert report.rule_metrics["SPT"].sequence == ["B", "C", "A"]   # shortest first
    assert report.rule_metrics["EDD"].sequence == ["B", "A", "C"]   # earliest due first
    assert report.recommended_rule == "EDD"                          # due dates present -> EDD
    assert tuple(report.sequence) == ("B", "A", "C")
    assert sj.verify(report) == []


def test_run_without_due_dates_recommends_spt():
    jobs = sj.prepare_records(pd.DataFrame({"job": ["X", "Y"], "processing_time": [4.0, 1.0]}))
    report = sj.run(jobs)
    assert report.recommended_rule == "SPT"


def test_build_deck_is_ascii_deliverable():
    report = sj.run(sj.prepare_records(_jobs_df()))
    deck = sj.build_deck(report, client="Acme", citations=("Jacobs & Chase ch.22",), confidence=0.85)
    assert isinstance(deck, Deliverable)
    md = deck.to_markdown()
    assert md.isascii()
    assert "Sequencing" in md and "## Coverage & handoff" in md


def test_brief_routes_to_scheduling():
    reg = tools.build_default_registry()
    res = intent.classify("what order to run these jobs to minimize lateness (sequence the jobs)",
                          reg, llm.RulesFallback())
    assert res.job_type == "scheduling"


def test_scheduling_keywords_do_not_steal_other_briefs():
    reg = tools.build_default_registry()
    p = llm.RulesFallback()
    assert intent.classify("set up reorder points and safety stock", reg, p).job_type == "inventory_optimization"
    assert intent.classify("how many servers to cut the queue wait", reg, p).job_type == "queuing"


def test_orchestrator_runs_scheduling_with_ranked_options(tmp_path):
    csv = tmp_path / "jobs.csv"
    _jobs_df().to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())
    res = orch.run("sequence the jobs - what run order", data_path=str(csv), client="Acme", out_dir=tmp_path)
    assert res.status == "ok" and res.tool == "scheduling"
    assert Path(res.deliverables["deck_report"]).exists()
    assert res.guided is not None and res.guided.status == OPTIONS
    assert sum(1 for o in res.guided.options if o.recommended) == 1
