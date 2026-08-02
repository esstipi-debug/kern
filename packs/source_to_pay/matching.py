"""Three-way match: purchase order, goods receipt, supplier invoice.

The core of accounts-payable automation, and the reason an AP team's day looks
the way it does. For every invoice line the question is the same: did we order
this, did we receive it, and is the supplier billing what was agreed? Everything
that fails one of those is an *exception*, and exceptions are where the entire
cost of the function lives - a matched invoice costs almost nothing to process,
an exception costs a person twenty minutes and two emails.

Design notes that matter more than they look:

**Every amount is ``Decimal``.** An invoice-matching engine built on binary
floats will disagree with the supplier's arithmetic by a cent somewhere and
raise an exception a human then has to dismiss, which is worse than useless: it
trains people to click through exceptions. ``0.1 + 0.2 != 0.3`` is not an
acceptable property for something that decides whether to pay a bill.

**Tolerances take whichever bound is greater**, absolute or percentage. That is
the AP convention: a 2% tolerance is meaningless on a €3 line, and a €5 absolute
tolerance is meaningless on a €50,000 one. Stated here because the other reading
("whichever is smaller", i.e. stricter) also exists in the wild and the choice
silently changes which invoices need a human.

**Severity is about consequence, not size.** A duplicate invoice blocks however
small it is, because the failure mode is paying twice. A price variance of the
same amount only asks for review, because the failure mode is overpaying once
and it is recoverable.

Pure and clock-free: everything the match needs - including which invoices have
been seen before - arrives as an argument. That makes the whole engine
replayable, which is what lets a disputed decision be reconstructed months later.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Iterable, Mapping, Sequence

# ---- outcome vocabulary -----------------------------------------------------

#: The line agrees with the order and the receipt, within tolerance.
MATCHED = "matched"
#: The line has at least one exception.
EXCEPTION = "exception"

#: Do not pay until a human resolves it. Reserved for failures whose cost is not
#: recoverable by paying later (duplicates) or where the document contradicts
#: itself and no amount can be trusted (arithmetic, currency).
BLOCK = "block"
#: Pay-ability is a judgement call; route to a human but do not stop the world.
REVIEW = "review"
#: Worth recording, not worth anyone's time on its own.
INFO = "info"

SEVERITY_RANK = {INFO: 0, REVIEW: 1, BLOCK: 2}

# Exception codes. An enumeration rather than free text so they can be counted,
# routed and translated - "what are our top three exception types this month" is
# the first question any AP lead asks, and free text cannot answer it.
NO_PO = "no_purchase_order"
NO_PO_LINE = "no_purchase_order_line"
NO_RECEIPT = "no_goods_receipt"
QTY_OVER_RECEIPT = "quantity_over_receipt"
QTY_OVER_ORDER = "quantity_over_order"
PRICE_VARIANCE = "price_variance"
LINE_ARITHMETIC = "line_arithmetic"
INVOICE_ARITHMETIC = "invoice_arithmetic"
CURRENCY_MISMATCH = "currency_mismatch"
DUPLICATE_INVOICE = "duplicate_invoice"

#: Which failures stop payment outright. Everything else is REVIEW.
BLOCKING_CODES = frozenset({DUPLICATE_INVOICE, CURRENCY_MISMATCH, LINE_ARITHMETIC, INVOICE_ARITHMETIC})

ZERO = Decimal("0")


def to_decimal(value: Decimal | int | float | str) -> Decimal:
    """Coerce to ``Decimal`` via ``str`` so binary float never leaks in."""
    return value if isinstance(value, Decimal) else Decimal(str(value))


# ---- documents --------------------------------------------------------------


@dataclass(frozen=True)
class PurchaseOrderLine:
    """What was agreed: quantity and unit price, per PO line."""

    po_number: str
    line_no: str
    sku_code: str
    ordered_qty: Decimal
    unit_price: Decimal
    currency: str = "EUR"

    @property
    def key(self) -> tuple[str, str]:
        return (self.po_number, self.line_no)


@dataclass(frozen=True)
class ReceiptLine:
    """What physically arrived, against a PO line.

    Several receipts can hit one PO line (partial deliveries), so the matcher
    sums them rather than expecting one.
    """

    receipt_id: str
    po_number: str
    po_line_no: str
    received_qty: Decimal

    @property
    def key(self) -> tuple[str, str]:
        return (self.po_number, self.po_line_no)


@dataclass(frozen=True)
class InvoiceLine:
    """What the supplier is billing, and which PO line it claims to be for."""

    line_no: str
    po_number: str
    po_line_no: str
    invoiced_qty: Decimal
    unit_price: Decimal
    line_total: Decimal

    @property
    def po_key(self) -> tuple[str, str]:
        return (self.po_number, self.po_line_no)


@dataclass(frozen=True)
class SupplierInvoice:
    """One invoice, as extracted from a document or pulled from a portal."""

    invoice_number: str
    supplier_tax_id: str
    invoice_date: str
    currency: str
    net_total: Decimal
    tax_total: Decimal
    gross_total: Decimal
    lines: tuple[InvoiceLine, ...] = ()

    @property
    def identity(self) -> tuple[str, str]:
        """What makes two invoices "the same invoice" for duplicate detection.

        Supplier plus invoice number, not the amount: a supplier legitimately
        issues two invoices for the same amount on the same day, and re-sending
        the same invoice number is the actual duplicate.
        """
        return (self.supplier_tax_id, self.invoice_number)


@dataclass(frozen=True)
class Tolerance:
    """Per-line allowances before a difference becomes an exception.

    Defaults are deliberately tight (1% price, no quantity slack) rather than
    permissive: a tolerance nobody chose should not be the reason an overcharge
    is paid silently. ``rounding_abs`` is separate and small - it exists to
    absorb the last-cent disagreements every tax calculation produces, not to
    excuse real differences.
    """

    price_pct: Decimal = Decimal("1.0")
    price_abs: Decimal = ZERO
    qty_pct: Decimal = ZERO
    qty_abs: Decimal = ZERO
    rounding_abs: Decimal = Decimal("0.02")

    def price_allowance(self, reference: Decimal) -> Decimal:
        """Whichever bound is greater - see the module docstring."""
        return max(self.price_abs, (abs(reference) * self.price_pct) / Decimal(100))

    def qty_allowance(self, reference: Decimal) -> Decimal:
        return max(self.qty_abs, (abs(reference) * self.qty_pct) / Decimal(100))


# ---- findings ---------------------------------------------------------------


@dataclass(frozen=True)
class MatchException:
    """One thing wrong, with the numbers that make it actionable.

    ``delta`` and ``allowed`` are carried so a reviewer sees *how far* out it is
    without recomputing - the difference between "reject" and "waive" is almost
    always magnitude, and making someone do the subtraction is how exceptions
    end up rubber-stamped.
    """

    code: str
    severity: str
    line_no: str | None = None
    detail: str = ""
    delta: Decimal | None = None
    allowed: Decimal | None = None

    @property
    def blocks(self) -> bool:
        return self.severity == BLOCK


@dataclass(frozen=True)
class LineMatch:
    """The verdict for one invoice line."""

    line_no: str
    status: str
    exceptions: tuple[MatchException, ...] = ()
    ordered_qty: Decimal | None = None
    received_qty: Decimal | None = None
    invoiced_qty: Decimal | None = None
    price_delta: Decimal | None = None
    value_delta: Decimal = ZERO

    @property
    def matched(self) -> bool:
        return self.status == MATCHED


@dataclass(frozen=True)
class MatchResult:
    """The verdict for a whole invoice."""

    invoice_number: str
    supplier_tax_id: str
    status: str
    lines: tuple[LineMatch, ...] = ()
    invoice_exceptions: tuple[MatchException, ...] = ()
    total_value_delta: Decimal = ZERO

    @property
    def all_exceptions(self) -> tuple[MatchException, ...]:
        """Invoice-level first, then per line - severity order within each."""
        collected = list(self.invoice_exceptions)
        for line in self.lines:
            collected.extend(line.exceptions)
        return tuple(sorted(collected, key=lambda e: (-SEVERITY_RANK[e.severity], e.code, e.line_no or "")))

    @property
    def blocked(self) -> bool:
        return any(e.blocks for e in self.all_exceptions)

    @property
    def matched(self) -> bool:
        return not self.all_exceptions

    @property
    def exception_codes(self) -> tuple[str, ...]:
        return tuple(sorted({e.code for e in self.all_exceptions}))

    def summary(self) -> str:
        if self.matched:
            return f"invoice {self.invoice_number}: {len(self.lines)} line(s) matched"
        counts: dict[str, int] = {}
        for exception in self.all_exceptions:
            counts[exception.code] = counts.get(exception.code, 0) + 1
        detail = ", ".join(f"{code} x{n}" for code, n in sorted(counts.items()))
        return f"invoice {self.invoice_number}: {self.status} - {detail}"


# ---- the match --------------------------------------------------------------


def _severity_for(code: str) -> str:
    return BLOCK if code in BLOCKING_CODES else REVIEW


def match_invoice(
    invoice: SupplierInvoice,
    *,
    po_lines: Iterable[PurchaseOrderLine],
    receipts: Iterable[ReceiptLine] = (),
    tolerance: Tolerance | None = None,
    seen_invoices: Iterable[tuple[str, str]] = (),
) -> MatchResult:
    """Match one invoice against its orders and receipts.

    ``seen_invoices`` is passed in rather than looked up so the engine stays
    pure: the caller owns "what have we processed before", and the same inputs
    always produce the same verdict.
    """
    allowances = tolerance or Tolerance()
    orders: Mapping[tuple[str, str], PurchaseOrderLine] = {line.key: line for line in po_lines}

    received: dict[tuple[str, str], Decimal] = {}
    for receipt in receipts:
        received[receipt.key] = received.get(receipt.key, ZERO) + to_decimal(receipt.received_qty)

    invoice_exceptions: list[MatchException] = []

    if invoice.identity in set(seen_invoices):
        invoice_exceptions.append(
            MatchException(
                code=DUPLICATE_INVOICE,
                severity=BLOCK,
                detail=(
                    f"invoice {invoice.invoice_number} from {invoice.supplier_tax_id} "
                    "has already been processed"
                ),
            )
        )

    invoice_exceptions.extend(_check_invoice_arithmetic(invoice, allowances))

    line_matches = tuple(
        _match_line(line, invoice, orders, received, allowances) for line in invoice.lines
    )

    total_delta = sum((line.value_delta for line in line_matches), ZERO)
    exceptions = tuple(invoice_exceptions)
    blocked = any(e.blocks for e in exceptions) or any(
        e.blocks for line in line_matches for e in line.exceptions
    )
    has_any = bool(exceptions) or any(line.exceptions for line in line_matches)

    return MatchResult(
        invoice_number=invoice.invoice_number,
        supplier_tax_id=invoice.supplier_tax_id,
        status=BLOCK if blocked else (REVIEW if has_any else MATCHED),
        lines=line_matches,
        invoice_exceptions=exceptions,
        total_value_delta=total_delta,
    )


def _check_invoice_arithmetic(invoice: SupplierInvoice, allowances: Tolerance) -> list[MatchException]:
    """Does the invoice agree with itself?

    Checked before anything else: if a document's own totals do not add up, no
    figure on it can be trusted, and matching its lines against a PO would be
    answering the wrong question.
    """
    found: list[MatchException] = []
    tolerance = to_decimal(allowances.rounding_abs)

    net = to_decimal(invoice.net_total)
    tax = to_decimal(invoice.tax_total)
    gross = to_decimal(invoice.gross_total)

    gross_delta = abs((net + tax) - gross)
    if gross_delta > tolerance:
        found.append(
            MatchException(
                code=INVOICE_ARITHMETIC,
                severity=BLOCK,
                detail=f"net {net} + tax {tax} != gross {gross}",
                delta=gross_delta,
                allowed=tolerance,
            )
        )

    if invoice.lines:
        lines_total = sum((to_decimal(line.line_total) for line in invoice.lines), ZERO)
        net_delta = abs(lines_total - net)
        if net_delta > tolerance:
            found.append(
                MatchException(
                    code=INVOICE_ARITHMETIC,
                    severity=BLOCK,
                    detail=f"line totals {lines_total} != net total {net}",
                    delta=net_delta,
                    allowed=tolerance,
                )
            )
    return found


def _match_line(
    line: InvoiceLine,
    invoice: SupplierInvoice,
    orders: Mapping[tuple[str, str], PurchaseOrderLine],
    received: Mapping[tuple[str, str], Decimal],
    allowances: Tolerance,
) -> LineMatch:
    found: list[MatchException] = []
    invoiced_qty = to_decimal(line.invoiced_qty)
    unit_price = to_decimal(line.unit_price)
    line_total = to_decimal(line.line_total)

    arithmetic_delta = abs((invoiced_qty * unit_price) - line_total)
    if arithmetic_delta > to_decimal(allowances.rounding_abs):
        found.append(
            MatchException(
                code=LINE_ARITHMETIC,
                severity=BLOCK,
                line_no=line.line_no,
                detail=f"{invoiced_qty} x {unit_price} != {line_total}",
                delta=arithmetic_delta,
                allowed=to_decimal(allowances.rounding_abs),
            )
        )

    order = orders.get(line.po_key)
    if order is None:
        code = NO_PO if not any(key[0] == line.po_number for key in orders) else NO_PO_LINE
        found.append(
            MatchException(
                code=code,
                severity=_severity_for(code),
                line_no=line.line_no,
                detail=f"invoice line references {line.po_number}/{line.po_line_no}, which is not on file",
            )
        )
        return LineMatch(
            line_no=line.line_no,
            status=EXCEPTION,
            exceptions=tuple(found),
            invoiced_qty=invoiced_qty,
            value_delta=line_total,
        )

    if order.currency != invoice.currency:
        found.append(
            MatchException(
                code=CURRENCY_MISMATCH,
                severity=BLOCK,
                line_no=line.line_no,
                detail=f"order is in {order.currency}, invoice in {invoice.currency}",
            )
        )

    ordered_qty = to_decimal(order.ordered_qty)
    order_price = to_decimal(order.unit_price)
    receipt_qty = received.get(line.po_key)

    price_delta = unit_price - order_price
    price_allowed = allowances.price_allowance(order_price)
    if abs(price_delta) > price_allowed:
        found.append(
            MatchException(
                code=PRICE_VARIANCE,
                severity=REVIEW,
                line_no=line.line_no,
                detail=f"invoiced {unit_price} vs ordered {order_price}",
                delta=price_delta,
                allowed=price_allowed,
            )
        )

    if receipt_qty is None:
        found.append(
            MatchException(
                code=NO_RECEIPT,
                severity=REVIEW,
                line_no=line.line_no,
                detail=f"nothing received against {line.po_number}/{line.po_line_no}",
                delta=invoiced_qty,
            )
        )
    else:
        over_receipt = invoiced_qty - receipt_qty
        if over_receipt > allowances.qty_allowance(receipt_qty):
            found.append(
                MatchException(
                    code=QTY_OVER_RECEIPT,
                    severity=REVIEW,
                    line_no=line.line_no,
                    detail=f"invoiced {invoiced_qty} but received {receipt_qty}",
                    delta=over_receipt,
                    allowed=allowances.qty_allowance(receipt_qty),
                )
            )

    over_order = invoiced_qty - ordered_qty
    if over_order > allowances.qty_allowance(ordered_qty):
        found.append(
            MatchException(
                code=QTY_OVER_ORDER,
                severity=REVIEW,
                line_no=line.line_no,
                detail=f"invoiced {invoiced_qty} but ordered {ordered_qty}",
                delta=over_order,
                allowed=allowances.qty_allowance(ordered_qty),
            )
        )

    # What this line costs us versus what was agreed, at the quantity billed.
    value_delta = (unit_price - order_price) * invoiced_qty

    return LineMatch(
        line_no=line.line_no,
        status=EXCEPTION if found else MATCHED,
        exceptions=tuple(found),
        ordered_qty=ordered_qty,
        received_qty=receipt_qty,
        invoiced_qty=invoiced_qty,
        price_delta=price_delta,
        value_delta=value_delta,
    )


@dataclass(frozen=True)
class MatchRun:
    """A batch of invoices matched together, with the numbers a lead asks for."""

    results: tuple[MatchResult, ...] = ()
    tolerance: Tolerance = field(default_factory=Tolerance)

    @property
    def matched(self) -> tuple[MatchResult, ...]:
        return tuple(r for r in self.results if r.matched)

    @property
    def blocked(self) -> tuple[MatchResult, ...]:
        return tuple(r for r in self.results if r.blocked)

    @property
    def needs_review(self) -> tuple[MatchResult, ...]:
        return tuple(r for r in self.results if not r.matched and not r.blocked)

    @property
    def touchless_rate(self) -> Decimal:
        """Share of invoices that need no human at all - the headline AP metric."""
        if not self.results:
            return ZERO
        return (Decimal(len(self.matched)) / Decimal(len(self.results))) * Decimal(100)

    @property
    def exposure(self) -> Decimal:
        """Total value at stake across everything not matched."""
        return sum((r.total_value_delta for r in self.results if not r.matched), ZERO)

    def by_exception_code(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for result in self.results:
            for code in result.exception_codes:
                counts[code] = counts.get(code, 0) + 1
        return dict(sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])))


def match_batch(
    invoices: Sequence[SupplierInvoice],
    *,
    po_lines: Iterable[PurchaseOrderLine],
    receipts: Iterable[ReceiptLine] = (),
    tolerance: Tolerance | None = None,
    seen_invoices: Iterable[tuple[str, str]] = (),
) -> MatchRun:
    """Match a batch, treating earlier invoices in it as "already seen".

    Duplicate detection therefore catches a repeat *inside* the same batch, not
    only against history - which is the common case when a supplier re-sends a
    statement and the whole file gets ingested twice.
    """
    orders = tuple(po_lines)
    receipt_lines = tuple(receipts)
    allowances = tolerance or Tolerance()
    history = set(seen_invoices)

    results: list[MatchResult] = []
    for invoice in invoices:
        results.append(
            match_invoice(
                invoice,
                po_lines=orders,
                receipts=receipt_lines,
                tolerance=allowances,
                seen_invoices=history,
            )
        )
        history.add(invoice.identity)

    return MatchRun(results=tuple(results), tolerance=allowances)
