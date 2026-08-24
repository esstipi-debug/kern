"""Regenerate documentation/GRAPH_LESSONS.md from knowledge/graph-memory/.

``graphify reflect`` drops any ``--nodes`` entry it cannot resolve in the graph it is
given, which is how it prunes lessons about code that no longer exists. That pruning
is right, but it means the graph you pass decides which memories survive:

  --graph graphify-out/graph.json        code lessons kept, the capacity gaps dropped
  --graph knowledge/scm-books/graph.json the capacity gaps kept, code lessons dropped

Since PR #28 the memory store spans both graphs, so neither alone is correct. This
merges them first and reflects against the union, which keeps every memory that still
resolves anywhere. Note that ``graphify merge-graphs`` re-namespaces ids
(``kernbrechas_x`` -> ``knowledge::kernbrechas_x``) but leaves labels intact — which is
why memory entries reference nodes by LABEL, not by id.

Deterministic and LLM-free, like ``reflect`` itself: two runs are byte-identical.

Usage:
    python scripts/reflect_lessons.py [--check]

    --check  regenerate into a temp file and diff against the committed doc; exit 1 if
             they differ. For CI, so the lessons doc cannot silently drift from the
             memories it claims to summarize.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MEMORY_DIR = REPO / "knowledge" / "graph-memory"
BOOKS_GRAPH = REPO / "knowledge" / "scm-books" / "graph.json"
CODE_GRAPH = REPO / "graphify-out" / "graph.json"
OUT = REPO / "documentation" / "GRAPH_LESSONS.md"


def _run(cmd: list[str]) -> None:
    proc = subprocess.run(cmd, capture_output=True, text=True)
    if proc.returncode != 0:
        raise SystemExit(f"{cmd[0]} failed:\n{proc.stdout}\n{proc.stderr}")


def regenerate(out: Path) -> None:
    graphs = [BOOKS_GRAPH]
    if CODE_GRAPH.is_file():
        graphs.append(CODE_GRAPH)
    else:
        # graphify-out/ is gitignored and rebuilt by the SessionStart hook. Without it
        # the code-side lessons prune away; say so rather than producing a quietly
        # shorter document.
        print(
            f"warning: {CODE_GRAPH.relative_to(REPO)} is absent — code-graph lessons "
            "will be pruned. Rebuild it with /graphify for a complete document.",
            file=sys.stderr,
        )

    with tempfile.TemporaryDirectory() as tmp:
        graph_arg = graphs[0]
        if len(graphs) > 1:
            merged = Path(tmp) / "merged.json"
            _run(["graphify", "merge-graphs", *[str(g) for g in graphs], "--out", str(merged)])
            graph_arg = merged
        _run(
            [
                "graphify", "reflect",
                "--memory-dir", str(MEMORY_DIR),
                "--out", str(out),
                "--graph", str(graph_arg),
            ]
        )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="fail if the committed doc is stale")
    args = ap.parse_args()

    if args.check:
        with tempfile.TemporaryDirectory() as tmp:
            candidate = Path(tmp) / "GRAPH_LESSONS.md"
            regenerate(candidate)
            if not OUT.is_file() or candidate.read_text(encoding="utf-8") != OUT.read_text(encoding="utf-8"):
                print(
                    f"{OUT.relative_to(REPO)} is stale — run: python scripts/reflect_lessons.py",
                    file=sys.stderr,
                )
                return 1
        print(f"{OUT.relative_to(REPO)} is up to date")
        return 0

    regenerate(OUT)
    print(f"wrote {OUT.relative_to(REPO)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
