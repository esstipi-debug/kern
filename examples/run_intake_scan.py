"""Run the intake scan on a raw client folder.

    python examples/run_intake_scan.py path/to/carpeta --out output/intake_scan

Reads every readable CSV/XLSX file in the folder, classifies it against the
known intake profiles (maestro/ventas/stock/finanzas), evaluates every
registered tool's data-readiness verdict, and ranks the single highest-value
column to request or file to fix next.
"""

from __future__ import annotations

import argparse

from jobs import intake_scan_job


def _print_tool_table(report) -> None:
    print("\nTool readiness:")
    for v in report.verdicts:
        extra = []
        if v.missing_required:
            extra.append(f"missing: {', '.join(v.missing_required)}")
        if v.blocked_required:
            extra.append(f"blocked: {', '.join(v.blocked_required)}")
        detail = f" ({'; '.join(extra)})" if extra else ""
        print(f"  {v.tool_key:<22} {v.state}{detail}")


def _print_ranked_gaps(report) -> None:
    print("\nRanked gaps:")
    if not report.gaps and not report.blocked_fixes:
        print("  nothing to request")
        return
    for g in report.gaps:
        print(f"  request column '{g.column}': unlocks {g.unlocks} tool(s) ({', '.join(g.tools)})")
    for f in report.blocked_fixes:
        print(f"  fix file '{f.filename}': unlocks {f.unlocks} tool(s) ({', '.join(f.tools)})")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_path")
    parser.add_argument("--out", default="output/intake_scan")
    parser.add_argument("--client", default="Client")
    args = parser.parse_args()

    folder = intake_scan_job.prepare(args.data_path)
    report = intake_scan_job.run(folder)
    issues = intake_scan_job.verify(report)

    print(report.summary)
    _print_tool_table(report)
    _print_ranked_gaps(report)

    if issues:
        print("\nQA gate blocked the delivery:")
        for issue in issues:
            print(f"  - {issue}")
        return 1

    paths = intake_scan_job.write_operational(report, args.out, args.client)
    for label, path in paths.items():
        print(f"{label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
