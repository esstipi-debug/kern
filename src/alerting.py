"""Inventory event detection (capability M14 — the pure core).

Deterministic detection over a per-SKU snapshot. Each SKU resolves to at most one
state event so signals don't double-count:

  - dead_stock     : demand has stopped but stock remains
  - stockout_risk  : at/below reorder point AND days-of-cover below the critical floor
  - reorder_due    : at/below reorder point but cover still above the floor
  - excess         : days-of-cover above the excess ceiling

Events feed the Guided Execution Layer (``alerts_outcome``) so an alert is always an
executable handoff — never a bare warning the user has to figure out alone. The
scheduler (APScheduler) and notification fan-out (Apprise/Slack) are thin optional
layers built on top of this core.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from nosilo_core.guided import HandoffPacket, Residual, as_executed, as_handoff

_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


@dataclass(frozen=True)
class InventoryEvent:
    product_id: str
    kind: str        # "stockout_risk" | "reorder_due" | "excess" | "dead_stock"
    severity: str    # "high" | "medium" | "low"
    message: str
    detail: dict = field(default_factory=dict)


def detect_events(
    skus: list[dict],
    *,
    critical_cover_days: float = 7.0,
    excess_cover_days: float = 90.0,
) -> list[InventoryEvent]:
    """Return the inventory events for a snapshot, most severe first.

    Each SKU dict has ``product_id``, ``on_hand``, ``reorder_point`` and
    ``avg_daily_demand``.
    """
    events: list[InventoryEvent] = []
    for s in skus:
        pid = s["product_id"]
        on_hand = float(s["on_hand"])
        rop = float(s["reorder_point"])
        adt = float(s["avg_daily_demand"])

        if adt <= 0:
            if on_hand > 0:
                events.append(InventoryEvent(
                    pid, "dead_stock", "medium",
                    f"{pid}: {on_hand:g} units on hand with no demand",
                    {"on_hand": on_hand},
                ))
            continue

        cover = on_hand / adt
        if on_hand <= rop:
            if cover < critical_cover_days:
                events.append(InventoryEvent(
                    pid, "stockout_risk", "high",
                    f"{pid}: ~{cover:.1f} days of cover left - stockout imminent",
                    {"on_hand": on_hand, "days_of_cover": cover},
                ))
            else:
                events.append(InventoryEvent(
                    pid, "reorder_due", "medium",
                    f"{pid}: at/below reorder point ({on_hand:g} <= {rop:g})",
                    {"on_hand": on_hand, "reorder_point": rop},
                ))
        elif cover > excess_cover_days:
            events.append(InventoryEvent(
                pid, "excess", "low",
                f"{pid}: ~{cover:.0f} days of cover — excess stock",
                {"on_hand": on_hand, "days_of_cover": cover},
            ))

    events.sort(key=lambda e: _SEVERITY_RANK.get(e.severity, 99))
    return events


def alerts_outcome(events: list[InventoryEvent]):
    """Wrap detected events in a protected GuidedOutcome (never a bare warning)."""
    if not events:
        return as_executed("No inventory alerts — all SKUs within their buffers.")

    steps = [f"[{e.severity}] {e.message}" for e in events]
    packet = HandoffPacket(
        title=f"{len(events)} inventory alert(s) need a decision",
        steps=steps,
        risk_if_skipped="unaddressed stockout/excess risk on the flagged SKUs",
    )
    residuals = [
        Residual(
            description=f"act on {e.kind} for {e.product_id}",
            risk_if_skipped=e.message,
        )
        for e in events
        if e.severity == "high"
    ]
    return as_handoff("Inventory alerts detected", [packet], residuals=residuals)
