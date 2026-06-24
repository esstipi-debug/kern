"""L3 knowledge layer — queries the SCM knowledge graphs for the agent.

Two graphs, one query surface:
  - books graph (knowledge/scm-books/graph.json) — domain theory from 18 SCM
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

    def search(self, query: str, graph: str = "both", limit: int = 8) -> list[Concept]:
        """Rank concept nodes by token overlap with the query.

        graph: "books", "code", or "both".
        """
        terms = _tokens(query)
        if not terms:
            return []
        names = ("books", "code") if graph == "both" else (graph,)

        scored: list[tuple[int, Concept]] = []
        for name in names:
            for n in self._graphs.get(name, {}).get("nodes", []):
                hay = _tokens(f"{n.get('label', '')} {n.get('id', '')} {n.get('norm_label', '')}")
                score = len(terms & hay)
                if score:
                    scored.append((score, self._to_concept(n, name)))

        scored.sort(key=lambda x: (-x[0], x[1].label))
        return [c for _, c in scored[:limit]]

    def explain(self, concept_id: str) -> ConceptDetail | None:
        """Return a concept's rationale + neighbors.

        An id-like argument (no spaces) is looked up exactly. A label-like
        argument (with spaces) falls back to a fuzzy search, so callers can
        pass either `crostons_method` or `Croston's Method`.
        """
        for name in ("books", "code"):
            node = self._index[name].get(concept_id)
            if node is not None:
                return self._detail(node, name)

        # Only fuzzy-resolve genuine label phrases; an unknown id stays unknown
        # (avoids a stray common token matching an unrelated node).
        if " " not in concept_id.strip():
            return None
        hits = self.search(concept_id, graph="both", limit=1)
        if not hits:
            return None
        node = self._index[hits[0].graph].get(hits[0].id)
        return self._detail(node, hits[0].graph) if node else None

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
        best: tuple[int, int, Concept] | None = None
        for n in self._graphs["code"]["nodes"]:
            src = n.get("source_file") or ""
            if not src.endswith(".py"):
                continue
            stem = Path(src).stem
            have = _tokens(f"{n.get('label', '')} {n.get('id', '')} {n.get('norm_label', '')} {stem}")
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

    def _to_concept(self, node: dict, graph: str) -> Concept:
        return Concept(
            id=node.get("id", ""),
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
            if e.get("source") == nid and e.get("target") in index:
                neighbors.append((rel, index[e["target"]].get("label", e["target"])))
            elif e.get("target") == nid and e.get("source") in index:
                neighbors.append((f"{rel} (from)", index[e["source"]].get("label", e["source"])))
        return ConceptDetail(
            concept=self._to_concept(node, graph),
            rationale=node.get("rationale"),
            neighbors=tuple(neighbors[:15]),
        )
