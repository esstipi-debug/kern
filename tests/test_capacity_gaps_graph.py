"""The capacity-gaps source (#28) is queryable, anchored, and never citable.

Three properties, in order of how much damage their absence would do:

1. NEVER A CLIENT CITATION. ``documentation/KERN_BRECHAS_DE_CAPACIDAD.md`` is one of
   Kern's own documents. If one of its nodes reached a deliverable it would sit next
   to Chopra & Meindl as if it were published theory. ``_INTERNAL_SOURCES`` plus
   ``ground_citations_detailed`` is the only thing preventing that, so it is tested
   adversarially: not "these nodes rank low" but "querying with the gap's own words
   still cannot produce it".
2. STILL DISCOVERABLE. Excluding a source from citations must not make it invisible
   to ``search`` — that is the whole point of committing it to the graph.
3. STRUCTURALLY HONEST. Every theory anchor resolves to a node that already existed,
   every gap has a code site, and the one gap with no genuine anchor has no theory
   edge rather than a fabricated one.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from scm_agent.knowledge import _INTERNAL_SOURCES, BOOKS_GRAPH, KnowledgeBase
from scm_agent.tools import build_default_registry

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from merge_capacity_gaps import (  # noqa: E402
    COMMUNITY,
    GAPS,
    HUB_ID,
    SOURCE_FILE,
    code_id,
    gap_id,
)

REPO = Path(__file__).resolve().parent.parent

# The relation vocabulary this graph actually uses for curated edges.
_ALLOWED_RELATIONS = frozenset(
    {"implements", "conceptually_related_to", "references", "cites", "rationale_for", "uses", "part_of"}
)


@pytest.fixture(scope="module")
def graph() -> dict:
    return json.loads(BOOKS_GRAPH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def kb() -> KnowledgeBase:
    return KnowledgeBase()


@pytest.fixture(scope="module")
def gap_node_ids() -> set[str]:
    ids = {HUB_ID}
    for gap in GAPS:
        ids.add(gap_id(gap))
        ids.add(code_id(gap))
    return ids


# ── 1. Never a client citation ──────────────────────────────────────


def test_source_is_registered_internal() -> None:
    assert SOURCE_FILE in _INTERNAL_SOURCES


def test_no_gap_node_is_citable_for_any_registered_tool(kb: KnowledgeBase, gap_node_ids: set[str]) -> None:
    """The real client path: every tool in the registry, at a generous pool."""
    leaked: list[str] = []
    tools = build_default_registry().list()
    assert len(tools) >= 50, f"registry shrank to {len(tools)} — widen this sweep"
    for tool in tools:
        for cite in kb.ground_citations_detailed(tool.intent_keywords, tool.title, limit=16):
            if cite.node_id in gap_node_ids or SOURCE_FILE in cite.text:
                leaked.append(f"{tool.key}: {cite.node_id} — {cite.text}")
    assert not leaked, "capacity-gap nodes reached the client citation path:\n" + "\n".join(leaked)


def test_no_gap_node_is_citable_when_queried_with_its_own_words(
    kb: KnowledgeBase, graph: dict, gap_node_ids: set[str]
) -> None:
    """Adversarial: use each gap node's own label and rationale as the query.

    This is the test that distinguishes "filtered" from "happened to rank low". These
    queries are the best possible match for the gap nodes; if the filter were removed
    they would be the top hit for every one of them.
    """
    by_id = {n["id"]: n for n in graph["nodes"]}
    leaked: list[str] = []
    for node_id in sorted(gap_node_ids):
        node = by_id[node_id]
        query = f"{node['label']} {node.get('rationale', '')}"
        for cite in kb.ground_citations_detailed(query.split()[:12], query, limit=16):
            if cite.node_id in gap_node_ids or SOURCE_FILE in cite.text:
                leaked.append(f"query for {node_id} returned {cite.node_id}")
    assert not leaked, "capacity-gap nodes are citable when queried directly:\n" + "\n".join(leaked)


def test_filter_would_actually_catch_these_nodes(kb: KnowledgeBase, graph: dict, gap_node_ids: set[str]) -> None:
    """Guard against a vacuous pass: prove the nodes DO rank, so the filter is load-bearing.

    If a rename or a source_file change made ``_is_internal_source`` stop matching,
    the two tests above would still pass only if the nodes had also become unfindable.
    This asserts the opposite — they are the top hit for their own text — so the tests
    above can only pass because the filter ran.
    """
    by_id = {n["id"]: n for n in graph["nodes"]}
    for gap in GAPS:
        node = by_id[gap_id(gap)]
        hits = kb.search(node["label"], graph="books", limit=3)
        assert node["id"] in {h.qualified_id for h in hits}, (
            f"{node['id']} is not even findable by its own label — the citation tests "
            "above would pass vacuously"
        )


# ── 2. Still discoverable ───────────────────────────────────────────


def test_gaps_are_discoverable_by_search(kb: KnowledgeBase) -> None:
    """The question the next session actually asks."""
    hits = kb.search("que le falta a kern para encadenar herramientas", graph="books", limit=10)
    ids = {h.qualified_id for h in hits}
    assert gap_id(GAPS[0]) in ids, f"gap 1 not surfaced; got {sorted(ids)}"


def test_every_gap_is_reachable_from_the_hub(kb: KnowledgeBase) -> None:
    neighbors = {nid for _, nid in kb.neighbors(HUB_ID)} if hasattr(kb, "neighbors") else set()
    if not neighbors:  # fall back to the raw graph if there is no neighbors() helper
        graph = json.loads(BOOKS_GRAPH.read_text(encoding="utf-8"))
        neighbors = {
            e["target"] for e in graph["links"] if e["source"] == HUB_ID
        } | {e["source"] for e in graph["links"] if e["target"] == HUB_ID}
    missing = [gap_id(g) for g in GAPS if gap_id(g) not in neighbors]
    assert not missing, f"gaps not linked to the hub: {missing}"


# ── 3. Structurally honest ──────────────────────────────────────────


def test_all_gap_nodes_present_with_a_community(graph: dict, gap_node_ids: set[str]) -> None:
    by_id = {n["id"]: n for n in graph["nodes"]}
    for node_id in sorted(gap_node_ids):
        assert node_id in by_id, f"{node_id} missing from the graph"
        node = by_id[node_id]
        assert node["source_file"] == SOURCE_FILE
        # A null community silently drops the node out of every community-scoped
        # surface (GRAPH_REPORT sections, CommunitySummaryIndex) with no error.
        assert node.get("community") == COMMUNITY, f"{node_id} has community {node.get('community')}"


def test_every_theory_anchor_predates_this_source(graph: dict) -> None:
    """No invented theory. Each anchor must be a node from a published book."""
    by_id = {n["id"]: n for n in graph["nodes"]}
    for gap in GAPS:
        if gap["theory"] is None:
            continue
        anchor = by_id.get(gap["theory"])
        assert anchor is not None, f"gap {gap['n']} anchors to a non-existent node {gap['theory']}"
        assert anchor["source_file"] != SOURCE_FILE, (
            f"gap {gap['n']} anchors to a node this very source introduced — that is a "
            "fabricated anchor, not a bridge to theory"
        )


def test_unanchored_gap_has_no_theory_edge(graph: dict) -> None:
    """Gap 2 (reranker) has no concept in the corpus. It must stay unanchored."""
    unanchored = [g for g in GAPS if g["theory"] is None]
    assert unanchored, "expected at least one honestly-unanchored gap"
    for gap in unanchored:
        gid = gap_id(gap)
        theory_edges = [
            e
            for e in graph["links"]
            if e["source"] == gid
            and e["relation"] in {"cites", "conceptually_related_to"}
        ]
        assert not theory_edges, f"gap {gap['n']} is documented as unanchored but has {theory_edges}"


def test_every_gap_has_a_code_site(graph: dict) -> None:
    by_id = {n["id"]: n for n in graph["nodes"]}
    edges = {(e["source"], e["target"], e["relation"]) for e in graph["links"]}
    for gap in GAPS:
        cid = code_id(gap)
        assert by_id[cid]["file_type"] == "code"
        assert "::" in by_id[cid]["label"], "a code site must be archivo::simbolo"
        assert (gap_id(gap), cid, "references") in edges


def test_only_known_relations_are_used(graph: dict) -> None:
    used = {e["relation"] for e in graph["links"] if e.get("source_file") == SOURCE_FILE}
    assert used <= _ALLOWED_RELATIONS, f"unknown relations introduced: {sorted(used - _ALLOWED_RELATIONS)}"


def test_code_sites_point_at_files_that_exist(graph: dict) -> None:
    """A code anchor naming a file that is gone is worse than no anchor."""
    for gap in GAPS:
        rel_path = gap["code_label"].split("::", 1)[0]
        assert (REPO / rel_path).is_file(), f"gap {gap['n']} points at missing file {rel_path}"


# ── 4. Survives a rebuild ───────────────────────────────────────────


def test_survives_the_book_rebuild_merge(graph: dict, gap_node_ids: set[str]) -> None:
    """Reproduce scripts/merge_l3_rebuild.py's keep rule against the real graph.

    That script rebuilds the books graph by dropping every node whose ``source_file``
    stem is in REPLACE_STEMS and re-adding it from a fresh per-book extraction. A
    source not in that list is carried through untouched — which is why this document
    is a durable support and an injected query-time node was not.
    """
    from merge_l3_rebuild import REPLACE_STEMS, _stem

    replace = {s.lower() for s in REPLACE_STEMS}
    assert _stem(SOURCE_FILE) not in replace, (
        f"{SOURCE_FILE} is listed for replacement — a rebuild would delete it"
    )
    kept = {n["id"] for n in graph["nodes"] if _stem(n.get("source_file")) not in replace}
    missing = sorted(gap_node_ids - kept)
    assert not missing, f"a rebuild would drop these gap nodes: {missing}"


def test_theory_anchors_are_rebuild_durable(graph: dict) -> None:
    """An anchor into a book the rebuild re-extracts is a bridge that cuts itself.

    Surviving the rebuild as a NODE is not enough: the edge to theory only survives if
    the anchor node survives too, and merge_l3_rebuild deletes every node from a book
    in REPLACE_STEMS before re-adding whatever the fresh extraction happens to produce.
    Measured, not assumed — gap 5 was first anchored to
    ``knowledge::business_process_reengineering`` (Christopher, a replaced book) and a
    real rebuild dropped both that node and the edge, leaving the gap unanchored while
    every other check still passed. This is the guard that would have caught it.
    """
    from merge_l3_rebuild import REPLACE_STEMS, _stem

    replace = {s.lower() for s in REPLACE_STEMS}
    by_id = {n["id"]: n for n in graph["nodes"]}
    fragile = [
        f"gap {g['n']} -> {g['theory']} (from {by_id[g['theory']]['source_file']})"
        for g in GAPS
        if g["theory"] is not None and _stem(by_id[g["theory"]]["source_file"]) in replace
    ]
    assert not fragile, (
        "theory anchors point at books that merge_l3_rebuild re-extracts; a rebuild "
        "would silently unanchor these gaps:\n" + "\n".join(fragile)
    )
