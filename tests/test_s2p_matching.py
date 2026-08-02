"""The three-way match engine.

Organised by what an AP team actually argues about: is this the same invoice we
already paid, does the document agree with itself, did we get what we are being
billed for, and is the price the one we agreed.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from packs.source_to_pay.matching import (
    BLOCK,
    CURRENCY_MISMATCH,
    DUPLICATE_INVOICE,
    INVOICE_ARITHMETIC,
    LINE_ARITHMETIC,
    MATCHED,
    NO_PO,
    NO_PO_LINE,
    NO_RECEIPT,
    PRICE_VARIANCE,
    QTY_OVER_ORDER,
    QTY_OVER_RECEIPT,
    REVIEW,
    InvoiceLine,
    PurchaseOrderLine,
    ReceiptLine,
    SupplierInvoice,
    Tolerance,
    match_batch,
    match_invoice,
)

SUPPLIER = "B12345678"


def _po(qty="10", price="100", currency="EUR") -> PurchaseOrderLine:
    return PurchaseOrderLine(
        po_number="PO-1", line_no="1", sku_code="A1",
        ordered_qty=Decimal(qty), unit_price=Decimal(price), currency=currency,
    )


def _receipt(qty="10") -> ReceiptLine:
    return ReceiptLine(receipt_id="GR-1", po_number="PO-1", po_line_no="1", received_qty=Decimal(qty))


def _invoice(
    *, qty="10", price="100", total=None, number="INV-1", currency="EUR", net=None, tax="0", gross=None
) -> SupplierInvoice:
    line_total = Decimal(total) if total is not None else Decimal(qty) * Decimal(price)
    net_total = Decimal(net) if net is not None else line_total
    tax_total = Decimal(tax)
    return SupplierInvoice(
        invoice_number=number, supplier_tax_id=SUPPLIER, invoice_date="2026-08-01",
        currency=currency, net_total=net_total, tax_total=tax_total,
        gross_total=Decimal(gross) if gross is not None else net_total + tax_total,
        lines=(InvoiceLine(
            line_no="1", po_number="PO-1", po_line_no="1",
            invoiced_qty=Decimal(qty), unit_price=Decimal(price), line_total=line_total,
        ),),
    )


def _match(invoice=None, *, po=None, receipts=(_receipt(),), tolerance=None, seen=()):
    return match_invoice(
        invoice or _invoice(),
        po_lines=[po or _po()],
        receipts=receipts,
        tolerance=tolerance,
        seen_invoices=seen,
    )


# ---- the happy path ---------------------------------------------------------


def test_an_invoice_that_agrees_with_order_and_receipt_matches() -> None:
    result = _match()
    assert result.matched
    assert result.status == MATCHED
    assert result.all_exceptions == ()
    assert "matched" in result.summary()


def test_money_stays_decimal_all_the_way_through() -> None:
    """An engine that decides whether to pay a bill cannot run on binary float."""
    result = _match(_invoice(qty="3", price="0.1", total="0.3"), po=_po(qty="3", price="0.1"))
    assert result.matched  # 0.1 * 3 == 0.3 exactly, in Decimal
    assert isinstance(result.total_value_delta, Decimal)


# ---- is it the same invoice again? ------------------------------------------


def test_a_repeat_invoice_number_blocks() -> None:
    result = _match(seen=[(SUPPLIER, "INV-1")])
    assert result.blocked
    assert DUPLICATE_INVOICE in result.exception_codes


def test_the_same_number_from_a_different_supplier_is_not_a_duplicate() -> None:
    """Invoice numbering collides freely across suppliers."""
    result = _match(seen=[("OTHER-TAX-ID", "INV-1")])
    assert result.matched


def test_a_duplicate_inside_one_batch_is_caught() -> None:
    """The common case: a supplier re-sends a statement and the file is ingested twice."""
    run = match_batch([_invoice(), _invoice()], po_lines=[_po()], receipts=[_receipt()])
    assert run.results[0].matched
    assert run.results[1].blocked
    assert len(run.blocked) == 1


# ---- does the document agree with itself? -----------------------------------


def test_a_line_that_does_not_multiply_out_blocks() -> None:
    result = _match(_invoice(qty="10", price="100", total="1200"), po=_po())
    assert result.blocked
    assert LINE_ARITHMETIC in result.exception_codes


def test_net_plus_tax_must_equal_gross() -> None:
    result = _match(_invoice(net="1000", tax="210", gross="1500"))
    assert result.blocked
    assert INVOICE_ARITHMETIC in result.exception_codes


def test_line_totals_must_add_up_to_the_net_total() -> None:
    result = _match(_invoice(net="9999"))
    assert result.blocked
    assert INVOICE_ARITHMETIC in result.exception_codes


def test_a_cent_of_rounding_is_absorbed_not_flagged() -> None:
    """Otherwise every tax calculation raises an exception and people learn to click through."""
    result = _match(_invoice(net="1000", tax="210.00", gross="1210.01"))
    assert result.matched


def test_currency_mismatch_blocks() -> None:
    result = _match(_invoice(currency="USD"), po=_po(currency="EUR"))
    assert result.blocked
    assert CURRENCY_MISMATCH in result.exception_codes


# ---- did we order it, did we get it? ----------------------------------------


def test_an_invoice_for_an_unknown_po_is_an_exception() -> None:
    invoice = _invoice()
    result = match_invoice(invoice, po_lines=[], receipts=[])
    assert NO_PO in result.exception_codes
    assert not result.matched


def test_a_known_po_with_an_unknown_line_is_reported_more_precisely() -> None:
    other_line = PurchaseOrderLine(
        po_number="PO-1", line_no="9", sku_code="Z", ordered_qty=Decimal("1"), unit_price=Decimal("1")
    )
    result = match_invoice(_invoice(), po_lines=[other_line], receipts=[])
    assert NO_PO_LINE in result.exception_codes


def test_nothing_received_is_flagged() -> None:
    result = _match(receipts=())
    assert NO_RECEIPT in result.exception_codes
    assert not result.blocked  # recoverable: review, not block


def test_invoicing_more_than_was_received() -> None:
    result = _match(_invoice(qty="12", price="100"), po=_po(qty="20"), receipts=[_receipt("10")])
    assert QTY_OVER_RECEIPT in result.exception_codes


def test_invoicing_more_than_was_ordered() -> None:
    result = _match(_invoice(qty="12", price="100"), po=_po(qty="10"), receipts=[_receipt("12")])
    assert QTY_OVER_ORDER in result.exception_codes


def test_partial_deliveries_are_summed_across_receipts() -> None:
    result = _match(receipts=[_receipt("4"), ReceiptLine("GR-2", "PO-1", "1", Decimal("6"))])
    assert result.matched


def test_invoicing_less_than_received_is_fine() -> None:
    """Under-invoicing costs us nothing and happens constantly with partial billing."""
    result = _match(_invoice(qty="4", price="100"), po=_po(qty="10"), receipts=[_receipt("10")])
    assert result.matched


# ---- is the price the one we agreed? ----------------------------------------


def test_a_price_within_tolerance_matches() -> None:
    result = _match(_invoice(price="100.5"), po=_po(price="100"), tolerance=Tolerance(price_pct=Decimal("1")))
    assert result.matched


def test_a_price_beyond_tolerance_is_flagged_with_the_numbers() -> None:
    result = _match(_invoice(price="110"), po=_po(price="100"), tolerance=Tolerance(price_pct=Decimal("1")))
    assert PRICE_VARIANCE in result.exception_codes
    variance = next(e for e in result.all_exceptions if e.code == PRICE_VARIANCE)
    assert variance.delta == Decimal("10")
    assert variance.allowed == Decimal("1")


def test_the_value_delta_is_what_the_variance_actually_costs() -> None:
    result = _match(_invoice(qty="10", price="110"), po=_po(qty="10", price="100"))
    assert result.total_value_delta == Decimal("100")  # 10 units x 10 over


def test_tolerance_takes_whichever_bound_is_greater() -> None:
    """A 1% tolerance is meaningless on a small line; an absolute one on a large one."""
    generous = Tolerance(price_pct=Decimal("1"), price_abs=Decimal("5"))
    # 3 over on a 100 line: beyond 1% (=1) but inside the 5 absolute.
    assert _match(_invoice(price="103"), po=_po(price="100"), tolerance=generous).matched
    # 3 over on a 10 line: inside neither... but 1% of 10 is 0.1, abs is 5, so still inside.
    assert _match(_invoice(price="13"), po=_po(price="10"), tolerance=generous).matched
    # 6 over on a 10 line: beyond both.
    assert not _match(_invoice(price="16"), po=_po(price="10"), tolerance=generous).matched


def test_default_tolerances_are_tight_not_permissive() -> None:
    """A tolerance nobody chose must not be why an overcharge is paid silently."""
    assert not _match(_invoice(price="102"), po=_po(price="100")).matched


# ---- severity ---------------------------------------------------------------


def test_severity_is_about_consequence_not_size() -> None:
    """A tiny duplicate blocks; a large price variance only asks for review."""
    tiny_duplicate = _match(_invoice(qty="1", price="1"), po=_po(qty="1", price="1"),
                            receipts=[_receipt("1")], seen=[(SUPPLIER, "INV-1")])
    big_variance = _match(_invoice(qty="10", price="9999"), po=_po(qty="10", price="100"),
                          receipts=[_receipt("10")])
    assert tiny_duplicate.status == BLOCK
    assert big_variance.status == REVIEW


def test_exceptions_come_back_most_severe_first_and_reproducibly() -> None:
    result = _match(
        _invoice(qty="12", price="110", total="1200", net="1200"),
        po=_po(qty="10", price="100"), receipts=[_receipt("10")], seen=[(SUPPLIER, "INV-1")],
    )
    severities = [e.severity for e in result.all_exceptions]
    assert severities == sorted(severities, key=lambda s: {BLOCK: 0, REVIEW: 1}[s])
    assert [e.code for e in result.all_exceptions] == [e.code for e in result.all_exceptions]


# ---- the batch view ---------------------------------------------------------


def test_the_batch_reports_the_numbers_an_ap_lead_asks_for() -> None:
    run = match_batch(
        [_invoice(number="A"), _invoice(number="B", price="130"), _invoice(number="C", currency="USD")],
        po_lines=[_po()], receipts=[_receipt()],
    )
    assert len(run.matched) == 1
    assert len(run.needs_review) == 1
    assert len(run.blocked) == 1
    assert run.touchless_rate == Decimal(100) / Decimal(3)
    assert run.by_exception_code()[PRICE_VARIANCE] == 1


def test_exposure_only_counts_unmatched_invoices() -> None:
    run = match_batch(
        [_invoice(number="A"), _invoice(number="B", price="110")],
        po_lines=[_po()], receipts=[_receipt()],
    )
    assert run.exposure == Decimal("100")


def test_an_empty_batch_does_not_divide_by_zero() -> None:
    run = match_batch([], po_lines=[], receipts=[])
    assert run.touchless_rate == Decimal(0)
    assert run.exposure == Decimal(0)


def test_the_same_inputs_always_produce_the_same_verdict() -> None:
    """Replayability: a disputed match must be reconstructible months later."""
    def once() -> tuple:
        run = match_batch(
            [_invoice(number="A", price="110"), _invoice(number="B")],
            po_lines=[_po()], receipts=[_receipt()],
        )
        return tuple(
            (r.invoice_number, r.status, r.exception_codes, str(r.total_value_delta)) for r in run.results
        )

    assert once() == once()


@pytest.mark.parametrize("qty", ["0", "-5"])
def test_zero_and_negative_quantities_do_not_crash_the_match(qty: str) -> None:
    """Credit notes and corrections arrive as negatives; the engine must survive them."""
    result = _match(_invoice(qty=qty, price="100"), po=_po(), receipts=[_receipt()])
    assert result is not None
