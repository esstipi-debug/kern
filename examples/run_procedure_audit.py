"""Run the procedure register audit (SOP document control) against an exported register.

    python examples/run_procedure_audit.py data/sample_procedure_register.csv --as-of 2026-09-03
    python examples/run_procedure_audit.py registro.csv --as-of 2026-09-03 --cadence-days 180 --out output/demo

One row per procedure, with at least a name and a last-reviewed date (or their
Spanish equivalents: procedimiento/ultima_revision); owner, approver, version,
status and review_cadence_days are read when present. Always pass --as-of: omit
it and the audit falls back to the server clock, so two runs on different days
can disagree about whether the same procedure is overdue -- the job reports
that as a residual, but the CLI does not force the flag.

Never emits EXECUTED: this reads a register, it never acts on it, and it never
stores the SOPs themselves -- the client's own system stays the system of record.
"""

from __future__ import annotations

import argparse

from jobs import procedure_audit_job


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data_path")
    parser.add_argument(
        "--as-of", default=None,
        help="ISO date (YYYY-MM-DD); omit and the audit falls back to the server clock",
    )
    parser.add_argument(
        "--cadence-days", type=int, default=None,
        help="default review cadence in days for rows with none of their own (convention: 365)",
    )
    parser.add_argument("--out", default="output/procedure_audit")
    parser.add_argument("--client", default="Client")
    args = parser.parse_args()

    params: dict = {}
    if args.as_of:
        params["as_of"] = args.as_of
    if args.cadence_days is not None:
        params["review_cadence_days"] = args.cadence_days

    prepared = procedure_audit_job.prepare(args.data_path, params)
    if prepared.n_dropped_blank_name:
        print(f"filas descartadas por nombre en blanco: {prepared.n_dropped_blank_name}")
    if prepared.n_dates_unparsed:
        print(f"filas sin fecha de ultima revision parseable: {prepared.n_dates_unparsed}")

    report = procedure_audit_job.run(prepared, params)
    issues = procedure_audit_job.verify(report)
    print(report.summary)
    if issues:
        print("QA gate blocked the delivery:")
        for issue in issues:
            print(f"  - {issue}")
        return 1

    if report.findings:
        shown = report.findings[:5]
        print()
        print(f"primeros {len(shown)} hallazgo(s) de {len(report.findings)}:")
        for f in shown:
            due = f" vence {f.due_date.isoformat()}" if f.due_date else ""
            print(f"  - [{f.severity}] {f.name}: {f.detail}{due}")

    paths = procedure_audit_job.write_operational(report, args.out, args.client)
    print()
    for label, path in paths.items():
        print(f"{label}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
