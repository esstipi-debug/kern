"""Run the SCOR + ISO 9001/28000 evidence pack (E4). Not a certificate.

    python examples/run_scor_audit.py tests/fixtures/audit/scor_pack.csv --dso 45 --dpo 30
    python examples/run_scor_audit.py pack.csv --as-of 2026-09-03 --out output/scor

Rows must carry a ``dataset`` tag: financial / cost_to_serve / sourcing /
orders / procedures / risk. Kern composes the existing jobs and wraps one
EvidenceRecord + Lineage sheet. Checklist statuses are observed / gap /
no_evaluable -- never "compliant" or "certified".

Always HANDOFF: a registrar's auditor concludes. Kern does not.
"""

from __future__ import annotations

import argparse

from jobs import scor_audit_job


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_path")
    parser.add_argument("--as-of", default="2026-09-03")
    parser.add_argument("--dso", type=float, default=None, dest="dso_days")
    parser.add_argument("--dpo", type=float, default=None, dest="dpo_days")
    parser.add_argument("--out", default="output/scor_iso_audit")
    parser.add_argument("--client", default="Client")
    args = parser.parse_args()

    params: dict = {
        "as_of": args.as_of,
        "produced_at": "2026-09-07T12:00:00Z",
        "run_id": "cli-scor-iso-audit",
    }
    if args.dso_days is not None:
        params["dso_days"] = args.dso_days
    if args.dpo_days is not None:
        params["dpo_days"] = args.dpo_days

    prepared = scor_audit_job.prepare(args.data_path, params)
    report = scor_audit_job.run(prepared, params)
    issues = scor_audit_job.verify(report)
    print(report.summary)
    if issues:
        print("QA gate blocked the delivery:")
        for issue in issues:
            print(f"  - {issue}")
        return 1

    paths = scor_audit_job.write_operational(report, args.out, args.client)
    print()
    for label, path in paths.items():
        print(f"{label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
