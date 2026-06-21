"""Query the L3 knowledge layer — domain theory + code, one surface.

    python examples/query_knowledge.py --search "intermittent demand"
    python examples/query_knowledge.py --explain crostons_method
    python examples/query_knowledge.py --bridge newsvendor

--bridge is the cross-graph link: it shows the book theory (with chapter) AND
the source file that implements it.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scm_agent import KnowledgeBase  # noqa: E402


def _fmt(concept) -> str:
    loc = f" [{concept.location}]" if concept.location else ""
    return f"  {concept.label}{loc}\n      ({concept.graph}: {concept.source or '?'})"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Query the SCM knowledge graphs.")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--search", help="rank concepts by a query, both graphs")
    g.add_argument("--explain", help="show a concept's neighbors + rationale")
    g.add_argument("--bridge", help="link a term: book theory <-> code implementation")
    p.add_argument("--graph", default="both", choices=["books", "code", "both"])
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    kb = KnowledgeBase()
    status = kb.available()
    print(f"knowledge: books={status['books']} nodes, code={status['code']} nodes\n")

    if args.search:
        hits = kb.search(args.search, graph=args.graph)
        if not hits:
            print("no matches")
            return 1
        print(f"Top matches for {args.search!r}:")
        for h in hits:
            print(_fmt(h))
        return 0

    if args.explain:
        detail = kb.explain(args.explain)
        if detail is None:
            print(f"concept {args.explain!r} not found")
            return 1
        c = detail.concept
        print(f"{c.label}  ({c.graph}: {c.source or '?'}{', ' + c.location if c.location else ''})")
        if detail.rationale:
            print(f"\nWhy: {detail.rationale}")
        if detail.neighbors:
            print("\nConnected to:")
            for rel, label in detail.neighbors:
                print(f"  --{rel}--> {label}")
        return 0

    # --bridge
    b = kb.bridge(args.bridge)
    print(f"BRIDGE: {b.term!r}\n")
    print("THEORY (books — what it is, where to read):")
    if b.theory:
        for c in b.theory:
            print(_fmt(c))
    else:
        print("  (nothing in the books graph)")
    print("\nIMPLEMENTATION (code — where it lives):")
    if b.implementation:
        for c in b.implementation:
            print(_fmt(c))
    else:
        print("  (code graph absent or no match — it's gitignored; run /graphify to build it)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
