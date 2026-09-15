"""Connector-backed replenishment — the offline execution loop (Gap #5).

Reads any ``InventorySource`` (a SimulatedStore, a store over HTTP, or a live adapter
later), forecasts each SKU from its order history, computes the restock needed to reach a
target cover, and returns a guided **never-unprotected** outcome:

- when the source is a writable store, the restock is **staged as a dry-run** through the
  safe-staging writeback plane (nothing mutates until a human applies it);
- otherwise a ready-to-execute **restock packet** (the quantities + steps) is handed off.

Either way no task ends in a dead end. Pure orchestration over the existing engines +
``nosilo_core.writeback`` plane; deterministic given the source.
"""

from __future__ import annotations

from dataclasses import dataclass

from nosilo_core.guided import GuidedOutcome, HandoffPacket, Residual, as_executed, as_handoff
from nosilo_core.writeback import Changeset
from src.connectors import InventorySource, Order
from src.ddmrp import net_flow_position
from src.forecasting import MIN_PERIODS_FOR_POLICY, forecast_demand


@dataclass(frozen=True)
class ReplenishmentLine:
    sku: str
    on_hand: float
    forecast_per_period: float
    target: float
    restock_qty: float
    #: "ok" when the line carries a real, evidence-backed quantity;
    #: "insufficient_history" when the SKU's order history was too short to
    #: forecast from and the engine declined to size it (restock_qty stays 0).
    status: str = "ok"
    #: Order-days observed for this SKU. See demand_by_sku()'s docstring on why
    #: this is order-DAYS and not calendar periods.
    n_observations: int = 0
    #: Units on open purchase orders netted out of ``restock_qty``, or ``None``
    #: when the caller supplied no ``on_order_by_sku`` map (or this SKU was
    #: absent from it). ``None`` and ``0.0`` mean different things: the first is
    #: "nobody told us", the second is "measured, and nothing is inbound".
    on_order: float | None = None
    #: The position the quantity was sized against - ``on_hand + on_order``.
    #: ``None`` on refused lines, which were never sized at all.
    position: float | None = None


@dataclass(frozen=True)
class ReplenishmentPlan:
    lines: tuple[ReplenishmentLine, ...]
    restock: dict[str, float]          # only the SKUs that need topping up
    outcome: GuidedOutcome             # handoff packet (or executed when nothing's needed)
    changeset: Changeset | None        # staged dry-run, when a writable store was provided


def demand_by_sku(orders: list[Order]) -> dict[str, list[float]]:
    """Per-SKU demand as a per-period series (summed within a date, ordered by date).

    Publica, no privada: la consume tambien `jobs/channel_replenishment_job.py`.
    Un guion bajo importado desde otro modulo no protege nada -- solo avisa mal.
    El alias `_demand_by_sku` se conserva por si algo interno lo nombraba asi.
    """
    by_date: dict[str, dict[str, float]] = {}
    for order in orders:
        for line in order.lines:
            bucket = by_date.setdefault(line.sku, {})
            bucket[order.created_at] = bucket.get(order.created_at, 0.0) + line.quantity
    return {sku: [dates[d] for d in sorted(dates)] for sku, dates in by_date.items()}


def _refusal_residual(refused: tuple[str, ...]) -> Residual:
    return Residual(
        description=f"{len(refused)} SKU(s) had too little order history to forecast from "
                    f"and were NOT sized: {', '.join(sorted(refused))}.",
        risk_if_skipped="these SKUs are unplanned, not well-stocked - a slow mover with a "
                        "genuine gap and a brand-new SKU look identical here; review each "
                        "by hand before assuming no action is needed.",
    )


def _packet(
    restock: dict[str, float],
    changeset: Changeset | None,
    refused: tuple[str, ...] = (),
) -> GuidedOutcome:
    """Wrap the restock as a protected handoff (or executed when there's nothing to do)."""
    if not restock:
        # A portfolio where every SKU was REFUSED is not a portfolio that is well
        # stocked, and must never report as one. Route the refusals to a human
        # instead: a refusal that goes nowhere is exactly the dead end the
        # never-unprotected contract (nosilo_core.guided.verify_guided) exists to stop.
        if refused:
            packet = HandoffPacket(
                title=f"Unplanned SKUs: {len(refused)}",
                steps=["Review each SKU's order history by hand.",
                       "Set an opening buy manually, or wait for more sales history."],
                artifact="\n".join(f"  {sku}" for sku in sorted(refused)),
                data={"insufficient_history": list(sorted(refused))},
                risk_if_skipped="An unplanned SKU stocks out with nobody watching it.",
            )
            return as_handoff(
                f"No SKU could be planned: {len(refused)} had too little order history.",
                [packet],
                residuals=[_refusal_residual(refused)],
            )
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
    residuals = [Residual(
        description="Approve and apply the restock (or issue the POs).",
        risk_if_skipped="Stockouts and lost sales on the under-covered SKUs.",
    )]
    if refused:
        residuals.append(_refusal_residual(refused))
    return as_handoff(f"Restock {len(restock)} SKU(s) to target cover.", [packet], residuals=residuals)


def plan_replenishment(
    source: InventorySource,
    *,
    cover_periods: float = 8.0,
    method: str = "auto",
    store: object | None = None,
    idempotency_key: str = "replenish-1",
    min_observations: int = MIN_PERIODS_FOR_POLICY,
    on_order_by_sku: dict[str, float] | None = None,
) -> ReplenishmentPlan:
    """Forecast each SKU and plan the restock to ``cover_periods`` of demand.

    Pass ``store`` (a writable SimulatedStore, usually the same object as ``source``) to
    stage the restock as a dry-run; omit it to get a ready-to-execute packet only.

    ``on_order_by_sku`` is units already on an open purchase order and not yet
    received. When supplied, each quantity is sized against the inventory
    POSITION (``src.ddmrp.net_flow_position``: on hand plus on order) instead of
    on-hand alone, so stock that is already paid for and in transit is not bought
    twice. ``OdooConnector.open_purchase_qty_by_sku()`` produces exactly this map.

    Omitting it is NOT the same as passing zeros: a SKU missing from the map gets
    ``on_order=None`` on its line and the plan reports that it could not net,
    rather than claiming nothing is inbound. A default of ``{}`` here would read
    as "measured, and it is zero", which is the failure this parameter exists to
    prevent.
    """
    on_hand = {lvl.sku: lvl.available for lvl in source.inventory_levels()}
    series = demand_by_sku(source.orders())

    lines: list[ReplenishmentLine] = []
    restock: dict[str, float] = {}
    refused: list[str] = []
    for product in source.list_products():
        sku = product.sku
        history = series.get(sku, [])
        current = on_hand.get(sku, 0.0)

        # Too few observations to forecast from. This path is thinner than the CSV
        # path's (src/forecasting.py's MIN_PERIODS_FOR_POLICY, shared) and needs the
        # gate MORE, not less: there is no safety stock here at all - the target is
        # a bare `forecast * cover_periods` - so a forecast built on two order-days
        # goes straight into a purchase quantity with nothing damping it.
        if len(history) < min_observations:
            lines.append(ReplenishmentLine(
                sku, current, 0.0, 0.0, 0.0,
                status="insufficient_history", n_observations=len(history),
            ))
            refused.append(sku)
            continue

        forecast = float(forecast_demand(history, method=method).forecast)
        target = forecast * cover_periods
        # Size against the position, not the shelf. `qualified_demand=0.0` is
        # honest here: an InventorySource exposes no committed-demand concept, so
        # there is nothing to subtract - not a shortcut, a declared absence.
        on_order = None if on_order_by_sku is None else on_order_by_sku.get(sku)
        position = net_flow_position(current, on_order or 0.0, 0.0)
        qty = max(0.0, round(target - position, 1))
        lines.append(ReplenishmentLine(
            sku, current, forecast, target, qty, n_observations=len(history),
            on_order=on_order, position=position,
        ))
        if qty > 0:
            restock[sku] = qty

    changeset = None
    if store is not None and restock and hasattr(store, "stage_restock"):
        changeset = store.stage_restock(restock, idempotency_key=idempotency_key,
                                        reason="replenish to target cover")

    return ReplenishmentPlan(
        lines=tuple(lines),
        restock=restock,
        outcome=_packet(restock, changeset, tuple(refused)),
        changeset=changeset,
    )


#: Alias historico: este helper fue privado entre 2026-08 y 2026-09-15.
_demand_by_sku = demand_by_sku
