"""Re-cluster knowledge/scm-books/graph.json in place after a source merge.

Uses graphify's own Louvain clustering but writes community ids back onto the
existing node dicts, so every custom attribute (rationale, source_location,
author, ...) survives.

Community labels are NOT carried over: the books graph stores labels in
GRAPH_REPORT.md, not on nodes, so there is nothing on a node to preserve.
Regenerate the report with scripts/regen_books_report.py after running this.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GRAPH = REPO / "knowledge" / "scm-books" / "graph.json"
BACKUP = REPO / "knowledge" / "scm-books" / "graph.cluster.bak"


def main() -> int:
    from graphify.build import build_from_json
    from graphify.cluster import cluster, score_all

    data = json.loads(GRAPH.read_text(encoding="utf-8"))
    nodes = data["nodes"]
    links = data.get("links", data.get("edges", []))

    prev_communities = {n.get("community") for n in nodes if n.get("community") is not None}

    G = build_from_json({"nodes": nodes, "edges": links})
    communities = cluster(G)
    cohesion = score_all(G, communities)

    assign: dict[str, int] = {}
    for cid, members in communities.items():
        for m in members:
            assign[m] = cid

    # Never write community=null for a real node: a null community silently drops
    # that node out of every community-scoped surface (report sections,
    # CommunitySummaryIndex) with no error anywhere. Two distinct cases:
    #   - in the graph but unclustered: Louvain is stochastic and occasionally
    #     leaves a node out; a singleton community is a valid outcome, so allocate one.
    #   - not in the graph at all: build_from_json dropped it as malformed (e.g. a
    #     node missing `source_file`). That is a real data defect -- keep whatever
    #     community it already had rather than inventing structure, and report it.
    next_cid = max(communities, default=-1) + 1
    singletons: list[str] = []
    dropped: list[str] = []
    for n in nodes:
        nid = n.get("id")
        if nid is None:
            continue
        if nid in assign:
            n["community"] = assign[nid]
        elif G.has_node(nid):
            n["community"] = next_cid
            assign[nid] = next_cid
            next_cid += 1
            singletons.append(nid)
        else:
            dropped.append(nid)
            if n.get("community") is None:
                n["community"] = next_cid
                next_cid += 1

    if singletons:
        print(f"NOTE: {len(singletons)} clustered-but-unassigned node(s) placed in singleton "
              f"communities (Louvain is stochastic): {singletons[:5]}")
    if dropped:
        print(f"WARNING: {len(dropped)} node(s) were dropped by the graph builder (malformed - "
              f"usually a missing required field) and kept their prior community: {dropped[:5]}")

    data["nodes"] = nodes
    shutil.copy2(GRAPH, BACKUP)
    GRAPH.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    avg = sum(cohesion.values()) / len(cohesion) if cohesion else 0
    print(f"nodes: {len(nodes)}  edges: {len(links)}")
    print(f"communities: {len(communities)} (was {len(prev_communities)})")
    print(f"every node assigned; mean cohesion: {avg:.3f}")
    print(f"backup -> {BACKUP}")
    print("NOTE: community labels live in GRAPH_REPORT.md -- re-run scripts/regen_books_report.py")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
