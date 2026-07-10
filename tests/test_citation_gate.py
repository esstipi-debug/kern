"""Tests for scm_agent/citation_gate.py: the citation-grounding gate (E5).

Unit tests use a fake KnowledgeBase (fast, no graph file I/O) to exercise
filter_citations()'s logic in isolation; the integration/regression tests at
the bottom use the REAL KnowledgeBase against the committed books graph, per
the acceptance criterion: "el deck del Diagnostico demo no vuelve a citar
fuentes irrelevantes."
"""
from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from scm_agent.citation_gate import MAX_HOPS, MIN_CITATIONS, TOOL_CONCEPTS, GateResult, filter_citations
from scm_agent.knowledge import GroundedCitation, KnowledgeBase


def _cite(node_id: str, text: str | None = None) -> GroundedCitation:
    return GroundedCitation(text=text or f"Citation for {node_id}", node_id=node_id, graph="books")


class _FakeKB:
    """Duck-typed stand-in for KnowledgeBase: no real graph, fully controllable."""

    def __init__(self, existing: set[str], distances: dict[tuple[str, str], int]):
        self._existing = existing
        self._distances = distances

    def node_exists(self, node_id: str, graph: str = "books") -> bool:
        return node_id in self._existing

    def concept_distance(self, from_id: str, to_id: str, *, graph: str = "books", max_hops: int = 2) -> int | None:
        if from_id not in self._existing or to_id not in self._existing:
            return None
        if from_id == to_id:
            return 0
        d = self._distances.get((from_id, to_id), self._distances.get((to_id, from_id)))
        if d is None or d > max_hops:
            return None
        return d


# ---- spec integrity: every real tool key is mapped to real, resolving concepts --

def test_every_registered_tool_has_a_concept_map():
    tools_src = (Path(__file__).resolve().parents[1] / "scm_agent" / "tools.py").read_text(encoding="utf-8")
    registered = set(re.findall(r'key="([a-z_]+)"', tools_src))
    assert registered == set(TOOL_CONCEPTS)


def test_every_anchor_concept_exists_in_the_real_graph():
    kb = KnowledgeBase()
    missing = {
        tool: [cid for cid in ids if not kb.node_exists(cid)]
        for tool, ids in TOOL_CONCEPTS.items()
    }
    missing = {t: ids for t, ids in missing.items() if ids}
    assert missing == {}, f"anchor concept ids no longer resolve: {missing}"


def test_no_tool_has_an_empty_anchor_tuple():
    for tool, ids in TOOL_CONCEPTS.items():
        assert len(ids) >= 1, tool


# ---- filter_citations: core policy -----------------------------------------

def test_two_citations_within_hop_budget_are_both_kept(monkeypatch):
    monkeypatch.setitem(TOOL_CONCEPTS, "t", ("anchor",))
    kb = _FakeKB({"anchor", "a", "b"}, {("a", "anchor"): 1, ("b", "anchor"): 2})
    result = filter_citations(kb, "t", [_cite("a"), _cite("b")])
    assert set(result.kept) == {"Citation for a", "Citation for b"}
    assert result.omitted == ()


def test_citation_beyond_max_hops_is_omitted(monkeypatch):
    monkeypatch.setitem(TOOL_CONCEPTS, "t", ("anchor",))
    kb = _FakeKB({"anchor", "far", "near"}, {("far", "anchor"): 3, ("near", "anchor"): 1})
    result = filter_citations(kb, "t", [_cite("far"), _cite("near")])
    assert result.kept == ()  # "near" alone is below MIN_CITATIONS=2
    assert any("far" in line and "more than" in line for line in result.omitted)


def test_nonexistent_node_is_omitted_with_a_clear_reason(monkeypatch):
    monkeypatch.setitem(TOOL_CONCEPTS, "t", ("anchor",))
    kb = _FakeKB({"anchor"}, {})
    result = filter_citations(kb, "t", [_cite("ghost")])
    assert result.kept == ()
    assert any("does not exist" in line for line in result.omitted)


def test_fewer_than_min_citations_degrades_the_whole_batch_to_empty(monkeypatch):
    monkeypatch.setitem(TOOL_CONCEPTS, "t", ("anchor",))
    kb = _FakeKB({"anchor", "a"}, {("a", "anchor"): 1})
    result = filter_citations(kb, "t", [_cite("a")])  # only 1 resolves, MIN_CITATIONS=2
    assert result.kept == ()
    assert any("only 1" in line for line in result.omitted)


def test_exactly_min_citations_are_kept(monkeypatch):
    monkeypatch.setitem(TOOL_CONCEPTS, "t", ("anchor",))
    kb = _FakeKB({"anchor", "a", "b"}, {("a", "anchor"): 1, ("b", "anchor"): 2})
    result = filter_citations(kb, "t", [_cite("a"), _cite("b")])
    assert len(result.kept) == MIN_CITATIONS
    assert result.omitted == ()


def test_tool_with_no_concept_map_omits_every_candidate(monkeypatch):
    monkeypatch.delitem(TOOL_CONCEPTS, "unmapped_tool", raising=False)
    kb = _FakeKB({"a", "b"}, {})
    result = filter_citations(kb, "unmapped_tool", [_cite("a"), _cite("b")])
    assert result.kept == ()
    assert len(result.omitted) == 2
    assert all("no concept map" in line for line in result.omitted)


def test_empty_candidates_list_is_a_clean_no_op(monkeypatch):
    monkeypatch.setitem(TOOL_CONCEPTS, "t", ("anchor",))
    kb = _FakeKB({"anchor"}, {})
    assert filter_citations(kb, "t", []) == GateResult(kept=(), omitted=())


def test_gate_never_invents_a_replacement_citation(monkeypatch):
    # Every string in `kept` must be the exact text of a surviving candidate -
    # the gate removes, it never substitutes or fabricates.
    monkeypatch.setitem(TOOL_CONCEPTS, "t", ("anchor",))
    kb = _FakeKB({"anchor", "a", "b"}, {("a", "anchor"): 1, ("b", "anchor"): 1})
    candidates = [_cite("a", "Real Text A"), _cite("b", "Real Text B")]
    result = filter_citations(kb, "t", candidates)
    assert set(result.kept) <= {"Real Text A", "Real Text B"}


def test_omissions_are_logged_and_inspectable(monkeypatch, caplog):
    monkeypatch.setitem(TOOL_CONCEPTS, "t", ("anchor",))
    kb = _FakeKB({"anchor"}, {})
    with caplog.at_level(logging.INFO, logger="linchpin.citation_gate"):
        filter_citations(kb, "t", [_cite("ghost")])
    assert any("does not exist" in rec.message for rec in caplog.records)


def test_hop_zero_self_match_is_kept_alongside_a_one_hop_match(monkeypatch):
    monkeypatch.setitem(TOOL_CONCEPTS, "t", ("anchor",))
    kb = _FakeKB({"anchor", "one_hop"}, {("one_hop", "anchor"): 1})
    result = filter_citations(kb, "t", [_cite("anchor"), _cite("one_hop")])
    assert len(result.kept) == 2


def test_max_hops_is_two_per_the_spec():
    assert MAX_HOPS == 2


def test_min_citations_is_two_per_the_spec():
    assert MIN_CITATIONS == 2


# ---- integration/regression: the real books graph + a real package run -------

@pytest.fixture(scope="module")
def demo_intake(tmp_path_factory):
    from examples.run_package import build_demo_intake
    return build_demo_intake(tmp_path_factory.mktemp("citation_gate_intake"))


@pytest.fixture(scope="module")
def diagnostico_result(demo_intake, tmp_path_factory):
    from scm_agent.package_specs import DIAGNOSTICO
    from scm_agent.packages import run_package

    kb = KnowledgeBase()
    out = tmp_path_factory.mktemp("citation_gate_out")
    result = run_package(DIAGNOSTICO, demo_intake, out_dir=out, knowledge=kb, clients_root=None)
    assert result.status == "ok"
    return result


def test_diagnostico_demo_no_longer_cites_irrelevant_sources(diagnostico_result):
    """The exact regression named in the 2.0 protocol: the Diagnostico demo
    deck used to cite 'Clean Technology'/MPS under data_quality - decorative
    grounding, off-topic for a duplicate/GTIN data-quality audit."""
    executed = [s for s in diagnostico_result.steps if s.status == "ok"]
    assert executed  # the fixture must actually exercise citation-bearing steps
    joined = " ".join(c for s in executed for c in s.citations)
    assert "Clean Technology" not in joined
    assert "Master Production Schedule" not in joined


def test_data_quality_step_degrades_to_no_citations_on_the_demo_intake(diagnostico_result):
    """Pins the exact regression case: data_quality's ranked candidates on
    the demo intake are all >2 hops from step_product_data_standard, so the
    step must render with zero citations, not the old off-topic ones."""
    dq = next(s for s in diagnostico_result.steps if s.tool_key == "data_quality")
    assert dq.citations == ()


def test_every_surviving_citation_is_re_derivable_and_within_hop_budget(diagnostico_result):
    """Structural version of the same regression: independently re-running
    ground_citations_detailed + filter_citations with each step's own tool
    keywords and brief must reproduce EXACTLY the citations that step shipped
    with - proving every survivor is genuinely hop-verified grounding, not a
    testing artifact (the gate's unit tests above already prove
    filter_citations only ever keeps hop-verified nodes; this proves a real
    run's output matches what the gate would independently produce)."""
    from scm_agent.package_specs import DIAGNOSTICO
    from scm_agent.tools import build_default_registry

    kb = KnowledgeBase()
    registry = build_default_registry()
    for step in diagnostico_result.steps:
        if step.status != "ok":
            continue
        tool = registry.get(step.tool_key)
        brief = f"{DIAGNOSTICO.title}: {tool.title}"
        candidates = kb.ground_citations_detailed(tool.intent_keywords, brief, limit=3)
        rerun = filter_citations(kb, step.tool_key, candidates)
        assert rerun.kept == step.citations, step.tool_key
