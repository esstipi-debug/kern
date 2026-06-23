"""Offline simulated storefront — a stand-in backend with no live API keys (Gap #5).

``SimulatedStore`` implements the ``InventorySource`` read side (products, inventory,
orders) entirely in memory, bridges its orders into the demand pipeline the engines
already consume, and routes restocks through the safe-staging writeback plane
(``src.writeback``) so applies are dry-run-first, idempotent, audited and reversible.

Point the whole chain at one of these to develop and test connector-driven flows
offline; a real Shopify/Amazon adapter later implements the same protocol.
"""

from __future__ import annotations

import pandas as pd

from src import writeback
from src.connectors import InventoryLevel, Order, OrderLine, Product

_TARGET = "simulated-store"


class SimulatedStore:
    """An in-memory storefront: catalog + inventory + orders, with safe restock."""

    def __init__(
        self,
        products: list[Product],
        levels: list[InventoryLevel],
        orders: list[Order],
    ) -> None:
        self._products = {p.sku: p for p in products}
        self._orders = list(orders)
        # Inventory lives in the writeback store so reads reflect applied restocks and
        # every write goes through the dry-run -> idempotent -> audit/rollback plane.
        self._wb = writeback.InMemoryStore(
            {lvl.sku: {"available": float(lvl.available), "location": lvl.location} for lvl in levels}
        )

    # -- read side (InventorySource) ------------------------------------------

    def list_products(self) -> list[Product]:
        return list(self._products.values())

    def inventory_levels(self) -> list[InventoryLevel]:
        out: list[InventoryLevel] = []
        for sku in self._products:
            rec = self._wb.read(sku)
            out.append(InventoryLevel(sku, float(rec.get("available", 0.0)),
                                      str(rec.get("location", "default"))))
        return out

    def orders(self, *, since: str | None = None) -> list[Order]:
        if since is None:
            return list(self._orders)
        return [o for o in self._orders if o.created_at >= since]

    # -- demand bridge --------------------------------------------------------

    def demand_frame(self) -> pd.DataFrame:
        """Order lines as a (date, product_id, quantity, unit_cost) demand history.

        This is the shape ``src.sources.DataFrameDemandSource`` consumes, so the
        simulated store drops straight into the forecasting / inventory engines.
        """
        rows = [
            {
                "date": o.created_at,
                "product_id": line.sku,
                "quantity": line.quantity,
                "unit_cost": self._products[line.sku].cost if line.sku in self._products else 0.0,
            }
            for o in self._orders
            for line in o.lines
        ]
        return pd.DataFrame(rows, columns=["date", "product_id", "quantity", "unit_cost"])

    # -- write side (safe-staging restock) ------------------------------------

    def stage_restock(
        self, restock: dict[str, float], *, idempotency_key: str, reason: str = ""
    ) -> writeback.Changeset:
        """Stage a dry-run inventory increase (current + qty) without writing."""
        edits = {
            sku: {"available": float(self._wb.read(sku).get("available", 0.0)) + float(qty)}
            for sku, qty in restock.items()
        }
        return writeback.stage(
            self._wb, _TARGET, edits,
            risk_tier=writeback.TIER_REVERSIBLE, idempotency_key=idempotency_key, reason=reason,
        )

    def apply_restock(
        self,
        changeset: writeback.Changeset,
        *,
        approval: writeback.Approval | None = None,
        now: float = 0.0,
        auto_apply_reversible: bool = True,
    ) -> writeback.ApplyResult:
        """Apply a staged restock. Reversible restocks auto-apply by default; pass an
        ``approval`` (and ``auto_apply_reversible=False``) to require a human in the loop."""
        return writeback.apply(
            self._wb, changeset, approval=approval, now=now,
            auto_apply_reversible=auto_apply_reversible,
        )

    def rollback(self, idempotency_key: str) -> None:
        """Undo an applied restock, restoring the prior inventory levels."""
        self._wb.rollback(idempotency_key)


def demo_store() -> SimulatedStore:
    """A small, deterministic store for demos and tests (no randomness)."""
    products = [Product(f"SKU-{i}", f"Item {i}", price=10.0 * i, cost=6.0 * i) for i in range(1, 5)]
    # Mixed on-hand: SKU-1/2 run thin (will need restock), SKU-3/4 are well-stocked.
    _on_hand = {"SKU-1": 10.0, "SKU-2": 30.0, "SKU-3": 200.0, "SKU-4": 300.0}
    levels = [InventoryLevel(p.sku, _on_hand[p.sku]) for p in products]
    dates = ["2026-01-05", "2026-01-20", "2026-02-08", "2026-02-22", "2026-03-10"]
    orders: list[Order] = []
    for j, date in enumerate(dates):
        lines = tuple(
            OrderLine(p.sku, float((i + 1) * (j + 2)), p.price)
            for i, p in enumerate(products)
            if (i + j) % 2 == 0
        )
        orders.append(Order(f"o{j + 1}", date, lines))
    return SimulatedStore(products, levels, orders)
