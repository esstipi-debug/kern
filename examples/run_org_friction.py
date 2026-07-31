"""Run the organizational-friction audit on a control-points export.

    python examples/run_org_friction.py path/to/puntos_control.csv --out output/org_friction
    python examples/run_org_friction.py puntos_control.csv --history historico_metas.csv

One row per KPI/checkpoint: a name, plus whichever of official_value,
shadow_value, owner, workaround_present and workaround_classification the
client already tracks. Every field but the name is optional -- a control point
with fewer fields simply lets fewer of the four patterns fire on it.

The optional history file (one row per control-point-period: name, period,
actual, target, already sorted chronologically per control point) is what feeds
the Goodhart/ratchet detector; without it the other three still run.

Every finding is a ranked question for a human to go confirm, never a verdict
on a person -- this tool structurally never emits EXECUTED.
"""

from __future__ import annotations

import argparse

from jobs import org_friction_job


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_path")
    parser.add_argument("--out", default="output/org_friction")
    parser.add_argument("--client", default="Client")
    parser.add_argument("--history", default=None, help="optional target-vs-actual history export")
    parser.add_argument(
        "--shadow-delta-threshold", type=float, default=None,
        help="relative gap between official and shadow value that counts as a hidden factory",
    )
    args = parser.parse_args()

    params: dict = {}
    if args.history:
        params["history_path"] = args.history
    if args.shadow_delta_threshold is not None:
        params["shadow_delta_threshold"] = args.shadow_delta_threshold

    control_points = org_friction_job.prepare(args.data_path, params)
    report = org_friction_job.run(control_points, params)
    issues = org_friction_job.verify(report)
    print(report.summary)
    if issues:
        print("QA gate blocked the delivery:")
        for issue in issues:
            print(f"  - {issue}")
        return 1
    paths = org_friction_job.write_operational(report, args.out, args.client)
    for label, path in paths.items():
        print(f"{label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
