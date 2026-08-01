"""Tests for the Graph RAG discovery improvements:
1. Semantic embeddings (scm_agent/embeddings.py)
2. Community summaries (scm_agent/community_summaries.py)

The query-learning loop (graph_memory.py) was removed: it wrote query_outcome
nodes back into the canonical citation graph, the exact perturbation the
citation-pool sweep exists to prevent. Retrieval-only scope keeps the graph
immutable at query time.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scm_agent.community_summaries import CommunitySummaryIndex, _build_summaries
from scm_agent.embeddings import EmbeddingIndex, _has_fastembed

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

    @pytest.mark.skipif(not _has_fastembed(), reason="fastembed not installed")
    def test_warm_cache_search_works(self, tmp_path: Path) -> None:
        """A cache-loaded index must still answer queries.

        Regression: build()'s cache-load path set _ready=True without
        instantiating a model, so search() returned [] on every warm start —
        the semantic path silently died on the 2nd process onward.
        """
        nodes = [
            {"id": "a", "label": "safety stock", "rationale": "buffer against demand uncertainty"},
            {"id": "b", "label": "reorder point", "rationale": "when to place a new order"},
        ]
        cache = tmp_path / "cache.json"
        EmbeddingIndex().build(nodes, cache_path=cache)  # cold build populates cache

        warm = EmbeddingIndex()
        warm.build(nodes, cache_path=cache)  # loads from cache; no model built in build()
        assert warm.ready
        assert warm._model is None  # confirm we're exercising the cache-load path
        results = warm.search("buffer for uncertain demand", top_k=1)
        assert len(results) == 1
        assert results[0][0] == "a"

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
