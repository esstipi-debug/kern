"""Run the flow map on a movements export.

    python examples/run_flow_map.py path/to/movimientos.csv --out output/flow_map

One row per shipment leg: departure date, arrival date, origin, destination,
product, quantity (unit cost optional). Lanes with fewer than 8 observed
movements are drawn but report no transit number -- that is deliberate.
"""

from __future__ import annotations

import argparse

from jobs import flow_map_job


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_path")
    parser.add_argument("--out", default="output/flow_map")
    parser.add_argument("--client", default="Client")
    parser.add_argument("--min-observations", type=int, default=8)
    args = parser.parse_args()

    movements = flow_map_job.prepare(args.data_path)
    report = flow_map_job.run(movements, {"min_observations": args.min_observations})
    issues = flow_map_job.verify(report)
    print(report.summary)
    if issues:
        print("QA gate blocked the delivery:")
        for issue in issues:
            print(f"  - {issue}")
        return 1
    paths = flow_map_job.write_operational(report, args.out, args.client)
    for label, path in paths.items():
        print(f"{label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
