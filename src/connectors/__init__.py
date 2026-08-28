"""Commerce connectors — read a client's storefront/ERP, write back safely (Gap #5).

The plumbing that lets Kern run against a real e-commerce / ERP backend. A thin
``InventorySource`` protocol (products, inventory levels, orders) sits in front of every
backend, exactly as ``src.sources.DemandSource`` does for demand — so swapping CSV for a
live Shopify/Amazon adapter later means writing one adapter, not touching the engines.

The write side reuses the safe-staging plane in ``nosilo_core.writeback`` (dry-run -> approval ->
idempotent apply -> audit/rollback): connectors never mutate a system of record directly.

This package ships an offline ``simulator.SimulatedStore`` so the whole pipeline can run
end-to-end without any live API keys; real adapters (``odoo.py``, ``shopify.py`` ...)
implement the same protocol against their SDKs. ``odoo.py`` is the first live adapter: it
reads Odoo's standard models over XML-RPC and stages restocks back as reorder rules, with
an ``InMemoryOdoo`` stand-in so it too runs and tests fully offline.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from src.order_fulfillment import RealizedService, measure_from_order_lines


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
    """One line of a sales order: what the customer asked for, and what they got.

    ``sku``/``quantity``/``price`` are the DEMAND side and every backend has them -
    they are all a forecast or a policy sizing ever needs. The three optional fields
    are the SERVICE side, and they are what ``src.order_fulfillment`` measures fill
    rate, OTIF and lost sales from. A connector that cannot report one leaves it
    ``None``.

    ``None`` is load-bearing, and is never a stand-in for a number:

    * ``delivered_qty=0.0`` is a measurement - nothing shipped, a total stockout.
      ``delivered_qty=None`` means nobody reported it. Collapsing the second into
      the first hands a client a 0% fill rate over lines nobody ever instrumented;
      defaulting it to ``quantity`` instead hands them a perfect 100%. Both are
      confident and wrong, and ``order_fulfillment`` is built to tell them apart -
      a line missing what a criterion needs drops out of that criterion's
      denominator instead of counting as a failure.
    * a missing ``promised_date`` means no delivery was ever promised, so the line
      can be neither on time nor late; it is left out of OTIF rather than scored
      against the order date or the shipment's own date.

    Dates are ISO ``YYYY-MM-DD`` text, like ``Order.created_at``.
    """

    sku: str
    quantity: float
    price: float
    delivered_qty: float | None = None
    promised_date: str | None = None
    delivered_date: str | None = None


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


#: Columns ``jobs.inventory_optimization`` reads off an ``order_lines`` frame:
#: ``product_id`` to group by, then the five keys ``measure_from_order_lines``
#: looks up. Spelled once so ``service_frame`` and ``service_lines`` cannot drift.
_SERVICE_FRAME_COLUMNS = (
    "product_id",
    "ordered_qty",
    "delivered_qty",
    "promised_date",
    "delivered_date",
    "unit_price",
)


def service_lines(orders: Iterable[Order]) -> dict[str, list[dict]]:
    """Order lines regrouped per SKU, in the shape ``src.order_fulfillment`` reads.

    The bridge between a connector and realized service. ``measure_from_order_lines``
    looks up five keys by name off each line dict, so the mapping is spelled out in
    exactly one place: a renamed key here does not raise, it silently drops the line
    out of every criterion and reports ``not_measured`` over a store full of history.

    Values travel exactly as the connector reported them - ``None`` included, since
    the engine counts an absence and a measured zero in different denominators.
    """
    grouped: dict[str, list[dict]] = {}
    for order in orders:
        for line in order.lines:
            grouped.setdefault(line.sku, []).append(
                {
                    "ordered_qty": line.quantity,
                    "delivered_qty": line.delivered_qty,
                    "promised_date": line.promised_date,
                    "delivered_date": line.delivered_date,
                    "unit_price": line.price,
                }
            )
    return grouped


def service_frame(orders: Iterable[Order]):
    """The same lines as one ``order_lines`` DataFrame, ready for the inventory job.

    ``jobs.inventory_optimization.run(..., order_lines=...)`` has accepted a frame of
    outbound lines (``product_id`` plus the five keys) since the realized-service work
    landed, and it is the ONLY input that can produce an outbound OTIF there - but
    nothing ever built one, so the argument defaulted to ``None`` on every call and
    every SKU came back ``not_measured``. This is the missing constructor.

    Returns an empty frame with the right columns when there are no lines, so a caller
    can hand it straight to the job either way. pandas is imported here rather than at
    module import: everything else in this package's ``__init__`` is dependency-free
    DTOs, and importing it eagerly would put pandas in the path of every consumer that
    only wants ``OrderLine``.
    """
    import pandas as pd

    rows = [
        {"product_id": sku, **line}
        for sku, lines in sorted(service_lines(orders).items())
        for line in lines
    ]
    return pd.DataFrame(rows, columns=_SERVICE_FRAME_COLUMNS)


def realized_service(
    orders: Iterable[Order], *, stockout_days: Mapping[str, int] | None = None
) -> list[RealizedService]:
    """Measured customer service per SKU, straight off any ``InventorySource``.

    One record per SKU that appeared in ``orders``, sorted by SKU. Each carries its
    own ``provenance``: ``observed`` where the lines actually reported deliveries,
    ``not_measured`` where they did not. Nothing here is ever the beta a policy
    IMPLIES (``src.fill_rate``) - that is a different number under a different name.

    ``stockout_days`` is the client's own stock-zero count per SKU where they have
    one; without it the engine falls back to a declared lower bound.
    """
    days = dict(stockout_days or {})
    return [
        measure_from_order_lines(sku, lines, stockout_days=days.get(sku))
        for sku, lines in sorted(service_lines(orders).items())
    ]
