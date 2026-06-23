"""Canonical multichannel ledger — one SKU-indexed view over many channels (plan §2.7).

Merges several ``InventorySource`` channels (Shopify, Amazon, ERP, or their offline
simulator/emulator stand-ins) into a single canonical picture: the union catalog, total
on-hand per SKU, the per-channel breakdown, and a combined demand history that drops
straight into the forecasting/inventory engines. It speaks only the ``InventorySource``
protocol, so in-memory and over-HTTP channels merge identically — and the rest of the
chain never learns whether the data was simulated or live.
"""

from __future__ import annotations

import pandas as pd

from src.connectors import InventorySource, Order, Product


class CanonicalLedger:
    """A SKU-indexed merge of named ``InventorySource`` channels."""

    def __init__(self, sources: dict[str, InventorySource]) -> None:
        self._sources = dict(sources)

    def channels(self) -> list[str]:
        return list(self._sources)

    def products(self) -> list[Product]:
        """Union catalog, one canonical record per SKU (first channel that lists it wins)."""
        seen: dict[str, Product] = {}
        for src in self._sources.values():
            for p in src.list_products():
                seen.setdefault(p.sku, p)
        return list(seen.values())

    def inventory_by_channel(self) -> dict[str, dict[str, float]]:
        """``{channel: {sku: available}}`` for the per-channel stock picture."""
        return {
            channel: {lvl.sku: lvl.available for lvl in src.inventory_levels()}
            for channel, src in self._sources.items()
        }

    def inventory_by_sku(self) -> dict[str, float]:
        """Total on-hand per SKU summed across every channel."""
        totals: dict[str, float] = {}
        for src in self._sources.values():
            for lvl in src.inventory_levels():
                totals[lvl.sku] = totals.get(lvl.sku, 0.0) + lvl.available
        return totals

    def channels_for(self, sku: str) -> list[str]:
        """Which channels carry a given SKU (in channel insertion order)."""
        return [
            channel
            for channel, src in self._sources.items()
            if any(lvl.sku == sku for lvl in src.inventory_levels())
        ]

    def orders(self) -> list[Order]:
        """Every channel's orders, merged and sorted by date."""
        merged = [o for src in self._sources.values() for o in src.orders()]
        return sorted(merged, key=lambda o: o.created_at)

    def demand_frame(self) -> pd.DataFrame:
        """Combined ``(date, product_id, quantity, unit_cost)`` demand across channels.

        Order lines are summed per (date, SKU) so two channels selling the same SKU on
        the same day count as one period of total demand. Feeds ``DataFrameDemandSource``.
        """
        costs = {p.sku: p.cost for p in self.products()}
        rows = [
            {
                "date": o.created_at,
                "product_id": line.sku,
                "quantity": line.quantity,
                "unit_cost": costs.get(line.sku, 0.0),
            }
            for src in self._sources.values()
            for o in src.orders()
            for line in o.lines
        ]
        frame = pd.DataFrame(rows, columns=["date", "product_id", "quantity", "unit_cost"])
        if frame.empty:
            return frame
        return frame.groupby(["date", "product_id"], as_index=False).agg(
            quantity=("quantity", "sum"), unit_cost=("unit_cost", "first")
        )
