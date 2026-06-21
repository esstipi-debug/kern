"""Run the SCM agent: a free-form brief (+ optional data) -> routed deliverable.

    python examples/run_agent.py --brief "set up reorder points" --data demand.csv
    python examples/run_agent.py --brief "what price maximizes profit" --data prices.csv
    python examples/run_agent.py --brief "evaluate our SC leadership" --scores "3 2 3 1 1" --name "Team"

Routing is automatic (override with --job). Runs with or without ANTHROPIC_API_KEY:
the deterministic core always works; an LLM only sharpens parsing and the summary.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make `scm_agent` importable no matter where this script is launched from.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scm_agent import Orchestrator  # noqa: E402

# CLI flag -> overrides key, with the type to coerce to.
_PARAM_FLAGS: dict[str, type] = {
    "service_level": float,
    "holding_rate": float,
    "order_cost": float,
    "budget": float,
    "cost_ratio": float,
    "period": str,
    "scores": str,
    "name": str,
}


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="SCM agent — brief + optional data -> deliverable.")
    p.add_argument("--brief", required=True, help="free-form request")
    p.add_argument("--data", default=None, help="client CSV/Excel (for quantitative jobs)")
    p.add_argument("--job", default=None, help="force a capability key (skips classification)")
    p.add_argument("--out", default="deliverables/agent", help="output directory")
    p.add_argument("--client", default="Client", help="client name for the report")
    p.add_argument("--period", default=None, help="bucketing period (W/D/MS)")
    p.add_argument("--service-level", type=float, default=None)
    p.add_argument("--holding-rate", type=float, default=None)
    p.add_argument("--order-cost", type=float, default=None)
    p.add_argument("--budget", type=float, default=None)
    p.add_argument("--cost-ratio", type=float, default=None)
    p.add_argument("--scores", default=None, help="leadership scores 'C H A I N', e.g. '3 2 3 1 1'")
    p.add_argument("--name", default=None, help="who/what is evaluated (leadership)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    overrides = {key: getattr(args, key) for key in _PARAM_FLAGS if getattr(args, key) is not None}

    result = Orchestrator().run(
        args.brief, data_path=args.data, overrides=overrides,
        job_type=args.job, client=args.client, out_dir=args.out,
    )

    print(f"[{result.status}] tool={result.tool} confidence={result.confidence:.2f}")
    print(result.summary)
    if result.deliverables:
        for name, path in result.deliverables.items():
            print(f"  {name:8s} -> {path}")
    if result.qa_issues:
        print("QA issues:", file=sys.stderr)
        for issue in result.qa_issues:
            print("  - " + issue, file=sys.stderr)
    if result.clarifications:
        print("Need more detail:")
        for c in result.clarifications:
            print("  - " + c)

    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
