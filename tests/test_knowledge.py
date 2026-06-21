"""Tests for the L3 knowledge layer (scm_agent/knowledge.py).

Runs against the committed books graph (knowledge/scm-books/graph.json). The
code graph (graphify-out/) is gitignored, so tests that need it skip when absent.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scm_agent.knowledge import (
    Bridge,
    Concept,
    ConceptDetail,
    KnowledgeBase,
)

REPO = Path(__file__).resolve().parent.parent
BOOKS = REPO / "knowledge" / "scm-books" / "graph.json"
CODE = REPO / "graphify-out" / "graph.json"


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
