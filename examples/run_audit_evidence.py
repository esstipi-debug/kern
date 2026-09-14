"""Run the MUS + GL tie-out + IPE workpaper (never a signed audit opinion).

    python examples/run_audit_evidence.py tests/fixtures/audit/population.csv --tm 20000
    python examples/run_audit_evidence.py gl.csv --tm 20000 --seed 0 --out output/audit

One row per inventory/COGS unit with unit_id + book_value. Optional ``source``
column (gl / subledger) puts both ledgers in one table -- MCP cannot pass
subledger_path. Always pass --tm: tolerable misstatement is auditor judgment;
Kern never derives it.

Never EXECUTED: the workpaper is not audit evidence until an auditor of record
concludes and signs (AS 1215). Prepared-by is the engine identity; Reviewed-by
stays blank (the HANDOFF).
"""

from __future__ import annotations

import argparse

from jobs import audit_evidence_job


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_path")
    parser.add_argument(
        "--tm", type=float, required=True, dest="tolerable_misstatement",
        help="tolerable misstatement (auditor judgment; required)",
    )
    parser.add_argument("--ria", type=float, default=0.05, dest="risk_of_incorrect_acceptance")
    parser.add_argument("--expected", type=float, default=0.0, dest="expected_misstatement")
    parser.add_argument("--tolerance", type=float, default=0.0)
    parser.add_argument("--seed", type=int, default=0, dest="selection_seed")
    parser.add_argument("--out", default="output/audit_evidence")
    parser.add_argument("--client", default="Client")
    args = parser.parse_args()

    params = {
        "tolerable_misstatement": args.tolerable_misstatement,
        "risk_of_incorrect_acceptance": args.risk_of_incorrect_acceptance,
        "expected_misstatement": args.expected_misstatement,
        "tolerance": args.tolerance,
        "selection_seed": args.selection_seed,
        "produced_at": "2026-09-07T12:00:00Z",
        "run_id": "cli-audit-evidence",
    }

    prepared = audit_evidence_job.prepare(args.data_path, params)
    report = audit_evidence_job.run(prepared, params)
    issues = audit_evidence_job.verify(report)
    print(report.summary)
    if issues:
        print("QA gate blocked the delivery:")
        for issue in issues:
            print(f"  - {issue}")
        return 1

    paths = audit_evidence_job.write_operational(report, args.out, args.client)
    print()
    for label, path in paths.items():
        print(f"{label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
