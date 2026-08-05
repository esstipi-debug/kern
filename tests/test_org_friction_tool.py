"""Tests for the org_friction tool wiring and the orchestrator end to end."""
from pathlib import Path

import pandas as pd
from nosilo_core.guided import HANDOFF, OPTIONS

from nosilo_core import llm
from scm_agent import intent, tools


def _control_points_csv(tmp_path: Path) -> Path:
    rows = [
        {"name": "Fill rate", "official_value": 0.95, "shadow_value": 0.60, "owner": "Ops Manager"},
        {"name": "Cycle count accuracy", "official_value": "", "shadow_value": "", "owner": ""},
    ]
    path = tmp_path / "control_points.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _untouched_csv(tmp_path: Path) -> Path:
    path = tmp_path / "untouched.csv"
    pd.DataFrame([{"name": "Untouched metric", "owner": "Someone"}]).to_csv(path, index=False)
    return path


def test_org_friction_is_registered():
    reg = tools.build_default_registry()
    assert reg.get("org_friction_audit") is not None


def test_brief_routes_to_org_friction():
    reg = tools.build_default_registry()
    res = intent.classify(
        "check if the fill rate KPI is being gamed, some kind of hidden factory or ratchet effect",
        reg, llm.RulesFallback(),
    )
    assert res.job_type == "org_friction_audit"


def test_prepare_without_data_asks_for_the_control_points_file():
    from nosilo_core.types import JobRequest

    tool = tools.build_default_registry().get("org_friction_audit")
    prepared = tool.prepare(
        JobRequest(brief="audit my kpis for hidden friction", data_path=None), llm.RulesFallback(),
    )
    assert prepared.status == "needs_data"
    assert any("control points" in m for m in prepared.messages)


def test_end_to_end_produces_options_and_the_findings_csv(tmp_path):
    from nosilo_core.types import JobRequest

    tool = tools.build_default_registry().get("org_friction_audit")
    prepared = tool.prepare(
        JobRequest(brief="audit my kpis for hidden friction", data_path=str(_control_points_csv(tmp_path))),
        llm.RulesFallback(),
    )
    assert prepared.status == "ok"
    produced = tool.run(prepared.payload, {})
    assert tool.qa(produced.report) == []
    paths = tool.deliver(produced.report, tmp_path / "out", "Acme")
    assert paths["csv"].exists()
    assert produced.report.outcome.status == OPTIONS


def test_end_to_end_yields_handoff_when_nothing_triangulates(tmp_path):
    from nosilo_core.types import JobRequest

    tool = tools.build_default_registry().get("org_friction_audit")
    prepared = tool.prepare(
        JobRequest(brief="audit my kpis for hidden friction", data_path=str(_untouched_csv(tmp_path))),
        llm.RulesFallback(),
    )
    assert prepared.status == "ok"
    produced = tool.run(prepared.payload, {})
    assert tool.qa(produced.report) == []
    assert produced.report.outcome.status == HANDOFF


def test_deck_builds_and_writes_both_files(tmp_path):
    from nosilo_core.types import JobRequest

    tool = tools.build_default_registry().get("org_friction_audit")
    prepared = tool.prepare(
        JobRequest(brief="audit my kpis for hidden friction", data_path=str(_control_points_csv(tmp_path))),
        llm.RulesFallback(),
    )
    produced = tool.run(prepared.payload, {})
    paths = tool.deck(
        produced.report, tmp_path / "deck_out", "Acme", (), 0.7, produced.report.outcome.options,
    )
    assert paths["report"].exists() and paths["workbook"].exists()
