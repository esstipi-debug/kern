"""The three-way match, wired into the core's tool contract.

One ``register()`` call puts source-to-pay through the identical
``prepare -> run -> qa -> deliver`` pipeline the 49 supply-chain capabilities use,
with the same "QA fails => no deliverable" veto and the same never-unprotected
outcome. No orchestrator change, no routing special case, no new plumbing - which
is the point of the exercise: if a second domain needed any of those, the tool
contract would be an SCM contract wearing a generic name.

Input is a single JSON payload rather than three CSVs. An AP feed arrives as
structured data far more often than as spreadsheets, and one file removes the
"which of these three did you forget" failure mode from every support
conversation. ``stdlib json`` keeps the pack free of a pandas dependency it
would otherwise carry for no analytical benefit.
"""

from __future__ import annotations

import csv
import json
from decimal import Decimal, InvalidOperation
from pathlib import Path
from typing import Any, Mapping, Sequence

from nosilo_core.guided import ExecutionOption, GuidedOutcome, HandoffPacket, Residual, as_handoff, as_options
from nosilo_core.llm import LLMProvider
from nosilo_core.registry import Prepared, Produced, Tool, ToolRegistry
from nosilo_core.types import STATUS_NEEDS_DATA, STATUS_OK, JobRequest

from .matching import (
    BLOCK,
    DUPLICATE_INVOICE,
    InvoiceLine,
    MatchRun,
    PurchaseOrderLine,
    ReceiptLine,
    SupplierInvoice,
    Tolerance,
    match_batch,
    to_decimal,
)

TOOL_KEY = "three_way_match"

#: Above this share of invoices needing a human, the run is telling you something
#: about the *data*, not about the invoices - almost always a mis-mapped column
#: or the wrong PO extract. Surfaced rather than silently reported as a result.
DATA_QUALITY_EXCEPTION_RATE = Decimal("80")


class PayloadError(ValueError):
    """Raised when the input payload cannot be read as documents."""


def _decimal(value: Any, field: str, where: str) -> Decimal:
    if value is None:
        raise PayloadError(f"{where}: missing required amount {field!r}")
    try:
        return to_decimal(value)
    except (InvalidOperation, TypeError, ValueError):
        raise PayloadError(f"{where}: {field}={value!r} is not a number") from None


def _require(record: Mapping[str, Any], field: str, where: str) -> Any:
    if field not in record:
        raise PayloadError(f"{where}: missing required field {field!r}")
    return record[field]


def load_documents(
    payload: Mapping[str, Any],
) -> tuple[tuple[SupplierInvoice, ...], tuple[PurchaseOrderLine, ...], tuple[ReceiptLine, ...]]:
    """Turn a decoded JSON payload into typed documents.

    Fails loudly on a malformed record rather than skipping it. A silently
    dropped invoice line is an invoice that matches when it should not, which is
    the one failure mode this tool exists to prevent.
    """
    orders = tuple(
        PurchaseOrderLine(
            po_number=str(_require(row, "po_number", "purchase_orders")),
            line_no=str(_require(row, "line_no", "purchase_orders")),
            sku_code=str(row.get("sku_code", "")),
            ordered_qty=_decimal(_require(row, "ordered_qty", "purchase_orders"), "ordered_qty", "purchase_orders"),
            unit_price=_decimal(_require(row, "unit_price", "purchase_orders"), "unit_price", "purchase_orders"),
            currency=str(row.get("currency", "EUR")),
        )
        for row in payload.get("purchase_orders", ())
    )

    receipts = tuple(
        ReceiptLine(
            receipt_id=str(_require(row, "receipt_id", "receipts")),
            po_number=str(_require(row, "po_number", "receipts")),
            po_line_no=str(_require(row, "po_line_no", "receipts")),
            received_qty=_decimal(_require(row, "received_qty", "receipts"), "received_qty", "receipts"),
        )
        for row in payload.get("receipts", ())
    )

    invoices: list[SupplierInvoice] = []
    for row in payload.get("invoices", ()):
        where = f"invoice {row.get('invoice_number', '?')}"
        lines = tuple(
            InvoiceLine(
                line_no=str(_require(line, "line_no", where)),
                po_number=str(_require(line, "po_number", where)),
                po_line_no=str(_require(line, "po_line_no", where)),
                invoiced_qty=_decimal(_require(line, "invoiced_qty", where), "invoiced_qty", where),
                unit_price=_decimal(_require(line, "unit_price", where), "unit_price", where),
                line_total=_decimal(_require(line, "line_total", where), "line_total", where),
            )
            for line in row.get("lines", ())
        )
        invoices.append(
            SupplierInvoice(
                invoice_number=str(_require(row, "invoice_number", where)),
                supplier_tax_id=str(_require(row, "supplier_tax_id", where)),
                invoice_date=str(row.get("invoice_date", "")),
                currency=str(row.get("currency", "EUR")),
                net_total=_decimal(_require(row, "net_total", where), "net_total", where),
                tax_total=_decimal(row.get("tax_total", 0), "tax_total", where),
                gross_total=_decimal(_require(row, "gross_total", where), "gross_total", where),
                lines=lines,
            )
        )

    return tuple(invoices), orders, receipts


def tolerance_from_params(params: Mapping[str, Any]) -> Tolerance:
    """Read tolerances from job params, falling back to the tight defaults."""
    base = Tolerance()
    return Tolerance(
        price_pct=to_decimal(params.get("price_tolerance_pct", base.price_pct)),
        price_abs=to_decimal(params.get("price_tolerance_abs", base.price_abs)),
        qty_pct=to_decimal(params.get("qty_tolerance_pct", base.qty_pct)),
        qty_abs=to_decimal(params.get("qty_tolerance_abs", base.qty_abs)),
        rounding_abs=to_decimal(params.get("rounding_tolerance_abs", base.rounding_abs)),
    )


# ---- the four stages --------------------------------------------------------


def prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    """Read and validate the payload. No matching happens here."""
    if not request.data_path:
        return Prepared(
            status=STATUS_NEEDS_DATA,
            messages=[
                "three_way_match needs a JSON file with 'invoices', 'purchase_orders' and "
                "optional 'receipts'."
            ],
        )
    try:
        payload = json.loads(Path(request.data_path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return Prepared(status=STATUS_NEEDS_DATA, messages=[f"could not read the payload: {exc}"])

    if not isinstance(payload, Mapping):
        return Prepared(status=STATUS_NEEDS_DATA, messages=["payload must be a JSON object"])

    try:
        invoices, orders, receipts = load_documents(payload)
    except PayloadError as exc:
        return Prepared(status=STATUS_NEEDS_DATA, messages=[str(exc)])

    if not invoices:
        return Prepared(status=STATUS_NEEDS_DATA, messages=["payload contains no invoices to match"])

    return Prepared(
        status=STATUS_OK,
        payload={
            "invoices": invoices,
            "purchase_orders": orders,
            "receipts": receipts,
            "seen": tuple(tuple(pair) for pair in payload.get("already_processed", ())),
        },
    )


def run(prepared: object, params: dict) -> Produced:
    payload = prepared  # type: ignore[assignment]
    run_result = match_batch(
        payload["invoices"],  # type: ignore[index]
        po_lines=payload["purchase_orders"],  # type: ignore[index]
        receipts=payload["receipts"],  # type: ignore[index]
        tolerance=tolerance_from_params(params or {}),
        seen_invoices=payload["seen"],  # type: ignore[index]
    )
    return Produced(report=run_result, summary=_summary(run_result))


def _summary(run_result: MatchRun) -> str:
    total = len(run_result.results)
    return (
        f"{total} invoice(s): {len(run_result.matched)} matched, "
        f"{len(run_result.needs_review)} to review, {len(run_result.blocked)} blocked "
        f"({run_result.touchless_rate:.0f}% touchless, exposure {run_result.exposure})"
    )


def qa(report: object) -> list[str]:
    """Refuse to ship a result that would mislead.

    The interesting one is the exception-rate check. A run where almost nothing
    matches is nearly always a mapping problem, not a supplier problem, and
    delivering it as "94% of your invoices have exceptions" sends an AP team
    chasing ghosts for a day.
    """
    run_result: MatchRun = report  # type: ignore[assignment]
    issues: list[str] = []

    if not run_result.results:
        issues.append("no invoices were matched")
        return issues

    for result in run_result.results:
        if result.matched and result.all_exceptions:
            issues.append(f"invoice {result.invoice_number} reported matched while carrying exceptions")
        if result.blocked and result.status != BLOCK:
            issues.append(f"invoice {result.invoice_number} has a blocking exception but status {result.status!r}")

    exception_rate = Decimal(100) - run_result.touchless_rate
    if exception_rate >= DATA_QUALITY_EXCEPTION_RATE:
        issues.append(
            f"{exception_rate:.0f}% of invoices raised exceptions - this is almost always a data "
            "mapping problem (wrong PO extract, mismatched line numbering), not a supplier problem. "
            "Refusing to deliver a result that would send an AP team chasing ghosts."
        )

    return issues


def deliver(report: object, out_dir: Path, client: str) -> dict[str, Path]:
    run_result: MatchRun = report  # type: ignore[assignment]
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    exceptions_csv = out_dir / "exceptions.csv"
    with exceptions_csv.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["invoice", "supplier", "line", "code", "severity", "delta", "allowed", "detail"])
        for result in run_result.results:
            for exception in result.all_exceptions:
                writer.writerow([
                    result.invoice_number, result.supplier_tax_id, exception.line_no or "",
                    exception.code, exception.severity,
                    "" if exception.delta is None else str(exception.delta),
                    "" if exception.allowed is None else str(exception.allowed),
                    exception.detail,
                ])

    report_md = out_dir / "report.md"
    report_md.write_text(_render_report(run_result, client), encoding="utf-8")
    return {"report": report_md, "exceptions": exceptions_csv}


def _render_report(run_result: MatchRun, client: str) -> str:
    lines = [
        f"# Three-way match - {client}",
        "",
        f"- Invoices processed: **{len(run_result.results)}**",
        f"- Matched without a human: **{len(run_result.matched)}** ({run_result.touchless_rate:.1f}%)",
        f"- To review: **{len(run_result.needs_review)}**",
        f"- Blocked: **{len(run_result.blocked)}**",
        f"- Value at stake across unmatched invoices: **{run_result.exposure}**",
        "",
    ]
    counts = run_result.by_exception_code()
    if counts:
        lines += ["## Exceptions by type", "", "| Code | Invoices |", "|---|---|"]
        lines += [f"| `{code}` | {n} |" for code, n in counts.items()]
        lines.append("")
    if run_result.blocked:
        lines += ["## Blocked - do not pay until resolved", ""]
        for result in run_result.blocked:
            lines.append(f"- **{result.invoice_number}** ({result.supplier_tax_id}): {result.summary()}")
        lines.append("")
    return "\n".join(lines)


def options(report: object) -> GuidedOutcome:
    """The never-unprotected outcome: always a safe next step, never a dead end."""
    run_result: MatchRun = report  # type: ignore[assignment]

    duplicates = [
        result for result in run_result.results
        if DUPLICATE_INVOICE in result.exception_codes
    ]
    if duplicates:
        # A duplicate is a payment about to go out twice. It outranks every other
        # finding in the run, so it is surfaced on its own rather than as one row
        # in a list of ninety exceptions somebody skims.
        return as_handoff(
            summary=f"{len(duplicates)} duplicate invoice(s) detected - hold payment",
            packets=[
                HandoffPacket(
                    title=f"Confirm duplicate: {result.invoice_number}",
                    steps=[
                        f"Check whether {result.invoice_number} from {result.supplier_tax_id} was already paid.",
                        "If already paid, reject the duplicate and notify the supplier.",
                        "If not, clear the flag and re-run the match.",
                    ],
                    data={"invoice": result.invoice_number, "supplier": result.supplier_tax_id},
                )
                for result in duplicates
            ],
            residuals=[
                Residual(
                    description="Confirming against payment history",
                    risk_if_skipped="Paying the same invoice twice",
                )
            ],
        )

    choices = [
        ExecutionOption(
            label=f"Post {len(run_result.matched)} matched invoice(s)",
            summary="Everything within tolerance, no human judgement required.",
            score=float(run_result.touchless_rate),
            recommended=bool(run_result.matched),
        ),
        ExecutionOption(
            label=f"Work the {len(run_result.needs_review)} review queue item(s)",
            summary=f"Price and quantity variances, {run_result.exposure} at stake.",
            score=float(len(run_result.needs_review)),
            recommended=not run_result.matched and bool(run_result.needs_review),
        ),
    ]
    if run_result.blocked:
        choices.append(
            ExecutionOption(
                label=f"Resolve {len(run_result.blocked)} blocked invoice(s) first",
                summary="Self-inconsistent or duplicate documents; no amount on them can be trusted.",
                score=float(len(run_result.blocked)) * 10,
                recommended=True,
            )
        )

    return as_options(
        summary=_summary(run_result),
        options=choices,
        residuals=[
            Residual(
                description="Posting to the ledger",
                risk_if_skipped="Invoices stay unpaid past their due date",
            )
        ],
    )


THREE_WAY_MATCH = Tool(
    key=TOOL_KEY,
    title="Three-way match (source-to-pay)",
    description=(
        "Match supplier invoices against purchase orders and goods receipts, "
        "flagging price and quantity variances, duplicates and self-inconsistent documents."
    ),
    intent_keywords=(
        "three way match", "3 way match", "invoice match", "invoice matching",
        "accounts payable", "ap exception", "invoice exception", "supplier invoice",
        "purchase order match", "goods receipt", "duplicate invoice", "conciliacion de facturas",
    ),
    requires_data=True,
    prepare=prepare,
    run=run,
    qa=qa,
    deliver=deliver,
    options=options,
)


def register(registry: ToolRegistry) -> ToolRegistry:
    """Add the source-to-pay capabilities to a registry.

    The whole integration surface of this pack: one call, no orchestrator edit.
    """
    registry.register(THREE_WAY_MATCH)
    return registry


def build_s2p_tools() -> ToolRegistry:
    """A registry containing only this pack - useful for tests and for running it alone."""
    return register(ToolRegistry())


def summarise(results: Sequence[Any]) -> str:  # pragma: no cover - convenience re-export
    return _summary(MatchRun(results=tuple(results)))
