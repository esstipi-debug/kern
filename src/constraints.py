"""Business constraints — the gap between a textbook policy and a real plan.

The models compute an *unconstrained* optimum: order Q*, hold safety stock Ss.
Reality adds limits the math ignores: suppliers enforce minimum order quantities
and case packs, perishables can't be over-ordered, and the warehouse / budget
can't hold every SKU's ideal stock at once.

This module adjusts engine output to respect those limits:
  - per-order   : MOQ, order multiples (case packs), shelf-life cap
  - lot choice  : the CHEAPEST feasible lot around a target Q, with the runner-up
                  kept as a named alternative (never a single rounded number)
  - portfolio   : a budget allocator that trims safety stock across SKUs to fit
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

from src.eoq import total_cost

#: Relative tolerance for "is this quantity ON the multiple / AT the MOQ". A lot
#: read from a CSV as 60.0000001 is 60, not an infeasible order.
_FEAS_TOL = 1e-9


def round_up_to_multiple(quantity: float, multiple: float) -> float:
    """Round quantity up to the nearest positive multiple (case pack)."""
    if multiple <= 0:
        return quantity
    return math.ceil(quantity / multiple) * multiple


def apply_order_rules(
    quantity: float,
    *,
    minimum_order_quantity: float = 0.0,
    order_multiple: float = 0.0,
    max_quantity: float | None = None,
) -> float:
    """Apply MOQ, case-pack rounding, then an upper cap, in that order."""
    q = max(quantity, minimum_order_quantity)
    q = round_up_to_multiple(q, order_multiple)
    if max_quantity is not None:
        q = min(q, max_quantity)
    return q


def shelf_life_max_quantity(demand_per_period: float, shelf_life_periods: float) -> float:
    """Largest order consumable before expiry: demand_rate * shelf_life."""
    if demand_per_period < 0 or shelf_life_periods < 0:
        raise ValueError("demand_per_period and shelf_life_periods must be >= 0")
    return demand_per_period * shelf_life_periods


# -- Lot choice under supplier terms -------------------------------------------
#
# `apply_order_rules` answers "what is the smallest legal lot at or above Q?".
# That is one terminal, and on its own it is a dead end: Kern says 40, the
# supplier sells by 30, the plan silently becomes 60 and nobody is told that 30
# existed, or what either choice costs. The functions below turn the same rule
# into a DECISION with named alternatives: the nearest feasible lot on each side
# of the target, valued with the engine's own EOQ cost curve (src.eoq.total_cost),
# the cheaper one chosen, the other one kept. Every consumer (the inventory job,
# the hats grid, the cross-impact verdict) reads the rule from here so the three
# never disagree about what "feasible" means.


def _fmt_qty(value: float) -> str:
    return f"{value:g}"


def lot_rule(minimum_order_quantity: float = 0.0, order_multiple: float = 0.0) -> str:
    """The supplier's terms as one short label: "" when nothing binds, else
    "moq 60", "multiple 30" or "moq 60, multiple 30". Same text everywhere a
    rule is printed (CSV column, option summary, residual) so a reader can match
    them."""
    if minimum_order_quantity < 0 or order_multiple < 0:
        raise ValueError("minimum_order_quantity and order_multiple must be >= 0")
    parts: list[str] = []
    if minimum_order_quantity > 0:
        parts.append(f"moq {_fmt_qty(minimum_order_quantity)}")
    if order_multiple > 0:
        parts.append(f"multiple {_fmt_qty(order_multiple)}")
    return ", ".join(parts)


def is_feasible_lot(
    quantity: float,
    *,
    minimum_order_quantity: float = 0.0,
    order_multiple: float = 0.0,
) -> bool:
    """True when the supplier would accept an order of exactly ``quantity``."""
    if quantity <= 0:
        return False
    if quantity < minimum_order_quantity * (1.0 - _FEAS_TOL):
        return False
    if order_multiple > 0:
        ratio = quantity / order_multiple
        if abs(ratio - round(ratio)) > _FEAS_TOL * max(1.0, ratio):
            return False
    return True


def feasible_neighbours(
    target: float,
    *,
    minimum_order_quantity: float = 0.0,
    order_multiple: float = 0.0,
) -> tuple[float, float | None]:
    """``(up, down)``: the nearest feasible lot at or above ``target`` and the
    nearest one strictly below it.

    ``up`` is exactly what `apply_order_rules` returns. ``down`` is ``None`` when
    the target is already feasible (there is nothing to choose) or when nothing
    feasible exists underneath -- a target below the MOQ has no smaller legal lot,
    and "order less than the minimum" is not an option Kern may print.
    """
    if target <= 0:
        raise ValueError("target must be > 0")
    if minimum_order_quantity < 0 or order_multiple < 0:
        raise ValueError("minimum_order_quantity and order_multiple must be >= 0")
    rule = dict(minimum_order_quantity=minimum_order_quantity, order_multiple=order_multiple)
    if is_feasible_lot(target, **rule):
        return target, None
    up = apply_order_rules(target, **rule)
    if order_multiple <= 0:
        # Only the MOQ binds and the target sits below it: the legal set is
        # [MOQ, inf), so there is no feasible lot under the target at all.
        return up, None
    down = math.floor(target / order_multiple) * order_multiple
    if down >= up * (1.0 - _FEAS_TOL):          # float guard: floor landed on `up`
        down -= order_multiple
    if down <= 0 or not is_feasible_lot(down, **rule):
        return up, None
    return up, down


def feasible_lots(
    lo: float,
    hi: float,
    *,
    minimum_order_quantity: float = 0.0,
    order_multiple: float = 0.0,
) -> list[float]:
    """Every feasible lot in ``[lo, hi]`` when a case-pack multiple binds, ascending.

    ``[]`` when there is no multiple: the legal set is then continuous above the
    MOQ and the caller lays its own grid. Computed as ``k * multiple`` from an
    integer ``k`` rather than by repeated addition, so a long range does not drift.
    """
    if order_multiple <= 0 or hi < lo:
        return []
    start = apply_order_rules(
        max(lo, order_multiple * _FEAS_TOL),
        minimum_order_quantity=minimum_order_quantity, order_multiple=order_multiple,
    )
    k0 = int(round(start / order_multiple))
    k1 = int(math.floor(hi / order_multiple * (1.0 + _FEAS_TOL)))
    return [k * order_multiple for k in range(k0, k1 + 1)]


@dataclass(frozen=True)
class LotOption:
    """One legal lot and what it costs per year on the EOQ curve."""

    quantity: float
    annual_cost: float      # K*D/Q + h*Q/2, src.eoq.total_cost
    penalty_usd: float      # annual_cost - annual_cost(target), floored at 0
    side: str               # "exact" | "up" | "down"


@dataclass(frozen=True)
class LotDecision:
    """The cheapest feasible lot around ``target`` AND the runner-up.

    ``alternative`` is the other neighbour when one exists; ``None`` when the
    target was already feasible or when the only legal move is up (a target
    below the MOQ). ``rule`` is `lot_rule(...)`, "" when nothing bound.
    """

    target: float
    chosen: LotOption
    alternative: LotOption | None
    rule: str

    @property
    def constrained(self) -> bool:
        """A supplier rule was declared for this SKU (it may or may not have bitten)."""
        return bool(self.rule)

    @property
    def snapped(self) -> bool:
        """The rule actually moved the quantity off the target."""
        return self.chosen.side != "exact"


def cheapest_feasible_lot(
    target: float,
    *,
    annual_demand: float,
    holding_cost_per_unit: float,
    fixed_order_cost: float,
    minimum_order_quantity: float = 0.0,
    order_multiple: float = 0.0,
) -> LotDecision:
    """Pick between the feasible lots on either side of ``target`` by yearly cost.

    Both neighbours are valued with the same EOQ cost the target was sized with
    (Vandeput eq. 2.1: ordering K*D/Q plus holding h*Q/2), the cheaper one is
    chosen and the other is returned as the alternative with its own penalty, so
    a downstream surface can always show TWO terminals instead of one rounded
    number. Ties go up: same cost, more cover, fewer orders. Penalties are
    measured against the target's cost and floored at zero -- when the target
    is the EOQ that floor never triggers, and when a caller passes some other
    target a feasible lot that happens to be cheaper costs nothing to honour.
    """
    if target <= 0:
        raise ValueError("target must be > 0")
    rule = lot_rule(minimum_order_quantity, order_multiple)

    def cost(q: float) -> float:
        return total_cost(q, annual_demand, holding_cost_per_unit, fixed_order_cost)

    base = cost(target)
    up, down = feasible_neighbours(
        target, minimum_order_quantity=minimum_order_quantity, order_multiple=order_multiple,
    )
    if down is None and abs(up - target) <= _FEAS_TOL * max(1.0, target):
        return LotDecision(target, LotOption(target, base, 0.0, "exact"), None, rule)
    options = [LotOption(up, cost(up), max(cost(up) - base, 0.0), "up")]
    if down is not None:
        options.append(LotOption(down, cost(down), max(cost(down) - base, 0.0), "down"))
    options.sort(key=lambda o: (o.annual_cost, o.side != "up"))
    return LotDecision(
        target=target, chosen=options[0],
        alternative=options[1] if len(options) > 1 else None, rule=rule,
    )


# -- Cantidad que se ESCRIBE en un ERP ------------------------------------------
#
# El plan razona en unidades continuas; el documento que llega al proveedor no.
# 187.6 rollos o 1.4 escaleras en una orden de compra no son un plan, son un
# numero que alguien tiene que corregir a mano. La regla es la MISMA que arriba:
# el paso de la unidad es un `order_multiple` y el minimo del proveedor un
# `minimum_order_quantity`, asi que "legal" significa lo mismo aca que en el
# inventory job, los sombreros y el veredicto cruzado.


@dataclass(frozen=True)
class PurchasableQty:
    """Una cantidad llevada HACIA ARRIBA al primer lote que el ERP y el proveedor aceptan.

    ``asked`` es lo que pidio el plan y no se pisa: quien muestra ``quantity`` tiene a
    mano la cifra original para que el ajuste se vea. ``rule`` es `lot_rule(minimo,
    paso)`, el mismo texto que imprime el resto de Kern ("moq 50, multiple 1").
    """

    asked: float
    quantity: float
    rule: str

    @property
    def adjusted(self) -> bool:
        """La regla movio la cantidad. Solo puede moverla hacia arriba."""
        return abs(self.quantity - self.asked) > _FEAS_TOL * max(1.0, abs(self.asked))


def purchasable_quantity(
    asked: float,
    *,
    unit_step: float = 0.0,
    minimum_order_quantity: float = 0.0,
) -> PurchasableQty:
    """El primer lote legal en o por encima de ``asked``.

    ``unit_step`` es el paso minimo de cantidad en la unidad del producto (1.0 para una
    pieza, 0.001 para un kilo con tres decimales); ``0.0`` = sin paso conocido.

    Mira PRIMERO si ``asked`` ya es legal, con la tolerancia de `is_feasible_lot`:
    `round_up_to_multiple(0.1 * 3, 0.1)` devuelve 0.4, porque el cociente sale
    3.0000000000000004 y ``ceil`` lo sube un paso entero. El resultado se limpia a 9
    decimales para que 12.346 no viaje como 12.346000000000002.

    Nada que comprar (``asked == 0``) sigue siendo nada: el minimo del proveedor se
    aplica a una compra, no inventa una.
    """
    rule = lot_rule(minimum_order_quantity, unit_step)  # valida que ambos sean >= 0
    if asked < 0:
        raise ValueError("asked must be >= 0")
    if asked == 0:
        return PurchasableQty(0.0, 0.0, rule)
    terms = dict(minimum_order_quantity=minimum_order_quantity, order_multiple=unit_step)
    base = max(asked, minimum_order_quantity)
    quantity = base if is_feasible_lot(base, **terms) else apply_order_rules(asked, **terms)
    return PurchasableQty(asked, round(quantity, 9), rule)


@dataclass(frozen=True)
class InventoryItem:
    """One SKU's stock position, used for portfolio-level constraints."""

    product_id: str
    order_quantity: float
    safety_stock: float
    unit_cost: float

    @property
    def cycle_investment(self) -> float:
        """Value of average cycle stock (Q/2) — the economic floor."""
        return (self.order_quantity / 2.0) * self.unit_cost

    @property
    def safety_investment(self) -> float:
        return max(self.safety_stock, 0.0) * self.unit_cost

    @property
    def investment(self) -> float:
        return self.cycle_investment + self.safety_investment


def total_investment(items: list[InventoryItem]) -> float:
    return sum(item.investment for item in items)


@dataclass(frozen=True)
class BudgetAllocation:
    """Result of fitting a portfolio under a budget cap."""

    items: list[InventoryItem]
    feasible: bool
    safety_stock_scale: float
    requested_investment: float
    final_investment: float


def allocate_under_budget(items: list[InventoryItem], budget: float) -> BudgetAllocation:
    """
    Trim safety stock proportionally so total inventory investment fits ``budget``.

    Cycle stock (Q/2) is the economic order floor and is preserved; only safety
    stock is reduced. If even zero safety stock exceeds the budget, the result is
    flagged infeasible (cycle stock alone is over budget).
    """
    if budget < 0:
        raise ValueError("budget must be >= 0")

    requested = total_investment(items)
    cycle_floor = sum(item.cycle_investment for item in items)
    safety_total = sum(item.safety_investment for item in items)

    if requested <= budget:
        return BudgetAllocation(items, True, 1.0, requested, requested)

    if budget < cycle_floor or safety_total <= 0:
        zeroed = [replace(item, safety_stock=0.0) for item in items]
        return BudgetAllocation(zeroed, budget >= cycle_floor, 0.0, requested, cycle_floor)

    scale = (budget - cycle_floor) / safety_total
    adjusted = [replace(item, safety_stock=item.safety_stock * scale) for item in items]
    return BudgetAllocation(adjusted, True, scale, requested, total_investment(adjusted))
