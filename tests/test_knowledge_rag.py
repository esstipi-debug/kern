"""Tests for the three Graph RAG improvements:
1. Semantic embeddings (scm_agent/embeddings.py)
2. Community summaries (scm_agent/community_summaries.py)
3. Graph query learning loop (scm_agent/graph_memory.py)
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scm_agent.community_summaries import CommunitySummaryIndex, _build_summaries
from scm_agent.embeddings import EmbeddingIndex, _has_fastembed
from scm_agent.graph_memory import (
    _slug,
    load_outcomes,
    load_reminders,
    query_history,
    save_query_outcome,
    save_reminder,
)

REPO = Path(__file__).resolve().parent.parent
BOOKS_GRAPH = REPO / "knowledge" / "scm-books" / "graph.json"


# ── Embeddings ──────────────────────────────────────────────────────


class TestEmbeddingIndex:
    """Tests for the semantic embedding index."""

    def test_has_fastembed_returns_bool(self) -> None:
        assert isinstance(_has_fastembed(), bool)

    @pytest.mark.skipif(not _has_fastembed(), reason="fastembed not installed")
    def test_build_and_search(self, tmp_path: Path) -> None:
        nodes = [
            {"id": "a", "label": "safety stock", "rationale": "buffer against demand uncertainty"},
            {"id": "b", "label": "reorder point", "rationale": "when to place a new order"},
            {"id": "c", "label": "economic order quantity", "rationale": "optimal order size balancing cost"},
        ]
        idx = EmbeddingIndex()
        idx.build(nodes, cache_path=tmp_path / "emb.json")
        assert idx.ready
        assert idx.size == 3

        results = idx.search("buffer for uncertain demand", top_k=2)
        assert len(results) <= 2
        assert len(results) > 0
        # "safety stock" should be top-1 for this paraphrase
        top_id, top_score = results[0]
        assert top_id == "a"
        assert 0 < top_score <= 1.0

    @pytest.mark.skipif(not _has_fastembed(), reason="fastembed not installed")
    def test_cache_roundtrip(self, tmp_path: Path) -> None:
        nodes = [
            {"id": "x", "label": "ABC analysis", "rationale": "classification by value"},
        ]
        cache = tmp_path / "cache.json"
        idx1 = EmbeddingIndex()
        idx1.build(nodes, cache_path=cache)
        assert cache.exists()

        idx2 = EmbeddingIndex()
        idx2.build(nodes, cache_path=cache)
        assert idx2.ready
        assert idx2.size == 1

    @pytest.mark.skipif(not _has_fastembed(), reason="fastembed not installed")
    def test_cache_invalidated_on_node_change(self, tmp_path: Path) -> None:
        nodes_v1 = [{"id": "a", "label": "foo"}]
        cache = tmp_path / "cache.json"
        idx1 = EmbeddingIndex()
        idx1.build(nodes_v1, cache_path=cache)

        nodes_v2 = [{"id": "a", "label": "foo"}, {"id": "b", "label": "bar"}]
        idx2 = EmbeddingIndex()
        idx2.build(nodes_v2, cache_path=cache)
        assert idx2.size == 2  # rebuilt, not stale

    def test_search_empty_when_not_built(self) -> None:
        idx = EmbeddingIndex()
        assert idx.search("anything") == []
        assert not idx.ready

    def test_search_empty_query(self, tmp_path: Path) -> None:
        idx = EmbeddingIndex()
        assert idx.search("") == []


# ── Community Summaries ─────────────────────────────────────────────


class TestCommunitySummaries:
    """Tests for community summary generation."""

    def test_build_summaries_basic(self) -> None:
        nodes = [
            {"id": "a", "label": "Safety Stock", "community": 1},
            {"id": "b", "label": "Reorder Point", "community": 1},
            {"id": "c", "label": "EOQ", "community": 2},
        ]
        summaries = _build_summaries(nodes)
        assert "1" in summaries
        assert "2" in summaries
        assert "Safety Stock" in summaries["1"]

    def test_from_graph_uses_cache(self, tmp_path: Path) -> None:
        graph = {
            "nodes": [
                {"id": "a", "label": "Foo", "community": 1},
                {"id": "b", "label": "Bar", "community": 1},
            ]
        }
        cache = tmp_path / "summaries.json"
        idx1 = CommunitySummaryIndex.from_graph(graph, cache_path=cache)
        assert cache.exists()
        assert idx1.get("1") != ""

        idx2 = CommunitySummaryIndex.from_graph(graph, cache_path=cache)
        assert idx2.get("1") == idx1.get("1")

    def test_cache_invalidated_on_graph_change(self, tmp_path: Path) -> None:
        graph_v1 = {"nodes": [{"id": "a", "label": "X", "community": 1}]}
        cache = tmp_path / "s.json"
        CommunitySummaryIndex.from_graph(graph_v1, cache_path=cache)

        graph_v2 = {"nodes": [
            {"id": "a", "label": "X", "community": 1},
            {"id": "b", "label": "Y", "community": 2},
        ]}
        idx = CommunitySummaryIndex.from_graph(graph_v2, cache_path=cache)
        assert idx.get("2") != ""

    def test_unclustered_nodes_ignored(self) -> None:
        nodes = [{"id": "a", "label": "Orphan", "community": None}]
        summaries = _build_summaries(nodes)
        assert "unclustered" not in summaries

    def test_get_default_for_missing(self) -> None:
        idx = CommunitySummaryIndex({}, {"node_count": 0, "community_count": 0})
        assert idx.get(999) == ""
        assert idx.get(999, "fallback") == "fallback"

    def test_real_books_graph(self) -> None:
        if not BOOKS_GRAPH.exists():
            pytest.skip("books graph not found")
        graph = json.loads(BOOKS_GRAPH.read_text(encoding="utf-8"))
        idx = CommunitySummaryIndex.from_graph(graph)
        summaries = idx.all()
        assert len(summaries) > 0
        # Community 12 is the largest (92 nodes)
        assert "12" in summaries


# ── Graph Memory / Learning Loop ────────────────────────────────────


class TestGraphMemory:
    """Tests for the query learning loop."""

    def test_slug_basic(self) -> None:
        assert _slug("How does safety stock work?") == "how_does_safety_stock_work"

    def test_slug_empty(self) -> None:
        assert _slug("") == ""

    def test_save_query_outcome(self, tmp_path: Path) -> None:
        graph = {
            "nodes": [
                {"id": "knowledge::safety_stock", "label": "Safety Stock"},
                {"id": "knowledge::reorder_point", "label": "Reorder Point"},
            ],
            "links": [],
        }
        graph_file = tmp_path / "graph.json"
        graph_file.write_text(json.dumps(graph), encoding="utf-8")
        mem_dir = tmp_path / "memory"

        result = save_query_outcome(
            question="What is safety stock?",
            answer="Safety stock buffers against demand uncertainty.",
            source_nodes=["knowledge::safety_stock", "knowledge::reorder_point"],
            graph_path=graph_file,
            memory_dir=mem_dir,
        )
        assert result is not None
        assert result["node_type"] == "query_outcome"
        assert "safety stock" in result["question"].lower()

        # Verify graph was updated
        updated = json.loads(graph_file.read_text(encoding="utf-8"))
        qo_nodes = [n for n in updated["nodes"] if n.get("node_type") == "query_outcome"]
        assert len(qo_nodes) == 1
        qo_edges = [e for e in updated["links"] if e.get("relation") == "cited_in"]
        assert len(qo_edges) == 2

        # Verify markdown was written
        md_files = list(mem_dir.glob("*.md"))
        assert len(md_files) == 1

    def test_dedup_within_5_minutes(self, tmp_path: Path) -> None:
        graph = {"nodes": [{"id": "a", "label": "A"}], "links": []}
        graph_file = tmp_path / "g.json"
        graph_file.write_text(json.dumps(graph), encoding="utf-8")

        r1 = save_query_outcome("test?", "ans", ["a"], graph_path=graph_file, memory_dir=tmp_path / "m1")
        r2 = save_query_outcome("test?", "ans", ["a"], graph_path=graph_file, memory_dir=tmp_path / "m2")
        assert r1 is not None
        assert r2 is None  # deduped

    def test_load_outcomes(self, tmp_path: Path) -> None:
        graph = {
            "nodes": [
                {"id": "q::1", "node_type": "query_outcome", "question": "Q1", "outcome": "useful", "captured_at": "2026-07-20T10:00:00"},
                {"id": "q::2", "node_type": "query_outcome", "question": "Q2", "outcome": "wrong", "captured_at": "2026-07-20T11:00:00"},
                {"id": "regular", "label": "Not a query"},
            ],
            "links": [],
        }
        graph_file = tmp_path / "g.json"
        graph_file.write_text(json.dumps(graph), encoding="utf-8")

        all_outcomes = load_outcomes(graph_file)
        assert len(all_outcomes) == 2

        useful_only = load_outcomes(graph_file, outcome_filter="useful")
        assert len(useful_only) == 1

    def test_query_history_finds_similar(self, tmp_path: Path) -> None:
        graph = {
            "nodes": [
                {
                    "id": "q::1",
                    "node_type": "query_outcome",
                    "question": "How does safety stock work?",
                    "outcome": "useful",
                    "answer": "Buffers against uncertainty",
                    "captured_at": "2026-07-20T10:00:00",
                },
            ],
            "links": [],
        }
        graph_file = tmp_path / "g.json"
        graph_file.write_text(json.dumps(graph), encoding="utf-8")

        history = query_history("safety stock mechanism", graph_file)
        assert len(history) == 1

    def test_query_history_no_match(self, tmp_path: Path) -> None:
        graph = {
            "nodes": [
                {
                    "id": "q::1",
                    "node_type": "query_outcome",
                    "question": "How does EOQ work?",
                    "outcome": "useful",
                },
            ],
            "links": [],
        }
        graph_file = tmp_path / "g.json"
        graph_file.write_text(json.dumps(graph), encoding="utf-8")

        history = query_history("quantum computing basics", graph_file)
        assert len(history) == 0

    def test_save_reminder(self, tmp_path: Path) -> None:
        graph = {"nodes": [], "links": []}
        graph_file = tmp_path / "g.json"
        graph_file.write_text(json.dumps(graph), encoding="utf-8")

        result = save_reminder(
            text="Evaluar Fable 5 para embeddings",
            tags=["model_upgrade", "fable5"],
            graph_path=graph_file,
        )
        assert result is not None
        assert result["node_type"] == "reminder"
        assert result["tags"] == ["model_upgrade", "fable5"]

        updated = json.loads(graph_file.read_text(encoding="utf-8"))
        reminders = [n for n in updated["nodes"] if n.get("node_type") == "reminder"]
        assert len(reminders) == 1

    def test_load_reminders(self, tmp_path: Path) -> None:
        graph = {
            "nodes": [
                {"id": "r::1", "node_type": "reminder", "text": "Upgrade model", "captured_at": "2026-07-22T10:00:00"},
                {"id": "r::2", "node_type": "reminder", "text": "Add tests", "captured_at": "2026-07-21T10:00:00"},
                {"id": "q::1", "node_type": "query_outcome", "question": "X"},
            ],
            "links": [],
        }
        graph_file = tmp_path / "g.json"
        graph_file.write_text(json.dumps(graph), encoding="utf-8")

        reminders = load_reminders(graph_file)
        assert len(reminders) == 2
        assert reminders[0]["text"] == "Upgrade model"  # most recent first


# ── KnowledgeBase integration ───────────────────────────────────────


class TestKnowledgeBaseRAG:
    """Tests for the new search_semantic / search_hybrid / community_summary methods."""

    def test_search_semantic_works(self) -> None:
        from scm_agent.knowledge import KnowledgeBase
        kb = KnowledgeBase()
        if not kb._embedding_index.ready:
            pytest.skip("fastembed not available")

        results = kb.search_semantic("buffer for uncertain demand")
        assert len(results) > 0
        concept, score = results[0]
        assert concept.label  # must have a label
        assert 0 < score <= 1.0

    def test_search_hybrid_merges_results(self) -> None:
        from scm_agent.knowledge import KnowledgeBase
        kb = KnowledgeBase()
        if not kb._embedding_index.ready:
            pytest.skip("fastembed not available")

        results = kb.search_hybrid("safety stock")
        assert len(results) > 0
        # Should find safety stock via keyword AND/OR semantic
        labels = [c.id for c in results]
        assert any("safety_stock" in lid for lid in labels)

    def test_community_summary_returns_string(self) -> None:
        from scm_agent.knowledge import KnowledgeBase
        kb = KnowledgeBase()
        summary = kb.community_summary(1)
        assert isinstance(summary, str)

    def test_community_summaries_all_not_empty(self) -> None:
        from scm_agent.knowledge import KnowledgeBase
        kb = KnowledgeBase()
        all_summaries = kb.community_summaries_all()
        assert len(all_summaries) > 0
