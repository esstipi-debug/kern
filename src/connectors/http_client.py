"""HTTP store-API client adapter — InventorySource over a commerce HTTP backend.

A thin, transport-agnostic adapter: hand it any object with httpx-style ``.get()`` /
``.post()`` (FastAPI's TestClient for offline tests against ``emulator.create_app``, or a
real ``httpx.Client`` with a base URL + auth headers against a live store). It satisfies
the ``InventorySource`` protocol by mapping the emulated/real JSON back to the canonical
DTOs, so the rest of the chain is identical whether the data is simulated or live.
"""

from __future__ import annotations

from typing import Any

from src.connectors import InventoryLevel, Order, OrderLine, Product


class StoreApiClient:
    """Reads a store over HTTP and maps the responses to canonical connector DTOs."""

    def __init__(self, http: Any) -> None:
        self._http = http  # TestClient or httpx.Client (pre-configured with base_url)

    def list_products(self) -> list[Product]:
        rows = self._http.get("/admin/products").json()["products"]
        return [Product(r["sku"], r["title"], float(r["price"]), float(r.get("cost", 0.0))) for r in rows]

    def inventory_levels(self) -> list[InventoryLevel]:
        rows = self._http.get("/admin/inventory_levels").json()["inventory_levels"]
        return [InventoryLevel(r["sku"], float(r["available"]), r.get("location", "default")) for r in rows]

    def orders(self, *, since: str | None = None) -> list[Order]:
        params = {"since": since} if since else {}
        rows = self._http.get("/admin/orders", params=params).json()["orders"]
        return [
            Order(
                r["id"],
                r["created_at"],
                tuple(OrderLine(li["sku"], float(li["quantity"]), float(li["price"])) for li in r["line_items"]),
            )
            for r in rows
        ]

    def restock(self, restock: dict[str, float], *, idempotency_key: str, reason: str = "") -> dict:
        """POST a restock through the emulator's safe-staging endpoint; return the result."""
        resp = self._http.post(
            "/admin/inventory/restock",
            json={"restock": restock, "idempotency_key": idempotency_key, "reason": reason},
        )
        return resp.json()
