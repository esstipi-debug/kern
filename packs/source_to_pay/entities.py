"""Source-to-pay entity types and field classifications.

Written to be read side by side with ``src/entities.py``. Same imports, same
shape, same amount of code - and not one line of supply chain. That is the whole
claim the core extraction was making, stated as something you can diff rather
than something you have to believe.

Note what is *reused* rather than redeclared: ``supplier`` is registered by the
SCM pack, and this pack does not define its own. Two packs naming the same
real-world entity is exactly the silo the platform exists to remove, so a shared
type is registered once and both packs observe into it. :func:`build_s2p_registry`
takes an optional existing registry for that reason.
"""

from __future__ import annotations

from nosilo_core.access import (
    CONFIDENTIAL,
    DROP,
    INTERNAL,
    MASK,
    PUBLIC,
    RESTRICTED,
    Classification,
    FieldPolicy,
    PolicySet,
    Rule,
)
from nosilo_core.entities import EntityType, EntityTypeRegistry, SqliteEntityStore

SOURCE_ERP = "erp"
SOURCE_OCR = "ocr"
SOURCE_PORTAL = "supplier_portal"
SOURCE_EMAIL = "email"

# ---- entity types -----------------------------------------------------------

PURCHASE_ORDER = EntityType(
    name="purchase_order",
    natural_keys=("po_number",),
    # The ERP is where a PO legally exists. An OCR read of a PDF copy is
    # evidence about it, never the record of it.
    field_owners={"po_number": SOURCE_ERP, "supplier_tax_id": SOURCE_ERP},
    # Agreed price is the number a fraudulent or careless feed would most like
    # to move, and the one every downstream variance is measured against.
    guarded_fields=frozenset({"agreed_total", "currency"}),
)

GOODS_RECEIPT = EntityType(
    name="goods_receipt",
    natural_keys=("receipt_id",),
    field_owners={"receipt_id": SOURCE_ERP, "received_qty": SOURCE_ERP},
    guarded_fields=frozenset(),
)

SUPPLIER_INVOICE = EntityType(
    name="supplier_invoice",
    natural_keys=("invoice_key",),  # supplier tax id + invoice number, joined
    # Deliberately NOT owned by the ERP: an invoice is the supplier's assertion,
    # and it usually reaches us as a PDF before it reaches the ERP at all. The
    # ERP owns its *posting status*, not its contents.
    field_owners={"posting_status": SOURCE_ERP},
    # Every figure that decides how much money leaves. A document-extraction
    # pipeline reading a scanned PDF must not be able to move these on its own -
    # extraction confidence is not the same thing as authority.
    guarded_fields=frozenset({"gross_total", "net_total", "tax_total", "remit_to_account"}),
)

S2P_ENTITY_TYPES = (PURCHASE_ORDER, GOODS_RECEIPT, SUPPLIER_INVOICE)


# ---- classifications --------------------------------------------------------

PURCHASE_ORDER_CLASSIFICATION = Classification(
    fields={
        "po_number": FieldPolicy(level=PUBLIC),
        "supplier_tax_id": FieldPolicy(level=INTERNAL, below_clearance=MASK),
        "agreed_total": FieldPolicy(level=CONFIDENTIAL),
        "currency": FieldPolicy(level=PUBLIC),
    }
)

GOODS_RECEIPT_CLASSIFICATION = Classification(
    fields={
        "receipt_id": FieldPolicy(level=PUBLIC),
        "po_number": FieldPolicy(level=PUBLIC),
        "received_qty": FieldPolicy(level=INTERNAL),
    }
)

SUPPLIER_INVOICE_CLASSIFICATION = Classification(
    fields={
        "invoice_key": FieldPolicy(level=INTERNAL),
        "invoice_number": FieldPolicy(level=INTERNAL),
        "supplier_tax_id": FieldPolicy(level=INTERNAL, below_clearance=MASK),
        "net_total": FieldPolicy(level=CONFIDENTIAL),
        "tax_total": FieldPolicy(level=CONFIDENTIAL),
        "gross_total": FieldPolicy(level=CONFIDENTIAL),
        # Where the money is actually sent. Dropped rather than masked for the
        # same reason a supplier's bank account is in the SCM pack: a masked
        # account still confirms which one is on file.
        "remit_to_account": FieldPolicy(level=RESTRICTED, below_clearance=DROP),
        "posting_status": FieldPolicy(level=INTERNAL),
    }
)

S2P_CLASSIFICATIONS = {
    PURCHASE_ORDER.name: PURCHASE_ORDER_CLASSIFICATION,
    GOODS_RECEIPT.name: GOODS_RECEIPT_CLASSIFICATION,
    SUPPLIER_INVOICE.name: SUPPLIER_INVOICE_CLASSIFICATION,
}


def invoice_key(supplier_tax_id: str, invoice_number: str) -> str:
    """The natural key that makes a re-sent invoice resolve to the same entity.

    Supplier plus number, not number alone: invoice numbering restarts every year and
    collides freely across suppliers, so a bare number would weld two unrelated
    invoices into one entity - the exact failure the natural-key mechanism is
    supposed to prevent.
    """
    return f"{supplier_tax_id.strip()}|{invoice_number.strip()}"


def build_s2p_registry(registry: EntityTypeRegistry | None = None) -> EntityTypeRegistry:
    """Register the source-to-pay types, optionally onto an existing registry.

    Passing the SCM pack's registry is the supported way to run both in one
    process against one store: ``supplier`` stays a single shared entity type
    rather than each pack minting its own idea of the same company.
    """
    target = registry if registry is not None else EntityTypeRegistry()
    for entity_type in S2P_ENTITY_TYPES:
        target.register(entity_type)
    return target


def s2p_policy(version: str, rules: tuple[Rule, ...] = ()) -> PolicySet:
    """A :class:`PolicySet` carrying the source-to-pay field classifications."""
    return PolicySet(version=version, rules=rules, classifications=S2P_CLASSIFICATIONS)


def open_s2p_store(
    path: str | None = None, *, registry: EntityTypeRegistry | None = None, **kwargs: object
) -> SqliteEntityStore:
    """A store with the source-to-pay types registered."""
    prepared = build_s2p_registry(registry)
    return SqliteEntityStore(prepared, path, **kwargs) if path else SqliteEntityStore(prepared, **kwargs)
