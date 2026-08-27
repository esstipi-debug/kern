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


def _number(row: dict, key: str) -> float | None:
    """A reported quantity, or ``None`` when the backend did not report one.

    A store that says nothing about deliveries and one that says nothing shipped
    are different facts (``src.order_fulfillment`` counts them in different
    denominators), and over JSON both arrive as an absent key or a ``null``, never
    as a number. Coercing either to 0.0 here is what turns a silent gap into a
    confident 0% fill rate downstream.
    """
    value = row.get(key)
    return None if value is None else float(value)


def _text(row: dict, key: str) -> str | None:
    value = row.get(key)
    return None if value is None else str(value)


def _line(li: dict) -> OrderLine:
    return OrderLine(
        li["sku"],
        float(li["quantity"]),
        float(li["price"]),
        delivered_qty=_number(li, "delivered_qty"),
        promised_date=_text(li, "promised_date"),
        delivered_date=_text(li, "delivered_date"),
    )


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
                tuple(_line(li) for li in r["line_items"]),
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
