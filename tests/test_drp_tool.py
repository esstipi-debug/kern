"""Tests for the DRP (distribution requirements planning) agent tool.

Wires src.drp into the orchestrator: a long-format demand CSV (branch, period, demand) ->
time-phased planned order releases per branch + the rolled-up central DC plan, with ranked
execution options on success.
"""

from pathlib import Path

import pandas as pd
import pytest

from jobs import drp_job as dj
from scm_agent import intent, llm, tools
from scm_agent.orchestrator import Orchestrator
from src.deliverable import Deliverable
from src.guided import OPTIONS


def _demand_df() -> pd.DataFrame:
    rows = []
    for branch, oh, lt, ss in (("East", 15, 1, 5), ("West", 0, 1, 0)):
        for period in range(1, 5):
            rows.append({"branch": branch, "period": period, "demand": 10,
                         "on_hand": oh, "lead_time": lt, "safety_stock": ss})
    return pd.DataFrame(rows)


def test_prepare_pivots_branches_and_periods(tmp_path):
    csv = tmp_path / "drp.csv"
    _demand_df().to_csv(csv, index=False)
    payload = dj.prepare(str(csv), {})
    assert payload["n_periods"] == 4
    names = {b.name for b in payload["branches"]}
    assert names == {"East", "West"}
    east = next(b for b in payload["branches"] if b.name == "East")
    assert east.on_hand == 15 and east.safety_stock == 5


def test_prepare_errors_without_required_columns(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1]}).to_csv(csv, index=False)
    with pytest.raises(ValueError, match="branch|period|demand"):
        dj.prepare(str(csv), {})


def test_run_plans_branches_and_rolls_up_to_dc():
    report = dj.run(dj.prepare_records(_demand_df()))
    assert report.n_branches == 2 and report.n_periods == 4
    assert report.total_branch_releases > 0
    assert len(report.dc_gross_requirements) == 4
    assert len(report.dc_plan) == 4
    assert dj.verify(report) == []


def test_build_deck_is_ascii_deliverable():
    report = dj.run(dj.prepare_records(_demand_df()))
    deck = dj.build_deck(report, client="Acme", citations=("Vollmann MPC - DRP",), confidence=0.85)
    assert isinstance(deck, Deliverable)
    md = deck.to_markdown()
    assert md.isascii()
    assert "Distribution Requirements Planning" in md and "## Coverage & handoff" in md


def test_brief_routes_to_drp():
    reg = tools.build_default_registry()
    res = intent.classify(
        "DRP distribution requirements planning: time-phased planned order releases for the branches and central DC",
        reg, llm.RulesFallback(),
    )
    assert res.job_type == "drp"


def test_orchestrator_runs_drp_with_ranked_options(tmp_path):
    csv = tmp_path / "drp.csv"
    _demand_df().to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())
    res = orch.run("distribution requirements planning (DRP): time-phased branch and DC replenishment",
                   data_path=str(csv), client="Acme", out_dir=tmp_path)
    assert res.status == "ok" and res.tool == "drp"
    assert Path(res.deliverables["deck_report"]).exists()
    assert Path(res.deliverables["csv"]).exists()
    assert res.guided is not None and res.guided.status == OPTIONS
    assert sum(1 for o in res.guided.options if o.recommended) == 1
