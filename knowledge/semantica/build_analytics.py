"""Lightweight analytics for the full kern graph (19477 nodes):
degree centrality, connectivity components, louvain communities.
Skips O(N^3) betweenness/closeness/eigenvector which are intractable here.
"""
import json
import sys
import traceback
from collections import Counter
from pathlib import Path

import networkx as nx
from networkx.algorithms.community import louvain_communities

OUT_DIR = Path(r"kern-semantica-out")


def main() -> None:
    print("== Loading existing graph ==")
    with open(OUT_DIR / "graph.json", encoding="utf-8") as fh:
        gd = json.load(fh)

    G = nx.Graph()
    for n in gd.get("nodes", []):
        nid = n.get("id") or n.get("node_id")
        if nid:
            G.add_node(nid)
    for e in gd.get("edges", []):
        s = e.get("source")
        t = e.get("target")
        if s and t and s in G and t in G:
            G.add_edge(s, t)

    print(f"  graph: {G.number_of_nodes()} nodes / {G.number_of_edges()} edges")

    out: dict = {}

    # ---- metrics ----
    deg = dict(G.degree())
    out["graph_metrics"] = {
        "node_count": G.number_of_nodes(),
        "edge_count": G.number_of_edges(),
        "density": round(nx.density(G), 6),
        "avg_degree": round(sum(deg.values()) / G.number_of_nodes(), 4) if G.number_of_nodes() else 0,
        "max_degree": max(deg.values()) if deg else 0,
    }

    # ---- degree centrality ----
    dc = nx.degree_centrality(G)
    top = sorted(dc.items(), key=lambda kv: kv[1], reverse=True)[:40]
    out["degree_centrality"] = {
        "rankings": [{"node": n, "score": round(s, 6)} for n, s in top],
    }

    # ---- connectivity ----
    comps = list(nx.connected_components(G))
    sizes = sorted((len(c) for c in comps), reverse=True)
    out["connectivity"] = {
        "num_components": len(comps),
        "largest_component_size": sizes[0] if sizes else 0,
        "component_sizes_top20": sizes[:20],
        "is_connected": len(comps) == 1,
    }

    # ---- communities ----
    print("== Louvain communities ==")
    comms = louvain_communities(G, seed=42)
    out["communities"] = {
        "algorithm": "louvain",
        "num_communities": len(comms),
        "sizes_top30": sorted((len(c) for c in comms), reverse=True)[:30],
    }
    # label communities by most common node type prefix
    prefix = {}
    for ci, c in enumerate(comms):
        if len(c) < 3:
            continue
        kinds = Counter()
        for nid in c:
            if nid.startswith("book:"):
                kinds["book"] += 1
            elif nid.startswith("doc:"):
                kinds["doc"] += 1
            elif nid.startswith("concept:"):
                kinds["concept"] += 1
            elif ":" in nid and nid.count(":") >= 2:
                kinds["code-symbol"] += 1
            else:
                kinds["module"] += 1
        label = kinds.most_common(1)[0][0]
        prefix.setdefault(label, []).append(ci)

    out["communities"]["biggest_by_kind"] = {}
    for kind, cids in prefix.items():
        sizes = [(ci, len(comms[ci])) for ci in cids]
        sizes.sort(key=lambda kv: kv[1], reverse=True)
        out["communities"]["biggest_by_kind"][kind] = [{"community": ci, "size": s} for ci, s in sizes[:3]]

    # node type counts
    types = Counter()
    for n in gd.get("nodes", []):
        nid = n.get("id") or n.get("node_id")
        if nid is None:
            continue
        if nid.startswith("book:"):
            types["book"] += 1
        elif nid.startswith("doc:"):
            types["doc"] += 1
        elif nid.startswith("concept:"):
            types["concept"] += 1
        elif nid.count(":") >= 2:
            types["code-symbol"] += 1
        else:
            types["module"] += 1
    out["node_kinds"] = dict(types)

    with open(OUT_DIR / "analytics.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    print("  analytics.json saved")
    print("== DONE ==")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        traceback.print_exc()
        sys.exit(1)
