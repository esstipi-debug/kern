"""Operator CLI: review -> approve -> apply (or roll back) a staged Excel replenishment.

Closes the last gap in the planilla loop: the agent stages the plan, but a human
executes it - from a terminal, in one command, with the exact before/after on
screen before anything is written.

    # Review the plan and apply it (asks for confirmation):
    python examples/apply_replenishment.py --file planilla_cliente.xlsx

    # Unattended (e.g. after reviewing the deliverables):
    python examples/apply_replenishment.py --file planilla_cliente.xlsx --yes

    # Undo a previous apply (the key is printed by the apply run):
    python examples/apply_replenishment.py --file planilla_cliente.xlsx \
        --rollback excel-replenish-abc123def456

The plan is re-staged from the CURRENT file: the idempotency key derives from the
staged content, so an unchanged file re-stages the identical changeset, while any
edit since the last look produces a new plan you approve fresh - you always approve
exactly what will be written. Idempotency and rollback survive across invocations
via a persistent SQLite ledger. All prints are ASCII (Windows consoles).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

# Make repo packages importable no matter where this script is launched from.
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from jobs import excel_replenishment_job as job  # noqa: E402
from src import writeback  # noqa: E402
from src.connectors.excel import ExcelWorkbookStore, ExcelWritebackError  # noqa: E402
from src.writeback_store import DEFAULT_PATH as LEDGER_DEFAULT_PATH  # noqa: E402
from src.writeback_store import SqliteAuditLedger  # noqa: E402


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Review, approve and apply (or roll back) a staged Excel replenishment.")
    p.add_argument("--file", required=True, help="the client's inventory Excel file (.xlsx/.xlsm)")
    p.add_argument("--sheet", default=None, help="sheet name (default: auto-detect)")
    p.add_argument("--cover-periods", type=float, default=8.0)
    p.add_argument("--order-up-to-factor", type=float, default=2.0)
    p.add_argument("--ledger", default=LEDGER_DEFAULT_PATH,
                   help="SQLite audit ledger (idempotency + rollback across runs)")
    p.add_argument("--approver", default="operator", help="name recorded on the approval")
    p.add_argument("--yes", action="store_true", help="apply without the interactive confirmation")
    p.add_argument("--rollback", default=None, metavar="KEY",
                   help="roll back a previously applied changeset instead of applying")
    return p


def _print_plan(report) -> None:
    print(report.summary)
    print()
    print(f"{'SKU':<20} {'on hand':>10} {'target':>10} {'order':>10}")
    for ln in report.lines:
        marker = "  <-- order" if ln.restock_qty > 0 else ""
        print(f"{ln.sku:<20} {ln.on_hand:>10.1f} {ln.target:>10.1f} {ln.restock_qty:>10.1f}{marker}")
    print()


def _print_changes(changeset) -> None:
    print(f"Staged changeset [{changeset.risk_tier}] key={changeset.idempotency_key}")
    for c in changeset.changes:
        tag = " (guard)" if c.before == c.after else ""
        print(f"  {c.entity_id}!{c.field}: {c.before!r} -> {c.after!r}{tag}")
    print()


def _rollback(args) -> int:
    store = ExcelWorkbookStore(args.file, ledger=SqliteAuditLedger(args.ledger))
    try:
        store.rollback(args.rollback)
    except KeyError:
        print(f"unknown changeset key: {args.rollback} (was it applied with this ledger?)")
        return 1
    except ExcelWritebackError as exc:
        print(f"rollback failed safely: {exc}")
        return 1
    print(f"rolled back {args.rollback}: {Path(args.file).name} restored to its pre-apply values.")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.rollback:
        return _rollback(args)

    params: dict = {"ledger_path": args.ledger}
    if args.sheet:
        params["sheet"] = args.sheet
    try:
        payload = job.prepare(args.file, params)
        report = job.run(payload, cover_periods=args.cover_periods,
                         order_up_to_factor=args.order_up_to_factor)
    except (ValueError, FileNotFoundError) as exc:
        print(f"cannot plan: {exc}")
        return 1

    _print_plan(report)
    cs = report.changeset
    if cs is None:
        print("Nothing to write: every SKU is at or above target.")
        return 0
    if cs.is_noop:
        print("The planilla already carries exactly this plan - nothing to change.")
        return 0
    _print_changes(cs)

    if not args.yes:
        answer = input(f"Apply these changes to {report.filename}? [y/N] ").strip().lower()
        if answer not in ("y", "yes", "s", "si"):
            print("aborted: nothing was written.")
            return 1

    approval = writeback.approve(cs, args.approver)
    try:
        result = writeback.apply(payload["store"], cs, approval=approval)
    except (ExcelWritebackError, writeback.WritebackRefused) as exc:
        print(f"apply refused safely (nothing written): {exc}")
        return 1

    if result.idempotent_skip:
        print(f"already applied earlier (key={cs.idempotency_key}) - skipped, nothing re-written.")
        return 0
    print(f"applied: {report.n_restock} SKU(s) written to {report.filename} "
          f"(key={cs.idempotency_key}).")
    print("A byte-exact backup of the original sits next to the file; undo any time with:")
    print(f"  python examples/apply_replenishment.py --file \"{args.file}\" "
          f"--ledger \"{args.ledger}\" --rollback {cs.idempotency_key}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
