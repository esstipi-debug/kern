"""Tests for the L3 knowledge layer (scm_agent/knowledge.py).

Runs against the committed books graph (knowledge/scm-books/graph.json). The
code graph (graphify-out/) is gitignored, so tests that need it skip when absent.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from scm_agent.knowledge import (
    _INTERNAL_SOURCES,
    Bridge,
    Concept,
    ConceptDetail,
    GroundedCitation,
    KnowledgeBase,
    MethodAdvice,
    _is_internal_source,
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
    # Every node carries a source_file: a citation must name where it comes from
    # (see _is_citable), and 3043 of the 3044 committed books nodes do.
    g = _write_graph(tmp_path / "b.json", [
        {"id": "anchor", "label": "Anchor Widget Topic", "norm_label": "anchor widget topic",
         "source_file": "anchor-book.pdf"},
        {"id": "vendorx::widget", "label": "On-Topic Widget", "norm_label": "widget topic",
         "rationale": "genuinely connected to the anchor", "source_file": "vendorx-book.pdf"},
        {"id": "knowledge::widget", "label": "Isolated Widget", "norm_label": "widget",
         "rationale": "an unrelated decoy sharing the same bare id, no edge to anything",
         "source_file": "decoy-book.pdf"},
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


def test_is_internal_source_matches_on_basename() -> None:
    """The denylist matches the file's basename, whatever path form the graph carries."""
    def c(src: str | None) -> Concept:
        return Concept(id="x", label="X", source=src, location=None, graph="books")

    assert _is_internal_source(c("KERN_ATLAS_SOMBREROS_SCM.md"))
    assert _is_internal_source(c("documentation/KERN_ATLAS_SOMBREROS_SCM.md"))
    assert _is_internal_source(c(r"documentation\KERN_ATLAS_SOMBREROS_SCM.md"))
    assert not _is_internal_source(c("chopra-meindl-supply-chain-management.pdf"))
    assert not _is_internal_source(c(None))


def test_internal_sources_are_never_cited_by_any_tool(kb: KnowledgeBase) -> None:
    """No registered tool may ground a client-facing citation in one of Kern's own
    documents. Kern's grounding claim is that it cites the field's published sources;
    a self-citation listed beside Chopra & Meindl silently voids it.

    Regression guard for the real leak found on 2026-07-24: 28 tools were pulling
    ``KERN_ATLAS_SOMBREROS_SCM.md`` into their candidate pool and one shipped it in
    a deliverable (``supplier_scorecard - KERN_ATLAS_SOMBREROS_SCM.md section 1``).
    """
    from scm_agent.tools import build_default_registry

    offenders: list[str] = []
    for tool in build_default_registry()._tools.values():
        for cite in kb.ground_citations_detailed(tool.intent_keywords, tool.title, limit=12):
            if any(internal in cite.text for internal in _INTERNAL_SOURCES):
                offenders.append(f"{tool.key}: {cite.text}")
    assert not offenders, "internal sources leaked into citations:\n" + "\n".join(offenders)


def test_internal_sources_stay_searchable(kb: KnowledgeBase) -> None:
    """The filter is scoped to *citations* only - the Atlas remains queryable as
    internal knowledge (it is what the hats/roles work reads)."""
    hits = kb.search("supply chain role certification", graph="books", limit=40)
    assert any(_is_internal_source(h) for h in hits), (
        "the Atlas should still be reachable via search; only citations are filtered"
    )


# -- a citation must name where it comes from ---------------------------------


def test_nodes_without_a_source_file_are_never_cited(tmp_path: Path) -> None:
    """A node that cannot say where it comes from is not a citation.

    ``ground_citations_detailed`` renders each hit as ``f"{label} - {source}{loc}"``,
    so a node whose ``source_file`` is missing ships to the client as a citation
    whose provenance reads literally ``None``. The invariant under test is the
    *field*, not any one offending id: whatever the node is, if it cannot name a
    source it is dropped from the ranking.
    """
    g = _write_graph(tmp_path / "b.json", [
        {"id": "note::reminder", "label": "Reminder: evaluate embedding models",
         "norm_label": "reminder evaluate embedding models", "rationale": ""},
        {"id": "book::embeddings", "label": "Evaluating Embedding Models",
         "norm_label": "evaluating embedding models", "rationale": "",
         "source_file": "retrieval-handbook.pdf", "source_location": "p. 10"},
    ])
    kb = KnowledgeBase(books_path=g, code_path=tmp_path / "none.json")

    cites = kb.ground_citations_detailed(("reminder", "evaluate", "embedding"), "", limit=5)

    assert cites, "the properly sourced node must still be cited"
    rendered = "; ".join(c.text for c in cites)
    assert all(c.node_id != "note::reminder" for c in cites), (
        "a source-less node reached a citation: " + rendered
    )
    assert not any("None" in c.text for c in cites)


def test_source_less_nodes_in_the_books_graph_are_never_cited(kb: KnowledgeBase) -> None:
    """Same invariant, against the committed product graph: query every source-less
    node by its own label - the friendliest possible query for it - and assert none
    comes back as a citation.

    Regression guard for the real defect found on 2026-09-11: the books graph carried
    one node without ``source_file`` (``reminder::evaluar_embeddings_con_modelos_...``,
    an internal Kern note, not a source), and it shipped the citation
    ``"Reminder: Evaluar embeddings con modelos mas actualizados: ... - None"``.
    ``filter_citations`` never caught it because that gate is topological (node id +
    hops), and the path the web/MCP/CLI actually use - ``Orchestrator._ground`` -
    calls ``ground_citations`` with no gate at all.
    """
    nodes = json.loads(BOOKS.read_text(encoding="utf-8"))["nodes"]
    source_less = [n for n in nodes if not (n.get("source_file") or "").strip()]

    offenders: list[str] = []
    for node in source_less:
        keywords = tuple(str(node.get("label", "")).split())
        for cite in kb.ground_citations_detailed(keywords, "", limit=8):
            if cite.node_id == node["id"]:
                offenders.append(f"{node['id']}: {cite.text}")
    assert not offenders, "source-less nodes leaked into citations: " + "; ".join(offenders)


def test_reported_embeddings_query_grounds_nothing_without_a_source(kb: KnowledgeBase) -> None:
    """The exact reproduction from the defect report: this query used to return a
    single citation ending in ``- None``."""
    cites = kb.ground_citations(["embeddings", "modelos", "evaluar"], "", limit=5)
    assert not [c for c in cites if c.rstrip().endswith("None")], cites


# -- implements() must cite implementations, not tests / demos / one-off scripts --


def _looks_like_a_test_file(source: str) -> bool:
    """Independent read of the path, so the assertion does not borrow the fix."""
    parts = source.replace("\\", "/").split("/")
    name = parts[-1]
    return (
        any(p in {"tests", "test", "testing"} for p in parts[:-1])
        or name.startswith("test_")
        or name.endswith("_test.py")
        or name == "conftest.py"
    )


def _looks_like_a_non_implementation(source: str) -> bool:
    """Test, demo, example or one-off script — written independently of the fix."""
    parts = source.replace("\\", "/").split("/")
    name = parts[-1]
    dirs = {"examples", "example", "demos", "demo", "scripts", "script",
            "samples", "sample"}
    return (
        _looks_like_a_test_file(source)
        or any(p in dirs for p in parts[:-1])
        or name.startswith(("demo_", "example_", "sample_"))
        or name.endswith(("_demo.py", "_example.py", "_sample.py"))
    )


def _code_node(nid: str, label: str, source: str) -> dict:
    """A code-graph node shaped like graphify's output."""
    return {"id": nid, "label": label, "norm_label": label.lower(),
            "rationale": "", "source_file": source, "location": "L1"}


def test_implements_skips_a_test_file_and_falls_back_to_theory_only(tmp_path: Path) -> None:
    """A test file verifies a concept, it does not implement it.

    With the test module as the only candidate, implements() must return None so
    the caller cites theory alone rather than pointing a client at tests/.
    """
    code = _write_graph(tmp_path / "c.json", [
        _code_node("t", "test economic order quantity",
                   "tests/test_economic_order_quantity.py"),
    ])
    kb = KnowledgeBase(books_path=BOOKS, code_path=code)
    concept = Concept(id="economic_order_quantity", label="Economic Order Quantity",
                      source=None, location=None, graph="books")
    assert kb.implements(concept) is None


def test_implements_prefers_source_over_a_better_scoring_test_file(tmp_path: Path) -> None:
    """Excluding tests promotes the runner-up in src/ rather than dropping the link.

    The test node shares four tokens with the concept and the src module only
    three, so on score alone the test file wins outright — the src/ tiebreak
    cannot save it. Only skipping test paths gets src/eoq.py cited.
    """
    code = _write_graph(tmp_path / "c.json", [
        _code_node("t", "test economic order quantity model", "tests/test_eoq_model.py"),
        _code_node("s", "economic order quantity", "src/eoq.py"),
    ])
    kb = KnowledgeBase(books_path=BOOKS, code_path=code)
    concept = Concept(id="economic_order_quantity_model",
                      label="Economic Order Quantity Model",
                      source=None, location=None, graph="books")
    impl = kb.implements(concept)
    assert impl is not None
    assert impl.source == "src/eoq.py"


@pytest.mark.parametrize("source", [
    "tests/test_pricing.py",
    "tests/helpers/fixtures.py",
    "odoo_addon/test/verify_install.py",
    "src/pricing_test.py",
    "src/conftest.py",
])
def test_implements_rejects_every_shape_of_test_path(tmp_path: Path, source: str) -> None:
    """Test-ness is a path property: a tests/ directory OR a test-named module."""
    code = _write_graph(tmp_path / "c.json", [
        _code_node("t", "economic order quantity", source),
    ])
    kb = KnowledgeBase(books_path=BOOKS, code_path=code)
    concept = Concept(id="economic_order_quantity", label="Economic Order Quantity",
                      source=None, location=None, graph="books")
    assert kb.implements(concept) is None


@pytest.mark.skipif(not CODE.exists(), reason="code graph is gitignored")
@pytest.mark.parametrize("label", [
    "Perfect Order (OTIF)",
    "Reorder Point",
    "Salvage Value",
    "Replenishment Lead Time",
    "Competitive Reference Price",
    "Earliest Due Date (EDD)",
])
def test_implements_cites_no_test_file_on_the_real_code_graph(
    kb: KnowledgeBase, label: str
) -> None:
    """Regression pin: these concepts each used to resolve to a tests/ module.

    Measured 2026-09-04 across the 52 anchored tools on pools 16/8/6, every one of
    these surfaced ``-> tests/test_*.py`` in the client-facing citation text.
    """
    concept = Concept(id=label.lower().replace(" ", "_"), label=label,
                      source=None, location=None, graph="books")
    impl = kb.implements(concept)
    if impl is not None:
        assert not _looks_like_a_test_file(impl.source or ""), (
            f"{label} resolved to test file {impl.source}"
        )


def test_implements_skips_a_demo_and_falls_back_to_theory_only(tmp_path: Path) -> None:
    """A demo illustrates a concept for a prospect; it is not where it lives.

    ``webapp/demo_lead_time_scan.py`` is a sales demo, so citing it as the code
    behind "Replenishment Lead Time" oversells what is actually built.
    """
    code = _write_graph(tmp_path / "c.json", [
        _code_node("d", "demo replenishment lead time scan",
                   "webapp/demo_lead_time_scan.py"),
    ])
    kb = KnowledgeBase(books_path=BOOKS, code_path=code)
    concept = Concept(id="replenishment_lead_time", label="Replenishment Lead Time",
                      source=None, location=None, graph="books")
    assert kb.implements(concept) is None


def test_implements_skips_the_ingest_script_that_read_the_book(tmp_path: Path) -> None:
    """The graph-ingest script is the most circular citation of all.

    ``scripts/merge_khan_resilience.py`` is the script that loaded Khan's book
    into the books graph. Offering it as the implementation of a Khan concept
    says "the code behind this idea is the code that read the idea".
    """
    code = _write_graph(tmp_path / "c.json", [
        _code_node("m", "merge khan efficiency resilience trade off",
                   "scripts/merge_khan_resilience.py"),
    ])
    kb = KnowledgeBase(books_path=BOOKS, code_path=code)
    concept = Concept(id="efficiency_vs_resilience_trade_off",
                      label="Efficiency-vs-Resilience Trade-off",
                      source=None, location=None, graph="books")
    assert kb.implements(concept) is None


def test_implements_prefers_source_over_a_better_scoring_demo(tmp_path: Path) -> None:
    """As with tests, excluding a demo promotes the real runner-up.

    The demo node shares four tokens with the concept and the src module three,
    so the demo wins on score and the src/ tiebreak cannot rescue it.
    """
    code = _write_graph(tmp_path / "c.json", [
        _code_node("d", "demo replenishment lead time gap", "webapp/demo_lead_time.py"),
        _code_node("s", "replenishment lead time", "src/lead_time.py"),
    ])
    kb = KnowledgeBase(books_path=BOOKS, code_path=code)
    concept = Concept(id="replenishment_lead_time_gap",
                      label="Replenishment Lead Time Gap",
                      source=None, location=None, graph="books")
    impl = kb.implements(concept)
    assert impl is not None
    assert impl.source == "src/lead_time.py"


@pytest.mark.parametrize("source", [
    "examples/run_disruption_scan.py",
    "scripts/merge_khan_resilience.py",
    "webapp/demo_lead_time_scan.py",
    "src/demos/lead_time.py",
    "src/lead_time_demo.py",
    "docs/example/lead_time.py",
])
def test_implements_rejects_every_shape_of_illustrative_path(
    tmp_path: Path, source: str
) -> None:
    """Illustrative-ness is a path property too: the directory OR the filename."""
    code = _write_graph(tmp_path / "c.json", [
        _code_node("d", "replenishment lead time", source),
    ])
    kb = KnowledgeBase(books_path=BOOKS, code_path=code)
    concept = Concept(id="replenishment_lead_time", label="Replenishment Lead Time",
                      source=None, location=None, graph="books")
    assert kb.implements(concept) is None


def test_implements_keeps_a_real_module_whose_name_merely_contains_demo(
    tmp_path: Path,
) -> None:
    """The filename rule anchors on prefix/suffix, so real modules survive.

    ``src/demand_planning.py`` starts with "dem" and ``src/order_demo_history.py``
    contains "demo" mid-name; neither is a demo module and both must stay citable.
    """
    code = _write_graph(tmp_path / "c.json", [
        _code_node("s", "replenishment lead time", "src/order_demo_history.py"),
    ])
    kb = KnowledgeBase(books_path=BOOKS, code_path=code)
    concept = Concept(id="replenishment_lead_time", label="Replenishment Lead Time",
                      source=None, location=None, graph="books")
    impl = kb.implements(concept)
    assert impl is not None
    assert impl.source == "src/order_demo_history.py"


@pytest.mark.skipif(not CODE.exists(), reason="code graph is gitignored")
@pytest.mark.parametrize("label", [
    "Efficiency-vs-Resilience Trade-off",
    "End-to-End Lead-Time Variability",
    "Lead-Time Gap",
    "Replenishment Lead Time",
    "Tiger Team (Cross-Functional Disruption Response Team)",
])
def test_implements_cites_no_demo_or_script_on_the_real_code_graph(
    kb: KnowledgeBase, label: str
) -> None:
    """Regression pin: these five each resolved to a demo, example or script.

    Measured 2026-09-04 on the 52 anchored tools over pools 16/8/6, after the
    tests/ exclusion had already landed — these were what was left.
    """
    concept = Concept(id=label.lower().replace(" ", "_"), label=label,
                      source=None, location=None, graph="books")
    impl = kb.implements(concept)
    if impl is not None:
        assert not _looks_like_a_non_implementation(impl.source or ""), (
            f"{label} resolved to non-implementation {impl.source}"
        )


# -- implements() scores information, not word count -------------------------


def test_implements_ignores_a_match_made_only_of_stopwords(tmp_path: Path) -> None:
    """"Meeting the Team Where They Are" -> jobs/reconciliation_job.py was real.

    It matched on ``are the they where`` — four tokens, zero of them domain
    words. Counting raw token overlap makes function words worth as much as
    "newsvendor", so a node can clear a 3-token bar carrying no information.
    """
    code = _write_graph(tmp_path / "c.json", [
        _code_node("n", "where they are", "src/reconciliation.py"),
    ])
    kb = KnowledgeBase(books_path=BOOKS, code_path=code)
    concept = Concept(id="meeting_the_team_where_they_are",
                      label="Meeting the Team Where They Are",
                      source=None, location=None, graph="books")
    assert kb.implements(concept) is None


def test_implements_ranks_information_above_token_count(tmp_path: Path) -> None:
    """A three-token match padded with stopwords must lose to two real terms.

    ``src/misc_report.py`` matches {and, for, demand} and ``src/forecast_accuracy.py``
    matches {forecast, accuracy}. On raw count 3 > 2 and the padded one wins;
    on content the padded one carries a single domain word and drops out.
    """
    code = _write_graph(tmp_path / "c.json", [
        _code_node("a", "and for the demand", "src/misc_report.py"),
        _code_node("b", "forecast accuracy", "src/forecast_accuracy.py"),
    ])
    kb = KnowledgeBase(books_path=BOOKS, code_path=code)
    concept = Concept(id="demand_and_forecast_accuracy_for_planning",
                      label="Demand and Forecast Accuracy for Planning",
                      source=None, location=None, graph="books")
    impl = kb.implements(concept)
    assert impl is not None
    assert impl.source == "src/forecast_accuracy.py"


def test_implements_does_not_treat_a_stopword_in_a_filename_as_named_after(
    tmp_path: Path,
) -> None:
    """The 2-token discount is for files named after the concept, not for "how".

    ``src/how_it_works.py`` shares only the word "how" with the concept. That
    is not "the file is named after this idea", so it must not buy the lowered
    bar that ``src/safety_stock.py`` earns for "Safety Stock".
    """
    code = _write_graph(tmp_path / "c.json", [
        _code_node("n", "buffers", "src/how_it_works.py"),
    ])
    kb = KnowledgeBase(books_path=BOOKS, code_path=code)
    concept = Concept(id="how_buffers_absorb_variability",
                      label="How Buffers Absorb Variability",
                      source=None, location=None, graph="books")
    assert kb.implements(concept) is None


def test_implements_ignores_spanish_stopwords_too(tmp_path: Path) -> None:
    """The graph is bilingual: ~16 concept labels carry Spanish function words."""
    code = _write_graph(tmp_path / "c.json", [
        _code_node("n", "", "src/reglas_del_sistema.py"),
    ])
    kb = KnowledgeBase(books_path=BOOKS, code_path=code)
    concept = Concept(id="reglas_del_negocio", label="Reglas del Negocio",
                      source=None, location=None, graph="books")
    assert kb.implements(concept) is None


@pytest.mark.skipif(not CODE.exists(), reason="code graph is gitignored")
@pytest.mark.parametrize("label,forbidden", [
    ("Meeting the Team Where They Are", "jobs/reconciliation_job.py"),
    ("Rethinking the US Strategic National Stockpile for Future Pandemics"
     " with Inventory, Capacity, and Capability", "src/deliverable.py"),
    ("Deloitte (2020) - COVID-19: Managing Supply Chain Risk and Disruption",
     "src/queuing.py"),
])
def test_implements_drops_the_stopword_matches_on_the_real_graph(
    kb: KnowledgeBase, label: str, forbidden: str
) -> None:
    """Regression pin: each of these was decided by function words.

    Measured 2026-09-04 over the whole books graph — 177 of 756 backlinks had
    fewer than two domain words behind them; these three are the starkest.
    """
    concept = Concept(id=label.lower().replace(" ", "_"), label=label,
                      source=None, location=None, graph="books")
    impl = kb.implements(concept)
    assert impl is None or impl.source != forbidden


@pytest.mark.skipif(not CODE.exists(), reason="code graph is gitignored")
@pytest.mark.parametrize("label,expected", [
    ("Safety Stock", "src/safety_stock.py"),
    ("Fill Rate", "src/fill_rate.py"),
    ("Newsvendor Model", "src/newsvendor.py"),
    ("Digital Twin", "src/digital_twin.py"),
])
def test_implements_keeps_the_bridges_the_filename_discount_earns(
    kb: KnowledgeBase, label: str, expected: str
) -> None:
    """Guard, green on both sides: the 2-token discount is the feature, not the bug.

    27 of the 68 backlinks a client sees clear the bar only because the module
    is named after the concept, and they are the best citations in the set.
    Scoring on content must not cost us a single one of them.
    """
    concept = Concept(id=label.lower().replace(" ", "_"), label=label,
                      source=None, location=None, graph="books")
    impl = kb.implements(concept)
    assert impl is not None
    assert impl.source == expected


# -- bridge().implementation is "where it lives": implementations, ranked by name --


def _stub(nid: str, label: str) -> dict:
    """graphify's per-file stub for an imported name: code, but no source_file."""
    return {"id": nid, "label": label, "norm_label": label.lower(),
            "file_type": "code", "source_file": ""}


def test_search_skips_a_sourceless_stub_whose_id_carries_the_query(tmp_path: Path) -> None:
    """The stub's ID inherits its file's words, so it matched with nowhere to point.

    Measured 2026-09-23: two of the top three code hits for "vehicle routing" were
    ``DataFrame`` stubs minted as ``jobs_vehicle_routing_job_py_dataframe``.
    """
    code = _write_graph(tmp_path / "c.json", [
        _stub("jobs_vehicle_routing_job_py_dataframe", "DataFrame"),
        _code_node("r", "vehicle routing sweep", "src/vehicle_routing.py"),
    ])
    kb = KnowledgeBase(books_path=BOOKS, code_path=code)
    for hits in (kb.search("vehicle routing", graph="code"),
                 kb.bridge("vehicle routing").implementation):
        assert [h.source for h in hits] == ["src/vehicle_routing.py"]


def test_bridge_implementation_drops_tests_docs_and_stubs_that_outscore_src(
    tmp_path: Path,
) -> None:
    """Each decoy scores ABOVE the module, so only the filter can put src/ alone.

    Tests are still found by ``search()`` itself: they left the bridge because
    they are not where a concept lives, not because they are unfindable.
    """
    code = _write_graph(tmp_path / "c.json", [
        _code_node("t", "newsvendor critical ratio newsvendor test",
                   "tests/test_newsvendor.py"),
        {**_code_node("d", "newsvendor critical ratio guide", "documentation/METHODOLOGY.md"),
         "file_type": "document"},
        _code_node("e", "newsvendor critical ratio demo", "examples/run_newsvendor.py"),
        _stub("jobs_newsvendor_job_py_newsvendor_critical_ratio", "DataFrame"),
        _code_node("s", "newsvendor", "src/newsvendor.py"),
    ])
    kb = KnowledgeBase(books_path=BOOKS, code_path=code)

    impl = kb.bridge("newsvendor critical ratio").implementation
    assert [c.source for c in impl] == ["src/newsvendor.py"]

    found = [c.source for c in kb.search("newsvendor critical ratio", graph="code")]
    assert "tests/test_newsvendor.py" in found


def test_bridge_breaks_a_score_tie_toward_the_file_named_after_the_term(
    tmp_path: Path,
) -> None:
    """Ties are the norm: 35 nodes tie at the top for "safety stock" on the real
    graph, and alphabetical order ("Compose..." < "Tune...") used to decide them."""
    code = _write_graph(tmp_path / "c.json", [
        _code_node("j", "Compose safety stock", "jobs/multi_echelon_job.py"),
        _code_node("s", "Tune safety stock", "src/safety_stock.py"),
    ])
    kb = KnowledgeBase(books_path=BOOKS, code_path=code)
    assert kb.bridge("safety stock").implementation[0].source == "src/safety_stock.py"
    # search() keeps its plain order: the tiebreak belongs to the bridge only.
    assert kb.search("safety stock", graph="code")[0].source == "jobs/multi_echelon_job.py"


def test_bridge_tiebreak_never_outranks_a_better_score(tmp_path: Path) -> None:
    """The name only breaks ties; a node matching MORE of the query still wins."""
    code = _write_graph(tmp_path / "c.json", [
        _code_node("j", "safety stock buffer placement", "jobs/multi_echelon_job.py"),
        _code_node("s", "safety stock", "src/safety_stock.py"),
    ])
    kb = KnowledgeBase(books_path=BOOKS, code_path=code)
    impl = kb.bridge("safety stock buffer placement").implementation
    assert impl[0].source == "jobs/multi_echelon_job.py"


def test_bridge_prefers_src_when_no_file_is_named_after_the_term(tmp_path: Path) -> None:
    code = _write_graph(tmp_path / "c.json", [
        _code_node("j", "reorder point", "jobs/inventory_optimization.py"),
        _code_node("s", "reorder point", "src/decision_support.py"),
    ])
    kb = KnowledgeBase(books_path=BOOKS, code_path=code)
    assert kb.bridge("reorder point").implementation[0].source == "src/decision_support.py"


@pytest.mark.skipif(not CODE.exists(), reason="code graph is gitignored")
@pytest.mark.parametrize("term,expected", [
    ("safety stock", "src/safety_stock.py"),
    ("landed cost", "src/landed_cost.py"),
    ("newsvendor", "src/newsvendor.py"),
    ("ddmrp buffer", "src/ddmrp.py"),
])
def test_bridge_leads_with_the_module_on_the_real_code_graph(
    kb: KnowledgeBase, term: str, expected: str
) -> None:
    impl = kb.bridge(term).implementation
    assert impl, f"no implementation for {term!r}"
    assert impl[0].source == expected
    assert not any(_looks_like_a_non_implementation(c.source or "") for c in impl)
    assert all(c.source for c in impl)


def _sop_code_graph(tmp_path: Path) -> Path:
    """The S&OP module against a scheduler that shares more of the phrase's words.

    Mirrors the real graph: src/sop.py carries "sop" only through its id (its
    docstrings write "S&OP", which tokenizes to nothing) and shares one word,
    "planning", with the spelled-out phrase; the scheduler shares two.
    """
    return _write_graph(tmp_path / "c.json", [
        _code_node("src_sop_aggregate_strategies",
                   "Evaluate the aggregate-planning strategies", "src/sop.py"),
        _code_node("src_scheduling_engine",
                   "Operations planning: sequencing engine", "src/scheduling.py"),
    ])


@pytest.mark.parametrize("term", [
    "sales and operations planning",
    "Sales & Operations Planning",
    "S&OP",
])
def test_bridge_finds_a_module_named_by_its_acronym(tmp_path: Path, term: str) -> None:
    """A module named by acronym wins the spelled-out query.

    Word overlap alone ranks the scheduler first on "sales and operations
    planning" (two shared words to one), and "S&OP" has no token of three
    letters at all, so it found nothing.
    """
    kb = KnowledgeBase(books_path=BOOKS, code_path=_sop_code_graph(tmp_path))

    impl = kb.bridge(term).implementation

    assert impl and impl[0].source == "src/sop.py", [c.source for c in impl]


def test_acronym_needs_the_whole_phrase(tmp_path: Path) -> None:
    """Guard: "operations planning" is not S&OP. Only the full phrase earns the
    acronym, so a partial one ranks on its words exactly as before."""
    kb = KnowledgeBase(books_path=BOOKS, code_path=_sop_code_graph(tmp_path))

    impl = kb.bridge("operations planning").implementation

    assert [c.source for c in impl] == ["src/scheduling.py", "src/sop.py"]


def test_acronym_expansion_stays_on_the_code_side(tmp_path: Path) -> None:
    """Guard: the books graph ranks on its own words. Its concepts spell the
    phrase out ("Sales and Operations Planning (S&OP)"), so it needs no
    expansion, and a books node that merely carries "sop" must not gain."""
    books = _write_graph(tmp_path / "b.json", [
        {"id": "note::sop", "label": "sop", "norm_label": "sop", "rationale": ""},
        {"id": "note::planning", "label": "Operations planning",
         "norm_label": "operations planning", "rationale": ""},
    ])
    kb = KnowledgeBase(books_path=books, code_path=tmp_path / "none.json")

    hits = kb.search("sales and operations planning", graph="books")

    assert [h.id for h in hits] == ["planning"]


def test_acronym_expansion_leaves_the_mixed_ranking_alone(tmp_path: Path) -> None:
    """Guard: ``graph="both"`` ranks exactly as before, code nodes included.

    It sets books and code scores side by side, each on its own graph's IDF, and
    the acronym's phrase weight tips that balance: measured on the real graphs,
    ``explain("Sales and Operations Planning")`` answered with a src/sop_engine
    docstring instead of Vollmann's S&OP concept.
    """
    kb = KnowledgeBase(books_path=tmp_path / "none.json", code_path=_sop_code_graph(tmp_path))

    hits = kb.search("sales and operations planning", graph="both")

    assert [h.source for h in hits] == ["src/scheduling.py", "src/sop.py"]


@pytest.mark.skipif(not CODE.exists(), reason="code graph is gitignored")
@pytest.mark.parametrize("term", ["sales and operations planning", "S&OP"])
def test_bridge_finds_the_sop_module_on_the_real_code_graph(
    kb: KnowledgeBase, term: str
) -> None:
    """Measured 2026-09-23: the spelled-out phrase put src/scheduling.py,
    src/queuing.py, src/ddmrp.py, jobs/odoo_job.py and src/connectors/ in the
    top-5, and the first S&OP node came 14th; "S&OP" returned nothing."""
    sources = [c.source or "" for c in kb.bridge(term).implementation]
    assert any(s.startswith(("src/sop", "jobs/sop_")) for s in sources), sources


@pytest.mark.skipif(not CODE.exists(), reason="code graph is gitignored")
def test_explain_sop_still_answers_with_the_book_concept(kb: KnowledgeBase) -> None:
    """The acronym weight lifts S&OP code on the code side only. Applied to the
    mixed ranking ``explain`` falls back to, it put a docstring above the book."""
    detail = kb.explain("Sales and Operations Planning")

    assert detail is not None and detail.concept.graph == "books", detail


# -- explain(): a label answers with the concept, not with prose about it --------


def _explain_graphs(tmp_path: Path, code_nodes: list[dict]) -> KnowledgeBase:
    """Vollmann's S&OP concept in a books graph where its words are common.

    Mirrors the real IDF: in the books graph "sales", "operations" and
    "planning" sit in many nodes and weigh little; in a large code graph the same
    words are rare and weigh more. That gap is what lets code prose outrank a
    concept that carries every word of the query.
    """
    books = _write_graph(tmp_path / "b.json", [
        {"id": "vollmann_sales_and_operations_planning",
         "label": "Sales and Operations Planning",
         "norm_label": "sales and operations planning", "rationale": ""},
    ] + [
        {"id": f"chapter_{i}", "label": f"Chapter {i}", "norm_label": f"chapter {i}",
         "rationale": "sales and operations planning"}
        for i in range(20)
    ])
    code = _write_graph(tmp_path / "c.json", code_nodes + [
        _code_node(f"src_module_{i}_helper", f"helper_{i}()", f"src/module_{i}.py")
        for i in range(50)
    ])
    return KnowledgeBase(books_path=books, code_path=code)


def test_explain_answers_a_label_with_the_concept_not_a_test_about_it(
    tmp_path: Path,
) -> None:
    """A test's docstring names the concept in words, so it matched the label.

    Measured 2026-09-23 on the graph regenerated from this branch:
    ``explain("Sales and Operations Planning")`` answered with the docstring of
    test_acronym_needs_the_whole_phrase instead of Vollmann's concept.
    """
    kb = _explain_graphs(tmp_path, [
        _code_node("tests_test_knowledge_rationale_980",
                   "Guard: operations planning is not S&OP",
                   "tests/test_knowledge.py"),
    ])

    detail = kb.explain("Sales and Operations Planning")

    assert detail is not None, detail
    assert detail.concept.id == "vollmann_sales_and_operations_planning", detail.concept


def test_explain_does_not_break_a_books_tie_by_the_book_file_name(tmp_path: Path) -> None:
    """The file-named-after-the-term tiebreak is for code. A book's source file is
    named after the book: "planning" in vollmann-manufacturing-planning-control
    made Vollmann's CPFR win a tie against Christopher's, whose label is the one
    asked for."""
    books = _write_graph(tmp_path / "b.json", [
        {"id": "christopher_cpfr",
         "label": "Collaborative Planning, Forecasting and Replenishment (CPFR)",
         "norm_label": "", "rationale": "", "source_file": "christopher-logistics.txt"},
        {"id": "vollmann_cpfr",
         "label": "Collaborative Planning, Forecasting, and Replenishment",
         "norm_label": "", "rationale": "",
         "source_file": "vollmann-manufacturing-planning-control.txt"},
    ])
    kb = KnowledgeBase(books_path=books, code_path=tmp_path / "none.json")

    detail = kb.explain("Collaborative Planning, Forecasting and Replenishment (CPFR)")

    assert detail is not None and detail.concept.id == "christopher_cpfr", detail


def test_explain_still_answers_with_code_when_no_concept_matches(tmp_path: Path) -> None:
    """Guard: only tests, demos, scripts and documents leave the fallback. A
    phrase that only code carries still resolves to the module that has it."""
    kb = _explain_graphs(tmp_path, [
        _code_node("src_routing_clarke_wright_savings", "clarke_wright_savings()",
                   "src/routing.py"),
    ])

    detail = kb.explain("clarke wright savings")

    assert detail is not None and detail.concept.source == "src/routing.py", detail


# -- implements(): a concept that spells an acronym out --------------------------


@pytest.mark.parametrize("concept_id,label", [
    ("sales_and_operations_planning", "Sales and Operations Planning (S&OP)"),
    ("vollmann_sales_and_operations_planning", "Sales and Operations Planning"),
    ("sales_operations_planning", "Sales & Operations Planning"),
])
def test_implements_credits_the_acronym_a_concept_spells_out(
    tmp_path: Path, concept_id: str, label: str
) -> None:
    """The S&OP module is named by acronym, so the phrase alone cannot reach it.

    src/sop.py shares one word with the concept, "planning", and its filename is
    "sop", which the concept never writes, so it needs 3 shared words and has 1.
    The scheduler shares two but is not named after the concept either. Both fell
    short and the citation shipped with no code behind it.
    """
    kb = KnowledgeBase(books_path=BOOKS, code_path=_sop_code_graph(tmp_path))
    concept = Concept(id=concept_id, label=label, source=None, location=None, graph="books")

    impl = kb.implements(concept)

    assert impl is not None and impl.source == "src/sop.py", impl


def test_implements_does_not_credit_an_acronym_written_as_such(tmp_path: Path) -> None:
    """Guard: only the phrase in words earns the acronym, not "S&OP" itself.

    A label that writes "S&OP" uses it to qualify something else. With the
    acronym credited, "Master Scheduler (S&OP -> MPS)" matched every
    src/sop_engine/ node on "sop" (from the path) plus "plan" (from the concept's
    id): 63 ties on the real graph, the winner a demand-plan record, not an MPS.
    """
    code = _write_graph(tmp_path / "c.json", [
        _code_node("src_sop_engine_demand_plan_demandplanline", "DemandPlanLine",
                   "src/sop_engine/demand_plan.py"),
    ])
    kb = KnowledgeBase(books_path=BOOKS, code_path=code)
    concept = Concept(id="master_scheduler_plan", label="Master Scheduler (S&OP -> MPS)",
                      source=None, location=None, graph="books")

    assert kb.implements(concept) is None


@pytest.mark.skipif(not CODE.exists(), reason="code graph is gitignored")
@pytest.mark.parametrize("concept_id", [
    "sales_and_operations_planning",
    "vollmann_sales_and_operations_planning",
])
def test_implements_links_the_sop_concepts_on_the_real_code_graph(
    kb: KnowledgeBase, concept_id: str
) -> None:
    """Measured 2026-09-23: both resolved to nothing, while "vollmann_sop" (the
    same idea, with "sop" in its id) resolved to src/sop.py. The S&OP citations of
    the ``sop`` tool shipped without the code that computes them."""
    detail = kb.explain(concept_id)
    assert detail is not None, concept_id

    impl = kb.implements(detail.concept)

    assert impl is not None and (impl.source or "").startswith("src/sop"), impl
