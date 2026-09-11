"""Shopify Admin API inventory writeback connector (Linchpin / Kern 3.0) -- ``[CRED]``, offline-first.

Mirrors ``src/connectors/shopify_prices.py`` and ``src/connectors/odoo.py``:

- ``ShopifyInventoryRPC`` is the Protocol defining RPC operations:
  finding inventory item by SKU, querying stock by location, adjusting and setting stock.
- ``ShopifyInventoryClient`` is the real transport over Shopify's GraphQL Admin API
  (inventorySetQuantities, inventoryAdjustQuantities, locations). Both inventory
  mutations carry the ``@idempotent`` directive, required since API version
  2026-04.
- ``ShopifyInventoryStore`` holds the writeback ``store`` surface
  (read/applied_keys/claim/release/commit/rollback) speaking ``ShopifyInventoryRPC``.
- ``InMemoryShopifyInventory`` is the offline stand-in for zero-dependency testing.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from nosilo_core import writeback
from src.connectors import WRITE_APPROVAL_GATED, WriteSurface
from src.connectors.shopify_api_version import PINNED_VERSION, check_served

try:
    import httpx  # noqa: F401  -- solo se testea disponibilidad

    _HAS_HTTPX = True
except ImportError:
    _HAS_HTTPX = False

_WB_TARGET = "shopify_inventory"


class ShopifyInventoryError(RuntimeError):
    """A Shopify inventory read/write failed (transport, auth, or unknown SKU)."""


@runtime_checkable
class ShopifyInventoryRPC(Protocol):
    """RPC interface for inventory operations against Shopify Admin API."""

    def list_locations(self) -> list[dict]: ...

    def find_inventory_item_by_sku(self, sku: str) -> dict | None: ...

    def get_inventory_level(self, inventory_item_id: str, location_id: str) -> int: ...

    def adjust_inventory(self, inventory_item_id: str, location_id: str, delta: int,
                         reason: str = "correction", *, idempotency_key: str) -> int: ...

    def set_inventory(self, inventory_item_id: str, location_id: str, available: int,
                      reason: str = "correction", *, idempotency_key: str) -> int: ...


class ShopifyInventoryClient:
    """Real Shopify GraphQL Admin API client for inventory management."""

    def __init__(self, http: Any, *, api_version: str = PINNED_VERSION) -> None:
        if not _HAS_HTTPX:
            raise ShopifyInventoryError(
                "httpx is required for live Shopify transport -- install 'repricing' extra "
                "or use InMemoryShopifyInventory for offline tests"
            )
        self._http = http
        self._api_version = api_version
        self._graphql_path = f"/admin/api/{api_version}/graphql.json"
        self._item_id_cache: dict[str, dict] = {}

    def _graphql(self, query: str, variables: dict, *, is_write: bool = False) -> dict:
        resp = self._http.post(self._graphql_path, json={"query": query, "variables": variables})
        resp.raise_for_status()
        check_served(resp, requested=self._api_version, is_write=is_write)
        body = resp.json()
        if body.get("errors"):
            raise ShopifyInventoryError(f"Shopify GraphQL error: {body['errors']}")
        return body["data"]

    def list_locations(self) -> list[dict]:
        query = """
        query ListLocations {
          locations(first: 50, includeInactive: false) {
            edges {
              node {
                id
                name
                isPrimary
              }
            }
          }
        }
        """
        data = self._graphql(query, {})
        edges = (data.get("locations") or {}).get("edges", [])
        return [e["node"] for e in edges]

    def find_inventory_item_by_sku(self, sku: str) -> dict | None:
        if sku in self._item_id_cache:
            return self._item_id_cache[sku]
        query = """
        query FindVariantInventoryBySku($q: String!) {
          productVariants(first: 1, query: $q) {
            edges {
              node {
                id
                sku
                inventoryItem {
                  id
                  tracked
                }
              }
            }
          }
        }
        """
        data = self._graphql(query, {"q": f"sku:{sku}"})
        edges = (data.get("productVariants") or {}).get("edges", [])
        if not edges:
            return None
        node = edges[0]["node"]
        inv_item = node.get("inventoryItem") or {}
        res = {
            "sku": node["sku"],
            "variant_id": node["id"],
            "inventory_item_id": inv_item.get("id"),
            "tracked": inv_item.get("tracked", True),
        }
        self._item_id_cache[sku] = res
        return res

    def get_inventory_level(self, inventory_item_id: str, location_id: str) -> int:
        query = """
        query GetInventoryLevel($id: ID!) {
          inventoryItem(id: $id) {
            inventoryLevels(first: 50) {
              edges {
                node {
                  location { id }
                  quantities(names: ["available"]) {
                    name
                    quantity
                  }
                }
              }
            }
          }
        }
        """
        data = self._graphql(query, {"id": inventory_item_id})
        item = data.get("inventoryItem") or {}
        levels = (item.get("inventoryLevels") or {}).get("edges", [])
        for edge in levels:
            node = edge["node"]
            if node["location"]["id"] == location_id:
                quantities = node.get("quantities", [])
                for q in quantities:
                    if q["name"] == "available":
                        return int(q["quantity"])
        return 0

    def adjust_inventory(self, inventory_item_id: str, location_id: str, delta: int,
                         reason: str = "correction", *, idempotency_key: str) -> int:
        mutation = """
        mutation AdjustInventory($input: InventoryAdjustQuantitiesInput!, $idempotencyKey: String!)
        @idempotent(key: $idempotencyKey) {
          inventoryAdjustQuantities(input: $input) {
            inventoryAdjustmentGroup {
              changes {
                delta
                quantityAfterChange
              }
            }
            userErrors {
              field
              message
            }
          }
        }
        """
        input_data = {
            "reason": reason,
            "name": "available",
            "changes": [
                {
                    "inventoryItemId": inventory_item_id,
                    "locationId": location_id,
                    "delta": delta,
                }
            ],
        }
        data = self._graphql(
            mutation,
            {"input": input_data, "idempotencyKey": idempotency_key},
            is_write=True,
        )
        res = data.get("inventoryAdjustQuantities") or {}
        errors = res.get("userErrors") or []
        if errors:
            raise ShopifyInventoryError(f"Shopify rejected inventory adjustment: {errors}")
        changes = (res.get("inventoryAdjustmentGroup") or {}).get("changes", [])
        if not changes:
            raise ShopifyInventoryError(
                f"Shopify did not confirm the inventory adjustment for {inventory_item_id!r} "
                f"at {location_id!r}: no inventoryAdjustmentGroup in the response"
            )
        return int(changes[0]["quantityAfterChange"])

    def set_inventory(self, inventory_item_id: str, location_id: str, available: int,
                      reason: str = "correction", *, idempotency_key: str) -> int:
        mutation = """
        mutation SetInventory($input: InventorySetQuantitiesInput!, $idempotencyKey: String!)
        @idempotent(key: $idempotencyKey) {
          inventorySetQuantities(input: $input) {
            inventoryAdjustmentGroup {
              changes {
                quantityAfterChange
              }
            }
            userErrors {
              field
              message
            }
          }
        }
        """
        input_data = {
            "reason": reason,
            "name": "available",
            "ignoreCompareQuantity": True,
            "quantities": [
                {
                    "inventoryItemId": inventory_item_id,
                    "locationId": location_id,
                    "quantity": available,
                }
            ],
        }
        data = self._graphql(
            mutation,
            {"input": input_data, "idempotencyKey": idempotency_key},
            is_write=True,
        )
        res = data.get("inventorySetQuantities") or {}
        errors = res.get("userErrors") or []
        if errors:
            raise ShopifyInventoryError(f"Shopify rejected setting inventory: {errors}")
        # The payload field is inventoryAdjustmentGroup, not inventorySetGroup. Echoing
        # `available` back on a missing field made a failed write look like a success.
        changes = (res.get("inventoryAdjustmentGroup") or {}).get("changes", [])
        if not changes:
            raise ShopifyInventoryError(
                f"Shopify did not confirm the inventory set for {inventory_item_id!r} "
                f"at {location_id!r}: no inventoryAdjustmentGroup in the response"
            )
        return int(changes[0]["quantityAfterChange"])


class ShopifyInventoryStore:
    """writeback system-of-record store for Shopify Inventory (addressed by SKU@location_id)."""

    def __init__(self, rpc: ShopifyInventoryRPC, location_id: str, *, ledger: object | None = None) -> None:
        self._rpc = rpc
        self._location_id = location_id
        self._audit = writeback.AuditBookkeeping(ledger)

    def read(self, entity_id: str) -> dict:
        item = self._rpc.find_inventory_item_by_sku(entity_id)
        if item is None or not item.get("inventory_item_id"):
            return {}
        qty = self._rpc.get_inventory_level(item["inventory_item_id"], self._location_id)
        return {"quantity": qty}

    def applied_keys(self) -> set[str]:
        return self._audit.applied_keys()

    def claim(self, idempotency_key: str, *, now: float | None = None) -> bool:
        return self._audit.claim(idempotency_key, now=now)

    def release(self, idempotency_key: str) -> None:
        self._audit.release(idempotency_key)

    def commit(self, changeset: writeback.Changeset, approved_by: str) -> writeback.AuditEntry:
        restore: list[tuple[str, str, object]] = []
        try:
            for c in changeset.changes:
                if c.is_noop:
                    continue
                item = self._rpc.find_inventory_item_by_sku(c.entity_id)
                if item is None or not item.get("inventory_item_id"):
                    raise ShopifyInventoryError(f"unknown SKU {c.entity_id!r} in Shopify")
                inv_item_id = item["inventory_item_id"]
                current_qty = self._rpc.get_inventory_level(inv_item_id, self._location_id)
                restore.append((c.entity_id, c.field, current_qty))
                new_qty = int(c.after)
                # One key per (changeset, entity): deterministic so a genuine retry
                # of the same write dedupes, but distinct per SKU so Shopify does not
                # reject sibling writes in the same changeset with
                # IDEMPOTENCY_KEY_PARAMETER_MISMATCH (it rejects reuse with different
                # parameters rather than silently deduping).
                self._rpc.set_inventory(inv_item_id, self._location_id, new_qty,
                                        idempotency_key=f"{changeset.idempotency_key}:{c.entity_id}")
        except Exception:
            self._apply_restore(restore, idempotency_key=changeset.idempotency_key)
            raise

        entry = writeback.AuditEntry(changeset.idempotency_key, changeset.target, approved_by, tuple(restore))
        self._audit.record(entry)
        return entry

    def rollback(self, idempotency_key: str) -> None:
        entry = self._audit.get(idempotency_key)
        if entry is None:
            raise KeyError(idempotency_key)
        self._apply_restore(entry.restore, idempotency_key=idempotency_key)
        self._audit.forget(idempotency_key)

    def _apply_restore(self, restore: list[tuple[str, str, object]] | tuple[tuple[str, str, object], ...],
                       *, idempotency_key: str) -> None:
        for entity_id, _field, original in restore:
            item = self._rpc.find_inventory_item_by_sku(entity_id)
            if item is None or not item.get("inventory_item_id"):
                raise ShopifyInventoryError(f"cannot restore: SKU {entity_id!r} no longer exists")
            # Same rationale as commit(): one key per (changeset, entity), plus the
            # ":restore" suffix so a restore never collides with the forward write
            # it undoes, and distinct entities never collide with each other.
            self._rpc.set_inventory(item["inventory_item_id"], self._location_id, int(original),
                                    idempotency_key=f"{idempotency_key}:{entity_id}:restore")


class InMemoryShopifyInventory:
    """Offline stand-in for Shopify inventory management testing."""

    def __init__(self, stock: dict[str, dict[str, int]] | None = None, locations: list[dict] | None = None) -> None:
        self._stock: dict[str, dict[str, int]] = {sku: dict(locs) for sku, locs in (stock or {}).items()}
        self._locations = locations or [{"id": "gid://shopify/Location/1", "name": "Main Warehouse", "isPrimary": True}]
        # Records every key it was told, so callers can assert the key was threaded
        # through and the parameter is not inert -- it does not emulate Shopify's
        # dedup behaviour, since every test defaults to the same "offline" key.
        self.idempotency_keys: list[str] = []

    def list_locations(self) -> list[dict]:
        return list(self._locations)

    def find_inventory_item_by_sku(self, sku: str) -> dict | None:
        if sku not in self._stock:
            return None
        return {
            "sku": sku,
            "variant_id": f"gid://shopify/ProductVariant/{sku}",
            "inventory_item_id": f"gid://shopify/InventoryItem/{sku}",
            "tracked": True,
        }

    def get_inventory_level(self, inventory_item_id: str, location_id: str) -> int:
        sku = inventory_item_id.replace("gid://shopify/InventoryItem/", "")
        return self._stock.get(sku, {}).get(location_id, 0)

    def adjust_inventory(self, inventory_item_id: str, location_id: str, delta: int,
                         reason: str = "correction", *, idempotency_key: str = "offline") -> int:
        self.idempotency_keys.append(idempotency_key)
        sku = inventory_item_id.replace("gid://shopify/InventoryItem/", "")
        if sku not in self._stock:
            self._stock[sku] = {}
        curr = self._stock[sku].get(location_id, 0)
        new_val = max(0, curr + delta)
        self._stock[sku][location_id] = new_val
        return new_val

    def set_inventory(self, inventory_item_id: str, location_id: str, available: int,
                      reason: str = "correction", *, idempotency_key: str = "offline") -> int:
        self.idempotency_keys.append(idempotency_key)
        sku = inventory_item_id.replace("gid://shopify/InventoryItem/", "")
        if sku not in self._stock:
            self._stock[sku] = {}
        self._stock[sku][location_id] = available
        return available


def demo_shopify_inventory() -> InMemoryShopifyInventory:
    """Deterministic in-memory Shopify inventory store for tests and demos."""
    loc = "gid://shopify/Location/1"
    return InMemoryShopifyInventory(
        stock={
            "SKU-1": {loc: 150},
            "SKU-2": {loc: 45},
            "SKU-3": {loc: 12},
        }
    )


#: Que escribe este conector en el sistema del cliente (ver ``WriteSurface``).
WRITE_SURFACE = WriteSurface(
    WRITE_APPROVAL_GATED,
    objects=(
        'inventoryLevel (adjust / set) por ubicacion',
    ),
    means=(
        'la escritura existe en el conector pero la superficie web/MCP de Kern es de solo lectura sobre Shopify por politica (dos tests lo vigilan); solo un operador con credenciales explicitas la usa.'
    ),
)
