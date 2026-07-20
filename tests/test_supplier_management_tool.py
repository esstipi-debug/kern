"""supplier_management tool: routing + orchestrator end-to-end + citation anchor."""

from pathlib import Path

import pandas as pd

from scm_agent import citation_gate, intent, llm, tools
from scm_agent.orchestrator import Orchestrator


def _suppliers_csv(path: Path) -> Path:
    pd.DataFrame({
        "supplier": ["A", "B", "C", "D"],
        "annual_spend": [500.0, 300.0, 120.0, 80.0],
        "lead_time_days": [40, 8, 34, 5],
        "single_source": [1, 0, 1, 0],
        "defect_ppm": [3000, 100, 2500, 50],
    }).to_csv(path, index=False)
    return path


def test_supplier_management_is_registered():
    reg = tools.build_default_registry()
    assert reg.get("supplier_management").key == "supplier_management"


def test_brief_routes_to_supplier_management():
    reg = tools.build_default_registry()
    res = intent.classify(
        "segment our suppliers on the kraljic matrix by profit impact and supply risk",
        reg, llm.RulesFallback(),
    )
    assert res.job_type == "supplier_management"


def test_supplier_management_keywords_do_not_steal_the_sourcing_brief():
    reg = tools.build_default_registry()
    p = llm.RulesFallback()
    assert intent.classify(
        "select the best supplier / sourcing award by OTIF and price", reg, p
    ).job_type == "sourcing"


def test_citation_anchor_is_registered_and_exists():
    assert "supplier_management" in citation_gate.TOOL_CONCEPTS
    assert "kraljic_matrix" in citation_gate.TOOL_CONCEPTS["supplier_management"]


def test_orchestrator_runs_supplier_management_and_emits_the_deck(tmp_path):
    csv = _suppliers_csv(tmp_path / "sup.csv")
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())
    res = orch.run(
        "segment suppliers on the kraljic matrix by profit impact and supply risk",
        data_path=str(csv), client="Acme", out_dir=tmp_path,
    )
    assert res.status == "ok"
    assert res.tool == "supplier_management"
    assert "csv" in res.deliverables
    deck = Path(res.deliverables["deck_report"])
    assert deck.exists()
    assert "strategic" in deck.read_text(encoding="utf-8")
