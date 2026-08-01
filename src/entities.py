"""The supply-chain entity types, declared on top of the generic core store.

``nosilo_core.entities`` stores entities without knowing what any of them mean.
This module supplies the meaning for the three master-data entities every
supply-chain conversation eventually needs a stable identity for: **SKU**,
**supplier** and **location**.

Why these three and nothing else yet: they are the entities that already appear
in every connector DTO and every job's output, under a different id in each
system. Until they have one identity, "the same SKU in Odoo and Shopify" is a
string-matching problem solved independently (and inconsistently) in a dozen
places. Orders, invoices and shipments matter just as much, but they are
transactional rather than master data and want the resolution and correction
machinery this layer deliberately does not have yet.

The declarations below are the entire vocabulary the core needs. Note what that
implies: a source-to-pay pack registers ``Invoice`` and ``PurchaseOrder`` exactly
like this, in its own module, with no change to ``nosilo_core``. That is the
property the whole extraction exists to buy, and this file is the first proof
of it.

Nothing here changes how any existing Kern job behaves. Observing entities is
additive: connectors and jobs can start feeding the store without any of them
having to read from it yet.
"""

from __future__ import annotations

from typing import Any, Mapping

from nosilo_core.entities import (
    EntityType,
    EntityTypeRegistry,
    ObserveResult,
    SourceRef,
    SqliteEntityStore,
    to_decimal,
)

# ---- source systems ---------------------------------------------------------
# Named constants rather than bare strings: these appear in field_owners below,
# and a typo there silently disables an ownership rule instead of failing.

SOURCE_ODOO = "odoo"
SOURCE_SHOPIFY = "shopify"
SOURCE_MELI = "meli"
SOURCE_EXCEL = "excel"
SOURCE_MANUAL = "manual"


# ---- entity types -----------------------------------------------------------

SKU = EntityType(
    name="sku",
    # The SKU code is the one identifier every channel agrees to carry, which is
    # what makes exact cross-system matching possible at all here. GTIN would be
    # stronger where it exists, but it is absent often enough in practice that
    # requiring it would leave most catalogues unresolved.
    natural_keys=("sku_code",),
    # Cost is an ERP fact. A storefront's idea of cost is a display artefact and
    # must never overwrite the book value that margin calculations run on.
    field_owners={"unit_cost": SOURCE_ODOO},
    # Contract/list price is guarded, not owned: several channels legitimately
    # carry their own price, so the rule is "an automated feed never silently
    # rewrites the established one", not "only the ERP may speak".
    guarded_fields=frozenset({"list_price"}),
)

SUPPLIER = EntityType(
    name="supplier",
    # Tax id is the identifier that survives name changes, punctuation and the
    # seven spellings of the same company across seven systems.
    natural_keys=("tax_id",),
    field_owners={"legal_name": SOURCE_ODOO},
    # Bank details and credit limit are never auto-updated by a data feed, at
    # any trust level. Supplier bank-account takeover arrives looking exactly
    # like a routine master-data sync, and the cost of being wrong once dwarfs
    # the cost of a human confirming every legitimate change.
    guarded_fields=frozenset({"bank_account", "credit_limit", "payment_terms"}),
)

LOCATION = EntityType(
    name="location",
    natural_keys=("location_code",),
    field_owners={"address": SOURCE_ODOO},
    guarded_fields=frozenset(),
)

SCM_ENTITY_TYPES = (SKU, SUPPLIER, LOCATION)


def build_scm_registry() -> EntityTypeRegistry:
    """A registry with the three supply-chain entity types registered.

    Returns a fresh instance rather than a module-level singleton, matching
    ``build_default_registry()``'s instance-based choice: two stores in the same
    process (a test and a job, say) must not share mutable registration state.
    """
    registry = EntityTypeRegistry()
    for entity_type in SCM_ENTITY_TYPES:
        registry.register(entity_type)
    return registry


def open_scm_store(path: str | None = None) -> SqliteEntityStore:
    """A store pre-loaded with the supply-chain entity types."""
    registry = build_scm_registry()
    return SqliteEntityStore(registry, path) if path else SqliteEntityStore(registry)


# ---- adapters from the existing connector DTOs ------------------------------
# These are the bridge from what Kern already reads to what the store keeps.
# Deliberately one-way and additive: a connector can start feeding the store
# without any existing code path changing behaviour.


def sku_attributes(
    sku_code: str,
    *,
    title: str | None = None,
    unit_cost: object | None = None,
    list_price: object | None = None,
) -> dict[str, Any]:
    """Canonical SKU attributes from loose connector values.

    Money arrives from connector DTOs as ``float`` (see
    ``src.connectors.Product``) and is converted through ``str`` so binary
    floating point never reaches a stored monetary value: ``Decimal(0.1)`` is
    not 0.1, ``Decimal("0.1")`` is. Omitted values are left out entirely rather
    than sent as ``None`` - "this source did not report cost" and "this source
    says cost is nothing" are different claims and must not collapse.
    """
    attributes: dict[str, Any] = {"sku_code": sku_code}
    if title is not None:
        attributes["title"] = title
    if unit_cost is not None:
        attributes["unit_cost"] = to_decimal(unit_cost)
    if list_price is not None:
        attributes["list_price"] = to_decimal(list_price)
    return attributes


def observe_product(
    store: SqliteEntityStore,
    product: Any,
    *,
    system: str,
    source_entity: str,
    source_id: str | None = None,
    recorded_at: str | None = None,
    trust: float = 1.0,
) -> ObserveResult:
    """Record a ``src.connectors.Product`` as an observation of a SKU entity.

    ``source_id`` defaults to the SKU code because several connectors key their
    catalogue that way; pass the system's own primary key when it has one
    (Odoo's numeric ``product.product`` id, a Shopify variant id) so the xref
    survives a SKU recode.
    """
    reference = SourceRef(system=system, entity=source_entity, id=source_id or product.sku)
    return store.observe(
        SKU.name,
        reference,
        sku_attributes(
            product.sku,
            title=getattr(product, "title", None),
            unit_cost=_reported_money(getattr(product, "cost", None)),
            list_price=_reported_money(getattr(product, "price", None)),
        ),
        recorded_at=recorded_at,
        trust=trust,
    )


def _reported_money(value: object | None) -> object | None:
    """Treat a non-positive amount from a connector DTO as "not reported".

    ``src.connectors.Product.cost`` defaults to ``0.0``, so a storefront that
    simply does not track cost is indistinguishable from one asserting the cost
    is zero - and the second reading would let a Shopify read write a zero book
    cost over a real one for as long as it took the ERP to speak. A genuinely
    free item is rare, and cost is owned by the ERP anyway, so dropping the
    claim is the safer half of the trade.
    """
    if value is None:
        return None
    return value if to_decimal(value) > 0 else None


def observe_attributes(
    store: SqliteEntityStore,
    entity_type: str,
    *,
    system: str,
    source_entity: str,
    source_id: str,
    attributes: Mapping[str, Any],
    recorded_at: str | None = None,
    trust: float = 1.0,
    change_reason: str = "",
) -> ObserveResult:
    """Generic escape hatch for sources without a typed DTO (Excel, manual entry)."""
    return store.observe(
        entity_type,
        SourceRef(system=system, entity=source_entity, id=source_id),
        attributes,
        recorded_at=recorded_at,
        trust=trust,
        change_reason=change_reason,
    )
