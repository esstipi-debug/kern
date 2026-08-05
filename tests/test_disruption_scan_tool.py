"""Tests for the disruption_scan tool wiring + orchestrator E2E. No network.

The GDELT fetcher is injected via overrides so the end-to-end run never touches
the network.
"""
from pathlib import Path

import pandas as pd
from nosilo_core.guided import OPTIONS

from nosilo_core import llm
from scm_agent import intent, tools
from scm_agent.orchestrator import Orchestrator

FIXTURES = Path(__file__).parent / "fixtures" / "gdelt"
_ACME = (FIXTURES / "acme_electronics.json").read_text(encoding="utf-8")
_EMPTY = (FIXTURES / "empty.json").read_text(encoding="utf-8")


def _suppliers_csv(tmp_path: Path) -> Path:
    csv = tmp_path / "suppliers.csv"
    pd.DataFrame({
        "supplier": ["Acme Electronics", "Calm Supplier"],
        "country": ["Brazil", "New Zealand"],
        "annual_spend": [4_200_000, 500_000],
    }).to_csv(csv, index=False)
    return csv


def _acme_only_fetcher(url: str) -> str:
    return _ACME if "Acme" in url else _EMPTY


def test_brief_routes_to_disruption_scan():
    reg = tools.build_default_registry()
    res = intent.classify(
        "scan my suppliers for disruption exposure in the news", reg, llm.RulesFallback()
    )
    assert res.job_type == "disruption_scan"


def test_disruption_keywords_do_not_steal_the_risk_register_brief():
    reg = tools.build_default_registry()
    p = llm.RulesFallback()
    # a hand-built risk register still routes to the classic risk tool
    assert intent.classify(
        "build a supply chain risk register with a likelihood impact heatmap and mitigation plan",
        reg, p,
    ).job_type == "risk"
    # and the disruption scan owns the supplier-news-monitoring brief
    assert intent.classify(
        "monitor my suppliers for disruption news and rank exposure", reg, p
    ).job_type == "disruption_scan"


def test_orchestrator_disruption_scan_end_to_end(tmp_path):
    csv = _suppliers_csv(tmp_path)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())

    res = orch.run(
        "scan our suppliers for disruption exposure in recent news",
        data_path=str(csv), client="Acme", out_dir=tmp_path,
        overrides={"fetcher": _acme_only_fetcher},
    )

    assert res.status == "ok"
    assert res.tool == "disruption_scan"
    assert "csv" in res.deliverables
    assert Path(res.deliverables["deck_report"]).exists()
    assert res.guided is not None
    assert res.guided.status == OPTIONS
    assert len(res.guided.options) >= 1
    assert sum(1 for o in res.guided.options if o.recommended) == 1
    # the flagged supplier is the one with news
    assert res.guided.options[0].label == "Acme Electronics"


def test_orchestrator_all_clear_scan_still_succeeds(tmp_path):
    csv = _suppliers_csv(tmp_path)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())
    res = orch.run(
        "screen suppliers for disruption news exposure",
        data_path=str(csv), client="Acme", out_dir=tmp_path,
        overrides={"fetcher": lambda url: _EMPTY},
    )
    assert res.status == "ok"
    assert res.guided.status == "executed"


def test_registry_includes_disruption_scan_tool():
    reg = tools.build_default_registry()
    keys = {t.key for t in reg.list()}
    assert "disruption_scan" in keys
    assert reg.get("disruption_scan").requires_data is True
