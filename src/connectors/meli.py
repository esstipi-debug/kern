"""MercadoLibre commerce connector -- the READ half, ``[CRED]``, offline-first.

``meli_prices.py`` already carries a write half (item prices, through the
``nosilo_core.writeback`` store surface). What was missing is the same thing Shopify was
missing before ``shopify.py``: the side every engine consumes. Without an
``InventorySource`` no forecast can run against a MercadoLibre seller account, so the
channel could be repriced but never planned.

**Deliberately read-only for stock.** MercadoLibre's writable quantity is
``available_quantity`` -- physical units, exactly like a Shopify location's ``available``
(see ``src/connectors/shopify.py`` for the argument in full). Writing a target cover there
would not order anything; it would publish stock the seller does not have and oversell
against it. The replenishment plan built from this channel is written to the ERP, by
``jobs/channel_replenishment_job.py``. Prices remain writable through ``meli_prices.py``,
which is a different claim: a price is a decision, not a physical fact.

``MeliCommerceClient`` is the live REST transport (``https://api.mercadolibre.com``, Bearer
token, seller-scoped); ``InMemoryMeliCommerce`` is the offline stand-in.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from src.connectors import InventoryLevel, Order, OrderLine, Product

try:
    import httpx  # noqa: F401  -- solo se testea disponibilidad

    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False

#: The items/search endpoint caps a page at 50 ids, and the multiget at 20 ids per call.
#: Both are MercadoLibre's own limits, not a choice made here.
_SEARCH_PAGE = 50
_MULTIGET = 20

#: How many pages of items and orders a single read walks. A ceiling that is announced
#: (``truncated``) rather than a silent cut -- the same contract the Shopify connector uses.
_MAX_PAGES = 5


class MeliCommerceError(RuntimeError):
    """A MercadoLibre catalogue/order read failed (transport, auth, or malformed response)."""


@runtime_checkable
class MeliCommerceRPC(Protocol):
    """RPC interface for the read side of a seller account."""

    def list_items(self, limit: int = _SEARCH_PAGE, offset: int = 0) -> tuple[list[dict], int]: ...

    def list_orders(self, since: str | None = None, limit: int = _SEARCH_PAGE, offset: int = 0
                    ) -> tuple[list[dict], int]: ...


class MeliCommerceClient:
    """Live MercadoLibre seller API transport for catalogue + order reads.

    ``http`` should already carry ``base_url="https://api.mercadolibre.com"`` and an
    ``Authorization: Bearer <access_token>`` header, matching ``MeliClient`` in
    ``meli_prices.py`` -- the two share a session in practice.
    """

    def __init__(self, http: Any, *, user_id: str) -> None:
        if not _HAS_HTTPX:
            raise MeliCommerceError(
                "httpx is required for the live MercadoLibre transport -- install the 'repricing' "
                "extra or use InMemoryMeliCommerce for offline use"
            )
        self._http = http
        self._user_id = user_id

    def list_items(self, limit: int = _SEARCH_PAGE, offset: int = 0) -> tuple[list[dict], int]:
        """One page of the seller's items, fully hydrated. Returns (items, total)."""
        search = self._http.get(
            f"/users/{self._user_id}/items/search", params={"limit": min(limit, _SEARCH_PAGE), "offset": offset}
        )
        search.raise_for_status()
        body = search.json() or {}
        ids = body.get("results") or []
        total = int((body.get("paging") or {}).get("total", len(ids)))
        return self._hydrate(ids), total

    def _hydrate(self, ids: list[str]) -> list[dict]:
        """Multiget in chunks. A per-id 404 comes back as an entry with a non-200 ``code``
        rather than failing the batch, so a single delisted item must not sink the read."""
        items: list[dict] = []
        for start in range(0, len(ids), _MULTIGET):
            chunk = ids[start:start + _MULTIGET]
            resp = self._http.get("/items", params={"ids": ",".join(chunk)})
            resp.raise_for_status()
            for entry in resp.json() or []:
                if int(entry.get("code", 0)) == 200 and entry.get("body"):
                    items.append(entry["body"])
        return items

    def list_orders(self, since: str | None = None, limit: int = _SEARCH_PAGE, offset: int = 0
                    ) -> tuple[list[dict], int]:
        params: dict[str, Any] = {
            "seller": self._user_id, "limit": min(limit, _SEARCH_PAGE), "offset": offset, "sort": "date_asc",
        }
        if since:
            params["order.date_created.from"] = since
        resp = self._http.get("/orders/search", params=params)
        resp.raise_for_status()
        body = resp.json() or {}
        results = body.get("results") or []
        total = int((body.get("paging") or {}).get("total", len(results)))
        return results, total


def _sku_of(item: dict) -> str:
    """A MercadoLibre item carries the seller's SKU in one of two places.

    ``seller_custom_field`` is the legacy field and still the most populated one;
    ``attributes[SELLER_SKU]`` is the modern one. Reading only one leaves a chunk of real
    catalogues looking SKU-less, which this connector would then drop entirely.
    """
    legacy = (item.get("seller_custom_field") or "").strip()
    if legacy:
        return legacy
    for attribute in item.get("attributes") or []:
        if attribute.get("id") == "SELLER_SKU":
            return str(attribute.get("value_name") or "").strip()
    return ""


def _iso_date(created_at: str) -> str:
    """MercadoLibre timestamps are full ISO-8601 with an offset. The replenishment engine
    buckets demand per DATE, so the time component would split one day into many periods."""
    return (created_at or "")[:10]


class MeliConnector:
    """A MercadoLibre seller account as an ``InventorySource``."""

    def __init__(self, rpc: MeliCommerceRPC) -> None:
        self._rpc = rpc
        self._items: list[dict] | None = None
        self.truncated = False

    def _catalogue(self) -> list[dict]:
        if self._items is None:
            items: list[dict] = []
            total = 0
            for page in range(_MAX_PAGES):
                batch, total = self._rpc.list_items(_SEARCH_PAGE, page * _SEARCH_PAGE)
                items.extend(batch)
                if len(batch) < _SEARCH_PAGE:
                    break
            # An item with no seller SKU cannot be joined to demand, stock or an ERP product.
            self._items = [i for i in items if _sku_of(i)]
            self.truncated = total > len(items)
        return self._items

    def list_products(self) -> list[Product]:
        return [
            Product(
                sku=_sku_of(item),
                title=str(item.get("title") or _sku_of(item)),
                price=float(item.get("price") or 0.0),
                # MercadoLibre models no cost of goods. Left at the DTO default so
                # src.entities._reported_money reads it as "not reported" rather than zero,
                # and the ERP's book cost stands.
                cost=0.0,
            )
            for item in self._catalogue()
        ]

    def inventory_levels(self) -> list[InventoryLevel]:
        return [
            InventoryLevel(_sku_of(item), float(item.get("available_quantity") or 0.0), "meli")
            for item in self._catalogue()
        ]

    def orders(self, *, since: str | None = None) -> list[Order]:
        rows: list[dict] = []
        for page in range(_MAX_PAGES):
            batch, _total = self._rpc.list_orders(since, _SEARCH_PAGE, page * _SEARCH_PAGE)
            rows.extend(batch)
            if len(batch) < _SEARCH_PAGE:
                break

        out: list[Order] = []
        for row in rows:
            lines = tuple(
                OrderLine(
                    sku=sku,
                    quantity=float(oi.get("quantity") or 0),
                    price=float(oi.get("unit_price") or 0.0),
                )
                for oi in (row.get("order_items") or [])
                if (sku := str((oi.get("item") or {}).get("seller_sku") or "").strip())
            )
            if not lines:
                continue
            out.append(Order(order_id=str(row.get("id", "")), created_at=_iso_date(row.get("date_created", "")),
                             lines=lines))
        return out

    def lead_times(self) -> dict[str, float]:
        """MercadoLibre models no supplier and no lead time. Empty rather than defaulted,
        for the same reason as the Shopify connector: a fabricated figure would reach the
        deliverable indistinguishable from a measured one."""
        return {}


class InMemoryMeliCommerce:
    """Offline stand-in for the read side, paging like the real endpoints do."""

    def __init__(self, items: list[dict] | None = None, orders: list[dict] | None = None) -> None:
        self._items = list(items or [])
        self._orders = list(orders or [])

    def list_items(self, limit: int = _SEARCH_PAGE, offset: int = 0) -> tuple[list[dict], int]:
        return self._items[offset:offset + limit], len(self._items)

    def list_orders(self, since: str | None = None, limit: int = _SEARCH_PAGE, offset: int = 0
                    ) -> tuple[list[dict], int]:
        rows = self._orders if since is None else [o for o in self._orders if o.get("date_created", "") >= since]
        return rows[offset:offset + limit], len(rows)


def _item(sku: str, price: float, available: int, *, legacy_sku: bool = True) -> dict:
    attributes = [] if legacy_sku else [{"id": "SELLER_SKU", "value_name": sku}]
    return {
        "id": f"MLA{abs(hash(sku)) % 100000}",
        "title": f"Producto {sku}",
        "price": price,
        "available_quantity": available,
        "seller_custom_field": sku if legacy_sku else None,
        "attributes": attributes,
    }


def _order(oid: str, date: str, lines: list[tuple[str, int, float]]) -> dict:
    return {
        "id": oid,
        "date_created": f"{date}T09:30:00.000-03:00",
        "order_items": [
            {"item": {"id": f"MLA-{sku}", "seller_sku": sku}, "quantity": qty, "unit_price": price}
            for sku, qty, price in lines
        ],
    }


def demo_meli_commerce() -> InMemoryMeliCommerce:
    """Deterministic MercadoLibre stand-in: 3 SKUs matching the Odoo demo catalogue, so the
    channel -> ERP loop demos against this channel exactly as it does against Shopify.

    SKU-2 carries its SKU the modern way (``attributes[SELLER_SKU]``) and the others the
    legacy way, so the demo exercises both spellings rather than one.
    """
    dates = [f"2026-01-0{d}" for d in range(1, 9)]
    return InMemoryMeliCommerce(
        items=[
            _item("SKU-1", 42.0, 30),
            _item("SKU-2", 26.0, 60, legacy_sku=False),
            _item("SKU-3", 13.0, 4),
        ],
        orders=[
            _order(str(1000 + i), d, [("SKU-1", 1, 42.0), ("SKU-3", 4, 13.0)])
            for i, d in enumerate(dates)
        ],
    )


__all__ = [
    "InMemoryMeliCommerce",
    "MeliCommerceClient",
    "MeliCommerceError",
    "MeliCommerceRPC",
    "MeliConnector",
    "demo_meli_commerce",
]
