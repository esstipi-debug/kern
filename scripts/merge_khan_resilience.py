"""Merge the Khan et al. (2022) Supply Chain Resilience extraction into the books graph.

Reads the 14 per-chapter chunk JSONs produced by the extraction subagents, builds a
per-book graph, then merges it into knowledge/scm-books/graph.json under the
`knowledge::` namespace so shared concepts collapse onto the existing canonical
nodes (forming cross-book bridges) and new concepts are added carrying this book's
citations.

The committed graph.json must be reproducible by running exactly this script over
the chunk JSONs, so every transformation applied to the artifact lives here --
including the `source_file` normalisation (chunk files carry a path into the
gitignored `knowledge/scm-books-rebuild/` working tree; citations must show a
stable book slug instead).

Re-running is refused by default: the merge is additive and a second pass would
double every edge while the id-set guard still reported "purely additive". Restore
the pre-merge graph (`git checkout HEAD -- knowledge/scm-books/graph.json`) and
re-run, or pass --force if you genuinely intend to re-apply.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
BOOK_DIR = REPO / "knowledge" / "scm-books-rebuild" / "khan-supply-chain-resilience"
CHUNKS = BOOK_DIR / "graphify-out"
BOOK_GRAPH = CHUNKS / "graph.json"
COMMITTED = REPO / "knowledge" / "scm-books" / "graph.json"
BACKUP = REPO / "knowledge" / "scm-books" / "graph.json.bak"

NS = "knowledge"
BOOK_SLUG = "khan-supply-chain-resilience.txt"
EXPECTED_CHUNKS = 14
MIN_INFERRED = 0.75
RUBRIC = (0.55, 0.65, 0.75, 0.85, 0.95)


def snap(score: float | None) -> float | None:
    """Snap an off-rubric INFERRED score to the nearest allowed value.

    Ties resolve to the LOWER value (the conservative direction: a tied score is
    treated as the weaker inference, and MIN_INFERRED may then prune it). The
    rounding on the distance keeps the tie deterministic instead of leaving it to
    float representation error.
    """
    if score is None:
        return None
    if score in RUBRIC:
        return score
    return min(RUBRIC, key=lambda r: (round(abs(r - score), 6), r))


def edge_key(source: str, target: str, relation: str | None) -> tuple:
    """Identity of an undirected edge. The graph declares multigraph=false, so two
    links with the same endpoints and relation are one edge, not two."""
    return (frozenset((source, target)), relation)


def _rank(edge: dict) -> tuple:
    """Higher is better: EXTRACTED beats INFERRED, then higher confidence_score."""
    return (1 if edge.get("confidence") == "EXTRACTED" else 0, edge.get("confidence_score") or 0)


def load_chunks() -> tuple[dict[str, dict], list[dict], list[dict]]:
    nodes: dict[str, dict] = {}
    edges: list[dict] = []
    hyper: list[dict] = []
    files = sorted(CHUNKS.glob(".graphify_chunk_*.json"))
    if len(files) != EXPECTED_CHUNKS:
        print(
            f"expected {EXPECTED_CHUNKS} chunk files in {CHUNKS}, found {len(files)}.\n"
            "The raw corpus and chunk JSONs live under knowledge/scm-books-rebuild/, "
            "which is gitignored by design -- regenerate them locally before merging.",
            file=sys.stderr,
        )
        raise SystemExit(1)
    for p in files:
        d = json.loads(p.read_text(encoding="utf-8"))
        for n in d.get("nodes", []):
            nid = n.get("id")
            if not nid:
                continue
            n["source_file"] = BOOK_SLUG
            if nid in nodes:
                # same canonical concept seen in another chapter: enrich, don't duplicate
                prev = nodes[nid]
                if not prev.get("rationale") and n.get("rationale"):
                    prev["rationale"] = n["rationale"]
            else:
                nodes[nid] = n
        for e in d.get("edges", []):
            if e.get("confidence") == "INFERRED":
                e["confidence_score"] = snap(e.get("confidence_score"))
            e["source_file"] = BOOK_SLUG
            edges.append(e)
        for h in d.get("hyperedges", []) or []:
            h["source_file"] = BOOK_SLUG
            hyper.append(h)
    return nodes, edges, hyper


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--force", action="store_true", help="re-apply even if already merged")
    args = ap.parse_args()

    nodes, edges, hyper = load_chunks()
    print(f"chunks loaded: {len(nodes)} unique nodes, {len(edges)} edges, {len(hyper)} hyperedges")

    # --- per-book graph (bare canonical ids), for provenance / re-merge ---
    BOOK_GRAPH.write_text(
        json.dumps(
            {
                "directed": False,
                "multigraph": False,
                "graph": {"source_book": "khan-supply-chain-resilience"},
                "nodes": list(nodes.values()),
                "links": edges,
                "hyperedges": hyper,
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"per-book graph -> {BOOK_GRAPH}")

    # --- merge into the committed canonical graph ---
    base = json.loads(COMMITTED.read_text(encoding="utf-8"))
    base_links = base.get("links", base.get("edges", []))
    committed_ids = {n["id"] for n in base["nodes"] if "id" in n}

    def qualify(bare: str) -> str:
        return f"{NS}::{bare}"

    already = [b for b in nodes if qualify(b) in committed_ids]
    if len(already) == len(nodes) and not args.force:
        print(
            f"ABORT: all {len(nodes)} node ids are already present in {COMMITTED.name} -- "
            "this book looks merged already.\nRe-running would double every edge while the "
            "id-set guard still reported 'purely additive'.\nRestore the pre-merge graph "
            "(git checkout HEAD -- knowledge/scm-books/graph.json) and re-run, or pass --force.",
            file=sys.stderr,
        )
        return 1

    bridged: list[str] = []
    added: list[dict] = []
    for bare, n in nodes.items():
        qid = qualify(bare)
        if qid in committed_ids:
            bridged.append(bare)  # shared concept: keep existing node, edges form the bridge
            continue
        new_node = dict(n)
        new_node["id"] = qid
        added.append(new_node)

    merged_nodes = base["nodes"] + added
    merged_ids = {n["id"] for n in merged_nodes if "id" in n}

    # Existing edges define the identities a new edge must not duplicate.
    seen: dict[tuple, dict] = {}
    for e in base_links:
        s, t = e.get("source"), e.get("target")
        if s is not None and t is not None:
            seen.setdefault(edge_key(s, t, e.get("relation")), e)

    new_links: list[dict] = []
    dropped_low_conf = dropped_dangling = collapsed = 0
    for e in edges:
        ne = dict(e)
        ne["source"] = qualify(e["source"])
        ne["target"] = qualify(e["target"])
        if ne.get("confidence") == "INFERRED" and (ne.get("confidence_score") or 0) < MIN_INFERRED:
            dropped_low_conf += 1
            continue
        if ne["source"] not in merged_ids or ne["target"] not in merged_ids:
            dropped_dangling += 1
            continue
        if ne["source"] == ne["target"]:
            dropped_dangling += 1
            continue
        key = edge_key(ne["source"], ne["target"], ne.get("relation"))
        prior = seen.get(key)
        if prior is not None:
            # Same edge already known (from this book or an earlier source): keep the
            # stronger evidence rather than adding a parallel link.
            collapsed += 1
            if _rank(ne) > _rank(prior) and prior in new_links:
                new_links[new_links.index(prior)] = ne
                seen[key] = ne
            continue
        seen[key] = ne
        new_links.append(ne)

    new_hyper = []
    for h in hyper:
        qh = dict(h)
        qh["id"] = qualify(h["id"])
        qh["nodes"] = [qualify(x) for x in h.get("nodes", [])]
        if all(x in merged_ids for x in qh["nodes"]):
            new_hyper.append(qh)

    merged_links = base_links + new_links
    merged_hyper = (base.get("hyperedges") or []) + new_hyper

    out = {
        **{k: v for k, v in base.items() if k not in ("nodes", "links", "edges", "hyperedges")},
        "nodes": merged_nodes,
        "links": merged_links,
        "hyperedges": merged_hyper,
    }

    if BACKUP.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        rotated = BACKUP.with_suffix(f".{stamp}.bak")
        shutil.move(str(BACKUP), str(rotated))
        print(f"rotated previous backup -> {rotated.name}")
    shutil.copy2(COMMITTED, BACKUP)
    COMMITTED.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

    print()
    print(
        f"nodes  {len(base['nodes'])} -> {len(merged_nodes)}  "
        f"(+{len(added)} new, {len(bridged)} bridged onto existing canonical nodes)"
    )
    print(
        f"edges  {len(base_links)} -> {len(merged_links)}  (+{len(new_links)}; dropped "
        f"{dropped_low_conf} low-confidence INFERRED, {dropped_dangling} dangling/self-loop, "
        f"collapsed {collapsed} duplicate parallel)"
    )
    print(f"hyper  {len(base.get('hyperedges') or [])} -> {len(merged_hyper)}")
    print(f"backup -> {BACKUP}")

    # id-set guard (the lesson from the 25th-source merge: counts alone hide renames)
    lost = committed_ids - merged_ids
    print()
    print(f"ID-SET GUARD: pre-existing ids lost in merge: {len(lost)}")
    if lost:
        print("  !! " + ", ".join(sorted(lost)[:20]))
        return 1
    print("  OK - every pre-existing node id survived (purely additive)")

    # parallel-edge guard: the graph declares multigraph=false
    keys = [edge_key(e["source"], e["target"], e.get("relation")) for e in merged_links]
    dupes = len(keys) - len(set(keys))
    print(f"PARALLEL-EDGE GUARD: duplicate (endpoints, relation) links: {dupes}")
    if dupes:
        print("  !! graph declares multigraph=false; these will silently collapse on load")
        return 1
    print("  OK - no parallel links")
    print(f"sample bridges: {sorted(bridged)[:15]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
