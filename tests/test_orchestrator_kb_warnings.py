"""The orchestrator surfaces KnowledgeBase.warnings() on the JobResult.

So a missing or corrupt code graph shows up in the result (and the webapp JSON)
instead of citations silently going theory-only.
"""

from __future__ import annotations

from pathlib import Path

from scm_agent import llm, tools
from scm_agent.knowledge import KnowledgeBase
from scm_agent.orchestrator import Orchestrator

REPO = Path(__file__).resolve().parent.parent
BOOKS = REPO / "knowledge" / "scm-books" / "graph.json"
LEAD = {"overrides": {"scores": "3 2 3 1 1", "name": "T"}}


def _orch(kb: KnowledgeBase) -> Orchestrator:
    return Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback(), knowledge=kb)


def test_missing_code_graph_surfaces_on_jobresult(tmp_path):
    kb = KnowledgeBase(books_path=BOOKS, code_path=tmp_path / "absent.json")
    res = _orch(kb).run("evaluate our SC leadership", out_dir=tmp_path, **LEAD)
    assert res.status == "ok"
    assert any("code" in w for w in res.kb_warnings)


def test_healthy_graphs_leave_kb_warnings_empty(tmp_path):
    good = tmp_path / "code.json"
    good.write_text('{"nodes": [{"id": "x", "label": "X"}], "links": []}', encoding="utf-8")
    kb = KnowledgeBase(books_path=BOOKS, code_path=good)
    res = _orch(kb).run("evaluate our SC leadership", out_dir=tmp_path, **LEAD)
    assert res.status == "ok"
    assert res.kb_warnings == []
