"""One-command demo: four briefs through the real orchestrator, in about a minute.

    make demo                 # or: PYTHONPATH=. python examples/run_demo.py

Runs against the committed sample data in `data/`, with no keys and no network:
the deterministic core is the whole demo, an LLM would only reword the summaries.
Deliverables land in `output/demo/` (git-ignored).

The fourth step is the point of the other three. Kern's differentiator is not
that it produces a plan -- it is that it refuses to when it cannot stand behind
one, and says which column it needed. A demo that only shows happy paths shows
the least interesting half of the system.
"""

from __future__ import annotations

import argparse
import sys
import time
from dataclasses import dataclass
from pathlib import Path

# Make `scm_agent` importable no matter where this script is launched from.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scm_agent import Orchestrator  # noqa: E402


@dataclass(frozen=True)
class Step:
    """One demo beat: a brief, the tool it exercises, and what it should prove."""

    title: str
    brief: str
    tool: str
    data: str | None
    overrides: dict[str, object]
    expect: tuple[str, ...]  # statuses that count as a pass for THIS step
    takeaway: str


# A malformed intake file, written at run time. Deliberately not committed: the
# repo should not carry a file whose only purpose is to be wrong.
_BAD_CSV_NAME = "malformed_intake.csv"
_BAD_CSV_BODY = "sku_reference,units_moved\nA-100,42\nA-101,17\n"

_STEPS: tuple[Step, ...] = (
    Step(
        title="Replenishment plan under a budget cap",
        brief="build a replenishment plan for my portfolio within an $80k budget",
        tool="inventory_optimization",
        data="data/sample_demand_portfolio.csv",
        overrides={"budget": 80000.0, "service_level": 0.95},
        expect=("ok",),
        takeaway="forecast -> (s,Q)/(R,S) -> MOQ and budget fit, as an Excel plan",
    ),
    Step(
        title="Margin-maximizing prices from observed elasticity",
        brief="what price maximizes margin on these SKUs",
        tool="pricing",
        data="data/sample_pricing.csv",
        overrides={},
        expect=("ok",),
        takeaway="per-SKU elasticity, and an explicit 'not enough signal' verdict",
    ),
    Step(
        title="ABC-XYZ classification of the portfolio",
        brief="classify my portfolio by value and demand variability",
        tool="abc_xyz",
        data="data/sample_demand_portfolio.csv",
        overrides={},
        expect=("ok",),
        takeaway="where the value sits, and which SKUs are discontinuation candidates",
    ),
    Step(
        title="The refusal: unusable intake produces NO deliverable",
        brief="build a replenishment plan from this file",
        tool="inventory_optimization",
        data=_BAD_CSV_NAME,  # resolved against the output dir, written below
        overrides={},
        expect=("needs_data", "needs_clarification", "qa_failed"),
        takeaway="names the missing columns instead of inventing a plan",
    ),
)


def _run_step(step: Step, out_root: Path, index: int, total: int) -> tuple[bool, float]:
    """Run one step. Returns (passed, elapsed_seconds). Never raises on a bad status."""
    print()
    print(f"[{index}/{total}] {step.title}")
    print(f'      brief: "{step.brief}"')

    data_path = None
    if step.data is not None:
        candidate = Path(step.data)
        # Step 4's file lives in the output dir; the rest are repo-relative.
        data_path = candidate if candidate.is_absolute() or candidate.exists() else out_root / candidate
        data_path = str(data_path)

    started = time.perf_counter()
    # The orchestrator already nests deliverables under the tool key, so passing
    # out_root (not out_root/tool) is what keeps the tree one level deep.
    result = Orchestrator().run(
        step.brief,
        data_path=data_path,
        overrides=dict(step.overrides),
        job_type=step.tool,
        client="Demo",
        out_dir=str(out_root),
    )
    elapsed = time.perf_counter() - started
    passed = result.status in step.expect

    mark = "OK " if passed else "!! "
    print(f"  {mark}status={result.status} tool={result.tool} ({elapsed:.1f}s)")
    print(f"      {result.summary}")
    for name, path in sorted(result.deliverables.items()):
        print(f"      -> {name}: {Path(path).relative_to(_REPO_ROOT)}")
    if not result.deliverables:
        print("      -> no deliverable (by design)")
    for issue in result.qa_issues:
        print(f"      QA: {issue}")
    for clarification in result.clarifications:
        print(f"      asks: {clarification}")
    if result.citations:
        # Citations are pre-formatted "concept -> module" strings and may carry
        # non-ASCII separators; the console is cp1252 on Windows (see CLAUDE.md).
        first = str(result.citations[0]).encode("ascii", "replace").decode("ascii")
        print(f"      grounded in: {first}")
    print(f"      takeaway: {step.takeaway}")

    if not passed:
        print(f"      EXPECTED one of {list(step.expect)}, got {result.status}", file=sys.stderr)
    return passed, elapsed


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Kern one-command demo.")
    parser.add_argument("--out", default="output/demo", help="output directory")
    args = parser.parse_args(argv)

    out_root = (_REPO_ROOT / args.out).resolve()
    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / _BAD_CSV_NAME).write_text(_BAD_CSV_BODY, encoding="utf-8")

    print("=" * 78)
    print("Kern demo -- four briefs, one orchestrator, no API keys required.")
    print(f"Deliverables: {out_root.relative_to(_REPO_ROOT)}/")
    print("=" * 78)

    results = [
        _run_step(step, out_root, i, len(_STEPS)) for i, step in enumerate(_STEPS, start=1)
    ]
    passed = sum(1 for ok, _ in results if ok)
    total_seconds = sum(seconds for _, seconds in results)

    print()
    print("=" * 78)
    print(f"{passed}/{len(_STEPS)} steps behaved as expected in {total_seconds:.1f}s.")
    print("Next: examples/run_agent.py --brief '<your own request>' --data <your.csv>")
    print("=" * 78)
    return 0 if passed == len(_STEPS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
