"""Preventa scan over MercadoLibre's official catalog -- read-only, never-unprotected.

A seller (or an operator researching a catalog) searches MercadoLibre's product
pages and asks: which of these can I list as preventa -- stock not yet on the
shelf, ``MANUFACTURING_TIME`` on the listing -- without publishing anything?

MercadoLibre has no ``preorder`` listing type. The documented mechanism is the
``MANUFACTURING_TIME`` sale term on a category (``GET /categories/{id}/sale_terms``).
This engine classifies each catalog hit against that fact plus the seller's
own existing ``catalog_product_id`` set. It NEVER imports
``nosilo_core.writeback`` and NEVER returns an ``EXECUTED`` outcome: publishing
a listing is a human step, and this repo does not POST ``/items``.

Statuses (one per query, first usable hit):

* ``presale_ready`` -- active catalog product, category allows preventa, seller
  does not already list it.
* ``catalog_only`` -- active and listable, but the category has no
  ``MANUFACTURING_TIME`` term. A marketplace/catalog listing is possible;
  preventa is not.
* ``already_listed`` -- the seller already has this ``catalog_product_id``.
* ``inactive`` -- catalog product is not electable (status != active).
* ``no_category`` -- hit found, but no category id (cannot check sale_terms
  without inventing one).
* ``no_match`` -- the catalog returned no rows for the query.

The optional listing *draft* is the JSON a human would POST to ``/items``
(or ``/items/catalog_listings``). It is an artifact on the handoff packet,
never sent.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.connectors.meli_catalog import (
    MANUFACTURING_TIME,
    CatalogProduct,
    MeliCatalogError,
    MeliCatalogRPC,
    manufacturing_time_allowed,
    parse_product_search,
)

PRESALE_READY = "presale_ready"
CATALOG_ONLY = "catalog_only"
ALREADY_LISTED = "already_listed"
INACTIVE = "inactive"
NO_CATEGORY = "no_category"
NO_MATCH = "no_match"

STATUSES = (
    PRESALE_READY,
    CATALOG_ONLY,
    ALREADY_LISTED,
    INACTIVE,
    NO_CATEGORY,
    NO_MATCH,
)

#: Default manufacturing window the draft would send. Official examples use
#: strings like ``"20 dias"``. Kept short so a demo draft is readable.
DEFAULT_MANUFACTURING_DAYS = 15


@dataclass(frozen=True)
class PresaleQuery:
    """One search the operator wants to run against the official catalog."""

    q: str = ""
    product_identifier: str = ""
    site_id: str = "MLA"
    sku: str = ""
    manufacturing_days: int = DEFAULT_MANUFACTURING_DAYS
    price: float | None = None

    def __post_init__(self) -> None:
        if not (self.q or "").strip() and not (self.product_identifier or "").strip():
            raise ValueError("PresaleQuery needs q or product_identifier")
        if self.manufacturing_days <= 0:
            raise ValueError("manufacturing_days must be > 0")


@dataclass(frozen=True)
class PresaleCandidate:
    """One classified catalog hit, plus the draft a human could publish."""

    query: PresaleQuery
    status: str
    product: CatalogProduct | None
    preventa_allowed: bool
    reason: str
    draft: dict | None


@dataclass(frozen=True)
class PresaleScan:
    """The full scan: one candidate per query.

    ``outcome`` stays ``None`` here. ``src.meli_presale_guided.outcome_for``
    attaches the OPTIONS/HANDOFF GuidedOutcome when ``nosilo_core`` is
    installed -- classification itself must not require the private package.
    """

    candidates: tuple[PresaleCandidate, ...]
    listed_checked: bool
    outcome: object | None = None


def _resolve_category(product: CatalogProduct, rpc: MeliCatalogRPC, query: PresaleQuery) -> str:
    if product.category_id:
        return product.category_id
    hint = (query.q or product.name or "").strip()
    if not hint:
        return ""
    return rpc.discover_category(query.site_id, hint) or ""


def draft_listing(
    product: CatalogProduct,
    *,
    manufacturing_days: int,
    sku: str = "",
    price: float | None = None,
    preventa: bool = True,
) -> dict:
    """The JSON a human would POST. This function does not send it.

    Shape follows MercadoLibre's documented catalog + sale_terms listing:
    ``catalog_product_id``, ``catalog_listing: true``, and -- only when the
    category allows it -- ``sale_terms: [{id: MANUFACTURING_TIME, value_name}]``.
    Price and SKU stay out unless the caller supplied them: an invented price
    is worse than no price.
    """
    body: dict = {
        "catalog_product_id": product.product_id,
        "catalog_listing": True,
        "title": product.name,
        "site_id": product.site_id,
        "category_id": product.category_id,
    }
    if sku:
        body["seller_custom_field"] = sku
    if price is not None:
        body["price"] = price
    if preventa:
        body["sale_terms"] = [{
            "id": MANUFACTURING_TIME,
            "value_name": f"{int(manufacturing_days)} dias",
        }]
    return body


def _classify_hit(
    query: PresaleQuery,
    product: CatalogProduct,
    rpc: MeliCatalogRPC,
    listed: frozenset[str],
) -> PresaleCandidate:
    if product.product_id in listed:
        return PresaleCandidate(
            query=query, status=ALREADY_LISTED, product=product,
            preventa_allowed=False,
            reason="the seller already lists this catalog_product_id",
            draft=None,
        )
    if product.status != "active":
        return PresaleCandidate(
            query=query, status=INACTIVE, product=product,
            preventa_allowed=False,
            reason=f"catalog product status is {product.status!r}, not active",
            draft=None,
        )
    category_id = _resolve_category(product, rpc, query)
    if not category_id:
        return PresaleCandidate(
            query=query, status=NO_CATEGORY, product=product,
            preventa_allowed=False,
            reason="no category_id on the product and domain_discovery did not resolve one",
            draft=None,
        )
    # Re-bind the resolved category onto a copy-like product for the draft.
    resolved = CatalogProduct(
        product_id=product.product_id,
        site_id=product.site_id,
        name=product.name,
        status=product.status,
        domain_id=product.domain_id,
        category_id=category_id,
        listing_strategy=product.listing_strategy,
        gtin=product.gtin,
        permalink=product.permalink,
    )
    allowed = manufacturing_time_allowed(rpc.category_sale_terms(category_id))
    if allowed:
        return PresaleCandidate(
            query=query, status=PRESALE_READY, product=resolved,
            preventa_allowed=True,
            reason="active catalog product; category allows MANUFACTURING_TIME; not yet listed",
            draft=draft_listing(
                resolved,
                manufacturing_days=query.manufacturing_days,
                sku=query.sku,
                price=query.price,
                preventa=True,
            ),
        )
    return PresaleCandidate(
        query=query, status=CATALOG_ONLY, product=resolved,
        preventa_allowed=False,
        reason="active catalog product, but this category has no MANUFACTURING_TIME sale term",
        draft=draft_listing(
            resolved,
            manufacturing_days=query.manufacturing_days,
            sku=query.sku,
            price=query.price,
            preventa=False,
        ),
    )


def _first_usable(hits: tuple[CatalogProduct, ...]) -> CatalogProduct | None:
    for hit in hits:
        if hit.status == "active":
            return hit
    return hits[0] if hits else None


def _scan_one(query: PresaleQuery, rpc: MeliCatalogRPC, listed: frozenset[str]) -> PresaleCandidate:
    body = rpc.search_products(
        site_id=query.site_id,
        q=query.q or None,
        product_identifier=query.product_identifier or None,
        status="active",
    )
    # Keyword search is active-only (the documented default for listing). A
    # GTIN miss retries inactive so "found but not electable" is not reported
    # as no_match -- those two are different operator next-steps.
    if query.product_identifier and not (body.get("results") or []):
        body = rpc.search_products(
            site_id=query.site_id,
            product_identifier=query.product_identifier,
            status="inactive",
        )
    hits = parse_product_search(body, site_id=query.site_id)
    product = _first_usable(hits)
    if product is None:
        label = query.product_identifier or query.q
        return PresaleCandidate(
            query=query, status=NO_MATCH, product=None,
            preventa_allowed=False,
            reason=f"catalog search returned no products for {label!r}",
            draft=None,
        )
    return _classify_hit(query, product, rpc, listed)


def classify_presale(
    queries: list[PresaleQuery] | tuple[PresaleQuery, ...],
    rpc: MeliCatalogRPC,
    *,
    already_listed: frozenset[str] | set[str] | None = None,
) -> PresaleScan:
    """Run each query against ``rpc``. No GuidedOutcome, no writes."""
    if not queries:
        raise MeliCatalogError("classify_presale requires at least one query")
    listed = frozenset(already_listed) if already_listed is not None else rpc.seller_catalog_ids()
    candidates = tuple(_scan_one(q, rpc, listed) for q in queries)
    return PresaleScan(candidates=candidates, listed_checked=True, outcome=None)


__all__ = [
    "ALREADY_LISTED",
    "CATALOG_ONLY",
    "DEFAULT_MANUFACTURING_DAYS",
    "INACTIVE",
    "NO_CATEGORY",
    "NO_MATCH",
    "PRESALE_READY",
    "PresaleCandidate",
    "PresaleQuery",
    "STATUSES",
    "PresaleScan",
    "classify_presale",
    "draft_listing",
]
