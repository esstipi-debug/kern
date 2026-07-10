"""L3 knowledge layer — queries the SCM knowledge graphs for the agent.

Two graphs, one query surface:
  - books graph (knowledge/scm-books/graph.json) — domain theory from SCM
    books (incl. supply-chain leadership / the CHAIN model), with chapter
    citations. Committed to the repo.
  - code graph (graphify-out/graph.json) — the codebase structure. Gitignored
    (regenerable), so it may be absent on a fresh clone — handled gracefully.

The agent uses this to ground decisions: define a concept, find which method
applies, cite the book/chapter, and (the bridge) jump from theory to the
function that implements it.

Pure read-only. Frozen dataclasses for results. Stdlib only.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
BOOKS_GRAPH = _REPO_ROOT / "knowledge" / "scm-books" / "graph.json"
CODE_GRAPH = _REPO_ROOT / "graphify-out" / "graph.json"

_LOG = logging.getLogger("linchpin.knowledge")

_TOKEN = re.compile(r"[a-z0-9]{3,}")


@dataclass(frozen=True)
class Concept:
    """A node in one of the knowledge graphs."""

    id: str
    label: str
    source: str | None
    location: str | None
    graph: str  # "books" | "code"


@dataclass(frozen=True)
class ConceptDetail:
    """A concept plus its rationale and directly connected neighbors."""

    concept: Concept
    rationale: str | None
    neighbors: tuple[tuple[str, str], ...]  # (relation, neighbor_label)


@dataclass(frozen=True)
class Bridge:
    """A term resolved on both sides: theory (books) and implementation (code)."""

    term: str
    theory: tuple[Concept, ...]
    implementation: tuple[Concept, ...]


@dataclass(frozen=True)
class MethodAdvice:
    """A demand/situation pattern mapped to a citable graph concept."""

    pattern: str
    concept: Concept
    rationale: str | None


@dataclass(frozen=True)
class GroundedCitation:
    """A ranked citation plus the graph node it actually resolved to.

    ``text`` is the display string ``ground_citations`` has always returned;
    ``node_id`` (bare, namespace-stripped - see ``_bare_id``) is what
    ``scm_agent.citation_gate`` verifies against the graph before a citation
    is allowed to reach a client deliverable.
    """

    text: str
    node_id: str
    graph: str = "books"


# Brief-token triggers -> graph concept id (method advisor for routing / grounding).
_METHOD_RULES: tuple[tuple[tuple[str, ...], str, str], ...] = (
    (("intermittent", "lumpy", "sporadic", "adi"), "crostons_method", "intermittent demand"),
    (("seasonal", "seasonality", "mstl", "holt", "winters"), "time_series_decomposition", "seasonal demand"),
    (("elasticity", "markdown", "price", "pricing"), "price_elasticity_of_demand", "pricing / elasticity"),
    (("safety", "stockout", "service", "level"), "safety_stock", "buffer / service level"),
    (("reorder", "rop", "review"), "reorder_point", "reorder policy"),
    (("ddmrp", "buffer", "decoupling"), "demand_driven_material_requirements_planning", "DDMRP buffers"),
    (("abc", "xyz", "pareto", "classification"), "abc_analysis", "ABC / XYZ classification"),
    (("sop", "aggregate", "planning"), "sales_and_operations_planning", "S&OP / IBP"),
    (("landed", "incoterm", "duty", "freight"), "landed_cost", "landed cost / TCO"),
    (("leadership", "chain", "collaborative"), "chain_model", "CHAIN leadership"),
    (("resilience", "risk", "disruption"), "supply_chain_resilience", "risk / resilience"),
    (("sustainab", "carbon", "circular"), "sustainable_procurement", "sustainable logistics"),
)

_MIN_INFERRED_CONFIDENCE = 0.75


def _tokens(text: str) -> set[str]:
    return set(_TOKEN.findall(text.lower()))


def _load(path: Path) -> tuple[dict, str | None]:
    """Load a node-link graph JSON.

    Returns ``(graph, problem)``. ``problem`` is ``None`` on success, otherwise a
    short human-readable reason ("missing", "unreadable (...)", "malformed ..."):
    a degraded graph yields an empty node set *plus* a surfaced reason, so callers
    can fail loud (a visible warning) instead of silently dropping citations.
    """
    p = Path(path)
    if not p.exists():
        return {"nodes": [], "links": []}, "missing"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"nodes": [], "links": []}, f"unreadable ({type(exc).__name__})"
    if not isinstance(data, dict) or "nodes" not in data:
        return {"nodes": [], "links": []}, "malformed (no 'nodes' key)"
    data.setdefault("links", data.get("edges", []))
    return data, None


class KnowledgeBase:
    """Read-only query surface over the books + code knowledge graphs."""

    def __init__(
        self,
        books_path: str | Path = BOOKS_GRAPH,
        code_path: str | Path = CODE_GRAPH,
    ) -> None:
        self._graphs: dict[str, dict] = {}
        self._problems: dict[str, str] = {}
        for name, path in (("books", Path(books_path)), ("code", Path(code_path))):
            graph, problem = _load(path)
            self._graphs[name] = graph
            if problem:
                self._problems[name] = problem
                # The books graph is committed, so a problem there is an error; the
                # code graph is regenerable (gitignored), so a problem is a warning.
                level = logging.ERROR if name == "books" else logging.WARNING
                _LOG.log(level, "%s knowledge graph %s (%s) - %s citations degraded",
                         name, problem, path, name)
        # id -> node index, per graph, for O(1) explain()
        self._index = {
            name: {n["id"]: n for n in g["nodes"] if "id" in n}
            for name, g in self._graphs.items()
        }
        # Precompute searchable token sets + IDF once at load, so search()/
        # bridge()/implements() never re-tokenize the whole graph on each call.
        # Two fields per node: the *title* (label/id/norm_label — what it is) and
        # the *body* (rationale — why it matters). IDF weights a rare, specific
        # term (e.g. "newsvendor") above a ubiquitous one (e.g. "demand").
        self._title_tokens: dict[str, dict[str, set[str]]] = {}
        self._body_tokens: dict[str, dict[str, set[str]]] = {}
        self._idf: dict[str, dict[str, float]] = {}
        for name, nodes_by_id in self._index.items():
            titles: dict[str, set[str]] = {}
            bodies: dict[str, set[str]] = {}
            doc_freq: dict[str, int] = {}
            for nid, node in nodes_by_id.items():
                title = _tokens(
                    f"{node.get('label', '')} {node.get('id', '')} {node.get('norm_label', '')}"
                )
                body = _tokens(node.get("rationale") or "")
                titles[nid] = title
                bodies[nid] = body
                for tok in title | body:
                    doc_freq[tok] = doc_freq.get(tok, 0) + 1
            n_docs = max(len(nodes_by_id), 1)
            # log(1 + N/df): always positive, monotonically smaller as a token
            # spreads across more nodes. A token in 1 node scores high; one in all
            # scores near log(2).
            self._title_tokens[name] = titles
            self._body_tokens[name] = bodies
            self._idf[name] = {tok: math.log(1 + n_docs / df) for tok, df in doc_freq.items()}
        # Undirected adjacency per graph, precomputed once - concept_distance()
        # does a bounded BFS per citation check (scm_agent.citation_gate calls
        # it per candidate citation per package step), so this avoids an O(E)
        # linear scan of `links` on every single call.
        self._adjacency: dict[str, dict[str, set[str]]] = {}
        for name, g in self._graphs.items():
            index = self._index[name]
            adj: dict[str, set[str]] = {}
            for e in g["links"]:
                conf = e.get("confidence")
                if conf == "INFERRED":
                    score = e.get("confidence_score")
                    if score is not None and score < _MIN_INFERRED_CONFIDENCE:
                        continue
                src, tgt = e.get("source"), e.get("target")
                if src in index and tgt in index:
                    adj.setdefault(src, set()).add(tgt)
                    adj.setdefault(tgt, set()).add(src)
            self._adjacency[name] = adj

    def available(self) -> dict[str, int]:
        """Node count per graph (0 means the graph file was missing/empty)."""
        return {name: len(g["nodes"]) for name, g in self._graphs.items()}

    def warnings(self) -> list[str]:
        """Actionable warnings for any graph that did not load cleanly.

        Empty when both graphs are healthy. Callers (orchestrator, deliverable)
        surface these so a missing or corrupt code graph shows up as an explicit
        note instead of citations silently going theory-only.
        """
        fix = {
            "books": "restore knowledge/scm-books/graph.json",
            "code": "regenerate graphify-out/ with /graphify",
        }
        return [
            f"{name}: graph {self._problems[name]} - {name} citations unavailable ({fix[name]})"
            for name in ("books", "code")
            if name in self._problems
        ]

    # Field weights for ranking: a query hit in the title (what the concept *is*)
    # counts more than one in the rationale (why it matters), so a terse-titled
    # exact match still outranks a node that only mentions the term in passing.
    _W_TITLE = 2.0
    _W_BODY = 1.0

    def advise(
        self, brief: str, *, limit: int = 3, domain_terms: frozenset[str] | None = None
    ) -> list[MethodAdvice]:
        """Map brief language to citable methods/concepts in the books graph.

        When ``domain_terms`` is given (the active tool's own keyword vocabulary), a
        rule only fires if at least one of its trigger tokens is ALSO a domain term.
        Without this gate, a bare "chain" mention - present in nearly every SCM brief
        via "supply chain" - fired the leadership CHAIN-model rule regardless of the
        tool's actual subject, surfacing leadership citations on e.g. an EOQ brief.
        """
        terms = _tokens(brief)
        if not terms:
            return []
        out: list[MethodAdvice] = []
        seen: set[str] = set()
        for triggers, concept_id, pattern in _METHOD_RULES:
            trigger_set = set(triggers)
            if not (trigger_set & terms):
                continue
            if domain_terms is not None and not (trigger_set & domain_terms):
                continue
            node = self._resolve_node(concept_id, "books")
            if node is None:
                hits = self.search(concept_id.replace("_", " "), graph="books", limit=1)
                if not hits:
                    continue
                node = self._resolve_node(hits[0].id, "books")
            if node is None or node["id"] in seen:
                continue
            seen.add(node["id"])
            out.append(MethodAdvice(
                pattern=pattern,
                concept=self._to_concept(node, "books"),
                rationale=node.get("rationale"),
            ))
            if len(out) >= limit:
                break
        return out

    def ground_citations(
        self,
        tool_keywords: tuple[str, ...] | list[str],
        brief: str = "",
        *,
        limit: int = 5,
    ) -> list[str]:
        """Ranked L3 citations from tool keywords, the client brief, and method advice.

        Re-ranks candidates by IDF-weighted token overlap, not a raw count: a common,
        low-information word shared by nearly every SCM brief ("supply", "chain",
        "across") must not outrank a rarer, precisely on-topic term ("eoq", "reorder
        point"). A raw count previously let an unrelated chapter that merely mentioned
        "supply chain" outrank the tool's own subject matter. Method-advice hits are
        also gated to the tool's own domain vocabulary (see ``advise``) and scored by
        their trigger's own specificity instead of a flat constant.

        Thin wrapper over :meth:`ground_citations_detailed` for callers that only
        want display text (most of them) - see there for the underlying node id,
        which ``scm_agent.citation_gate`` needs to actually verify a citation
        resolves to something real and topically connected, not merely formatted.
        """
        return [c.text for c in self.ground_citations_detailed(tool_keywords, brief, limit=limit)]

    def ground_citations_detailed(
        self,
        tool_keywords: tuple[str, ...] | list[str],
        brief: str = "",
        *,
        limit: int = 5,
    ) -> list[GroundedCitation]:
        """Same ranking as :meth:`ground_citations`, but keeps each citation's
        resolved node id alongside its display text - see ``scm_agent.citation_gate``,
        the only current consumer that needs the id (to verify the citation
        actually resolves to a real, topically-connected graph node before it
        reaches a client deliverable)."""
        domain_terms = frozenset(_tokens(" ".join(tool_keywords)))
        idf = self._idf.get("books", {})
        queries = [" ".join(tool_keywords)]
        if brief.strip():
            queries.append(brief)
        scored: dict[str, tuple[float, int, Concept]] = {}
        for qi, query in enumerate(queries):
            weight = 2.0 if qi == 0 else 3.0  # brief matches weigh more
            for hit in self.search(query, graph="books", limit=limit + 2):
                loc_bonus = 1 if hit.location else 0
                shared = _tokens(query) & _tokens(f"{hit.label} {hit.id}")
                weighted = sum(idf.get(t, 0.0) for t in shared) * weight
                key = hit.id
                rank = (weighted + loc_bonus, loc_bonus, hit)
                prev = scored.get(key)
                if prev is None or rank[:2] > prev[:2]:
                    scored[key] = rank
        for advice in self.advise(brief, limit=2, domain_terms=domain_terms):
            key = advice.concept.id
            # Score by the trigger's own specificity (highest IDF among its trigger
            # tokens) rather than a flat constant, so a rare, precise trigger (e.g.
            # "ddmrp") ranks higher than a broad one and can't win by merely existing.
            base = max((idf.get(t, 0.0) for t in _tokens(advice.pattern)), default=1.0)
            rank = (base + (1 if advice.concept.location else 0), 1 if advice.concept.location else 0, advice.concept)
            prev = scored.get(key)
            if prev is None or rank[:2] > prev[:2]:
                scored[key] = rank
        ordered = sorted(scored.values(), key=lambda r: (-r[0], -r[1], r[2].label))
        cites: list[GroundedCitation] = []
        for _, _, hit in ordered[:limit]:
            loc = f" {hit.location}" if hit.location else ""
            cite = f"{hit.label} — {hit.source}{loc}".strip()
            impl = self.implements(hit)
            if impl and impl.source:
                impl_loc = f":{impl.location}" if impl.location else ""
                cite += f"  -> {impl.source}{impl_loc}"
            cites.append(GroundedCitation(text=cite, node_id=hit.id, graph="books"))
        return cites

    def search(self, query: str, graph: str = "both", limit: int = 8) -> list[Concept]:
        """Rank concept nodes by IDF-weighted token overlap with the query.

        Matches on the node title (label/id/norm_label) *and* its rationale, with
        the title weighted higher; rarer query terms (high IDF) dominate common
        ones. graph: "books", "code", or "both".
        """
        terms = _tokens(query)
        if not terms:
            return []
        names = ("books", "code") if graph == "both" else (graph,)

        scored: list[tuple[float, Concept]] = []
        for name in names:
            idf = self._idf.get(name, {})
            titles = self._title_tokens.get(name, {})
            bodies = self._body_tokens.get(name, {})
            for nid, node in self._index.get(name, {}).items():
                title_hit = terms & titles.get(nid, set())
                body_hit = (terms & bodies.get(nid, set())) - title_hit
                if not title_hit and not body_hit:
                    continue
                score = self._W_TITLE * sum(idf.get(t, 0.0) for t in title_hit) + (
                    self._W_BODY * sum(idf.get(t, 0.0) for t in body_hit)
                )
                scored.append((score, self._to_concept(node, name)))

        scored.sort(key=lambda x: (-x[0], x[1].label))
        return [c for _, c in scored[:limit]]

    def explain(self, concept_id: str) -> ConceptDetail | None:
        """Return a concept's rationale + neighbors.

        An id-like argument (no spaces) is looked up exactly. A label-like
        argument (with spaces) falls back to a fuzzy search, so callers can
        pass either `crostons_method` or `Croston's Method`.
        """
        for name in ("books", "code"):
            node = self._resolve_node(concept_id, name)
            if node is not None:
                return self._detail(node, name)

        # Only fuzzy-resolve genuine label phrases; an unknown id stays unknown
        # (avoids a stray common token matching an unrelated node).
        if " " not in concept_id.strip():
            return None
        hits = self.search(concept_id, graph="both", limit=1)
        if not hits:
            return None
        node = self._resolve_node(hits[0].id, hits[0].graph)
        return self._detail(node, hits[0].graph) if node else None

    def node_exists(self, concept_id: str, graph: str = "books") -> bool:
        """Whether a (possibly bare) concept id resolves to a real graph node.

        Used by ``scm_agent.citation_gate`` to reject a citation whose id came
        back from ranking but doesn't actually exist as a node - shouldn't
        happen in practice (ids come straight from the graph), but a citation
        that can't be re-resolved must never be silently trusted.
        """
        return self._resolve_node(concept_id, graph) is not None

    def concept_distance(
        self, from_id: str, to_id: str, *, graph: str = "books", max_hops: int = 2,
    ) -> int | None:
        """BFS hop count between two (possibly bare) concept ids, or ``None``
        if either doesn't resolve or they aren't connected within ``max_hops``.

        0 means ``from_id`` and ``to_id`` resolve to the SAME node (the
        strongest possible match - the citation IS one of the anchor
        concepts). Undirected: a citation's relevance to a tool's subject
        doesn't depend on which end of an edge the graph happened to record.
        """
        a = self._resolve_node(from_id, graph)
        b = self._resolve_node(to_id, graph)
        if a is None or b is None:
            return None
        a_id, b_id = a["id"], b["id"]
        if a_id == b_id:
            return 0
        adjacency = self._adjacency.get(graph, {})
        frontier = {a_id}
        visited = {a_id}
        for hop in range(1, max_hops + 1):
            next_frontier: set[str] = set()
            for node in frontier:
                for neighbor in adjacency.get(node, ()):
                    if neighbor == b_id:
                        return hop
                    if neighbor not in visited:
                        visited.add(neighbor)
                        next_frontier.add(neighbor)
            frontier = next_frontier
            if not frontier:
                break
        return None

    def bridge(self, term: str) -> Bridge:
        """Resolve a term on both sides: theory (books) and code (implementation).

        This is the cross-graph link: e.g. bridge("newsvendor") returns the
        book concept (with chapter) AND the source file that implements it.
        """
        theory = tuple(self.search(term, graph="books", limit=5))
        impl = tuple(self.search(term, graph="code", limit=5))
        return Bridge(term=term, theory=theory, implementation=impl)

    def implements(self, concept: Concept, min_overlap: int = 2) -> Concept | None:
        """Best code node that implements a books concept (theory -> code), or None.

        The precise half of bridge() for grounding: requires at least `min_overlap`
        shared tokens between the concept and a `.py` code node, so a single common
        word (e.g. "price") can't forge a spurious link. Prefers real src/ modules.
        Returns None when the code graph is absent or nothing clears the bar — the
        caller then cites theory only.
        """
        want = _tokens(f"{concept.label} {concept.id}")
        if not want:
            return None
        titles = self._title_tokens.get("code", {})
        best: tuple[int, int, Concept] | None = None
        for nid, n in self._index.get("code", {}).items():
            src = n.get("source_file") or ""
            if not src.endswith(".py"):
                continue
            stem = Path(src).stem
            have = titles.get(nid, set()) | _tokens(stem)
            score = len(want & have)
            # A 2-token hit is only trustworthy when the file is named after the
            # concept (eoq.py for "Economic Order Quantity"); otherwise a pair of
            # common domain words ("dynamic", "pricing") forges a link, so require
            # 3+. Keeps the strong bridges, drops the coincidental ones.
            named_after = bool(_tokens(stem) & want)
            if score < (min_overlap if named_after else min_overlap + 1):
                continue
            rank = (score, 1 if src.startswith("src/") else 0, self._to_concept(n, "code"))
            if best is None or rank[:2] > best[:2]:
                best = rank
        return best[2] if best else None

    # -- internals ------------------------------------------------------

    @staticmethod
    def _bare_id(node_id: str) -> str:
        """Strip a leading ``<source>::`` namespace from a node id.

        The committed books graph namespaces ids by source (``knowledge::x``,
        ``cohen-dai-...::y``); the method rules, the ``--explain`` CLI, and every
        test use the bare slug (``x``). Normalizing here keeps ``Concept.id``
        stable across graph re-merges that change the source prefix.
        """
        return node_id.split("::", 1)[1] if "::" in node_id else node_id

    def _resolve_node(self, concept_id: str, graph: str) -> dict | None:
        """Resolve a (possibly bare) concept id to a node, tolerant of namespacing.

        Tries an exact match, then the curated ``knowledge::`` namespace, then any
        source-prefixed ``<ns>::<id>`` - so a bare id like ``chain_model`` keeps
        resolving after a merge re-prefixes it to ``knowledge::chain_model``. The
        exact-then-``knowledge::`` order means the original curated source always
        wins over a same-slug node from a newer source.
        """
        index = self._index.get(graph, {})
        node = index.get(concept_id) or index.get(f"knowledge::{concept_id}")
        if node is not None:
            return node
        suffix = f"::{concept_id}"
        for nid, candidate in index.items():
            if nid.endswith(suffix):
                return candidate
        return None

    def _to_concept(self, node: dict, graph: str) -> Concept:
        return Concept(
            id=self._bare_id(node.get("id", "")),
            label=node.get("label", node.get("id", "")),
            source=node.get("source_file"),
            location=node.get("source_location"),
            graph=graph,
        )

    def _detail(self, node: dict, graph: str) -> ConceptDetail:
        nid = node["id"]
        index = self._index[graph]
        neighbors: list[tuple[str, str]] = []
        for e in self._graphs[graph]["links"]:
            rel = e.get("relation", "related")
            conf = e.get("confidence")
            if conf == "INFERRED":
                score = e.get("confidence_score")
                if score is not None and score < _MIN_INFERRED_CONFIDENCE:
                    continue
            if e.get("source") == nid and e.get("target") in index:
                neighbors.append((rel, index[e["target"]].get("label", e["target"])))
            elif e.get("target") == nid and e.get("source") in index:
                neighbors.append((f"{rel} (from)", index[e["source"]].get("label", e["source"])))
        return ConceptDetail(
            concept=self._to_concept(node, graph),
            rationale=node.get("rationale"),
            neighbors=tuple(neighbors[:15]),
        )
