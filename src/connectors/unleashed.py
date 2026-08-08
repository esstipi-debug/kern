"""Unleashed connector -- the second ERP, ``[CRED]``, offline-first.

Unleashed is what an Australian SMB runs for inventory on top of Xero, and unlike Xero it
holds the field that matters here: ``MinStockAlertLevel``, a **reorder point** its own
Reorder Report uses to compute suggested replenishment. That puts it on Odoo's side of the
line this repo keeps drawing -- a system that can receive a plan -- and not on Shopify's
and MercadoLibre's, which only hold physical stock. Xero itself has no reorder point at
all, which is why this connector exists and a Xero one does not.

The surface mirrors ``src/connectors/odoo.py`` because ``jobs/channel_replenishment_job.py``
consumes both through the same slot: ``InventorySource`` for reads, ``stage_restock`` /
``apply_restock`` / ``rollback`` for the safe-staging plane, and ``lead_times``.

Two things differ from Odoo and are not incidental:

- **Auth is an HMAC-SHA256 signature over the query string**, not a token. The signature
  covers the parameters only -- not the endpoint, not the ``?`` -- so a request with no
  parameters signs the empty string. Getting that boundary wrong produces a 403 that looks
  like bad credentials.
- **There is no lead time to read.** Unleashed's Product carries stock-alert levels and
  order minimums but no supplier lead time, so ``lead_times`` returns empty, as it does for
  the channels. Unlike them this is "not found on the Product endpoint", not "the system
  has no such concept" -- see the method docstring.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Any, Protocol, runtime_checkable
from urllib.parse import urlencode

from nosilo_core import writeback

from src.connectors import InventoryLevel, Order, OrderLine, Product

try:
    import httpx  # noqa: F401  -- solo se testea disponibilidad

    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False

_WB_TARGET = "unleashed"
_BASE_URL = "https://api.unleashedsoftware.com"

#: Unleashed's documented default page size for SalesOrders is 200; the same ceiling is
#: used for every collection here so one constant governs how much a read walks.
_PAGE = 200
_MAX_PAGES = 10


class UnleashedError(RuntimeError):
    """An Unleashed read/write failed (transport, auth, or unknown product)."""


def sign_query(query_string: str, api_key: str) -> str:
    """The ``api-auth-signature`` header: HMAC-SHA256 of the query string, base64.

    The signature covers the parameters ONLY -- not the endpoint name and not the leading
    ``?``. A request with no parameters signs the empty string. This is the single most
    common way an Unleashed integration fails, and it fails as a 403 that reads like a bad
    key rather than a bad signature.
    """
    digest = hmac.new(api_key.encode("utf-8"), query_string.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


@runtime_checkable
class UnleashedRPC(Protocol):
    """The two operations the connector needs: a paged GET and a product update."""

    def get(self, resource: str, params: dict | None = None) -> dict: ...

    def update_product(self, product_guid: str, changes: dict) -> None: ...


class UnleashedClient:
    """Live Unleashed API transport over any httpx-style object."""

    def __init__(self, http: Any, *, api_id: str, api_key: str,
                 client_type: str = "kern/channel-replenishment") -> None:
        if not _HAS_HTTPX:
            raise UnleashedError(
                "httpx is required for the live Unleashed transport -- install the 'repricing' "
                "extra or use InMemoryUnleashed for offline use"
            )
        self._http = http
        self._api_id = api_id
        self._api_key = api_key
        self._client_type = client_type

    def _headers(self, query_string: str) -> dict[str, str]:
        return {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "api-auth-id": self._api_id,
            "api-auth-signature": sign_query(query_string, self._api_key),
            # Unleashed asks integrators to identify themselves so it can attribute API
            # volume. Omitting it is not fatal today, but being anonymous in someone
            # else's rate-limit accounting is not a position worth being in.
            "client-type": self._client_type,
        }

    def get(self, resource: str, params: dict | None = None) -> dict:
        query = urlencode(sorted((params or {}).items()))
        url = f"/{resource}" + (f"?{query}" if query else "")
        resp = self._http.get(url, headers=self._headers(query))
        resp.raise_for_status()
        return resp.json() or {}

    def update_product(self, product_guid: str, changes: dict) -> None:
        # The update is a POST to the product's own URL; the guid is part of the path and
        # therefore NOT part of the signed query string.
        resp = self._http.post(f"/Products/{product_guid}", json=changes, headers=self._headers(""))
        resp.raise_for_status()


def _money(*candidates: Any) -> float:
    """First positive money value among the candidates, else 0.0.

    Unleashed exposes several cost figures and the public docs are not self-consistent
    about one of the names (``AverageLandPrice`` in the field list, ``AverageLandedPrice``
    in the prose). Both spellings are read rather than betting on one, because betting
    wrong yields a silent 0.0 -- and a zero book cost deflates the restock's value below
    the financial escalation threshold instead of raising an error.
    """
    for value in candidates:
        try:
            amount = float(value)
        except (TypeError, ValueError):
            continue
        if amount > 0:
            return amount
    return 0.0


class UnleashedConnector:
    """Unleashed as an ``InventorySource`` plus a reorder-point writeback."""

    def __init__(self, rpc: UnleashedRPC, *, ledger: object | None = None) -> None:
        self._rpc = rpc
        self._products: list[dict] | None = None
        self._guid_by_sku: dict[str, str] = {}
        self.truncated = False
        self._levels = _MinStockLevelStore(rpc, self._guid_by_sku, ledger=ledger)

    @property
    def store(self):
        """The writeback store this connector stages reorder points through.

        Public so ``tests/test_connector_contract.py`` can hold it to the same six
        invariants as every other store, rather than this connector being the one that
        got a private attribute and an exemption.
        """
        return self._levels

    def _paged(self, resource: str, params: dict | None = None) -> list[dict]:
        """Walk up to ``_MAX_PAGES`` pages. Unleashed wraps every collection in
        ``{"Pagination": {...}, "Items": [...]}``."""
        rows: list[dict] = []
        total_pages = 1
        for page in range(1, _MAX_PAGES + 1):
            body = self._rpc.get(resource, {**(params or {}), "pageSize": _PAGE, "page": page})
            rows.extend(body.get("Items") or [])
            total_pages = int((body.get("Pagination") or {}).get("NumberOfPages", page))
            if page >= total_pages:
                break
        if total_pages > _MAX_PAGES:
            self.truncated = True
        return rows

    def _catalogue(self) -> list[dict]:
        if self._products is None:
            rows = self._paged("Products")
            self._products = [r for r in rows if str(r.get("ProductCode") or "").strip()]
            self._guid_by_sku.clear()
            for row in self._products:
                self._guid_by_sku[str(row["ProductCode"]).strip()] = str(row.get("Guid") or "")
        return self._products

    def list_products(self) -> list[Product]:
        return [
            Product(
                sku=str(row["ProductCode"]).strip(),
                title=str(row.get("ProductDescription") or row["ProductCode"]),
                price=_money(row.get("DefaultSellPrice")),
                cost=_money(row.get("AverageLandPrice"), row.get("AverageLandedPrice"),
                            row.get("LastCost"), row.get("DefaultPurchasePrice")),
            )
            for row in self._catalogue()
        ]

    def inventory_levels(self) -> list[InventoryLevel]:
        """On-hand per SKU, summed across warehouses.

        ``AvailableQty`` is on-hand minus allocated; ``QtyOnHand`` is what is physically
        there. The reorder point is compared against physical stock by Unleashed's own
        Reorder Report, so planning uses ``QtyOnHand`` to stay consistent with the system
        that will act on what gets written.
        """
        totals: dict[str, float] = {}
        for row in self._paged("StockOnHand"):
            sku = str(row.get("ProductCode") or "").strip()
            if sku:
                totals[sku] = totals.get(sku, 0.0) + float(row.get("QtyOnHand") or 0.0)
        return [InventoryLevel(sku, qty, "unleashed") for sku, qty in totals.items()]

    def orders(self, *, since: str | None = None) -> list[Order]:
        """Sales history. Lines arrive with the order, so this is one call per page."""
        params = {"startDate": since} if since else {}
        out: list[Order] = []
        for row in self._paged("SalesOrders", params):
            lines = tuple(
                OrderLine(sku=sku, quantity=float(ln.get("OrderQuantity") or 0),
                          price=_money(ln.get("UnitPrice")))
                for ln in (row.get("SalesOrderLines") or [])
                if (sku := str((ln.get("Product") or {}).get("ProductCode") or "").strip())
            )
            if not lines:
                continue
            out.append(Order(order_id=str(row.get("OrderNumber") or row.get("Guid") or ""),
                             created_at=str(row.get("OrderDate") or "")[:10], lines=lines))
        return out

    def lead_times(self) -> dict[str, float]:
        """Empty: Unleashed's Product carries stock-alert levels and order minimums, but no
        supplier lead time.

        Stated as "not on the endpoints this connector reads" rather than "the system has
        no such concept" -- supplier records were not surveyed. Either way an invented
        default would reach the deliverable indistinguishable from a measured one, which is
        the failure this repo refuses everywhere else.
        """
        return {}

    def stage_restock(self, restock: dict[str, float], *, idempotency_key: str,
                      reason: str = "") -> writeback.Changeset:
        """Stage a dry-run reorder-point update (alert level = on-hand + restock).

        Same interpretation as ``OdooConnector.stage_restock``: the restock delta is the
        target cover, written as the level below which Unleashed's Reorder Report will
        suggest replenishment. Reversible -- applying edits one field.
        """
        self._catalogue()
        on_hand = {lvl.sku: lvl.available for lvl in self.inventory_levels()}
        edits = {
            sku: {"MinStockAlertLevel": round(on_hand.get(sku, 0.0) + float(qty), 4)}
            for sku, qty in restock.items()
        }
        return writeback.stage(
            self._levels, _WB_TARGET, edits,
            risk_tier=writeback.TIER_REVERSIBLE, idempotency_key=idempotency_key, reason=reason,
        )

    def apply_restock(self, changeset: writeback.Changeset, *,
                      approval: writeback.Approval | None = None, now: float | None = None,
                      auto_apply_reversible: bool = True) -> writeback.ApplyResult:
        """Apply a staged reorder-point change. Matches ``OdooConnector.apply_restock``,
        including its default of auto-applying reversible edits: a planning parameter is
        not physical stock, so the bar is the same one Odoo already set."""
        return writeback.apply(self._levels, changeset, approval=approval, now=now,
                               auto_apply_reversible=auto_apply_reversible)

    def rollback(self, idempotency_key: str) -> None:
        self._levels.rollback(idempotency_key)


class _MinStockLevelStore:
    """writeback store surface over Unleashed's ``MinStockAlertLevel``, addressed by SKU."""

    def __init__(self, rpc: UnleashedRPC, guid_by_sku: dict[str, str], *,
                 ledger: object | None = None) -> None:
        self._rpc = rpc
        self._guid_by_sku = guid_by_sku
        self._audit = writeback.AuditBookkeeping(ledger)

    def _guid(self, sku: str) -> str:
        guid = self._guid_by_sku.get(sku)
        if not guid:
            raise UnleashedError(f"unknown product {sku!r} in Unleashed")
        return guid

    def read(self, entity_id: str) -> dict:
        body = self._rpc.get("Products", {"productCode": entity_id, "pageSize": 1, "page": 1})
        items = body.get("Items") or []
        if not items:
            return {}
        row = items[0]
        self._guid_by_sku.setdefault(entity_id, str(row.get("Guid") or ""))
        level = row.get("MinStockAlertLevel")
        return {} if level is None else {"MinStockAlertLevel": float(level)}

    def applied_keys(self) -> set[str]:
        return self._audit.applied_keys()

    def claim(self, idempotency_key: str, *, now: float | None = None) -> bool:
        return self._audit.claim(idempotency_key, now=now)

    def release(self, idempotency_key: str) -> None:
        self._audit.release(idempotency_key)

    def commit(self, changeset: writeback.Changeset, approved_by: str) -> writeback.AuditEntry:
        restore: list[tuple[str, str, object]] = []
        try:
            for change in changeset.changes:
                if change.is_noop:
                    continue
                before = self.read(change.entity_id).get(change.field, writeback.ABSENT)
                restore.append((change.entity_id, change.field, before))
                self._rpc.update_product(self._guid(change.entity_id), {change.field: change.after})
        except Exception:
            self._restore(restore)
            raise
        entry = writeback.AuditEntry(changeset.idempotency_key, changeset.target, approved_by, tuple(restore))
        self._audit.record(entry)
        return entry

    def rollback(self, idempotency_key: str) -> None:
        entry = self._audit.get(idempotency_key)
        if entry is None:
            raise KeyError(idempotency_key)
        self._restore(entry.restore)
        self._audit.forget(idempotency_key)

    def _restore(self, restore) -> None:
        for entity_id, field, original in restore:
            # ABSENT means the field had no value before. Writing None clears it, which is
            # the closest Unleashed offers to "put it back to not set" -- writing 0.0
            # instead would leave a real alert level of zero behind.
            value = None if original is writeback.ABSENT else original
            self._rpc.update_product(self._guid(entity_id), {field: value})


class InMemoryUnleashed:
    """Offline stand-in speaking the same paged shape the real API returns."""

    def __init__(self, products: list[dict] | None = None, stock: list[dict] | None = None,
                 sales_orders: list[dict] | None = None) -> None:
        self._products = list(products or [])
        self._stock = list(stock or [])
        self._orders = list(sales_orders or [])

    def _page(self, rows: list[dict], params: dict) -> dict:
        size = int(params.get("pageSize", _PAGE))
        page = int(params.get("page", 1))
        start = (page - 1) * size
        pages = max(1, -(-len(rows) // size))
        return {
            "Pagination": {"NumberOfItems": len(rows), "PageSize": size,
                           "PageNumber": page, "NumberOfPages": pages},
            "Items": rows[start:start + size],
        }

    def get(self, resource: str, params: dict | None = None) -> dict:
        params = params or {}
        if resource == "Products":
            rows = self._products
            code = params.get("productCode")
            if code:
                rows = [r for r in rows if r.get("ProductCode") == code]
            return self._page(rows, params)
        if resource == "StockOnHand":
            return self._page(self._stock, params)
        if resource == "SalesOrders":
            rows = self._orders
            since = params.get("startDate")
            if since:
                rows = [r for r in rows if str(r.get("OrderDate") or "") >= since]
            return self._page(rows, params)
        raise UnleashedError(f"unknown resource {resource!r}")

    def update_product(self, product_guid: str, changes: dict) -> None:
        for row in self._products:
            if row.get("Guid") == product_guid:
                row.update(changes)
                return
        raise UnleashedError(f"unknown product guid {product_guid!r}")


def _product(sku: str, sell: float, cost: float, alert: float | None = None) -> dict:
    return {
        "Guid": f"guid-{sku}",
        "ProductCode": sku,
        "ProductDescription": f"Producto {sku}",
        "DefaultSellPrice": sell,
        "AverageLandPrice": cost,
        "LastCost": cost,
        "MinStockAlertLevel": alert,
    }


def _sales_order(number: str, date: str, lines: list[tuple[str, float, float]]) -> dict:
    return {
        "Guid": f"so-{number}",
        "OrderNumber": number,
        "OrderDate": f"{date}T00:00:00",
        "SalesOrderLines": [
            {"Product": {"ProductCode": sku}, "OrderQuantity": qty, "UnitPrice": price}
            for sku, qty, price in lines
        ],
    }


def demo_unleashed() -> InMemoryUnleashed:
    """Deterministic stand-in over the same three SKUs as the Odoo demo, so the channel ->
    ERP loop can be run against either ERP and the two compared."""
    dates = [f"2026-01-0{d}" for d in range(1, 9)]
    return InMemoryUnleashed(
        products=[_product("SKU-1", 40.0, 12.0), _product("SKU-2", 25.0, 30.0), _product("SKU-3", 12.0, 5.0)],
        stock=[
            {"ProductCode": "SKU-1", "QtyOnHand": 12.0, "AvailableQty": 12.0, "WarehouseCode": "MAIN"},
            {"ProductCode": "SKU-2", "QtyOnHand": 300.0, "AvailableQty": 300.0, "WarehouseCode": "MAIN"},
            {"ProductCode": "SKU-3", "QtyOnHand": 20.0, "AvailableQty": 18.0, "WarehouseCode": "MAIN"},
        ],
        sales_orders=[_sales_order(str(i + 1), d, [("SKU-2", 4.0, 25.0)]) for i, d in enumerate(dates)],
    )


__all__ = [
    "InMemoryUnleashed",
    "UnleashedClient",
    "UnleashedConnector",
    "UnleashedError",
    "UnleashedRPC",
    "demo_unleashed",
    "sign_query",
]
