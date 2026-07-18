"""Tests for the launch_readiness aggregation + agent-tool wiring (Kern tool #41)."""

from datetime import date

import pandas as pd

from jobs import launch_readiness_job as lrj
from scm_agent import intent, llm, tool_options, tools
from scm_agent.orchestrator import Orchestrator
from src.guided import ESCALATED, EXECUTED, OPTIONS

AS_OF = date(2026, 7, 1)


def _covered(**kw):
    base = dict(product_id="sku", launch_date=date(2026, 7, 31), lift_pct=0.0, has_coverage=True,
                on_hand=200.0, daily_demand=10.0, lead_time_days=7.0, demand_std=0.0, lead_time_std=0.0)
    base.update(kw)
    return lrj.LaunchInput(**base)


def _report(inputs):
    return lrj.run({"records": inputs, "as_of_date": AS_OF})


# -- Task 5: aggregation ------------------------------------------------------


def test_aggregate_escalates_when_any_sku_is_red():
    rep = _report([
        _covered(product_id="ok", on_hand=1000.0),
        _covered(product_id="late", launch_date=date(2026, 7, 4), on_hand=20.0, lead_time_days=14.0),
    ])
    out = tool_options.launch_readiness_options(rep)
    assert out.status == ESCALATED
    assert out.escalation.route_to == "marketing campaign owner"
    assert out.escalation.sla and out.escalation.reason
    assert len(out.options) >= 2  # options carried at the top level too


def test_aggregate_is_options_when_yellow_but_no_red():
    rep = _report([_covered(product_id="y", on_hand=200.0)])  # yellow
    out = tool_options.launch_readiness_options(rep)
    assert out.status == OPTIONS


def test_aggregate_is_executed_when_all_green():
    rep = _report([_covered(product_id="g", on_hand=1000.0)])
    out = tool_options.launch_readiness_options(rep)
    assert out.status == EXECUTED


# -- Task 6: wiring (registration, routing, end-to-end) -----------------------


def test_registry_registers_launch_readiness():
    reg = tools.build_default_registry()
    assert "launch_readiness" in {t.key for t in reg.list()}


def test_brief_routes_to_launch_readiness():
    reg = tools.build_default_registry()
    res = intent.classify(
        "launch readiness check: will these SKUs be in stock for the campaign launch date given lead time",
        reg, llm.RulesFallback())
    assert res.job_type == "launch_readiness"


def test_end_to_end_orchestrator_run(tmp_path):
    camp = tmp_path / "campanas.csv"
    pd.DataFrame({"product_id": ["a", "b"], "launch_date": ["2026-07-31", "2026-07-04"]}).to_csv(camp, index=False)
    inv = tmp_path / "inv.csv"
    pd.DataFrame({"product_id": ["a", "b"], "on_hand": [1000, 20],
                  "daily_demand": [10, 10], "lead_time_days": [7, 14]}).to_csv(inv, index=False)
    orch = Orchestrator(tools.build_default_registry(), llm.RulesFallback(), clients_root=None)
    res = orch.run("launch readiness for the marketing campaign launch dates", data_path=str(camp),
                   job_type="launch_readiness", params={"inventory_path": str(inv), "as_of_date": "2026-07-01"},
                   out_dir=str(tmp_path / "out"))
    assert res.status == "ok"
    assert res.guided is not None and res.guided.status == "escalated"  # b is red
