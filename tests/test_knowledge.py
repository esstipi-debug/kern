"""Tests for the L3 knowledge layer (scm_agent/knowledge.py).

Runs against the committed books graph (knowledge/scm-books/graph.json). The
code graph (graphify-out/) is gitignored, so tests that need it skip when absent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scm_agent.knowledge import (
    Bridge,
    Concept,
    ConceptDetail,
    GroundedCitation,
    KnowledgeBase,
    MethodAdvice,
)

REPO = Path(__file__).resolve().parent.parent
BOOKS = REPO / "knowledge" / "scm-books" / "graph.json"
CODE = REPO / "graphify-out" / "graph.json"


def _write_graph(path: Path, nodes: list[dict]) -> Path:
    """Write a minimal node-link graph for deterministic ranking tests."""
    path.write_text(json.dumps({"nodes": nodes, "links": []}), encoding="utf-8")
    return path


@pytest.fixture
def kb() -> KnowledgeBase:
    return KnowledgeBase()


def test_books_graph_loads(kb: KnowledgeBase) -> None:
    status = kb.available()
    assert status["books"] > 0  # books graph is committed


def test_search_finds_croston_in_books(kb: KnowledgeBase) -> None:
    hits = kb.search("croston", graph="books")
    assert any("croston" in h.id.lower() for h in hits)
    assert all(isinstance(h, Concept) for h in hits)


def test_search_respects_limit(kb: KnowledgeBase) -> None:
    hits = kb.search("pricing", graph="books", limit=3)
    assert len(hits) <= 3


def test_search_empty_query_returns_nothing(kb: KnowledgeBase) -> None:
    assert kb.search("", graph="books") == []


def test_search_unknown_term_returns_empty(kb: KnowledgeBase) -> None:
    assert kb.search("zzqqxnonsense", graph="books") == []


def test_explain_returns_detail_with_neighbors(kb: KnowledgeBase) -> None:
    # find a real concept id first
    hits = kb.search("safety stock", graph="books")
    assert hits, "expected a safety-stock concept in the books graph"
    detail = kb.explain(hits[0].id)
    assert isinstance(detail, ConceptDetail)
    assert detail.concept.id == hits[0].id
    assert isinstance(detail.neighbors, tuple)


def test_explain_unknown_id_returns_none(kb: KnowledgeBase) -> None:
    assert kb.explain("does_not_exist_zzz") is None


def test_explain_fuzzy_falls_back_to_search(kb: KnowledgeBase) -> None:
    # passing a label-ish string should resolve via search fallback
    detail = kb.explain("crostons method")
    assert detail is None or isinstance(detail, ConceptDetail)


def test_explain_resolves_bare_id_across_source_namespacing(kb: KnowledgeBase) -> None:
    """A bare concept id must resolve even though the books graph namespaces node
    ids by source (e.g. ``knowledge::chain_model``). Regression for the 25th-source
    merge, which re-prefixed every original node id and silently broke the exact-id
    lookup in both advise() and explain() - the fuzzy fallback masked most rules but
    not all. The bare slug is the documented interface (CLAUDE.md's --explain, the
    _METHOD_RULES), so it must survive any re-prefixing merge."""
    detail = kb.explain("chain_model")  # documented bare id, real committed node
    assert detail is not None, "bare 'chain_model' must resolve regardless of source prefix"
    assert detail.concept.id == "chain_model", "Concept.id is normalized back to the bare slug"
    # the prefixed form resolves to the very same node
    prefixed = kb.explain("knowledge::chain_model")
    assert prefixed is not None
    assert prefixed.concept.id == detail.concept.id


def test_bridge_returns_theory_side(kb: KnowledgeBase) -> None:
    b = kb.bridge("newsvendor")
    assert isinstance(b, Bridge)
    assert b.term == "newsvendor"
    # theory side comes from the committed books graph
    assert len(b.theory) >= 1
    assert all(c.graph == "books" for c in b.theory)


@pytest.mark.skipif(not CODE.exists(), reason="code graph is gitignored")
def test_bridge_links_theory_to_implementation(kb: KnowledgeBase) -> None:
    b = kb.bridge("newsvendor")
    assert len(b.implementation) >= 1
    assert all(c.graph == "code" for c in b.implementation)
    # the implementation side should point at real source files
    assert any(c.source for c in b.implementation)


@pytest.mark.skipif(not CODE.exists(), reason="code graph is gitignored")
def test_implements_bridges_a_concept_to_source_code(kb: KnowledgeBase) -> None:
    hits = kb.search("economic order quantity", graph="books")
    assert hits, "expected an EOQ concept in the books graph"
    impl = kb.implements(hits[0])
    assert impl is not None
    assert impl.graph == "code"
    assert impl.source and impl.source.endswith(".py")


def test_implements_returns_none_when_code_graph_absent(tmp_path: Path) -> None:
    kb = KnowledgeBase(books_path=BOOKS, code_path=tmp_path / "none.json")
    concept = Concept(id="economic_order_quantity", label="Economic Order Quantity",
                      source=None, location=None, graph="books")
    assert kb.implements(concept) is None


def test_implements_ignores_a_lone_common_token(kb: KnowledgeBase) -> None:
    # A concept sharing only one ubiquitous domain word must not forge a code link.
    concept = Concept(id="thing", label="price", source=None, location=None, graph="books")
    assert kb.implements(concept) is None


def test_missing_graph_paths_degrade_gracefully(tmp_path: Path) -> None:
    kb = KnowledgeBase(books_path=tmp_path / "nope.json", code_path=tmp_path / "nope2.json")
    assert kb.available() == {"books": 0, "code": 0}
    assert kb.search("anything") == []
    assert kb.explain("anything") is None
    b = kb.bridge("anything")
    assert b.theory == () and b.implementation == ()


def test_search_both_graphs_tags_origin(kb: KnowledgeBase) -> None:
    hits = kb.search("inventory", graph="both", limit=10)
    graphs = {h.graph for h in hits}
    assert graphs.issubset({"books", "code"})


# -- ranking improvements (deterministic, synthetic graphs) -----------------


def test_search_weights_title_over_rationale(tmp_path: Path) -> None:
    """A title match outranks a node that only mentions the term in its rationale."""
    g = _write_graph(tmp_path / "b.json", [
        {"id": "a", "label": "Reorder Point", "norm_label": "reorder point",
         "rationale": "trigger level"},
        {"id": "b", "label": "Generic Concept", "norm_label": "generic concept",
         "rationale": "the reorder point is computed here"},
    ])
    kb = KnowledgeBase(books_path=g, code_path=tmp_path / "none.json")
    hits = kb.search("reorder point", graph="books")
    assert hits[0].id == "a"


def test_search_matches_via_rationale(tmp_path: Path) -> None:
    """A term present only in the rationale still surfaces the node (recall)."""
    g = _write_graph(tmp_path / "b.json", [
        {"id": "a", "label": "Buffer Sizing", "norm_label": "buffer sizing",
         "rationale": "handles intermittent croston demand"},
    ])
    kb = KnowledgeBase(books_path=g, code_path=tmp_path / "none.json")
    hits = kb.search("croston", graph="books")
    assert any(h.id == "a" for h in hits)


def test_search_idf_favors_rarer_term(tmp_path: Path) -> None:
    """A rare, specific term outweighs a term common across the corpus."""
    nodes = [
        {"id": f"d{i}", "label": f"Demand Topic {i}",
         "norm_label": f"demand topic {i}", "rationale": ""}
        for i in range(5)
    ]
    nodes.append({"id": "nv", "label": "Newsvendor", "norm_label": "newsvendor",
                  "rationale": ""})
    g = _write_graph(tmp_path / "b.json", nodes)
    kb = KnowledgeBase(books_path=g, code_path=tmp_path / "none.json")
    hits = kb.search("demand newsvendor", graph="books")
    assert hits[0].id == "nv"


def test_advise_maps_intermittent_brief(kb: KnowledgeBase) -> None:
    tips = kb.advise("we have intermittent lumpy demand on spare parts")
    assert tips
    assert all(isinstance(t, MethodAdvice) for t in tips)
    assert any("croston" in t.concept.id.lower() for t in tips)


def test_ground_citations_uses_brief_and_keywords(kb: KnowledgeBase) -> None:
    cites = kb.ground_citations(
        ("reorder", "safety stock", "inventory"),
        "intermittent spare-parts demand needs a better forecast method",
        limit=5,
    )
    assert 1 <= len(cites) <= 5
    assert all(isinstance(c, str) for c in cites)


def test_advise_gates_a_trigger_outside_the_tool_domain(kb: KnowledgeBase) -> None:
    """A bare 'chain' token (from 'supply chain') must not fire the leadership CHAIN
    rule for a tool whose domain has nothing to do with leadership."""
    inventory_domain = frozenset({"reorder", "safety", "stock", "inventory", "eoq"})
    tips = kb.advise("optimize reorder points across our supply chain", domain_terms=inventory_domain)
    assert not any(t.concept.id == "chain_model" for t in tips)


def test_advise_still_fires_when_trigger_is_in_domain(kb: KnowledgeBase) -> None:
    leadership_domain = frozenset({"leadership", "chain", "director", "ceo"})
    tips = kb.advise("evaluate our supply chain leadership", domain_terms=leadership_domain)
    assert any(t.concept.id == "chain_model" for t in tips)


def test_ground_citations_does_not_surface_leadership_for_an_eoq_brief(kb: KnowledgeBase) -> None:
    """Repro of the spurious-#1-citation bug: an EOQ/inventory brief that happens to
    say 'supply chain' must not surface leadership or off-topic sustainability
    citations ahead of the actually relevant EOQ / safety-stock concepts."""
    inventory_keywords = (
        "reorder", "safety stock", "stock level", "inventory", "replenish",
        "eoq", "service level", "reorder point", "order quantity",
    )
    brief = "Optimize reorder points across our supply chain inventory using EOQ and safety stock."
    cites = kb.ground_citations(inventory_keywords, brief, limit=5)
    assert not any("leadership" in c.lower() for c in cites)
    assert any("economic order quantity" in c.lower() or "eoq" in c.lower() for c in cites[:2])


# ---- E5: ground_citations_detailed / node_exists / concept_distance ----------
# (scm_agent.citation_gate is the consumer that needs these - see there.)

def test_ground_citations_detailed_matches_ground_citations_text(kb: KnowledgeBase) -> None:
    keywords = ("reorder", "safety stock", "inventory")
    brief = "intermittent spare-parts demand needs a better forecast method"
    plain = kb.ground_citations(keywords, brief, limit=5)
    detailed = kb.ground_citations_detailed(keywords, brief, limit=5)
    assert plain == [c.text for c in detailed]
    assert all(isinstance(c, GroundedCitation) for c in detailed)
    assert all(c.node_id and c.graph == "books" for c in detailed)


def test_ground_citations_detailed_node_ids_resolve(kb: KnowledgeBase) -> None:
    detailed = kb.ground_citations_detailed(
        ("abc", "xyz", "classification", "pareto"), "classify SKUs by value", limit=5,
    )
    assert detailed
    assert all(kb.node_exists(c.node_id) for c in detailed)


def test_node_exists_true_for_a_known_bare_id(kb: KnowledgeBase) -> None:
    assert kb.node_exists("safety_stock") is True


def test_node_exists_false_for_an_unknown_id(kb: KnowledgeBase) -> None:
    assert kb.node_exists("this_concept_does_not_exist_xyz") is False


def test_concept_distance_zero_for_identical_ids(kb: KnowledgeBase) -> None:
    assert kb.concept_distance("safety_stock", "safety_stock") == 0


def test_concept_distance_none_when_either_id_is_unknown(kb: KnowledgeBase) -> None:
    assert kb.concept_distance("safety_stock", "not_a_real_concept") is None
    assert kb.concept_distance("not_a_real_concept", "safety_stock") is None


def test_concept_distance_is_symmetric(kb: KnowledgeBase) -> None:
    # Undirected by design (see the docstring): grounding relevance shouldn't
    # depend on which end of an edge the graph happened to record.
    a, b = "abc_classification", "pareto_law"
    assert kb.concept_distance(a, b) == kb.concept_distance(b, a)


def test_concept_distance_none_beyond_max_hops(kb: KnowledgeBase) -> None:
    # Two genuinely unrelated domain concepts should not resolve within 2 hops.
    assert kb.concept_distance("safety_stock", "chain_model", max_hops=2) is None


def test_concept_distance_respects_a_tighter_max_hops(kb: KnowledgeBase) -> None:
    # A pair known to be exactly 1 hop apart must fail a max_hops=0 budget
    # (0 only matches identical ids) but pass max_hops=1.
    a, b = "abc_classification", "pareto_law"
    one_hop = kb.concept_distance(a, b, max_hops=2)
    assert one_hop == 1  # documents the fixture's actual graph distance
    assert kb.concept_distance(a, b, max_hops=0) is None
    assert kb.concept_distance(a, b, max_hops=1) == 1


def test_concept_distance_resolves_bare_ids_across_namespacing(kb: KnowledgeBase) -> None:
    # Mirrors test_explain_resolves_bare_id_across_source_namespacing above -
    # concept_distance must tolerate the same knowledge::-prefixed storage.
    assert kb.concept_distance("chain_model", "chain_model") == 0


def test_ground_citations_detailed_node_id_is_qualified_not_bare(kb: KnowledgeBase) -> None:
    """node_id must be the raw graph id (e.g. "knowledge::x"), not the bare
    slug - see GroundedCitation's docstring for why (bare-id collisions
    across a future graph-merge could otherwise re-resolve to the wrong
    node). The committed graph namespaces every node, so every real hit
    should come back with a "::" - qualified prefix."""
    detailed = kb.ground_citations_detailed(
        ("reorder", "safety stock", "inventory"),
        "intermittent spare-parts demand needs a better forecast method",
        limit=5,
    )
    assert detailed
    assert all("::" in c.node_id for c in detailed)


def test_ground_citations_detailed_disambiguates_a_bare_id_collision(tmp_path: Path) -> None:
    """Regression for the HIGH-severity finding in the 2026-07 adversarial
    review: two nodes can share a bare id across source namespaces (this has
    happened before - see PR #121's graph-merge collision). Ranking must not
    silently collapse them into one scoring slot, and the emitted
    GroundedCitation.node_id must identify the EXACT node that was ranked -
    not a bare slug that citation_gate's re-resolution could later map to a
    different node than the one actually scored."""
    g = _write_graph(tmp_path / "b.json", [
        {"id": "anchor", "label": "Anchor Widget Topic", "norm_label": "anchor widget topic"},
        {"id": "vendorx::widget", "label": "On-Topic Widget", "norm_label": "widget topic",
         "rationale": "genuinely connected to the anchor"},
        {"id": "knowledge::widget", "label": "Isolated Widget", "norm_label": "widget",
         "rationale": "an unrelated decoy sharing the same bare id, no edge to anything"},
    ])
    data = json.loads(g.read_text(encoding="utf-8"))
    data["links"] = [{"source": "anchor", "target": "vendorx::widget", "relation": "related"}]
    g.write_text(json.dumps(data), encoding="utf-8")

    kb = KnowledgeBase(books_path=g, code_path=tmp_path / "none.json")
    detailed = kb.ground_citations_detailed(("widget", "topic"), "widget topic", limit=5)

    on_topic = next(c for c in detailed if c.text.startswith("On-Topic Widget"))
    assert on_topic.node_id == "vendorx::widget"
    # Resolving citation_gate's exact check: the on-topic node is 1 hop from
    # the anchor; if node_id had been the bare "widget", _resolve_node's
    # "knowledge::<id> always wins" tie-break would have silently graded the
    # ISOLATED decoy's connectivity instead (None, wrongly omitting a real hit).
    assert kb.concept_distance(on_topic.node_id, "anchor", max_hops=2) == 1

    isolated = next((c for c in detailed if c.text.startswith("Isolated Widget")), None)
    if isolated is not None:
        assert isolated.node_id == "knowledge::widget"
        assert kb.concept_distance(isolated.node_id, "anchor", max_hops=2) is None


def test_concept_distance_on_a_synthetic_disconnected_graph(tmp_path: Path) -> None:
    path = tmp_path / "graph.json"
    path.write_text(json.dumps({
        "nodes": [{"id": "a"}, {"id": "b"}, {"id": "c"}, {"id": "isolated"}],
        "links": [
            {"source": "a", "target": "b", "relation": "related"},
            {"source": "b", "target": "c", "relation": "related"},
        ],
    }), encoding="utf-8")
    kb = KnowledgeBase(books_path=path, code_path=CODE)
    assert kb.concept_distance("a", "b") == 1
    assert kb.concept_distance("a", "c") == 2
    assert kb.concept_distance("a", "c", max_hops=1) is None
    assert kb.concept_distance("a", "isolated") is None
    assert kb.node_exists("isolated") is True
    assert kb.node_exists("nope") is False
