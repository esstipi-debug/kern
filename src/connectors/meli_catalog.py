"""MercadoLibre official catalog search -- the READ half of preventa, ``[CRED]``,
offline-first.

Distinct from the three MELI surfaces this repo already has:

* ``src/pricing_intel/acquire/meli_api.py`` -- unauthenticated public
  ``GET /items/{id}``. robots.txt on ``api.mercadolibre.com`` is ``Disallow: /``
  (verified 2026-07-12); that fetcher correctly refuses the live host. It does
  NOT implement ``/products/search`` (its own docstring says so).
* ``src/connectors/meli_prices.py`` -- authenticated seller PRICE writeback
  (``PUT /items/{id}``). A different claim: a price is a decision.
* ``feat/shopify-replenishment-tool``'s unmerged ``meli.py`` -- seller
  inventory/orders as an ``InventorySource``. Replenishment, not listing
  research. Still open as PR #238; this module does not land that work.

THIS module is the sanctioned catalog-finder a seller uses BEFORE publishing:
``GET /products/search`` (q or GTIN) and ``GET /categories/{id}/sale_terms``
to see whether the category allows ``MANUFACTURING_TIME`` -- MercadoLibre's
documented stock-availability / preventa term, not a fabricated "preorder"
flag. ``GET /sites/{site}/domain_discovery/search`` fills in a category when
the product row itself does not carry one.

``MeliCatalogClient`` is the live REST transport. It REQUIRES a Bearer token
already on the injected httpx-style client -- the same deploy-time OAuth
wiring as ``MeliClient`` in ``meli_prices.py``. It never starts an OAuth
flow, never scrapes, and never POSTs or PUTs an item. ``InMemoryMeliCatalog``
is the offline stand-in the tests and ``--demo`` CLI exercise.

Required operator scopes (documented, not requested from here): a MercadoLibre
Developers Program app with a user access token that can read
``/products/search``, ``/categories/{id}/sale_terms``, and optionally
``/users/{id}/items/search``. Typical grant: ``offline_access`` plus the app's
read scope. Until those exist, use the in-memory stand-in.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable

from src.connectors import WRITE_NONE, WriteSurface

try:
    import httpx  # noqa: F401  -- availability only; the caller builds the client

    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False

#: Official catalog product search is documented for these site ids.
CATALOG_SITES = frozenset({"MLA", "MLB", "MLM", "MLC", "MCO", "MLU"})

#: MercadoLibre's documented preventa / stock-availability sale term.
#: A listing with this term stays active while the goods are not yet on the
#: shelf; buyers see when the item will arrive. Not a separate "preorder"
#: listing type -- the official docs name this id.
MANUFACTURING_TIME = "MANUFACTURING_TIME"

_DEFAULT_LIMIT = 10
_MAX_LIMIT = 50


class MeliCatalogError(RuntimeError):
    """A MercadoLibre catalog / sale-terms read failed (transport, auth, or shape)."""


@runtime_checkable
class MeliCatalogRPC(Protocol):
    """The three reads the preventa scan needs against a seller-authorized app."""

    def search_products(
        self,
        *,
        site_id: str,
        q: str | None = None,
        product_identifier: str | None = None,
        status: str = "active",
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
    ) -> dict: ...

    def category_sale_terms(self, category_id: str) -> list[dict]: ...

    def discover_category(self, site_id: str, query: str) -> str | None: ...

    def seller_catalog_ids(self) -> frozenset[str]: ...


def _bearer_of(http: Any) -> str:
    """The Authorization header already configured on ``http``, or empty.

    MercadoLibre's public host is robots-prohibited without a sanctioned
    Developers Program token. A live client that cannot show a Bearer token
    must not hit the network -- that would be the unauthenticated 403 path
    ``meli_api.py`` already refused.
    """
    headers = getattr(http, "headers", None) or {}
    if hasattr(headers, "get"):
        raw = headers.get("Authorization") or headers.get("authorization") or ""
    else:
        raw = ""
    return str(raw).strip()


def manufacturing_time_allowed(sale_terms: list[dict] | tuple[dict, ...]) -> bool:
    """True when the category's sale_terms list includes ``MANUFACTURING_TIME``."""
    for term in sale_terms:
        if str(term.get("id") or "").strip() == MANUFACTURING_TIME:
            return True
    return False


def _attr(product: dict, attr_id: str) -> str:
    for attribute in product.get("attributes") or []:
        if str(attribute.get("id") or "") == attr_id:
            return str(attribute.get("value_name") or "").strip()
    return ""


def gtin_of(product: dict) -> str:
    """GTIN/EAN/UPC as MercadoLibre spells it on a catalog product (``GTIN``)."""
    return _attr(product, "GTIN")


def category_of(product: dict) -> str:
    """Category id from the product itself, or from the buy-box winner.

    Catalog search rows often omit ``category_id`` and only carry ``domain_id``.
    The official product resource puts the selling category on
    ``buy_box_winner.category_id`` when a winner exists. Empty means the
    caller should ask :meth:`MeliCatalogRPC.discover_category` -- never guess.
    """
    direct = str(product.get("category_id") or "").strip()
    if direct:
        return direct
    winner = product.get("buy_box_winner") or {}
    if isinstance(winner, dict):
        return str(winner.get("category_id") or "").strip()
    return ""


def listing_strategy_of(product: dict) -> str:
    settings = product.get("settings") or {}
    if isinstance(settings, dict):
        return str(settings.get("listing_strategy") or "").strip()
    return ""


@dataclass(frozen=True)
class CatalogProduct:
    """One row of ``GET /products/search`` after a conservative parse."""

    product_id: str
    site_id: str
    name: str
    status: str
    domain_id: str
    category_id: str
    listing_strategy: str
    gtin: str
    permalink: str


def parse_product_search(body: dict, *, site_id: str) -> tuple[CatalogProduct, ...]:
    """Parse one ``/products/search`` JSON object. Pure -- no network.

    Raises :class:`MeliCatalogError` on an error envelope or a non-object body.
    A well-formed empty ``results`` list is a valid miss, not an error.
    """
    if not isinstance(body, dict):
        raise MeliCatalogError(f"expected a JSON object, got {type(body).__name__}")
    if "error" in body and "results" not in body:
        raise MeliCatalogError(
            f"MercadoLibre API error response: {body.get('message') or body.get('error')}"
        )
    rows: list[CatalogProduct] = []
    for raw in body.get("results") or []:
        if not isinstance(raw, dict):
            continue
        product_id = str(raw.get("id") or "").strip()
        if not product_id:
            continue
        rows.append(
            CatalogProduct(
                product_id=product_id,
                site_id=str(raw.get("site_id") or site_id),
                name=str(raw.get("name") or ""),
                status=str(raw.get("status") or "").strip().lower(),
                domain_id=str(raw.get("domain_id") or ""),
                category_id=category_of(raw),
                listing_strategy=listing_strategy_of(raw),
                gtin=gtin_of(raw),
                permalink=str(raw.get("permalink") or ""),
            )
        )
    return tuple(rows)


class MeliCatalogClient:
    """Live MercadoLibre catalog transport over any httpx-style object.

    ``http`` must already carry ``base_url="https://api.mercadolibre.com"`` and
    ``Authorization: Bearer <access_token>``. This class does not acquire or
    refresh that token. ``user_id`` is optional and only used to list the
    seller's own ``catalog_product_id`` values.
    """

    def __init__(self, http: Any, *, user_id: str = "") -> None:
        if not _HAS_HTTPX:
            raise MeliCatalogError(
                "httpx is required for the live MercadoLibre catalog transport -- "
                "install the 'repricing' extra or use InMemoryMeliCatalog"
            )
        if not _bearer_of(http):
            raise MeliCatalogError(
                "live MercadoLibre catalog reads require a Bearer token on the "
                "http client (Developers Program OAuth). Unauthenticated calls "
                "against api.mercadolibre.com are robots-prohibited; use "
                "InMemoryMeliCatalog for offline work"
            )
        self._http = http
        self._user_id = user_id
        self._terms_cache: dict[str, list[dict]] = {}
        self._listed: frozenset[str] | None = None

    def search_products(
        self,
        *,
        site_id: str,
        q: str | None = None,
        product_identifier: str | None = None,
        status: str = "active",
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
    ) -> dict:
        site = site_id.strip().upper()
        if site not in CATALOG_SITES:
            raise MeliCatalogError(
                f"site_id {site_id!r} is not a documented catalog site "
                f"({', '.join(sorted(CATALOG_SITES))})"
            )
        query = (q or "").strip()
        gtin = (product_identifier or "").strip()
        if not query and not gtin:
            raise MeliCatalogError("search requires q or product_identifier")
        params: dict[str, Any] = {
            "site_id": site,
            "limit": min(max(int(limit), 1), _MAX_LIMIT),
            "offset": max(int(offset), 0),
        }
        if status.strip():
            params["status"] = status
        if gtin:
            params["product_identifier"] = gtin
        else:
            params["q"] = query
        resp = self._http.get("/products/search", params=params)
        resp.raise_for_status()
        body = resp.json()
        if not isinstance(body, dict):
            raise MeliCatalogError("products/search returned a non-object body")
        return body

    def category_sale_terms(self, category_id: str) -> list[dict]:
        cid = category_id.strip()
        if not cid:
            raise MeliCatalogError("category_id is required")
        if cid in self._terms_cache:
            return self._terms_cache[cid]
        resp = self._http.get(f"/categories/{cid}/sale_terms")
        resp.raise_for_status()
        body = resp.json()
        if not isinstance(body, list):
            raise MeliCatalogError("sale_terms returned a non-list body")
        self._terms_cache[cid] = body
        return body

    def discover_category(self, site_id: str, query: str) -> str | None:
        q = query.strip()
        if not q:
            return None
        resp = self._http.get(
            f"/sites/{site_id.strip().upper()}/domain_discovery/search",
            params={"q": q},
        )
        resp.raise_for_status()
        body = resp.json()
        if not isinstance(body, list) or not body:
            return None
        first = body[0] if isinstance(body[0], dict) else {}
        cid = str(first.get("category_id") or "").strip()
        return cid or None

    def seller_catalog_ids(self) -> frozenset[str]:
        """The seller's own listings' ``catalog_product_id`` values (one page).

        Used only to mark a catalog hit as already listed. Empty when no
        ``user_id`` was configured -- that absence is reported, not guessed.
        """
        if self._listed is not None:
            return self._listed
        if not self._user_id:
            self._listed = frozenset()
            return self._listed
        search = self._http.get(
            f"/users/{self._user_id}/items/search",
            params={"limit": _MAX_LIMIT, "offset": 0},
        )
        search.raise_for_status()
        ids = (search.json() or {}).get("results") or []
        if not ids:
            self._listed = frozenset()
            return self._listed
        resp = self._http.get(
            "/items",
            params={"ids": ",".join(str(i) for i in ids[:20]), "attributes": "id,catalog_product_id"},
        )
        resp.raise_for_status()
        found: set[str] = set()
        for entry in resp.json() or []:
            body = entry.get("body") if isinstance(entry, dict) else None
            if not isinstance(body, dict):
                continue
            cid = str(body.get("catalog_product_id") or "").strip()
            if cid:
                found.add(cid)
        self._listed = frozenset(found)
        return self._listed


class InMemoryMeliCatalog:
    """Offline stand-in for catalog search + sale_terms + seller listings."""

    def __init__(
        self,
        products: list[dict] | None = None,
        sale_terms: dict[str, list[dict]] | None = None,
        listed_catalog_ids: set[str] | frozenset[str] | None = None,
        domain_categories: dict[str, str] | None = None,
    ) -> None:
        self._products = list(products or [])
        self._sale_terms = {k: list(v) for k, v in (sale_terms or {}).items()}
        self._listed = frozenset(listed_catalog_ids or ())
        self._domains = dict(domain_categories or {})
        self.calls: list[tuple[str, dict]] = []

    def search_products(
        self,
        *,
        site_id: str,
        q: str | None = None,
        product_identifier: str | None = None,
        status: str = "active",
        limit: int = _DEFAULT_LIMIT,
        offset: int = 0,
    ) -> dict:
        self.calls.append(
            ("search_products", {
                "site_id": site_id, "q": q, "product_identifier": product_identifier,
                "status": status, "limit": limit, "offset": offset,
            })
        )
        query = (q or "").strip().lower()
        gtin = (product_identifier or "").strip()
        if not query and not gtin:
            raise MeliCatalogError("search requires q or product_identifier")
        hits: list[dict] = []
        for product in self._products:
            if gtin:
                if gtin_of(product) != gtin:
                    continue
            elif query not in str(product.get("name") or "").lower():
                continue
            if status and str(product.get("status") or "").lower() != status.lower():
                continue
            hits.append(product)
        sliced = hits[offset:offset + limit]
        return {"keywords": q or gtin, "paging": {"total": len(hits), "limit": limit, "offset": offset},
                "results": sliced}

    def category_sale_terms(self, category_id: str) -> list[dict]:
        self.calls.append(("category_sale_terms", {"category_id": category_id}))
        if category_id not in self._sale_terms:
            raise MeliCatalogError(f"unknown category {category_id!r}")
        return list(self._sale_terms[category_id])

    def discover_category(self, site_id: str, query: str) -> str | None:
        self.calls.append(("discover_category", {"site_id": site_id, "query": query}))
        return self._domains.get(query.strip().lower()) or self._domains.get(query.strip())

    def seller_catalog_ids(self) -> frozenset[str]:
        self.calls.append(("seller_catalog_ids", {}))
        return self._listed


def _product(
    product_id: str,
    name: str,
    *,
    status: str = "active",
    domain_id: str = "MLA-CELLPHONES",
    category_id: str = "MLA1055",
    gtin: str = "",
    listing_strategy: str = "catalog_required",
    permalink: str = "",
) -> dict:
    attributes = [{"id": "GTIN", "value_name": gtin}] if gtin else []
    return {
        "id": product_id,
        "site_id": "MLA",
        "status": status,
        "domain_id": domain_id,
        "category_id": category_id,
        "name": name,
        "permalink": permalink or f"https://www.mercadolibre.com.ar/p/{product_id}",
        "settings": {"listing_strategy": listing_strategy},
        "attributes": attributes,
    }


def demo_meli_catalog() -> InMemoryMeliCatalog:
    """Deterministic catalog: four products that exercise every preventa verdict.

    * MLA-PRE-1 -- active, category allows MANUFACTURING_TIME, not listed.
    * MLA-CAT-2 -- active, category has no preventa term.
    * MLA-OFF-3 -- inactive (not electable for catalog listing).
    * MLA-OWN-4 -- active + preventa-capable, already on the seller account.
    """
    allow = [{"id": MANUFACTURING_TIME, "name": "Disponibilidad de stock"}]
    deny = [{"id": "WARRANTY_TYPE", "name": "Tipo de garantia"}]
    return InMemoryMeliCatalog(
        products=[
            _product("MLA-PRE-1", "Auriculares inalambricos demo", gtin="7790000000001"),
            _product("MLA-CAT-2", "Cable USB-C demo", category_id="MLA1234", gtin="7790000000002",
                     listing_strategy="open"),
            _product("MLA-OFF-3", "Telefono descatalogado demo", status="inactive",
                     category_id="MLA1055", gtin="7790000000003"),
            _product("MLA-OWN-4", "Cargador ya publicado demo", gtin="7790000000004"),
        ],
        sale_terms={"MLA1055": allow, "MLA1234": deny},
        listed_catalog_ids={"MLA-OWN-4"},
        domain_categories={"auriculares inalambricos demo": "MLA1055"},
    )


#: Que escribe este conector en el sistema del cliente (ver ``WriteSurface``).
WRITE_SURFACE = WriteSurface(
    WRITE_NONE,
    means=(
        "solo lectura del catalogo oficial de MercadoLibre (/products/search, "
        "/categories). La mitad de ESCRITURA de preventa (publicar o modificar una "
        "publicacion) no vive aca: los cambios de precio van por meli_prices.py, con "
        "dry-run y aprobacion firmada. Buscar un producto en el catalogo ajeno no "
        "cambia nada en ningun sistema."
    ),
)

__all__ = [
    "CATALOG_SITES",
    "CatalogProduct",
    "InMemoryMeliCatalog",
    "MANUFACTURING_TIME",
    "MeliCatalogClient",
    "MeliCatalogError",
    "MeliCatalogRPC",
    "category_of",
    "demo_meli_catalog",
    "gtin_of",
    "listing_strategy_of",
    "manufacturing_time_allowed",
    "parse_product_search",
]
