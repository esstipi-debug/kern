"""Shopify commerce connector -- the READ half that makes Shopify a first-class
``InventorySource`` (Gap #5), ``[CRED]``, offline-first.

``src/connectors/__init__.py`` has promised a ``shopify.py`` since the package was
written; this is it. ``shopify_prices.py`` and ``shopify_inventory.py`` already
carry the WRITE half (both hold the ``nosilo_core.writeback`` store surface). What
was missing is the side every engine actually consumes: catalog, on-hand and order
history behind the same ``InventorySource`` protocol ``OdooConnector`` implements,
so ``plan_replenishment`` runs against a storefront exactly as it runs against an ERP.

**Why this connector does not stage a restock the way Odoo does.** ``OdooConnector``
stages ``stock.warehouse.orderpoint.product_min_qty`` -- a *reorder point*, a planning
parameter. Shopify has no such field. Its only writable inventory quantity is
``available``: physical units on a shelf. Writing a target cover into ``available``
would not order anything; it would tell Shopify the warehouse holds stock it does not
hold, and oversell against it. So the replenishment path here ends in a protected
**handoff** (POs / export), and the writeback plane is reserved for the one Shopify
write that is actually true-to-life: **reconciliation** against a physical count, via
``stage_reconciliation`` -> ``ShopifyInventoryStore``.

``ShopifyCommerceClient`` is the live GraphQL Admin API transport;
``InMemoryShopifyCommerce`` is the offline stand-in (it also serves the inventory RPC,
so one object drives the whole loop in tests, as ``InMemoryOdoo`` does).
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from nosilo_core import writeback
from src.connectors import InventoryLevel, Order, OrderLine, Product
from src.connectors.shopify_api_version import PINNED_VERSION, check_served
from src.connectors.shopify_inventory import (
    InMemoryShopifyInventory,
    ShopifyInventoryError,
    ShopifyInventoryStore,
)

try:
    import httpx  # noqa: F401  -- solo se testea disponibilidad

    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False

_WB_TARGET = "shopify_inventory"

# Shopify's Admin API caps a page at 250 nodes. The connector reads one page of each
# collection: enough for the demo/pilot catalogs this tool is sold into, and an honest
# ceiling rather than a silent truncation -- `truncated` on the connector says so, and
# the job surfaces it in the deliverable.
_PAGE = 250


class ShopifyCommerceError(RuntimeError):
    """A Shopify catalog/order read failed (transport, auth, or malformed response)."""


@runtime_checkable
class ShopifyCommerceRPC(Protocol):
    """RPC interface for the read side: catalog and order history."""

    def list_variants(self, limit: int = _PAGE) -> list[dict]: ...

    def list_orders(self, since: str | None = None, limit: int = _PAGE) -> list[dict]: ...


class ShopifyCommerceClient:
    """Live Shopify GraphQL Admin API client for catalog + order reads.

    Shares the pinned-version guard with the inventory client: a storefront silently
    served by a different API version than the one requested is the failure mode that
    makes a connector look correct and read the wrong shape.
    """

    def __init__(self, http: Any, *, api_version: str = PINNED_VERSION) -> None:
        if not _HAS_HTTPX:
            raise ShopifyCommerceError(
                "httpx is required for live Shopify transport -- install 'repricing' extra "
                "or use InMemoryShopifyCommerce for offline tests"
            )
        self._http = http
        self._api_version = api_version
        self._graphql_path = f"/admin/api/{api_version}/graphql.json"

    def _graphql(self, query: str, variables: dict) -> dict:
        resp = self._http.post(self._graphql_path, json={"query": query, "variables": variables})
        resp.raise_for_status()
        check_served(resp, requested=self._api_version, is_write=False)
        body = resp.json()
        if body.get("errors"):
            raise ShopifyCommerceError(f"Shopify GraphQL error: {body['errors']}")
        return body["data"]

    def list_variants(self, limit: int = _PAGE) -> list[dict]:
        query = """
        query ListVariants($n: Int!) {
          productVariants(first: $n) {
            edges {
              node {
                id
                sku
                title
                price
                displayName
                inventoryItem {
                  id
                  unitCost { amount }
                }
              }
            }
          }
        }
        """
        data = self._graphql(query, {"n": min(limit, _PAGE)})
        edges = (data.get("productVariants") or {}).get("edges", [])
        return [e["node"] for e in edges]

    def list_orders(self, since: str | None = None, limit: int = _PAGE) -> list[dict]:
        query = """
        query ListOrders($n: Int!, $q: String) {
          orders(first: $n, query: $q, sortKey: CREATED_AT) {
            edges {
              node {
                id
                createdAt
                lineItems(first: 50) {
                  edges {
                    node {
                      sku
                      quantity
                      originalUnitPriceSet { shopMoney { amount } }
                    }
                  }
                }
              }
            }
          }
        }
        """
        variables = {"n": min(limit, _PAGE), "q": f"created_at:>={since}" if since else None}
        data = self._graphql(query, variables)
        edges = (data.get("orders") or {}).get("edges", [])
        return [e["node"] for e in edges]


def _money(value: Any) -> float:
    """Shopify returns money as a decimal STRING; a bare float() on a missing/None
    field is the difference between a $0 cost and a crash mid-read."""
    if value is None:
        return 0.0
    if isinstance(value, dict):
        value = value.get("amount")
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _iso_date(created_at: str) -> str:
    """Shopify timestamps are full ISO-8601 (``2026-01-05T14:22:31Z``). The
    replenishment engine buckets demand per DATE and sorts lexically, so the time
    component would split one day into many one-order periods and flatten the forecast."""
    return (created_at or "")[:10]


class ShopifyConnector:
    """Shopify as an ``InventorySource``: catalog, on-hand at a location, order history.

    ``rpc`` must satisfy ``ShopifyCommerceRPC``; ``inventory_rpc`` must satisfy
    ``ShopifyInventoryRPC`` (pass one object that does both -- the live pair, or
    ``InMemoryShopifyCommerce`` offline).
    """

    def __init__(
        self,
        rpc: ShopifyCommerceRPC,
        inventory_rpc: Any = None,
        *,
        location_id: str = "gid://shopify/Location/1",
        ledger: object | None = None,
    ) -> None:
        self._rpc = rpc
        self._inventory_rpc = inventory_rpc if inventory_rpc is not None else rpc
        self._location_id = location_id
        self._store = ShopifyInventoryStore(self._inventory_rpc, location_id, ledger=ledger)
        self._variants: list[dict] | None = None
        self.truncated = False

    @property
    def location_id(self) -> str:
        return self._location_id

    @property
    def store(self) -> ShopifyInventoryStore:
        """The writeback store this connector reconciles through."""
        return self._store

    def _catalog(self) -> list[dict]:
        if self._variants is None:
            variants = self._rpc.list_variants(_PAGE)
            # A variant with no SKU cannot be joined to demand, inventory or a PO;
            # every engine downstream is SKU-indexed. Dropping it here is honest;
            # carrying it as "" would collide every unlabelled variant into one row.
            self._variants = [v for v in variants if (v.get("sku") or "").strip()]
            self.truncated = len(variants) >= _PAGE
        return self._variants

    def list_products(self) -> list[Product]:
        return [
            Product(
                sku=v["sku"].strip(),
                title=v.get("displayName") or v.get("title") or v["sku"].strip(),
                price=_money(v.get("price")),
                cost=_money((v.get("inventoryItem") or {}).get("unitCost")),
            )
            for v in self._catalog()
        ]

    def inventory_levels(self) -> list[InventoryLevel]:
        levels: list[InventoryLevel] = []
        for v in self._catalog():
            item_id = (v.get("inventoryItem") or {}).get("id")
            if not item_id:
                continue
            qty = self._inventory_rpc.get_inventory_level(item_id, self._location_id)
            levels.append(InventoryLevel(v["sku"].strip(), float(qty), self._location_id))
        return levels

    def orders(self, *, since: str | None = None) -> list[Order]:
        out: list[Order] = []
        for node in self._rpc.list_orders(since, _PAGE):
            lines = tuple(
                OrderLine(
                    sku=(e["node"].get("sku") or "").strip(),
                    quantity=float(e["node"].get("quantity") or 0),
                    price=_money((e["node"].get("originalUnitPriceSet") or {}).get("shopMoney")),
                )
                for e in ((node.get("lineItems") or {}).get("edges") or [])
                if (e["node"].get("sku") or "").strip()
            )
            if not lines:
                continue
            out.append(Order(order_id=str(node.get("id", "")), created_at=_iso_date(node.get("createdAt", "")), lines=lines))
        return out

    def lead_times(self) -> dict[str, float]:
        """Shopify models no supplier and no lead time -- there is no field to read.

        Returned empty rather than defaulted to a plausible number: a fabricated
        14-day lead time would flow into the deliverable indistinguishable from a
        measured one. The job states the absence in words instead.
        """
        return {}

    def stage_reconciliation(
        self, counted: dict[str, float], *, idempotency_key: str, reason: str = "physical count"
    ) -> writeback.Changeset:
        """Stage a dry-run correcting Shopify's ``available`` to a COUNTED quantity.

        This is the only inventory write this connector offers, and the only one that
        is true: the number came from someone looking at a shelf. Nothing mutates until
        ``apply_reconciliation``.
        """
        if not counted:
            raise ValueError("stage_reconciliation needs at least one counted SKU")
        desired = {sku: {"quantity": int(round(qty))} for sku, qty in counted.items()}
        return writeback.stage(
            self._store,
            _WB_TARGET,
            desired,
            risk_tier=writeback.TIER_REVERSIBLE,
            idempotency_key=idempotency_key,
            reason=reason,
        )

    def apply_reconciliation(
        self,
        changeset: writeback.Changeset,
        *,
        approval: writeback.Approval | None = None,
        now: float | None = None,
        auto_apply_reversible: bool = False,
    ) -> writeback.ApplyResult:
        """Commit a staged reconciliation.

        Unlike ``OdooConnector.apply_restock``, this does NOT auto-apply reversible
        edits by default. Odoo's edit moves a planning parameter; this one overwrites
        what the storefront believes is physically on the shelf, and overselling is
        what happens when it is wrong. A human approves, explicitly, every time.
        """
        return writeback.apply(
            self._store, changeset, approval=approval, now=now, auto_apply_reversible=auto_apply_reversible
        )

    def rollback(self, idempotency_key: str) -> None:
        """Undo an applied reconciliation, restoring the quantities that were there."""
        self._store.rollback(idempotency_key)


class InMemoryShopifyCommerce(InMemoryShopifyInventory):
    """Offline stand-in serving BOTH the commerce and inventory RPCs.

    Subclasses the inventory stand-in so one object drives read -> plan -> stage ->
    apply -> rollback with no live credentials, mirroring ``InMemoryOdoo``.
    """

    def __init__(
        self,
        variants: list[dict] | None = None,
        orders: list[dict] | None = None,
        stock: dict[str, dict[str, int]] | None = None,
        locations: list[dict] | None = None,
    ) -> None:
        super().__init__(stock=stock, locations=locations)
        self._variants = list(variants or [])
        self._orders = list(orders or [])

    def list_variants(self, limit: int = _PAGE) -> list[dict]:
        return list(self._variants[:limit])

    def list_orders(self, since: str | None = None, limit: int = _PAGE) -> list[dict]:
        rows = self._orders if since is None else [o for o in self._orders if o.get("createdAt", "") >= since]
        return list(rows[:limit])


def _variant(sku: str, price: float, cost: float) -> dict:
    return {
        "id": f"gid://shopify/ProductVariant/{sku}",
        "sku": sku,
        "title": "Default",
        "displayName": f"Product {sku}",
        "price": f"{price:.2f}",
        "inventoryItem": {"id": f"gid://shopify/InventoryItem/{sku}", "unitCost": {"amount": f"{cost:.2f}"}},
    }


def _order(order_id: str, date: str, lines: list[tuple[str, int, float]]) -> dict:
    return {
        "id": f"gid://shopify/Order/{order_id}",
        "createdAt": f"{date}T12:00:00Z",
        "lineItems": {
            "edges": [
                {"node": {"sku": sku, "quantity": qty, "originalUnitPriceSet": {"shopMoney": {"amount": f"{price:.2f}"}}}}
                for sku, qty, price in lines
            ]
        },
    }


def demo_shopify() -> InMemoryShopifyCommerce:
    """Deterministic Shopify stand-in: 3 SKUs, 8 daily orders, one location.

    Shaped so the replenishment demo is not vacuous: SKU-3 sells steadily on 12 units
    of cover and comes out clearly short, SKU-1 is comfortably long.
    """
    loc = "gid://shopify/Location/1"
    dates = [f"2026-01-0{d}" for d in range(1, 9)]
    orders = [
        _order(str(i + 1), d, [("SKU-1", 2, 40.0), ("SKU-2", 3, 25.0), ("SKU-3", 5, 12.0)])
        for i, d in enumerate(dates)
    ]
    return InMemoryShopifyCommerce(
        variants=[_variant("SKU-1", 40.0, 18.0), _variant("SKU-2", 25.0, 11.0), _variant("SKU-3", 12.0, 5.0)],
        orders=orders,
        stock={"SKU-1": {loc: 150}, "SKU-2": {loc: 45}, "SKU-3": {loc: 12}},
    )


__all__ = [
    "InMemoryShopifyCommerce",
    "ShopifyCommerceClient",
    "ShopifyCommerceError",
    "ShopifyCommerceRPC",
    "ShopifyConnector",
    "ShopifyInventoryError",
    "demo_shopify",
]
