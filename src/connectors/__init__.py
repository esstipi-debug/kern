"""Commerce connectors — read a client's storefront/ERP, write back safely (Gap #5).

The plumbing that lets Linchpin run against a real e-commerce / ERP backend. A thin
``InventorySource`` protocol (products, inventory levels, orders) sits in front of every
backend, exactly as ``src.sources.DemandSource`` does for demand — so swapping CSV for a
live Shopify/Amazon adapter later means writing one adapter, not touching the engines.

The write side reuses the safe-staging plane in ``src.writeback`` (dry-run -> approval ->
idempotent apply -> audit/rollback): connectors never mutate a system of record directly.

This package ships an offline ``simulator.SimulatedStore`` so the whole pipeline can run
end-to-end without any live API keys; real adapters (``shopify.py``, ``amazon.py`` ...)
implement the same protocol against their SDKs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class Product:
    """A catalog item, canonical and SKU-indexed across every connector."""

    sku: str
    title: str
    price: float
    cost: float = 0.0


@dataclass(frozen=True)
class InventoryLevel:
    """On-hand available units for a SKU at a location."""

    sku: str
    available: float
    location: str = "default"


@dataclass(frozen=True)
class OrderLine:
    sku: str
    quantity: float
    price: float


@dataclass(frozen=True)
class Order:
    """A sales order; ``created_at`` is an ISO date so it sorts/filters lexically."""

    order_id: str
    created_at: str
    lines: tuple[OrderLine, ...]


@runtime_checkable
class InventorySource(Protocol):
    """Any backend that can yield a catalog, inventory levels and orders."""

    def list_products(self) -> list[Product]: ...

    def inventory_levels(self) -> list[InventoryLevel]: ...

    def orders(self, *, since: str | None = None) -> list[Order]: ...
