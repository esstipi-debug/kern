"""Offline HTTP store emulator — serves a SimulatedStore over commerce-shaped endpoints.

A FastAPI app that exposes the read side (products / inventory / orders) and a safe
restock POST of a ``SimulatedStore`` over HTTP, shaped like a Shopify Admin / SP-API
response. Paired with ``http_client.StoreApiClient`` it lets a real HTTP client be
developed and tested entirely offline (drive it with FastAPI's TestClient), so the live
adapters are buildable before any client API key exists. No global state: each app is
bound to one store instance.
"""

from __future__ import annotations

from fastapi import Body, FastAPI, Query

from src.connectors.simulator import SimulatedStore


def create_app(store: SimulatedStore) -> FastAPI:
    """Build a FastAPI app serving ``store`` over the emulated admin endpoints."""
    app = FastAPI(title="Linchpin Store Emulator")

    @app.get("/admin/products")
    def products() -> dict:
        return {"products": [
            {"sku": p.sku, "title": p.title, "price": p.price, "cost": p.cost}
            for p in store.list_products()
        ]}

    @app.get("/admin/inventory_levels")
    def inventory_levels() -> dict:
        return {"inventory_levels": [
            {"sku": lvl.sku, "available": lvl.available, "location": lvl.location}
            for lvl in store.inventory_levels()
        ]}

    @app.get("/admin/orders")
    def orders(since: str | None = Query(default=None)) -> dict:
        return {"orders": [
            {
                "id": o.order_id,
                "created_at": o.created_at,
                "line_items": [
                    {"sku": ln.sku, "quantity": ln.quantity, "price": ln.price} for ln in o.lines
                ],
            }
            for o in store.orders(since=since)
        ]}

    @app.post("/admin/inventory/restock")
    def restock(payload: dict = Body(...)) -> dict:
        changeset = store.stage_restock(
            {k: float(v) for k, v in payload["restock"].items()},
            idempotency_key=payload["idempotency_key"],
            reason=payload.get("reason", ""),
        )
        result = store.apply_restock(changeset)
        return {
            "applied": result.applied,
            "idempotent_skip": result.idempotent_skip,
            "audit_id": result.audit_id,
        }

    return app
