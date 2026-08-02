"""Los corredores: piso, Kern y techo, todos tras el mismo contrato.

Un corredor produce una ``Policy``; el mundo la aplica todas las semanas. Es la
forma honesta: Kern entrega una politica, no una cantidad, y quien la ejecuta es
la operacion.

El oraculo es la excepcion: decide semana a semana porque ve el futuro y no
tiene costo de replanificar. Nunca ve la lente.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp
from scipy.sparse import lil_matrix

from nosilo_core import guided as guided_mod
from scm_agent.orchestrator import Orchestrator
from src.simworld.lens import export_history
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
    forecast: dict[str, float] = field(default_factory=dict)  # SkuRecommendation.forecast por SKU


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


WEEKS_PER_YEAR = 52.0


def oracle_orders(
    world: World,
    state: WorldState,
    *,
    order_cost: float = 75.0,
    holding_rate: float = 0.25,
    shortage_penalty_rate: float = 1.0,
) -> dict[str, float]:
    """El techo: el plan de compra exacto de costo minimo, por SKU, sobre el
    horizonte restante -- dado conocimiento perfecto de la demanda futura y de
    cada lead time real.

    No es una heuristica de ventana fija: es un lot-sizing de Wagner-Whitin con
    costo fijo de ordenar y opcion de venta perdida, resuelto como un MILP
    pequeno (scipy/HiGHS). Cuando dejar una semana sin cubrir cuesta menos que
    evitarla (el margen perdido es menor que comprar + mantener + el costo fijo
    de ordenar), el solver lo decide con ese calculo -- no hay una regla de
    ventana que lo intente aproximar y falle en los bordes.

    Optimiza la MISMA funcion de costo que ``src/simworld/scorer.py`` usa para
    juzgar a cualquier corredor (procurement + holding + shortage + costo fijo
    de ordenar), asi el techo y la regla que lo mide nunca discrepan.

    Costoso por llamada (un MILP por SKU, ~50-60ms via HiGHS) pero exacto
    siempre. Si un futuro llamador necesita invocarlo semana a semana sobre un
    horizonte largo y muchos SKUs, debe resolver el plan completo una vez por
    SKU en lugar de re-resolver cada semana -- ver la Task 11.

    **Nunca ve la lente.**
    """
    t0 = state.week
    if t0 >= world.weeks:
        return {}

    orders: dict[str, float] = {}
    for i, sku in enumerate(world.skus):
        in_transit_i = [
            (arrival, qty) for sku_idx, arrival, qty in state.in_transit if sku_idx == i
        ]
        qty = _oracle_order_for_sku(
            demand_full=world.demand[i],
            real_lead_full=world.real_lead_weeks[i],
            unit_cost=sku.unit_cost,
            price=sku.price,
            on_hand0=float(state.on_hand[i]),
            in_transit=in_transit_i,
            t0=t0,
            horizon=world.weeks,
            order_cost=order_cost,
            holding_rate=holding_rate,
            shortage_penalty_rate=shortage_penalty_rate,
        )
        if qty > 0:
            orders[sku.sku] = qty
    return orders


def _oracle_order_for_sku(
    *,
    demand_full: np.ndarray,
    real_lead_full: np.ndarray,
    unit_cost: float,
    price: float,
    on_hand0: float,
    in_transit: list[tuple[int, float]],
    t0: int,
    horizon: int,
    order_cost: float,
    holding_rate: float,
    shortage_penalty_rate: float,
) -> float:
    """Resuelve el MILP de lot-sizing para una SKU sobre ``[t0, horizon)`` y
    devuelve solo la cantidad a pedir en la semana ``t0``.

    Variables de decision, para cada semana ``t`` en el horizonte restante:
    ``q_t`` (cuanto pedir, continua), ``y_t`` (si se pidio algo, binaria -- liga
    el costo fijo de ordenar), ``I_t`` (inventario al cierre de la semana,
    continua) y ``s_t`` (unidades vendidas, continua, acotada por la demanda).
    Solo se crea una variable de pedido para semanas cuya llegada
    (``t + lead_real[t]``) cae dentro del horizonte -- pedir para que llegue
    despues del final de la corrida no puede reducir el costo, asi que no hace
    falta una restriccion explicita para excluirlo.

    Balance de inventario: ``I_t = I_{t-1} + recibido_t - s_t``, con
    ``I_{-1} = on_hand0`` y ``recibido_t`` la suma de lo ya comprometido en
    ``in_transit`` mas cualquier ``q`` cuya llegada caiga en ``t``.
    """
    n = horizon - t0
    if n <= 0:
        return 0.0

    margin = price - unit_cost
    weekly_holding = unit_cost * holding_rate / WEEKS_PER_YEAR
    demand = np.asarray(demand_full[t0:horizon], dtype=float)
    lead = np.asarray(real_lead_full[t0:horizon], dtype=int)
    arrival = np.arange(t0, horizon) + lead

    orderable = np.nonzero(arrival < horizon)[0]  # local week indices
    m = len(orderable)
    if m == 0:
        return 0.0

    committed_receipt = np.zeros(n, dtype=float)
    for arrival_week, qty in in_transit:
        if arrival_week < t0:
            # No alcanzable por el loop normal de `advance` (purga las entradas
            # ya llegadas), pero un `WorldState` armado a mano si puede traer
            # una entrada vencida sin acreditar. Antes esto se descartaba en
            # silencio, perdiendo suministro comprometido real; ahora es un
            # error explicito del llamador, no algo que el oraculo deba tapar.
            raise ValueError(
                f"in_transit trae una entrada vencida sin acreditar: llega en "
                f"la semana {arrival_week}, antes de t0={t0} (cantidad={qty}); "
                "el llamador debe acreditarla en on_hand0 antes de invocar al "
                "oraculo"
            )
        if arrival_week < horizon:
            committed_receipt[arrival_week - t0] += qty

    n_q = n_y = m
    n_I = n_s = n
    n_vars = n_q + n_y + n_I + n_s
    off_y, off_I, off_s = n_q, n_q + n_y, n_q + n_y + n_I

    total_remaining_demand = float(demand.sum())
    big_m = max(total_remaining_demand, 1.0)

    c = np.zeros(n_vars)
    c[:n_q] = unit_cost
    c[off_y:off_y + n_y] = order_cost
    c[off_I:off_I + n_I] = weekly_holding
    c[off_s:off_s + n_s] = -margin * shortage_penalty_rate

    arrival_local = arrival[orderable] - t0
    A_eq = lil_matrix((n, n_vars))
    b_eq = np.zeros(n)
    for pos in range(m):
        A_eq[arrival_local[pos], pos] -= 1.0
    for t in range(n):
        A_eq[t, off_I + t] = 1.0
        A_eq[t, off_s + t] = 1.0
        if t == 0:
            b_eq[t] = on_hand0 + committed_receipt[t]
        else:
            A_eq[t, off_I + t - 1] = -1.0
            b_eq[t] = committed_receipt[t]

    A_ub = lil_matrix((m, n_vars))
    for pos in range(m):
        A_ub[pos, pos] = 1.0
        A_ub[pos, off_y + pos] = -big_m
    b_ub = np.zeros(m)

    lb = np.zeros(n_vars)
    ub = np.full(n_vars, np.inf)
    ub[off_y:off_y + n_y] = 1.0
    ub[off_s:off_s + n_s] = demand

    integrality = np.zeros(n_vars)
    integrality[off_y:off_y + n_y] = 1

    result = milp(
        c,
        constraints=[
            LinearConstraint(A_eq.tocsr(), b_eq, b_eq),
            LinearConstraint(A_ub.tocsr(), -np.inf, b_ub),
        ],
        integrality=integrality,
        bounds=Bounds(lb, ub),
        options={"mip_rel_gap": 0.0},
    )
    if not result.success:
        raise RuntimeError(f"el MILP del oraculo no convergio: {result.message}")

    if orderable[0] == 0:
        return max(0.0, round(float(result.x[0]), 6))
    return 0.0


KERN_BRIEF = (
    "Revisa el historico de ventas adjunto y fija el punto de reorden y el stock "
    "de seguridad por SKU para la reposicion de este mes."
)
BLOCKING_GUIDED = (guided_mod.HANDOFF, guided_mod.ESCALATED)


def kern_policy(
    world: World,
    outcomes: list[WeekOutcome],
    *,
    level: int,
    seed: int,
    out_dir: str | Path,
    forced: bool,
) -> Policy:
    """Corre a Kern por el orquestador completo sobre la lente del nivel pedido.

    ``forced=True`` fija ``job_type="inventory_optimization"`` (la corrida
    controlada del arnes); ``forced=False`` deja que ``KERN_BRIEF`` pase por
    ``intent.classify`` como lo haria un cliente real. Nunca lee el mundo
    directamente -- solo la lente (``export_history``), y nunca llama al
    orquestador con un atajo que se salte prepare/run/QA/deliver.
    """
    out = Path(out_dir)
    view = export_history(world, outcomes, out / "view.csv", level=level, seed=seed)

    orch = Orchestrator(clients_root=None)
    result = orch.run(
        KERN_BRIEF,
        data_path=str(view),
        job_type="inventory_optimization" if forced else None,
        out_dir=str(out / "agent"),
    )

    announced = _announced(result)
    if result.status != "ok" or result.report is None:
        return Policy(reorder_point={}, order_up_to={}, announced=announced,
                      note=result.summary, status=result.status)

    guided = result.guided
    if guided is not None and guided.status in BLOCKING_GUIDED:
        # HANDOFF/ESCALATED: un humano tendria que actuar, y en este arnes nadie
        # lo hace -- la semana debe quedar sin ninguna orden, no con una
        # adivinanza razonable. Es el precio real del contrato de "nunca
        # desprotegido" cuando el que deberia protegerse esta dormido.
        return Policy(reorder_point={}, order_up_to={}, announced=announced,
                      note=guided.summary, status=guided.status)

    reorder: dict[str, float] = {}
    target: dict[str, float] = {}
    forecast: dict[str, float] = {}
    for rec in result.report.recommendations:
        rop = float(rec.reorder_point)
        reorder[rec.product_id] = rop
        forecast[rec.product_id] = float(rec.forecast)
        if rec.order_up_to is not None:
            target[rec.product_id] = float(rec.order_up_to)
        else:
            target[rec.product_id] = rop + float(rec.order_quantity or 0.0)

    return Policy(reorder_point=reorder, order_up_to=target, forecast=forecast,
                  announced=announced, note=result.summary, status="ok")


def _announced(result: object) -> bool:
    """Kern emitio alguna senal protectora sobre la calidad de lo que leyo.

    Lee SOLO senales propias de Kern (status, qa_issues, guided, intake_quality)
    -- nunca el nivel de suciedad de la lente, que el arnes conoce pero Kern no.
    """
    status = getattr(result, "status", "")
    if status in {"needs_data", "needs_clarification", "qa_failed", "error"}:
        return True
    if getattr(result, "qa_issues", None):
        return True
    guided = getattr(result, "guided", None)
    if guided is not None:
        if guided.status in BLOCKING_GUIDED:
            return True
        if getattr(guided, "residuals", None):
            return True
    report = getattr(result, "report", None)
    quality = getattr(report, "intake_quality", None) if report is not None else None
    if quality is not None:
        # El umbral es el que el propio tool declara (JobReport.intake_quality_threshold,
        # 0.20 por defecto) — no uno inventado por el arnes, o estariamos midiendo
        # nuestra idea de "aviso" en vez de la de Kern.
        threshold = float(getattr(report, "intake_quality_threshold", 0.20))
        n_raw = getattr(quality, "n_raw", 0) or 0
        kept = getattr(quality, "n_canonical_rows", 0) or 0
        dropped = n_raw - kept
        if n_raw > 0 and dropped / n_raw > threshold:
            return True
        if getattr(quality, "synthesized", ()):
            return True
    return False
