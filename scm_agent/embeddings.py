"""Semantic embeddings for knowledge graph nodes.

Pre-computes vector embeddings for every node in a graph using fastembed
(lightweight, CPU-only, ~20 MB). At query time, cosine similarity between the
query embedding and node embeddings surfaces semantically relevant starting
points — catching paraphrases, synonyms, and conceptual proximity that
keyword matching misses.

Falls back to an empty index when fastembed is not installed, so callers
degrade gracefully to the existing IDF-weighted token search.
"""

from __future__ import annotations

import json
import logging
import math
from pathlib import Path

_LOG = logging.getLogger("kern.embeddings")

_MODEL_NAME = "BAAI/bge-small-en-v1.5"
_CACHE_DIR = Path.home() / ".cache" / "kern" / "embeddings"


def _has_fastembed() -> bool:
    try:
        import fastembed  # noqa: F401
        return True
    except ImportError:
        return False


class EmbeddingIndex:
    """Cosine-similarity search over graph node embeddings.

    Build once at KnowledgeBase init (or lazily on first semantic query),
    then answer queries in O(N) cosine — fast enough for ≤10k nodes.
    """

    def __init__(self) -> None:
        self._model = None
        self._node_ids: list[str] = []
        self._vectors: list[list[float]] = []
        self._ready = False

    @property
    def ready(self) -> bool:
        return self._ready

    @property
    def size(self) -> int:
        return len(self._node_ids)

    def build(
        self,
        nodes: list[dict],
        adjacency: dict[str, set[str]] | None = None,
        *,
        cache_path: Path | None = None,
    ) -> None:
        """Compute embeddings for all nodes and store them.

        Each node is embedded as a text snippet combining its label, type,
        rationale, and up to 5 neighbor labels (relationship-aware context).

        Parameters
        ----------
        nodes : list of node dicts (must have 'id' and 'label' keys).
        adjacency : optional precomputed adjacency dict (node_id -> set of neighbor ids).
        cache_path : optional path to persist/load embeddings cache as JSON.
        """
        if not _has_fastembed():
            _LOG.debug("fastembed not installed — semantic search disabled")
            return

        if not nodes:
            return

        # Try loading cache first
        if cache_path and cache_path.exists():
            cached = self._load_cache(cache_path, nodes)
            if cached is not None:
                self._node_ids, self._vectors = cached
                self._ready = True
                _LOG.info("Loaded %d cached embeddings from %s", len(self._node_ids), cache_path)
                return

        from fastembed import TextEmbedding

        self._model = TextEmbedding(_MODEL_NAME)

        # Build node index for neighbor lookup
        node_index = {n["id"]: n for n in nodes if "id" in n}
        adj = adjacency or {}

        # Build text snippets for embedding
        texts: list[str] = []
        ids: list[str] = []
        for node in nodes:
            nid = node.get("id", "")
            if not nid:
                continue
            text = self._node_to_text(node, node_index, adj, max_neighbors=5)
            texts.append(text)
            ids.append(nid)

        if not texts:
            return

        # Batch embed (fastembed handles batching internally)
        embeddings = list(self._model.embed(texts, show_progress=False))

        self._node_ids = ids
        self._vectors = [e.tolist() if hasattr(e, "tolist") else list(e) for e in embeddings]
        self._ready = True

        # Persist cache
        if cache_path:
            self._save_cache(cache_path)
            _LOG.info("Cached %d embeddings to %s", len(ids), cache_path)

    def search(self, query: str, top_k: int = 5) -> list[tuple[str, float]]:
        """Return top_k (node_id, cosine_score) pairs for a query string.

        Returns empty list if the index is not ready or query is empty.
        """
        if not self._ready or not self._model or not query.strip():
            return []

        q_vec = list(self._model.embed([query], show_progress=False))[0]
        q_list = q_vec.tolist() if hasattr(q_vec, "tolist") else list(q_vec)

        # Cosine similarity: dot(a, b) / (||a|| * ||b||)
        q_norm = math.sqrt(sum(x * x for x in q_list))
        if q_norm == 0:
            return []

        scored: list[tuple[float, str]] = []
        for i, vec in enumerate(self._vectors):
            v_norm = math.sqrt(sum(x * x for x in vec))
            if v_norm == 0:
                continue
            dot = sum(a * b for a, b in zip(q_list, vec))
            cosine = dot / (q_norm * v_norm)
            scored.append((cosine, self._node_ids[i]))

        scored.sort(reverse=True)
        return [(nid, score) for score, nid in scored[:top_k]]

    # -- internals --

    @staticmethod
    def _node_to_text(
        node: dict,
        node_index: dict[str, dict],
        adjacency: dict[str, set[str]],
        *,
        max_neighbors: int = 5,
    ) -> str:
        """Build a descriptive text snippet for a node."""
        parts: list[str] = []

        label = node.get("label", "")
        if label:
            parts.append(label)

        norm = node.get("norm_label", "")
        if norm and norm != label:
            parts.append(norm)

        rationale = node.get("rationale")
        if rationale:
            parts.append(rationale[:200])

        # Add neighbor context for relationship awareness
        neighbors = adjacency.get(node.get("id", ""), set())
        neighbor_labels: list[str] = []
        for nid in list(neighbors)[:max_neighbors]:
            n = node_index.get(nid)
            if n:
                neighbor_labels.append(n.get("label", nid))
        if neighbor_labels:
            parts.append("connects to: " + ", ".join(neighbor_labels))

        return " ".join(parts)

    def _save_cache(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        data = {"ids": self._node_ids, "vectors": self._vectors}
        path.write_text(json.dumps(data), encoding="utf-8")

    def _load_cache(
        self, path: Path, current_nodes: list[dict]
    ) -> tuple[list[str], list[list[float]]] | None:
        """Load cache and invalidate if node set changed."""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            cached_ids = data["ids"]
            cached_vectors = data["vectors"]
        except (json.JSONDecodeError, KeyError, OSError):
            return None

        current_ids = {n["id"] for n in current_nodes if "id" in n}
        cached_id_set = set(cached_ids)

        # Invalidate if node set changed (added or removed nodes)
        if current_ids != cached_id_set:
            _LOG.debug("Embedding cache stale (node set changed), recomputing")
            return None

        return cached_ids, cached_vectors
