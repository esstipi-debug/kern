"""Wiring tests for the intake_scan Tool: registration, routing, and the
prepare -> run -> qa -> deliver path through the registry's own Tool object
(not the pure job functions, which tests/test_intake_scan_job.py covers)."""
import pandas as pd

from nosilo_core import llm
from scm_agent import intent, tools
from nosilo_core.types import JobRequest


def _folder(tmp_path, **frames):
    d = tmp_path / "carpeta"
    d.mkdir()
    for name, frame in frames.items():
        pd.DataFrame(frame).to_csv(d / name, index=False)
    return d


def _maestro(n=20):
    return {"sku": [f"SKU-{i}" for i in range(n)], "name": ["x"] * n, "unit_cost": [5.0] * n}


def _ventas(n=20):
    return {
        "date": ["2026-01-01"] * n,
        "product_id": [f"SKU-{i}" for i in range(n)],
        "quantity": [10] * n,
        "unit_cost": [5.0] * n,
    }


def test_registry_includes_intake_scan_tool():
    reg = tools.build_default_registry()
    tool = reg.get("intake_scan")
    assert tool.requires_data is True
    assert tool.key == "intake_scan"


def test_a_folder_brief_routes_to_intake_scan():
    reg = tools.build_default_registry()
    res = intent.classify(
        "scan the folder and tell me what can i run given the data readiness of what "
        "the client sent",
        reg, llm.RulesFallback(),
    )
    assert res.job_type == "intake_scan"


def test_intake_scan_keywords_do_not_steal_data_quality_briefs():
    """intake_scan must never win a brief that is really about data_quality's own
    job (dedup/GTIN validation) -- and the data_quality brief must still route
    to data_quality, not fall to ambiguity."""
    reg = tools.build_default_registry()
    p = llm.RulesFallback()
    res = intent.classify(
        "audit our sku master for duplicate skus and validate gtins (data quality / mdm)",
        reg, p,
    )
    assert res.job_type != "intake_scan"
    assert res.job_type == "data_quality"


def test_prepare_without_data_needs_data_mentioning_a_folder():
    reg = tools.build_default_registry()
    tool = reg.get("intake_scan")
    prepared = tool.prepare(JobRequest(brief="scan the folder"), llm.RulesFallback())
    assert prepared.status == "needs_data"
    assert any("folder" in m.lower() for m in prepared.messages)


def test_prepare_rejects_a_file_path_as_needs_data(tmp_path):
    reg = tools.build_default_registry()
    tool = reg.get("intake_scan")
    f = tmp_path / "archivo.csv"
    f.write_text("a,b\n1,2\n", encoding="utf-8")
    prepared = tool.prepare(JobRequest(brief="scan", data_path=str(f)), llm.RulesFallback())
    assert prepared.status == "needs_data"
    assert any("folder" in m.lower() for m in prepared.messages)


def test_end_to_end_prepare_run_qa_deliver(tmp_path):
    folder = _folder(tmp_path, **{"maestro.csv": _maestro(), "ventas.csv": _ventas()})
    reg = tools.build_default_registry()
    tool = reg.get("intake_scan")
    provider = llm.RulesFallback()

    prepared = tool.prepare(JobRequest(brief="scan the folder", data_path=str(folder)), provider)
    assert prepared.status == "ok"

    produced = tool.run(prepared.payload, {})
    qa_issues = tool.qa(produced.report)
    assert qa_issues == []

    paths = tool.deliver(produced.report, tmp_path / "out", "Acme")
    assert paths["csv"].exists()
    assert paths["files_csv"].exists()
