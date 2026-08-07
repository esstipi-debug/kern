"""What an AP exception is worth in money, and when it is worth nothing knowable.

These tests exist because ``MatchRun.exposure`` answers a different question than the
one a recovery engagement is paid on. ``exposure`` sums ``LineMatch.value_delta``, and
``value_delta`` is *price* variance only (matching.py, ``_match_line``). Measured on a
real fixture: a duplicated invoice plus eight units billed and never received produced
``exposure == 0``. The two findings a client actually gets money back for were worth
zero in the only money figure the pack exposed.

So the rule the suite pins is not "compute more": it is **an amount is a number with
its derivation, or it is explicitly not quantified. Never a silent zero.** Same shape as
``jobs/cross_impact_job.py``, which degrades to NO_EVALUABLE rather than assume a
decision point nobody chose.
"""

from decimal import Decimal

import pytest

from packs.source_to_pay.matching import (
    DUPLICATE_INVOICE,
    InvoiceLine,
    MatchException,
    PurchaseOrderLine,
    ReceiptLine,
    SupplierInvoice,
    Tolerance,
    match_batch,
    match_invoice,
)
from packs.source_to_pay.recovery import (
    RecoveryItem,
    quantified_items,
    recover_invoice,
    recover_run,
)

RUT = "76.543.210-K"
TIGHT = Tolerance(
    price_pct=Decimal("0"),
    price_abs=Decimal("0"),
    qty_pct=Decimal("0"),
    qty_abs=Decimal("0"),
    rounding_abs=Decimal("1"),
)


def _po(line_no: str, qty: str, price: str) -> PurchaseOrderLine:
    return PurchaseOrderLine("OC-1", line_no, f"SKU-{line_no}", Decimal(qty), Decimal(price), "CLP")


def _receipt(line_no: str, qty: str) -> ReceiptLine:
    return ReceiptLine("GR-1", "OC-1", line_no, Decimal(qty))


def _invoice(number: str, lines: tuple[InvoiceLine, ...], net: str) -> SupplierInvoice:
    net_d = Decimal(net)
    tax = (net_d * Decimal("0.19")).quantize(Decimal("1"))
    return SupplierInvoice(number, RUT, "2026-07-15", "CLP", net_d, tax, net_d + tax, lines)


def _line(line_no: str, qty: str, price: str) -> InvoiceLine:
    total = Decimal(qty) * Decimal(price)
    return InvoiceLine(line_no, "OC-1", line_no, Decimal(qty), Decimal(price), total)


def _by_code(recovery, code):
    found = [item for item in recovery.items if item.code == code]
    assert found, f"no recovery item for {code}; got {[i.code for i in recovery.items]}"
    return found[0]


# ---- the two findings that were worth zero ----------------------------------


def test_duplicate_invoice_is_worth_the_whole_net_total():
    """A re-sent invoice is the full net amount, because the whole payment repeats.

    Net, not gross: the VAT on a duplicate was already taken as a fiscal credit, so
    charging a recovery fee on the gross bills the client for money never lost.
    """
    line = _line("1", "10", "18500")
    invoice = _invoice("F-1", (line,), "185000")
    result = match_invoice(
        invoice,
        po_lines=[_po("1", "10", "18500")],
        receipts=[_receipt("1", "10")],
        tolerance=TIGHT,
        seen_invoices=[(RUT, "F-1")],
    )

    recovery = recover_invoice(result, invoice)
    item = _by_code(recovery, DUPLICATE_INVOICE)

    assert item.quantified is True
    assert item.amount == Decimal("185000")
    assert recovery.quantified_total == Decimal("185000")
    assert "185000" in item.basis


def test_units_billed_but_never_received_are_worth_the_price_billed():
    """Billed 12, received 9: the three phantom units are recoverable at what they cost.

    At the *invoiced* price, not the ordered one - that is the money that leaves the
    bank for goods on nobody's dock.
    """
    invoice = _invoice("F-2", (_line("1", "12", "74900"),), "898800")
    result = match_invoice(
        invoice,
        po_lines=[_po("1", "12", "74900")],
        receipts=[_receipt("1", "9")],
        tolerance=TIGHT,
    )

    recovery = recover_invoice(result, invoice)
    item = _by_code(recovery, "quantity_over_receipt")

    assert item.quantified is True
    assert item.amount == Decimal("3") * Decimal("74900")
    assert recovery.quantified_total == Decimal("224700")


# ---- no double counting ------------------------------------------------------


def test_price_variance_is_priced_on_what_arrived_not_on_what_was_billed():
    """When both faults hit one line, the over-billed units must be counted once.

    Billed 12 at 19800 against an order of 12 at 18500, with 9 received. The naive sum
    charges the 1300 overcharge on all 12 *and* the full 19800 on the 3 phantom units,
    double counting 3 x 1300. The split is: overcharge on the 9 that arrived, full
    invoiced price on the 3 that did not.
    """
    invoice = _invoice("F-3", (_line("1", "12", "19800"),), "237600")
    result = match_invoice(
        invoice,
        po_lines=[_po("1", "12", "18500")],
        receipts=[_receipt("1", "9")],
        tolerance=TIGHT,
    )

    recovery = recover_invoice(result, invoice)
    variance = _by_code(recovery, "price_variance")
    phantom = _by_code(recovery, "quantity_over_receipt")

    assert variance.amount == Decimal("1300") * Decimal("9")
    assert phantom.amount == Decimal("3") * Decimal("19800")
    assert recovery.quantified_total == Decimal("11700") + Decimal("59400")


def test_over_order_and_over_receipt_do_not_both_charge_the_same_units():
    """Ordered 10, received 9, billed 12. The excess is three units, not five."""
    invoice = _invoice("F-4", (_line("1", "12", "1000"),), "12000")
    result = match_invoice(
        invoice,
        po_lines=[_po("1", "10", "1000")],
        receipts=[_receipt("1", "9")],
        tolerance=TIGHT,
    )

    recovery = recover_invoice(result, invoice)
    assert recovery.quantified_total == Decimal("3") * Decimal("1000")


# ---- the honest-absence rule -------------------------------------------------


@pytest.mark.parametrize("code", ["no_purchase_order", "no_goods_receipt"])
def test_unprovable_exceptions_are_not_quantified_and_are_never_zero(code):
    """A line with no PO or no receipt has money at stake that cannot be *proven*.

    ``_match_line`` books the whole line total into ``value_delta`` for the no-PO case.
    Recovery must not repeat that assumption: an unbacked claim invoiced as recovered
    is the fastest way to lose the client and the reference.
    """
    if code == "no_purchase_order":
        invoice = _invoice("F-5", (_line("1", "5", "2000"),), "10000")
        result = match_invoice(invoice, po_lines=[], receipts=[], tolerance=TIGHT)
    else:
        invoice = _invoice("F-6", (_line("1", "5", "2000"),), "10000")
        result = match_invoice(
            invoice, po_lines=[_po("1", "5", "2000")], receipts=[], tolerance=TIGHT
        )

    recovery = recover_invoice(result, invoice)
    item = _by_code(recovery, code)

    assert item.quantified is False
    assert item.amount is None, "an unprovable amount must be absent, not zero"
    assert item.reason
    assert recovery.quantified_total == Decimal("0")
    assert recovery.unquantified_count == 1


def test_an_undercharge_is_a_declared_zero_not_a_hidden_one():
    """The supplier billed below the order. Zero is the true answer, so it is stated."""
    invoice = _invoice("F-7", (_line("1", "10", "900"),), "9000")
    result = match_invoice(
        invoice,
        po_lines=[_po("1", "10", "1000")],
        receipts=[_receipt("1", "10")],
        tolerance=TIGHT,
    )

    recovery = recover_invoice(result, invoice)
    item = _by_code(recovery, "price_variance")

    assert item.quantified is True
    assert item.amount == Decimal("0")
    assert "below" in item.basis.lower()


def test_a_clean_invoice_recovers_nothing_and_says_so():
    invoice = _invoice("F-8", (_line("1", "10", "1000"),), "10000")
    result = match_invoice(
        invoice,
        po_lines=[_po("1", "10", "1000")],
        receipts=[_receipt("1", "10")],
        tolerance=TIGHT,
    )

    recovery = recover_invoice(result, invoice)
    assert recovery.items == ()
    assert recovery.quantified_total == Decimal("0")
    assert recovery.unquantified_count == 0


# ---- every finding carries its own derivation --------------------------------


def test_every_item_carries_the_arithmetic_that_produced_it():
    """The basis line is what lets the client's accountant disprove us alone.

    Without it the number is an assertion, and an assertion is what every failed AP
    recovery pitch is made of.
    """
    invoice = _invoice("F-9", (_line("1", "12", "19800"),), "237600")
    result = match_invoice(
        invoice,
        po_lines=[_po("1", "12", "18500")],
        receipts=[_receipt("1", "9")],
        tolerance=TIGHT,
    )

    recovery = recover_invoice(result, invoice)
    assert recovery.items
    for item in recovery.items:
        assert item.basis.strip(), f"{item.code} has no derivation"
        if item.quantified:
            assert item.amount is not None
        else:
            assert item.amount is None and item.reason


# ---- run level ---------------------------------------------------------------


def test_run_total_counts_quantified_only_and_reports_what_it_could_not_price():
    """The headline number is what is defensible; the rest is visible, not folded in."""
    po = [_po("1", "12", "74900")]
    receipts = [_receipt("1", "9")]
    phantom = _invoice("F-10", (_line("1", "12", "74900"),), "898800")
    orphan = _invoice("F-11", (InvoiceLine("1", "OC-9", "1", Decimal("5"), Decimal("2000"), Decimal("10000")),), "10000")

    run = match_batch([phantom, orphan], po_lines=po, receipts=receipts, tolerance=TIGHT)
    summary = recover_run(run, [phantom, orphan])

    assert summary.quantified_total == Decimal("224700")
    assert summary.unquantified_count == 1
    assert len(summary.invoices) == 2


def test_recover_run_refuses_an_invoice_it_was_not_given():
    """A silent skip here would under-report the total and nobody would see it."""
    invoice = _invoice("F-12", (_line("1", "1", "100"),), "100")
    run = match_batch([invoice], po_lines=[_po("1", "1", "100")], receipts=[_receipt("1", "1")], tolerance=TIGHT)

    with pytest.raises(KeyError, match="F-12"):
        recover_run(run, [])


def test_quantified_items_ranks_the_batch_largest_first():
    """What goes on the fee invoice, in the order a client reads it."""
    po = [_po("1", "12", "74900")]
    receipts = [_receipt("1", "9")]
    phantom = _invoice("F-13", (_line("1", "12", "74900"),), "898800")
    repeated = _invoice("F-14", (_line("1", "12", "74900"),), "898800")

    run = match_batch(
        [phantom, repeated],
        po_lines=po,
        receipts=receipts,
        tolerance=TIGHT,
        seen_invoices=[(RUT, "F-14")],
    )
    ranked = quantified_items(recover_run(run, [phantom, repeated]))

    assert [i.code for i in ranked][0] == DUPLICATE_INVOICE
    assert [i.amount for i in ranked] == sorted((i.amount for i in ranked), reverse=True)


# ---- the guardrails have to be able to fail ---------------------------------
#
# Everything below exists because a mutation run proved the module's central rule was
# unfalsifiable: replacing the KeyError in ``_unquantified`` with a quantified zero left
# all eleven tests green. A guardrail no test can break is a guardrail that arrives at
# the future already broken - and the future here is the day someone adds a code to
# ``matching.py``, which is exactly when a silent zero would ship unnoticed.


def test_an_unclassified_code_raises_instead_of_falling_through_as_zero():
    """The rule the whole module rests on, and the one nothing could break before."""
    from packs.source_to_pay.recovery import _unquantified

    orphan = MatchException(code="a_code_nobody_classified", severity="review", line_no="1")

    with pytest.raises(KeyError, match="a_code_nobody_classified"):
        _unquantified(orphan, basis="whatever")


def test_an_invoice_level_finding_that_is_not_a_duplicate_is_not_priced():
    """Totals that do not add up: no single line can be held responsible."""
    line = _line("1", "10", "1000")
    invoice = _invoice("F-15", (line,), "999999")  # net_total disagrees with the lines

    result = match_invoice(
        invoice,
        po_lines=[_po("1", "10", "1000")],
        receipts=[_receipt("1", "10")],
        tolerance=TIGHT,
    )
    recovery = recover_invoice(result, invoice)
    item = _by_code(recovery, "invoice_arithmetic")

    assert item.quantified is False
    assert item.amount is None
    assert recovery.quantified_total == Decimal("0")


@pytest.mark.parametrize(
    "kwargs, expected",
    [
        (dict(quantified=True, amount=None), "without an amount"),
        (dict(quantified=False, amount=Decimal("1"), reason="r"), "carrying an amount"),
        (dict(quantified=False, amount=None), "without a reason"),
    ],
)
def test_a_recovery_item_cannot_be_built_in_a_dishonest_shape(kwargs, expected):
    """The three states that would let a silent zero exist are unconstructable."""
    with pytest.raises(ValueError, match=expected):
        RecoveryItem(code="c", severity="review", line_no=None, basis="b", **kwargs)
