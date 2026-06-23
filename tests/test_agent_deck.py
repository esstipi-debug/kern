"""The agent emits the premium "artifacts that sell" deck from its deliver path (wire-up).

The orchestrator already wrote the operational files (workbook / report / csv) per tool.
This wires the richer `src.deliverable` deck - KPI table with rationale, data-source map,
L3 citations woven in, and the never-unprotected coverage/handoff block - into the deliver
path via an optional `Tool.deck` hook, so an agent run yields the client-facing deck too.
Additive: a tool without a deck hook is unchanged.
"""

from pathlib import Path

from scm_agent import llm
from scm_agent.orchestrator import Orchestrator
from scm_agent.tools import build_default_registry

PORTFOLIO = "data/sample_demand_portfolio.csv"


def _orch() -> Orchestrator:
    return Orchestrator(registry=build_default_registry(), provider=llm.RulesFallback())


def test_inventory_job_emits_the_premium_deck(tmp_path):
    res = _orch().run("set up reorder points and safety stock", data_path=PORTFOLIO,
                      client="Acme", out_dir=tmp_path)

    assert res.status == "ok"
    deck_keys = [k for k in res.deliverables if k.startswith("deck_")]
    assert deck_keys, res.deliverables
    md = Path(res.deliverables["deck_report"])
    assert md.exists()
    text = md.read_text(encoding="utf-8")
    assert "## KPIs" in text                       # the premium KPI-rationale table
    assert "## Coverage & handoff" in text         # never-unprotected block


def test_premium_deck_weaves_in_the_l3_citations(tmp_path):
    res = _orch().run("set up reorder points and safety stock", data_path=PORTFOLIO, out_dir=tmp_path)

    assert res.citations  # books graph is committed -> grounded
    text = Path(res.deliverables["deck_report"]).read_text(encoding="utf-8")
    assert "Methodology & grounding" in text       # citations rendered into the deck


def test_operational_files_still_emitted_alongside_the_deck(tmp_path):
    res = _orch().run("set up reorder points and safety stock", data_path=PORTFOLIO, out_dir=tmp_path)

    assert "excel" in res.deliverables             # the operational workbook survives
    assert Path(res.deliverables["excel"]).exists()


def test_only_inventory_wires_a_deck_so_far():
    reg = build_default_registry()

    assert reg.get("inventory_optimization").deck is not None
    assert reg.get("pricing").deck is None
    assert reg.get("leadership_chain").deck is None
