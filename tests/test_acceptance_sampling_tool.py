"""Tests for the acceptance-sampling (receiving quality) agent tool."""

from pathlib import Path

import pandas as pd

from jobs import acceptance_sampling_job as asj
from scm_agent import intent, llm, tools
from scm_agent.orchestrator import Orchestrator
from src.guided import OPTIONS


def _parts_df() -> pd.DataFrame:
    return pd.DataFrame({
        "part": ["P1", "P2"],
        "aql": [0.01, 0.02],
        "ltpd": [0.06, 0.10],
    })


def test_prepare_reads_parts(tmp_path):
    csv = tmp_path / "p.csv"
    _parts_df().to_csv(csv, index=False)
    records = asj.prepare(str(csv), {})
    by = {r["part"]: r for r in records}
    assert by["P1"]["aql"] == 0.01 and by["P2"]["ltpd"] == 0.10


def test_run_designs_a_plan_per_part():
    report = asj.run(asj.prepare_records(_parts_df()))
    assert report.n_parts == 2
    assert all(p.sample_size > 0 and p.accept_number >= 0 for p in report.parts)
    assert report.total_sample == sum(p.sample_size for p in report.parts)
    assert asj.verify(report) == []


def test_build_deck_ascii():
    report = asj.run(asj.prepare_records(_parts_df()))
    md = asj.build_deck(report, client="Acme", citations=("Jacobs & Chase ch.13",), confidence=0.85).to_markdown()
    assert md.isascii() and "Sampling" in md and "## Coverage & handoff" in md


def test_routes_and_runs_end_to_end(tmp_path):
    csv = tmp_path / "p.csv"
    _parts_df().to_csv(csv, index=False)
    reg = tools.build_default_registry()
    assert intent.classify("design the receiving inspection sampling plan from aql and ltpd",
                           reg, llm.RulesFallback()).job_type == "acceptance_sampling"
    orch = Orchestrator(registry=reg, provider=llm.RulesFallback())
    res = orch.run("acceptance sampling plans for incoming inspection", data_path=str(csv), client="Acme", out_dir=tmp_path)
    assert res.status == "ok" and res.tool == "acceptance_sampling"
    assert Path(res.deliverables["deck_report"]).exists()
    assert res.guided is not None and res.guided.status == OPTIONS
