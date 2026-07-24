"""Regenerate knowledge/scm-books/GRAPH_REPORT.md from the committed graph.

Community labels are derived deterministically from each community's
highest-degree node rather than an LLM naming pass, so the report can be
rebuilt after any merge with no API key and no fabricated names.
"""

from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
GRAPH = REPO / "knowledge" / "scm-books" / "graph.json"
REPORT = REPO / "knowledge" / "scm-books" / "GRAPH_REPORT.md"


def main() -> int:
    from graphify.analyze import god_nodes, suggest_questions, surprising_connections
    from graphify.build import build_from_json
    from graphify.cluster import score_all
    from graphify.report import generate

    data = json.loads(GRAPH.read_text(encoding="utf-8"))
    nodes = data["nodes"]
    links = data.get("links", data.get("edges", []))
    G = build_from_json({"nodes": nodes, "edges": links})

    # Only nodes the graph builder actually kept can be reported on: a malformed
    # node (e.g. missing `source_file`) is dropped from G but still carries a
    # community in graph.json, and graphify indexes G by member id.
    communities: dict[int, list[str]] = defaultdict(list)
    skipped = 0
    for n in nodes:
        cid, nid = n.get("community"), n.get("id")
        if cid is None or nid is None:
            continue
        if not G.has_node(nid):
            skipped += 1
            continue
        communities[cid].append(nid)
    communities = {cid: m for cid, m in communities.items() if m}
    if skipped:
        print(f"note: {skipped} node(s) absent from the built graph were excluded from the report")

    label_of = {n["id"]: n.get("label", n["id"]) for n in nodes if "id" in n}
    community_labels: dict[int, str] = {}
    for cid, members in communities.items():
        best = max(members, key=lambda m: G.degree(m) if m in G else 0)
        community_labels[cid] = label_of.get(best, f"Community {cid}")

    cohesion = score_all(G, communities)
    gods = god_nodes(G)
    surprises = surprising_connections(G, communities)
    questions = suggest_questions(G, communities, community_labels)

    sources: dict[str, int] = defaultdict(int)
    for n in nodes:
        sources[str(n.get("source_file") or "(none)")] += 1
    detection = {
        "total_files": len(sources),
        "total_words": 0,
        "files": {"paper": sorted(sources)},
        "skipped_sensitive": [],
    }

    report = generate(
        G, communities, cohesion, community_labels, gods, surprises,
        detection, {"input": 0, "output": 0}, str(REPO),
        suggested_questions=questions,
    )
    REPORT.write_text(report, encoding="utf-8")
    print(f"nodes {G.number_of_nodes()} edges {G.number_of_edges()} communities {len(communities)}")
    print(f"report -> {REPORT} ({len(report):,} chars)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
