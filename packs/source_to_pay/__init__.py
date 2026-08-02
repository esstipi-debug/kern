"""Source-to-pay: three-way match of invoice against purchase order and receipt.

The second domain pack, and the reason the core was extracted at all. Everything
here is accounts payable - not one line of supply chain - and it imports nothing
from ``src``. ``tests/test_pack_boundary.py`` enforces that, which turns "the
core generalises" from a claim into a check that fails when it stops being true.

What the pack supplies:

``matching``
    The engine. Pure, ``Decimal`` throughout, clock-free: even "which invoices
    have we seen before" arrives as an argument, so a disputed match can be
    replayed exactly months later.
``entities``
    Purchase order, goods receipt and supplier invoice as canonical entity types,
    with the field classifications that decide who may see a remit-to account.
    Note it does *not* redeclare ``supplier`` - that type belongs to whichever
    pack registered it first, and both packs observe into the same entity. Two
    packs with their own idea of the same company is the silo this whole design
    exists to remove.
``tool``
    One ``register()`` call puts the match through the identical
    ``prepare -> run -> qa -> deliver`` pipeline the supply-chain capabilities
    use, with the same "QA fails => no deliverable" veto.

What it got for free, and would otherwise have had to build: stable entity
identity across the ERP, the supplier portal and an OCR pipeline; per-field
provenance; bitemporal history; capability-bounded reads; per-field masking; the
safe-staging write plane; and the guided outcome contract.
"""

from .entities import (
    GOODS_RECEIPT,
    PURCHASE_ORDER,
    S2P_CLASSIFICATIONS,
    S2P_ENTITY_TYPES,
    SUPPLIER_INVOICE,
    build_s2p_registry,
    invoice_key,
    open_s2p_store,
    s2p_policy,
)
from .matching import (
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
    LineMatch,
    MatchException,
    MatchResult,
    MatchRun,
    PurchaseOrderLine,
    ReceiptLine,
    SupplierInvoice,
    Tolerance,
    match_batch,
    match_invoice,
)
from .tool import THREE_WAY_MATCH, TOOL_KEY, build_s2p_tools, register

__all__ = [
    "BLOCK",
    "CURRENCY_MISMATCH",
    "DUPLICATE_INVOICE",
    "GOODS_RECEIPT",
    "INVOICE_ARITHMETIC",
    "InvoiceLine",
    "LINE_ARITHMETIC",
    "LineMatch",
    "MATCHED",
    "MatchException",
    "MatchResult",
    "MatchRun",
    "NO_PO",
    "NO_PO_LINE",
    "NO_RECEIPT",
    "PRICE_VARIANCE",
    "PURCHASE_ORDER",
    "PurchaseOrderLine",
    "QTY_OVER_ORDER",
    "QTY_OVER_RECEIPT",
    "REVIEW",
    "ReceiptLine",
    "S2P_CLASSIFICATIONS",
    "S2P_ENTITY_TYPES",
    "SUPPLIER_INVOICE",
    "SupplierInvoice",
    "THREE_WAY_MATCH",
    "TOOL_KEY",
    "Tolerance",
    "build_s2p_registry",
    "build_s2p_tools",
    "invoice_key",
    "match_batch",
    "match_invoice",
    "open_s2p_store",
    "register",
    "s2p_policy",
]
