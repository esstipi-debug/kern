"""What an AP exception is worth, in money, and when it is worth nothing knowable.

``MatchRun.exposure`` answers a different question than the one a recovery engagement
is paid on. It sums ``LineMatch.value_delta``, and ``value_delta`` is *price* variance
only. Measured on a real fixture: a duplicated invoice of 1.190.000 plus eight units
billed and never received produced ``exposure == 0``. The two findings a client
actually gets money back for were worth zero in the only money figure the pack exposed.

This module prices each exception separately, and its whole contract is one rule:

    **An amount is a number with its derivation, or it is explicitly not quantified.
    Never a silent zero.**

That is the same shape as ``jobs/cross_impact_job.py``, which degrades to NO_EVALUABLE
rather than assume a decision point nobody chose. Here the stakes are higher than a
report: an unprovable claim invoiced as recovered is how the engagement, the client and
the reference are lost on the same afternoon.

Two decisions worth stating because they are not obvious:

**Net, never gross.** The VAT on an overcharge was already taken as a fiscal credit, so
a fee computed on the gross bills the client for money that was never lost.

**A quantity fault and a price fault on one line overlap.** Billed 12 at 19.800 against
an order of 12 at 18.500 with 9 received: charging the 1.300 overcharge on all twelve
*and* the full 19.800 on the three phantom units counts three units twice. The split is
overcharge on what arrived, full invoiced price on what did not.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import Iterable, Sequence

from .matching import (
    CURRENCY_MISMATCH,
    DUPLICATE_INVOICE,
    INVOICE_ARITHMETIC,
    LINE_ARITHMETIC,
    NO_PO,
    NO_PO_LINE,
    NO_RECEIPT,
    PRICE_VARIANCE,
    QTY_OVER_ORDER,
    QTY_OVER_RECEIPT,
    ZERO,
    LineMatch,
    MatchException,
    MatchResult,
    MatchRun,
    SupplierInvoice,
)

#: Why each unprovable code cannot be priced. Absence of an entry is a bug, not a zero:
#: ``_unquantified`` raises rather than invent a reason for a code nobody classified.
_WHY_NOT_QUANTIFIED = {
    NO_PO: (
        "the invoice cites a purchase order that is not on file: the amount at stake is "
        "the whole line, but nothing proves it was not agreed elsewhere"
    ),
    NO_PO_LINE: (
        "the purchase order exists and the cited line does not: the line may have been "
        "agreed under a different number"
    ),
    NO_RECEIPT: (
        "nothing was received against this line, which is either goods never delivered "
        "or a receipt never keyed. The two are worth very different amounts"
    ),
    LINE_ARITHMETIC: (
        "quantity x unit price does not equal the line total, so it is unknown which of "
        "the three figures is the wrong one"
    ),
    INVOICE_ARITHMETIC: (
        "the invoice totals do not add up, so no single line can be held responsible"
    ),
    CURRENCY_MISMATCH: (
        "order and invoice are in different currencies: pricing this needs a rate and a "
        "date that nobody supplied"
    ),
}


@dataclass(frozen=True)
class RecoveryItem:
    """One finding, priced or explicitly not priced.

    ``basis`` is the arithmetic in words. It is what lets the client's accountant
    disprove the claim without us - and a number a buyer cannot check alone is an
    assertion, which is what every failed AP recovery pitch is made of.
    """

    code: str
    severity: str
    line_no: str | None
    quantified: bool
    amount: Decimal | None
    basis: str
    reason: str | None = None

    def __post_init__(self) -> None:
        if self.quantified and self.amount is None:
            raise ValueError(f"{self.code}: quantified item without an amount")
        if not self.quantified and self.amount is not None:
            raise ValueError(f"{self.code}: unquantified item carrying an amount")
        if not self.quantified and not self.reason:
            raise ValueError(f"{self.code}: unquantified item without a reason")


@dataclass(frozen=True)
class InvoiceRecovery:
    """Everything recoverable on one invoice, and everything that could not be priced."""

    invoice_number: str
    supplier_tax_id: str
    items: tuple[RecoveryItem, ...] = ()

    @property
    def quantified_total(self) -> Decimal:
        return sum((i.amount for i in self.items if i.quantified and i.amount), ZERO)

    @property
    def unquantified_count(self) -> int:
        return sum(1 for i in self.items if not i.quantified)


@dataclass(frozen=True)
class RunRecovery:
    """The batch. The headline is what is defensible; the rest stays visible."""

    invoices: tuple[InvoiceRecovery, ...] = ()

    @property
    def quantified_total(self) -> Decimal:
        return sum((i.quantified_total for i in self.invoices), ZERO)

    @property
    def unquantified_count(self) -> int:
        return sum(i.unquantified_count for i in self.invoices)


def _unquantified(exception: MatchException, basis: str) -> RecoveryItem:
    reason = _WHY_NOT_QUANTIFIED.get(exception.code)
    if reason is None:
        raise KeyError(
            f"{exception.code} has no quantification rule and no reason for lacking one; "
            "classify it explicitly rather than letting it fall through as zero"
        )
    return RecoveryItem(
        code=exception.code,
        severity=exception.severity,
        line_no=exception.line_no,
        quantified=False,
        amount=None,
        basis=basis,
        reason=reason,
    )


def _price_variance(exception: MatchException, line: LineMatch) -> RecoveryItem:
    """The overcharge, on the units that actually arrived.

    When no receipt is on file the billed quantity is the only figure available; the
    ``no_goods_receipt`` finding sits alongside and stays unquantified, so the split
    still tells the reader what is known.
    """
    delta = exception.delta if exception.delta is not None else ZERO
    if delta <= ZERO:
        return RecoveryItem(
            code=exception.code,
            severity=exception.severity,
            line_no=exception.line_no,
            quantified=True,
            amount=ZERO,
            basis=(
                f"invoiced below the ordered price by {abs(delta)} per unit: nothing to "
                "recover, the difference is in the client's favour"
            ),
        )
    priced_qty = line.received_qty if line.received_qty is not None else line.invoiced_qty
    priced_qty = priced_qty if priced_qty is not None else ZERO
    return RecoveryItem(
        code=exception.code,
        severity=exception.severity,
        line_no=exception.line_no,
        quantified=True,
        amount=delta * priced_qty,
        basis=(
            f"{delta} overcharged per unit x {priced_qty} unit(s) "
            f"{'received' if line.received_qty is not None else 'invoiced'} = {delta * priced_qty}"
        ),
    )


def _excess_units(line: LineMatch) -> tuple[str | None, Decimal]:
    """The units billed beyond what is owed, counted once.

    Over-receipt and over-order describe the same excess seen from two sides. Adding
    them bills the client twice for one fault, so the wider of the two wins and the
    other is reported at zero *with the reason stated*, not dropped.
    """
    codes = {e.code: e for e in line.exceptions}
    candidates: list[tuple[str, Decimal]] = []
    for code in (QTY_OVER_RECEIPT, QTY_OVER_ORDER):
        exception = codes.get(code)
        if exception is not None and exception.delta is not None:
            candidates.append((code, exception.delta))
    if not candidates:
        return None, ZERO
    return max(candidates, key=lambda pair: pair[1])


def _quantity_items(line: LineMatch) -> list[RecoveryItem]:
    dominant_code, excess = _excess_units(line)
    if dominant_code is None:
        return []
    unit_price = line.invoiced_unit_price if line.invoiced_unit_price is not None else ZERO
    items: list[RecoveryItem] = []
    for exception in line.exceptions:
        if exception.code not in (QTY_OVER_RECEIPT, QTY_OVER_ORDER):
            continue
        if exception.code == dominant_code:
            items.append(
                RecoveryItem(
                    code=exception.code,
                    severity=exception.severity,
                    line_no=exception.line_no,
                    quantified=True,
                    amount=excess * unit_price,
                    basis=f"{excess} unit(s) billed and not owed x {unit_price} invoiced = {excess * unit_price}",
                )
            )
        else:
            items.append(
                RecoveryItem(
                    code=exception.code,
                    severity=exception.severity,
                    line_no=exception.line_no,
                    quantified=True,
                    amount=ZERO,
                    basis=(
                        f"the same excess is already priced under {dominant_code} "
                        f"({excess} unit(s)); counted once, not twice"
                    ),
                )
            )
    return items


def recover_invoice(result: MatchResult, invoice: SupplierInvoice) -> InvoiceRecovery:
    """Price every exception on one invoice, or say why it cannot be priced."""
    items: list[RecoveryItem] = []

    for exception in result.invoice_exceptions:
        if exception.code == DUPLICATE_INVOICE:
            items.append(
                RecoveryItem(
                    code=exception.code,
                    severity=exception.severity,
                    line_no=None,
                    quantified=True,
                    amount=invoice.net_total,
                    basis=(
                        f"the whole net amount of a document already processed: "
                        f"{invoice.net_total} (net of VAT, which was taken as a fiscal credit)"
                    ),
                )
            )
        else:
            items.append(
                _unquantified(exception, basis=f"invoice-level finding: {exception.detail}")
            )

    for line in result.lines:
        items.extend(_quantity_items(line))
        for exception in line.exceptions:
            if exception.code in (QTY_OVER_RECEIPT, QTY_OVER_ORDER):
                continue
            if exception.code == PRICE_VARIANCE:
                items.append(_price_variance(exception, line))
            else:
                items.append(
                    _unquantified(exception, basis=f"line {line.line_no}: {exception.detail}")
                )

    return InvoiceRecovery(
        invoice_number=result.invoice_number,
        supplier_tax_id=result.supplier_tax_id,
        items=tuple(items),
    )


def recover_run(run: MatchRun, invoices: Iterable[SupplierInvoice]) -> RunRecovery:
    """Price a whole batch.

    Raises rather than skip an invoice it was not handed: a silent skip under-reports
    the total, and an under-reported total is invisible to everyone including us.
    """
    by_identity = {inv.identity: inv for inv in invoices}
    priced: list[InvoiceRecovery] = []
    for result in run.results:
        identity = (result.supplier_tax_id, result.invoice_number)
        invoice = by_identity.get(identity)
        if invoice is None:
            raise KeyError(
                f"no invoice supplied for {result.invoice_number} "
                f"(supplier {result.supplier_tax_id}); cannot price its findings"
            )
        priced.append(recover_invoice(result, invoice))
    return RunRecovery(invoices=tuple(priced))


def quantified_items(recovery: RunRecovery) -> Sequence[RecoveryItem]:
    """The findings that can go on an invoice, largest first."""
    items = [i for inv in recovery.invoices for i in inv.items if i.quantified and i.amount]
    return sorted(items, key=lambda i: i.amount or ZERO, reverse=True)
