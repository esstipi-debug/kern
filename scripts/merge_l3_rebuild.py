"""Merge re-extracted per-book graphs into knowledge/scm-books/graph.json.

Replaces nodes (and their edges) for sources that were successfully re-extracted,
keeps all other committed sources, prunes weak INFERRED edges, writes graph.json.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
COMMITTED = REPO / "knowledge" / "scm-books" / "graph.json"
REBUILD = REPO / "knowledge" / "scm-books-rebuild"
BACKUP = REPO / "knowledge" / "scm-books" / "graph.json.bak"
MIN_INFERRED = 0.75

# source_file stems replaced when a rebuild graph exists
REPLACE_STEMS = (
    "palamariu-alicke-from-source-to-sold",
    "hyndman-fpp3-forecasting-principles-practice-FULL",
    "hyndman-forecasting-principles-practice-2ed",
    "christopher-logistics-supply-chain-management",
    "grant-sustainable-logistics-supply-chain",
    "ivanov-global-supply-chain-operations",
    "vandeput-inventory-optimization-models-simulations",
)


def _stem(source: str | None) -> str:
    if not source:
        return ""
    return Path(source).stem.lower()


def _load(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("links", data.get("edges", []))
    return data


def _collect_rebuild_graphs() -> list[Path]:
    graphs: list[Path] = []
    for d in sorted(REBUILD.iterdir()):
        g = d / "graphify-out" / "graph.json"
        if g.is_file():
            graphs.append(g)
    return graphs


def main() -> int:
    if not COMMITTED.is_file():
        print(f"missing {COMMITTED}", file=sys.stderr)
        return 1
    rebuild_paths = _collect_rebuild_graphs()
    if not rebuild_paths:
        print("no rebuild graphs found", file=sys.stderr)
        return 1

    base = _load(COMMITTED)
    replace_stems = {s.lower() for s in REPLACE_STEMS}

    kept_nodes = [
        n for n in base["nodes"]
        if _stem(n.get("source_file")) not in replace_stems
    ]
    kept_ids = {n["id"] for n in kept_nodes if "id" in n}

    new_nodes: dict[str, dict] = {}
    new_links: list[dict] = []
    for path in rebuild_paths:
        g = _load(path)
        for n in g.get("nodes", []):
            if "id" in n:
                new_nodes[n["id"]] = n
        new_links.extend(g.get("links", []))

    merged_nodes = kept_nodes + [n for nid, n in new_nodes.items() if nid not in kept_ids]
    merged_ids = {n["id"] for n in merged_nodes if "id" in n}

    def _edge_ok(e: dict) -> bool:
        if e.get("confidence") == "INFERRED":
            sc = e.get("confidence_score")
            if sc is not None and sc < MIN_INFERRED:
                return False
        s, t = e.get("source"), e.get("target")
        return s in merged_ids and t in merged_ids

    old_links = [
        e for e in base.get("links", [])
        if e.get("source") in kept_ids and e.get("target") in kept_ids and _edge_ok(e)
    ]
    new_links = [e for e in new_links if _edge_ok(e)]
    merged_links = old_links + new_links

    out = {**{k: v for k, v in base.items() if k not in ("nodes", "links", "edges")},
           "nodes": merged_nodes, "links": merged_links}

    shutil.copy2(COMMITTED, BACKUP)
    COMMITTED.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    ex = sum(1 for e in merged_links if e.get("confidence") == "EXTRACTED")
    inf = sum(1 for e in merged_links if e.get("confidence") == "INFERRED")
    print(f"rebuild graphs merged: {len(rebuild_paths)}")
    print(f"nodes {len(base['nodes'])} -> {len(merged_nodes)}")
    print(f"edges {len(base.get('links', []))} -> {len(merged_links)}")
    print(f"EXTRACTED {ex} ({100*ex/len(merged_links):.1f}%) INFERRED {inf}")
    print(f"backup -> {BACKUP}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
