"""Connector-backed replenishment — the offline execution loop (Gap #5).

Reads any ``InventorySource`` (a SimulatedStore, a store over HTTP, or a live adapter
later), forecasts each SKU from its order history, computes the restock needed to reach a
target cover, and returns a guided **never-unprotected** outcome:

- when the source is a writable store, the restock is **staged as a dry-run** through the
  safe-staging writeback plane (nothing mutates until a human applies it);
- otherwise a ready-to-execute **restock packet** (the quantities + steps) is handed off.

Either way no task ends in a dead end. Pure orchestration over the existing engines +
``src.writeback`` plane; deterministic given the source.
"""

from __future__ import annotations

from dataclasses import dataclass

from src.connectors import InventorySource, Order
from src.forecasting import forecast_demand
from src.guided import GuidedOutcome, HandoffPacket, Residual, as_executed, as_handoff
from src.writeback import Changeset


@dataclass(frozen=True)
class ReplenishmentLine:
    sku: str
    on_hand: float
    forecast_per_period: float
    target: float
    restock_qty: float


@dataclass(frozen=True)
class ReplenishmentPlan:
    lines: tuple[ReplenishmentLine, ...]
    restock: dict[str, float]          # only the SKUs that need topping up
    outcome: GuidedOutcome             # handoff packet (or executed when nothing's needed)
    changeset: Changeset | None        # staged dry-run, when a writable store was provided


def _demand_by_sku(orders: list[Order]) -> dict[str, list[float]]:
    """Per-SKU demand as a per-period series (summed within a date, ordered by date)."""
    by_date: dict[str, dict[str, float]] = {}
    for order in orders:
        for line in order.lines:
            bucket = by_date.setdefault(line.sku, {})
            bucket[order.created_at] = bucket.get(order.created_at, 0.0) + line.quantity
    return {sku: [dates[d] for d in sorted(dates)] for sku, dates in by_date.items()}


def _packet(restock: dict[str, float], changeset: Changeset | None) -> GuidedOutcome:
    """Wrap the restock as a protected handoff (or executed when there's nothing to do)."""
    if not restock:
        return as_executed("All SKUs are above target cover - no restock needed.")

    artifact = "\n".join(f"  {sku}: +{qty:g} units" for sku, qty in sorted(restock.items()))
    steps = (
        ["Review the staged restock (dry-run).", "Approve and apply it to update inventory."]
        if changeset is not None
        else ["Issue restock POs for the quantities below.", "Receive and update inventory."]
    )
    packet = HandoffPacket(
        title=f"Restock packet: {len(restock)} SKU(s)",
        steps=steps,
        artifact=artifact,
        data=dict(restock),
        risk_if_skipped="The thin SKUs stock out before the next replenishment cycle.",
    )
    residual = Residual(
        description="Approve and apply the restock (or issue the POs).",
        risk_if_skipped="Stockouts and lost sales on the under-covered SKUs.",
    )
    return as_handoff(f"Restock {len(restock)} SKU(s) to target cover.", [packet], residuals=[residual])


def plan_replenishment(
    source: InventorySource,
    *,
    cover_periods: float = 8.0,
    method: str = "auto",
    store: object | None = None,
    idempotency_key: str = "replenish-1",
) -> ReplenishmentPlan:
    """Forecast each SKU and plan the restock to ``cover_periods`` of demand.

    Pass ``store`` (a writable SimulatedStore, usually the same object as ``source``) to
    stage the restock as a dry-run; omit it to get a ready-to-execute packet only.
    """
    on_hand = {lvl.sku: lvl.available for lvl in source.inventory_levels()}
    series = _demand_by_sku(source.orders())

    lines: list[ReplenishmentLine] = []
    restock: dict[str, float] = {}
    for product in source.list_products():
        sku = product.sku
        history = series.get(sku, [])
        forecast = float(forecast_demand(history, method=method).forecast) if history else 0.0
        target = forecast * cover_periods
        current = on_hand.get(sku, 0.0)
        qty = max(0.0, round(target - current, 1))
        lines.append(ReplenishmentLine(sku, current, forecast, target, qty))
        if qty > 0:
            restock[sku] = qty

    changeset = None
    if store is not None and restock and hasattr(store, "stage_restock"):
        changeset = store.stage_restock(restock, idempotency_key=idempotency_key,
                                        reason="replenish to target cover")

    return ReplenishmentPlan(
        lines=tuple(lines),
        restock=restock,
        outcome=_packet(restock, changeset),
        changeset=changeset,
    )
