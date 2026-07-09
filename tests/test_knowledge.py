"""Tests for the L3 knowledge layer (scm_agent/knowledge.py).

Runs against the committed books graph (knowledge/scm-books/graph.json). The
code graph (graphify-out/) is gitignored, so tests that need it skip when absent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scm_agent.knowledge import (
    Bridge,
    Concept,
    ConceptDetail,
    KnowledgeBase,
    MethodAdvice,
)

REPO = Path(__file__).resolve().parent.parent
BOOKS = REPO / "knowledge" / "scm-books" / "graph.json"
CODE = REPO / "graphify-out" / "graph.json"


def _write_graph(path: Path, nodes: list[dict]) -> Path:
    """Write a minimal node-link graph for deterministic ranking tests."""
    path.write_text(json.dumps({"nodes": nodes, "links": []}), encoding="utf-8")
    return path


@pytest.fixture
def kb() -> KnowledgeBase:
    return KnowledgeBase()


def test_books_graph_loads(kb: KnowledgeBase) -> None:
    status = kb.available()
    assert status["books"] > 0  # books graph is committed


def test_search_finds_croston_in_books(kb: KnowledgeBase) -> None:
    hits = kb.search("croston", graph="books")
    assert any("croston" in h.id.lower() for h in hits)
    assert all(isinstance(h, Concept) for h in hits)


def test_search_respects_limit(kb: KnowledgeBase) -> None:
    hits = kb.search("pricing", graph="books", limit=3)
    assert len(hits) <= 3


def test_search_empty_query_returns_nothing(kb: KnowledgeBase) -> None:
    assert kb.search("", graph="books") == []


def test_search_unknown_term_returns_empty(kb: KnowledgeBase) -> None:
    assert kb.search("zzqqxnonsense", graph="books") == []


def test_explain_returns_detail_with_neighbors(kb: KnowledgeBase) -> None:
    # find a real concept id first
    hits = kb.search("safety stock", graph="books")
    assert hits, "expected a safety-stock concept in the books graph"
    detail = kb.explain(hits[0].id)
    assert isinstance(detail, ConceptDetail)
    assert detail.concept.id == hits[0].id
    assert isinstance(detail.neighbors, tuple)


def test_explain_unknown_id_returns_none(kb: KnowledgeBase) -> None:
    assert kb.explain("does_not_exist_zzz") is None


def test_explain_fuzzy_falls_back_to_search(kb: KnowledgeBase) -> None:
    # passing a label-ish string should resolve via search fallback
    detail = kb.explain("crostons method")
    assert detail is None or isinstance(detail, ConceptDetail)


def test_explain_resolves_bare_id_across_source_namespacing(kb: KnowledgeBase) -> None:
    """A bare concept id must resolve even though the books graph namespaces node
    ids by source (e.g. ``knowledge::chain_model``). Regression for the 25th-source
    merge, which re-prefixed every original node id and silently broke the exact-id
    lookup in both advise() and explain() - the fuzzy fallback masked most rules but
    not all. The bare slug is the documented interface (CLAUDE.md's --explain, the
    _METHOD_RULES), so it must survive any re-prefixing merge."""
    detail = kb.explain("chain_model")  # documented bare id, real committed node
    assert detail is not None, "bare 'chain_model' must resolve regardless of source prefix"
    assert detail.concept.id == "chain_model", "Concept.id is normalized back to the bare slug"
    # the prefixed form resolves to the very same node
    prefixed = kb.explain("knowledge::chain_model")
    assert prefixed is not None
    assert prefixed.concept.id == detail.concept.id


def test_bridge_returns_theory_side(kb: KnowledgeBase) -> None:
    b = kb.bridge("newsvendor")
    assert isinstance(b, Bridge)
    assert b.term == "newsvendor"
    # theory side comes from the committed books graph
    assert len(b.theory) >= 1
    assert all(c.graph == "books" for c in b.theory)


@pytest.mark.skipif(not CODE.exists(), reason="code graph is gitignored")
def test_bridge_links_theory_to_implementation(kb: KnowledgeBase) -> None:
    b = kb.bridge("newsvendor")
    assert len(b.implementation) >= 1
    assert all(c.graph == "code" for c in b.implementation)
    # the implementation side should point at real source files
    assert any(c.source for c in b.implementation)


@pytest.mark.skipif(not CODE.exists(), reason="code graph is gitignored")
def test_implements_bridges_a_concept_to_source_code(kb: KnowledgeBase) -> None:
    hits = kb.search("economic order quantity", graph="books")
    assert hits, "expected an EOQ concept in the books graph"
    impl = kb.implements(hits[0])
    assert impl is not None
    assert impl.graph == "code"
    assert impl.source and impl.source.endswith(".py")


def test_implements_returns_none_when_code_graph_absent(tmp_path: Path) -> None:
    kb = KnowledgeBase(books_path=BOOKS, code_path=tmp_path / "none.json")
    concept = Concept(id="economic_order_quantity", label="Economic Order Quantity",
                      source=None, location=None, graph="books")
    assert kb.implements(concept) is None


def test_implements_ignores_a_lone_common_token(kb: KnowledgeBase) -> None:
    # A concept sharing only one ubiquitous domain word must not forge a code link.
    concept = Concept(id="thing", label="price", source=None, location=None, graph="books")
    assert kb.implements(concept) is None


def test_missing_graph_paths_degrade_gracefully(tmp_path: Path) -> None:
    kb = KnowledgeBase(books_path=tmp_path / "nope.json", code_path=tmp_path / "nope2.json")
    assert kb.available() == {"books": 0, "code": 0}
    assert kb.search("anything") == []
    assert kb.explain("anything") is None
    b = kb.bridge("anything")
    assert b.theory == () and b.implementation == ()


def test_search_both_graphs_tags_origin(kb: KnowledgeBase) -> None:
    hits = kb.search("inventory", graph="both", limit=10)
    graphs = {h.graph for h in hits}
    assert graphs.issubset({"books", "code"})


# -- ranking improvements (deterministic, synthetic graphs) -----------------


def test_search_weights_title_over_rationale(tmp_path: Path) -> None:
    """A title match outranks a node that only mentions the term in its rationale."""
    g = _write_graph(tmp_path / "b.json", [
        {"id": "a", "label": "Reorder Point", "norm_label": "reorder point",
         "rationale": "trigger level"},
        {"id": "b", "label": "Generic Concept", "norm_label": "generic concept",
         "rationale": "the reorder point is computed here"},
    ])
    kb = KnowledgeBase(books_path=g, code_path=tmp_path / "none.json")
    hits = kb.search("reorder point", graph="books")
    assert hits[0].id == "a"


def test_search_matches_via_rationale(tmp_path: Path) -> None:
    """A term present only in the rationale still surfaces the node (recall)."""
    g = _write_graph(tmp_path / "b.json", [
        {"id": "a", "label": "Buffer Sizing", "norm_label": "buffer sizing",
         "rationale": "handles intermittent croston demand"},
    ])
    kb = KnowledgeBase(books_path=g, code_path=tmp_path / "none.json")
    hits = kb.search("croston", graph="books")
    assert any(h.id == "a" for h in hits)


def test_search_idf_favors_rarer_term(tmp_path: Path) -> None:
    """A rare, specific term outweighs a term common across the corpus."""
    nodes = [
        {"id": f"d{i}", "label": f"Demand Topic {i}",
         "norm_label": f"demand topic {i}", "rationale": ""}
        for i in range(5)
    ]
    nodes.append({"id": "nv", "label": "Newsvendor", "norm_label": "newsvendor",
                  "rationale": ""})
    g = _write_graph(tmp_path / "b.json", nodes)
    kb = KnowledgeBase(books_path=g, code_path=tmp_path / "none.json")
    hits = kb.search("demand newsvendor", graph="books")
    assert hits[0].id == "nv"


def test_advise_maps_intermittent_brief(kb: KnowledgeBase) -> None:
    tips = kb.advise("we have intermittent lumpy demand on spare parts")
    assert tips
    assert all(isinstance(t, MethodAdvice) for t in tips)
    assert any("croston" in t.concept.id.lower() for t in tips)


def test_ground_citations_uses_brief_and_keywords(kb: KnowledgeBase) -> None:
    cites = kb.ground_citations(
        ("reorder", "safety stock", "inventory"),
        "intermittent spare-parts demand needs a better forecast method",
        limit=5,
    )
    assert 1 <= len(cites) <= 5
    assert all(isinstance(c, str) for c in cites)


def test_advise_gates_a_trigger_outside_the_tool_domain(kb: KnowledgeBase) -> None:
    """A bare 'chain' token (from 'supply chain') must not fire the leadership CHAIN
    rule for a tool whose domain has nothing to do with leadership."""
    inventory_domain = frozenset({"reorder", "safety", "stock", "inventory", "eoq"})
    tips = kb.advise("optimize reorder points across our supply chain", domain_terms=inventory_domain)
    assert not any(t.concept.id == "chain_model" for t in tips)


def test_advise_still_fires_when_trigger_is_in_domain(kb: KnowledgeBase) -> None:
    leadership_domain = frozenset({"leadership", "chain", "director", "ceo"})
    tips = kb.advise("evaluate our supply chain leadership", domain_terms=leadership_domain)
    assert any(t.concept.id == "chain_model" for t in tips)


def test_ground_citations_does_not_surface_leadership_for_an_eoq_brief(kb: KnowledgeBase) -> None:
    """Repro of the spurious-#1-citation bug: an EOQ/inventory brief that happens to
    say 'supply chain' must not surface leadership or off-topic sustainability
    citations ahead of the actually relevant EOQ / safety-stock concepts."""
    inventory_keywords = (
        "reorder", "safety stock", "stock level", "inventory", "replenish",
        "eoq", "service level", "reorder point", "order quantity",
    )
    brief = "Optimize reorder points across our supply chain inventory using EOQ and safety stock."
    cites = kb.ground_citations(inventory_keywords, brief, limit=5)
    assert not any("leadership" in c.lower() for c in cites)
    assert any("economic order quantity" in c.lower() or "eoq" in c.lower() for c in cites[:2])
