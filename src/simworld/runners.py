"""Los corredores: piso, Kern y techo, todos tras el mismo contrato.

Un corredor produce una ``Policy``; el mundo la aplica todas las semanas. Es la
forma honesta: Kern entrega una politica, no una cantidad, y quien la ejecuta es
la operacion.

El oraculo es la excepcion: decide semana a semana porque ve el futuro y no
tiene costo de replanificar. Nunca ve la lente.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from src.simworld.world import WeekOutcome, World, WorldState

NAIVE_COVER_WEEKS = 5.0
NAIVE_LOOKBACK_WEEKS = 4


@dataclass(frozen=True)
class Policy:
    """Que hacer cada semana, hasta la proxima replanificacion."""

    reorder_point: dict[str, float]
    order_up_to: dict[str, float]
    announced: bool = False   # el corredor emitio senal protectora al producirla
    note: str = ""
    status: str = "ok"
    extras: dict[str, float] = field(default_factory=dict)


def naive_policy(world: World, outcomes: list[WeekOutcome]) -> Policy:
    """El piso: punto de reorden fijo, congelado, sin revisar nunca.

    ``R = 5 x promedio semanal de las ultimas 4 semanas observadas``. Es lo que
    hace el ``stale_reorder_point`` del demo actual, y lo que hace el cliente real.
    """
    window = outcomes[-NAIVE_LOOKBACK_WEEKS:] if outcomes else []
    reorder: dict[str, float] = {}
    for i, sku in enumerate(world.skus):
        avg = float(np.mean([o.sold[i] for o in window])) if window else 0.0
        reorder[sku.sku] = NAIVE_COVER_WEEKS * avg
    return Policy(reorder_point=dict(reorder), order_up_to=dict(reorder),
                  announced=False, note="punto de reorden fijo, sin revision")


def apply_policy(world: World, state: WorldState, policy: Policy) -> dict[str, float]:
    """Traduce una politica en las ordenes de esta semana.

    Posicion de inventario = en mano + en transito. Si cae al o por debajo del
    punto de reorden, se pide hasta ``order_up_to``. Solo se devuelven las
    cantidades estrictamente positivas.
    """
    pipeline = np.zeros(len(world.skus), dtype=float)
    for sku_idx, _arrival, qty in state.in_transit:
        pipeline[sku_idx] += qty

    orders: dict[str, float] = {}
    for i, sku in enumerate(world.skus):
        rop = float(policy.reorder_point.get(sku.sku, 0.0))
        target = float(policy.order_up_to.get(sku.sku, rop))
        position = float(state.on_hand[i]) + float(pipeline[i])
        if position <= rop:
            qty = target - position
            if qty > 0:
                orders[sku.sku] = qty
    return orders


def oracle_orders(world: World, state: WorldState) -> dict[str, float]:
    """El techo: pide exactamente lo que se consumira durante su lead time real.

    Sin stock de seguridad, porque no hay incertidumbre que cubrir. Miope por
    diseno y aun asi inalcanzable. **Nunca ve la lente.**
    """
    t = state.week
    if t >= world.weeks:
        return {}

    pipeline = np.zeros(len(world.skus), dtype=float)
    for sku_idx, _arrival, qty in state.in_transit:
        pipeline[sku_idx] += qty

    orders: dict[str, float] = {}
    for i, sku in enumerate(world.skus):
        lead = int(world.real_lead_weeks[i, t])
        end = min(t + lead, world.weeks)
        need = float(world.demand[i, t:end].sum())
        have = float(state.on_hand[i]) + float(pipeline[i])
        qty = need - have
        if qty > 0:
            orders[sku.sku] = qty
    return orders
