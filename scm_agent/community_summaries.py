"""On-demand community summaries for the knowledge graph.

Groups nodes by their ``community`` field, generates a short plain-language
summary per community (1-2 sentences listing the key concepts), and caches
the result so subsequent lookups are instant.

Summaries are generated deterministically from node labels (no LLM required)
— a template-based approach that works well for domain graphs with clear
community structure. The summary is: "Community N: <top labels joined>".

Cache invalidation is automatic: if the graph's node count or community count
changes, the cache is rebuilt.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from pathlib import Path

_LOG = logging.getLogger("kern.community_summaries")

_SUMMARY_CACHE = Path("knowledge") / "community_summaries.json"


def _community_key(community_id: int | str | None) -> str:
    """Normalize a community identifier to a string key."""
    return str(community_id) if community_id is not None else "unclustered"


class CommunitySummaryIndex:
    """Lazy, cached summaries for every community in a graph.

    Usage::

        index = CommunitySummaryIndex.from_graph(graph_data, cache_path)
        index.get(42)   # "Community 42: Safety Stock, Reorder Point, ..."
        index.all()     # {community_id: summary_text, ...}
    """

    def __init__(self, summaries: dict[str, str], meta: dict[str, int]) -> None:
        self._summaries = summaries
        self._meta = meta  # {"node_count": N, "community_count": M}

    @property
    def meta(self) -> dict[str, int]:
        return dict(self._meta)

    def get(self, community_id: int | str | None, default: str = "") -> str:
        """Return the summary for a community, or ``default`` if not found."""
        return self._summaries.get(_community_key(community_id), default)

    def all(self) -> dict[str, str]:
        """Return all community summaries."""
        return dict(self._summaries)

    def find_communities_for_node(
        self, node_id: str, graph: dict
    ) -> list[str]:
        """Return community summaries that contain the given node.

        Useful for understanding which communities a query result belongs to.
        """
        node_index = {n["id"]: n for n in graph.get("nodes", []) if "id" in n}
        node = node_index.get(node_id)
        if node is None:
            return []
        cid = _community_key(node.get("community"))
        summary = self._summaries.get(cid)
        return [summary] if summary else []

    @classmethod
    def from_graph(
        cls,
        graph: dict,
        cache_path: Path | str | None = None,
    ) -> CommunitySummaryIndex:
        """Build summaries from a graph, using cache when available.

        Parameters
        ----------
        graph : dict with ``"nodes"`` list (each node may have a ``"community"`` field).
        cache_path : path to the JSON cache file. Defaults to ``knowledge/community_summaries.json``.
        """
        cache = Path(cache_path) if cache_path else _SUMMARY_CACHE

        nodes = graph.get("nodes", [])
        node_count = len(nodes)
        communities = {n.get("community") for n in nodes if "community" in n}
        community_count = len(communities)

        # Check cache validity
        if cache.exists():
            try:
                cached = json.loads(cache.read_text(encoding="utf-8"))
                meta = cached.get("_meta", {})
                if meta.get("node_count") == node_count and meta.get("community_count") == community_count:
                    summaries = {k: v for k, v in cached.items() if k != "_meta"}
                    _LOG.debug("Loaded %d community summaries from cache", len(summaries))
                    return cls(summaries, meta)
            except (json.JSONDecodeError, OSError):
                pass

        # Build summaries
        summaries = _build_summaries(nodes)

        # Persist
        meta = {"node_count": node_count, "community_count": community_count}
        cache_data = {**summaries, "_meta": meta}
        try:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(cache_data, indent=2, ensure_ascii=False), encoding="utf-8")
            _LOG.info("Wrote %d community summaries to %s", len(summaries), cache)
        except OSError as exc:
            _LOG.warning("Could not write community summary cache: %s", exc)

        return cls(summaries, meta)


def _build_summaries(nodes: list[dict]) -> dict[str, str]:
    """Generate 1-2 sentence summaries per community from node labels.

    Strategy: group nodes by community, rank by degree (connections),
    and join the top labels into a readable summary.
    """
    # Group nodes by community
    by_community: dict[str, list[dict]] = defaultdict(list)
    for node in nodes:
        cid = _community_key(node.get("community"))
        by_community[cid].append(node)

    # We don't have edges here directly, so we use label length as a proxy
    # for specificity (longer labels = more specific concepts)

    summaries: dict[str, str] = {}
    for cid, community_nodes in by_community.items():
        if cid == "unclustered":
            continue

        # Sort by label specificity (longer = more specific), take top 5
        ranked = sorted(
            community_nodes,
            key=lambda n: len(n.get("label", "")),
            reverse=True,
        )
        top_labels = [n.get("label", n.get("id", "?")) for n in ranked[:5]]

        if len(community_nodes) == 1:
            summaries[cid] = f"Community {cid}: {top_labels[0]}"
        else:
            joined = ", ".join(top_labels[:4])
            summaries[cid] = f"Community {cid} ({len(community_nodes)} concepts): {joined}"

    return summaries
