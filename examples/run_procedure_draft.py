"""Render a bilingual SOP draft (Job Breakdown Sheet + control plan + training
timetable) with a document-control header. Always born unapproved -- see RB-17.

    python examples/run_procedure_draft.py --process replenishment --owner "Jefe de Operaciones" \
        --approver "Gerente General" --as-of 2026-09-03

Three templates: replenishment, cycle_count, sop_cycle. Pass --owner and
--approver as two different people -- when they are blank or the same, the job
reports a segregation-of-duties residual instead of failing the QA gate; a
client's staffing gap is not something this tool can fix on its own.

Never emits EXECUTED and never stores the SOP itself: the outcome is always a
HANDOFF asking a human to review, approve and publish the draft in the client's
own system of record.
"""

from __future__ import annotations

import argparse

from jobs import procedure_draft_job


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--process", required=True, help="replenishment | cycle_count | sop_cycle")
    parser.add_argument("--owner", default="")
    parser.add_argument("--approver", default="")
    parser.add_argument("--version", default=None, help="default: 1.0")
    parser.add_argument(
        "--cadence-days", type=int, default=None,
        help="review cadence in days (convention: 365)",
    )
    parser.add_argument(
        "--as-of", default=None,
        help="ISO date (YYYY-MM-DD), the draft's effective date; omit and it falls back to the server clock",
    )
    parser.add_argument("--lang", default="es", help="es | en")
    parser.add_argument("--location", default="", help="where the approved SOP will be published")
    parser.add_argument("--out", default="output/procedure_draft")
    parser.add_argument("--client", default="Client")
    args = parser.parse_args()

    params: dict = {
        "process": args.process,
        "owner": args.owner,
        "approver": args.approver,
        "lang": args.lang,
        "location": args.location,
    }
    if args.version is not None:
        params["version"] = args.version
    if args.cadence_days is not None:
        params["review_cadence_days"] = args.cadence_days
    if args.as_of:
        params["as_of"] = args.as_of

    request = procedure_draft_job.prepare(params)
    draft = procedure_draft_job.run(request)
    issues = procedure_draft_job.verify(draft)
    print(draft.outcome.summary)
    if issues:
        print("QA gate blocked the delivery:")
        for issue in issues:
            print(f"  - {issue}")
        return 1

    if draft.warnings:
        print("advertencias:")
        for w in draft.warnings:
            print(f"  - {w}")

    paths = procedure_draft_job.write_operational(draft, args.out, args.client)
    print()
    for label, path in paths.items():
        print(f"{label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
