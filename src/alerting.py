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
from src.ddmrp import net_flow_position

_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}


def _en_transito(raw) -> float | None:
    """Lo que viene en camino, o ``None`` si NO SE MIDIO. Nunca 0,0 por omision.

    Un cero significa "medido, y no viene nada", y manda al comprador a no restar
    nada; la ausencia significa "no sabemos", que es otra cosa y se declara. Es la
    misma regla que ``jobs/intake.py::stock_position`` y los siete emisores de
    cantidad a pedir (documentation/KERN_BARRIDO_NETEO.md).

    El ``NaN`` es el caso que importa aca y no es teorico: el dominio ``stock`` del
    estado viaja por parquet, donde un ``None`` de Python vuelve como ``NaN``. Sin
    este filtro, ``on_hand + nan`` es ``nan`` y ``nan <= rop`` es ``False``: el
    monitor se quedaria MUDO sobre un quiebre real, que es peor que dispararlo de mas.
    """
    if raw is None:
        return None
    try:
        valor = float(raw)
    except (TypeError, ValueError):
        return None
    if valor != valor:          # NaN
        return None
    if valor < 0:
        # No existe un en-transito negativo. Dejarlo pasar BAJARIA la posicion y
        # dispararia de mas, que es la direccion contraria a la de este neteo. El
        # esquema del dominio `stock` ya lo rechaza al escribir, pero `detect_events`
        # tambien lo llaman rutas que no pasan por el estado.
        return None
    return valor


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
    ``avg_daily_demand``, y OPCIONALMENTE ``on_order`` (lo ya comprado que todavia
    no llego).

    **El en-transito netea el DISPARADOR, no la cobertura**, y la asimetria es
    deliberada:

    - El disparador pregunta *"¿hay que pedir?"*. Se decide contra la POSICION
      (``net_flow_position(on_hand, on_order, 0)``, la identidad de ``src/ddmrp.py``,
      no una segunda formula). Una OC que transporte atraso dos semanas ya esta
      comprada: volver a pedir seria comprar dos veces lo mismo. Es la regla que los
      siete emisores de cantidad a pedir ya cumplen (kern#251).
    - La cobertura pregunta *"¿cuantos dias faltan para quedar fisicamente vacio?"*,
      y eso se mide sobre el ESTANTE. Netearla tambien cambiaria un falso positivo
      por un falso NEGATIVO: una OC que llega en tres semanas no alimenta el
      mostrador manana, y un SKU a punto de quebrar saldria "sano".

    Consecuencia que conviene tener presente: un SKU con el estante bajo pero la
    posicion cubierta **no emite evento de reposicion**, que es exactamente el punto
    (no se vuelve a pedir lo que ya viene). El riesgo de que esa OC llegue tarde es
    otra pregunta, y la contesta ``lead_time_drift``, no este detector.

    **Sin ``on_order`` no se inventa un cero**: el evento sale igual, decidido sobre
    el estante, y lo DICE (``netted=False`` en el detalle y en el mensaje). Un cero
    por omision diria "medido, y no viene nada".
    """
    events: list[InventoryEvent] = []
    for s in skus:
        pid = s["product_id"]
        on_hand = float(s["on_hand"])
        rop = float(s["reorder_point"])
        adt = float(s["avg_daily_demand"])
        on_order = _en_transito(s.get("on_order"))
        netted = on_order is not None
        position = net_flow_position(on_hand, on_order, 0.0) if netted else on_hand
        #: Lo que el disparador uso, para que el evento no obligue a re-derivarlo.
        base = {"on_hand": on_hand, "on_order": on_order,
                "inventory_position": position, "netted": netted}
        sin_netear = "" if netted else " (sin netear: no hay dato de en-transito)"

        if adt <= 0:
            if on_hand > 0:
                events.append(InventoryEvent(
                    pid, "dead_stock", "medium",
                    f"{pid}: {on_hand:g} units on hand with no demand",
                    dict(base),
                ))
            continue

        cover = on_hand / adt          # sobre el ESTANTE, a proposito (ver el docstring)
        if position <= rop:
            if cover < critical_cover_days:
                events.append(InventoryEvent(
                    pid, "stockout_risk", "high",
                    f"{pid}: ~{cover:.1f} days of cover left - stockout imminent"
                    f"{sin_netear}",
                    {**base, "days_of_cover": cover},
                ))
            else:
                events.append(InventoryEvent(
                    pid, "reorder_due", "medium",
                    f"{pid}: at/below reorder point "
                    f"(position {position:g} <= {rop:g}){sin_netear}",
                    {**base, "reorder_point": rop},
                ))
        elif cover > excess_cover_days:
            events.append(InventoryEvent(
                pid, "excess", "low",
                f"{pid}: ~{cover:.0f} days of cover — excess stock",
                {**base, "days_of_cover": cover},
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
