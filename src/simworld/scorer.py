"""El marcador: contrato de KPI portado de gym-invmgmt (MIT) mas la captura
normalizada entre piso y techo.

Un numero de costo absoluto es discutible. ``capture`` aguanta a un comprador
esceptico: 0 es no mejor que un punto de reorden fijo, 1 es igualar a un oraculo
que ve el futuro. El oraculo es inalcanzable por construccion, asi que superarlo
no es un logro sino una fuga de informacion -- y por eso ``capture`` levanta en
vez de devolver un numero bonito.

**Consistencia de costo con el oraculo (carga critica de esta tarea):**
``weekly_cost``/``score_run`` calculan el costo EXACTAMENTE como
``src/simworld/runners.py``'s ``oracle_orders`` lo optimiza -- procurement +
holding + shortage + costo fijo de ordenar, con los mismos parametros por
defecto (``order_cost=75.0, holding_rate=0.25, shortage_penalty_rate=1.0``).
Si esta formula se desincroniza de la del oraculo, el techo deja de ser un
techo genuino respecto de lo que este modulo mide.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from src.simworld.runners import WEEKS_PER_YEAR
from src.simworld.world import WeekOutcome, World


@dataclass(frozen=True)
class Kpis:
    weeks: int
    service_level: float       # fraccion de semanas-SKU sin quiebre
    fill_rate: float           # fraccion de la demanda total satisfecha
    unfulfilled_units: float
    mean_on_hand: float
    # --- sesgo, portado de AIM-Bench ---
    bullwhip: float            # var(ordenes) / var(demanda)
    anchoring: float           # |media(ordenes) - media(demanda)| / media(demanda)
    demand_chasing: float      # corr(orden_t, demanda_{t-1})
    procurement_cost: float
    holding_cost: float
    shortage_cost: float
    fixed_order_cost: float
    total_cost: float
    revenue: float
    profit: float


def weekly_cost(
    world: World,
    outcomes: list[WeekOutcome],
    *,
    order_cost: float = 75.0,
    holding_rate: float = 0.25,
    shortage_penalty_rate: float = 1.0,
) -> np.ndarray:
    """Costo total de cada semana. La suma es ``Kpis.total_cost``.

    La atribucion de la degradacion anunciada (Task 10) es por periodo, asi
    que el costo tiene que existir semana a semana, no solo agregado.

    Levanta ``ValueError`` si ``outcomes`` esta vacio, igual que
    ``score_run`` -- ambas funciones documentadas como sumando al mismo
    total deben coincidir en este limite: "cero semanas" es un error real
    de quien llama, no una respuesta valida de costo cero.
    """
    if not outcomes:
        raise ValueError("no hay semanas que puntuar")

    unit_cost = np.array([s.unit_cost for s in world.skus], dtype=float)
    margin = np.array([s.price - s.unit_cost for s in world.skus], dtype=float)
    weekly_holding = unit_cost * holding_rate / WEEKS_PER_YEAR

    out = np.zeros(len(outcomes), dtype=float)
    for j, o in enumerate(outcomes):
        procurement = float((o.ordered * unit_cost).sum())
        holding = float((o.on_hand_end * weekly_holding).sum())
        shortage = float((o.lost * margin * shortage_penalty_rate).sum())
        fixed = float(order_cost * int((o.ordered > 0).sum()))
        out[j] = procurement + holding + shortage + fixed
    return out


def score_run(
    world: World,
    outcomes: list[WeekOutcome],
    *,
    order_cost: float = 75.0,
    holding_rate: float = 0.25,
    shortage_penalty_rate: float = 1.0,
) -> Kpis:
    """El contrato de KPI completo para una corrida."""
    if not outcomes:
        raise ValueError("no hay semanas que puntuar")

    unit_cost = np.array([s.unit_cost for s in world.skus], dtype=float)
    price = np.array([s.price for s in world.skus], dtype=float)
    margin = price - unit_cost
    weekly_holding = unit_cost * holding_rate / WEEKS_PER_YEAR

    demand = np.stack([o.demand for o in outcomes], axis=1)
    sold = np.stack([o.sold for o in outcomes], axis=1)
    lost = np.stack([o.lost for o in outcomes], axis=1)
    ordered = np.stack([o.ordered for o in outcomes], axis=1)
    on_hand = np.stack([o.on_hand_end for o in outcomes], axis=1)

    total_demand = float(demand.sum())
    procurement = float((ordered * unit_cost[:, None]).sum())
    holding = float((on_hand * weekly_holding[:, None]).sum())
    shortage = float((lost * margin[:, None] * shortage_penalty_rate).sum())
    fixed = float(order_cost * int((ordered > 0).sum()))
    revenue = float((sold * price[:, None]).sum())
    total_cost = procurement + holding + shortage + fixed

    demand_series = demand.sum(axis=0)
    order_series = ordered.sum(axis=0)
    demand_var = float(demand_series.var())
    order_var = float(order_series.var())
    bullwhip = order_var / demand_var if demand_var > 0 else 0.0

    return Kpis(
        weeks=len(outcomes),
        service_level=float((lost <= 0).mean()),
        fill_rate=float(sold.sum() / total_demand) if total_demand > 0 else 1.0,
        unfulfilled_units=float(lost.sum()),
        mean_on_hand=float(on_hand.mean()),
        bullwhip=bullwhip,
        anchoring=_anchoring(order_series, demand_series),
        demand_chasing=_demand_chasing(order_series, demand_series),
        procurement_cost=procurement,
        holding_cost=holding,
        shortage_cost=shortage,
        fixed_order_cost=fixed,
        total_cost=total_cost,
        revenue=revenue,
        profit=revenue - total_cost,
    )


def _anchoring(orders: np.ndarray, demand: np.ndarray) -> float:
    """Cuanto se pega el volumen pedido a la demanda media, ignorando su forma.

    AIM-Bench lo llama pull-to-centre. Un corredor que pide siempre lo mismo,
    igual a la media, marca 0 aca y aun asi puede ser pesimo: por eso convive
    con ``demand_chasing``, que mira la forma en vez del nivel.
    """
    mean_d = float(demand.mean())
    if mean_d <= 0:
        return 0.0
    return float(abs(orders.mean() - mean_d) / mean_d)


def _demand_chasing(orders: np.ndarray, demand: np.ndarray) -> float:
    """Correlacion entre lo que se pide hoy y lo que se vendio la semana pasada.

    Alto = el corredor persigue el ultimo dato en vez de anticipar. Un
    pronostico de media movil hace exactamente esto, asi que **un motor
    determinista puede perseguir demanda igual que un LLM**: no es un sesgo
    cognitivo, es una propiedad del estimador. Por eso se mide -- y por eso
    esto no es exclusivo de politicas no deterministas o basadas en un LLM.
    """
    if len(orders) < 3:
        return 0.0
    x = orders[1:].astype(float)
    y = demand[:-1].astype(float)
    if x.std() == 0 or y.std() == 0:
        return 0.0
    return float(np.corrcoef(x, y)[0, 1])


def capture(*, floor_cost: float, kern_cost: float, oracle_cost: float) -> float:
    """Donde cae Kern entre el piso y el techo.

    0 = no mejor que el punto de reorden fijo. 1 = igual al oraculo. Negativo
    es peor que el piso, y se informa con su signo. Por encima de 1 es
    imposible: el oraculo ve el futuro, asi que superarlo significa fuga de
    informacion del mundo hacia la lente. Falla ruidosa, no silenciosa.
    """
    if not (math.isfinite(floor_cost) and math.isfinite(kern_cost) and math.isfinite(oracle_cost)):
        raise ValueError(
            f"entrada no finita: floor_cost={floor_cost!r}, kern_cost={kern_cost!r}, "
            f"oracle_cost={oracle_cost!r} deben ser numeros finitos "
            "(nan/inf pasarian en silencio por las comparaciones de mas abajo)"
        )
    span = floor_cost - oracle_cost
    if span <= 0:
        raise ValueError(
            f"span degenerado: el piso ({floor_cost:.2f}) no cuesta mas que el "
            f"oraculo ({oracle_cost:.2f}); el mundo no discrimina"
        )
    value = (floor_cost - kern_cost) / span
    if value > 1.0:
        raise ValueError(
            f"captura {value:.3f} > 1: Kern le gano al oraculo, que ve el futuro. "
            "Hay fuga de informacion del mundo hacia la lente."
        )
    return float(value)


@dataclass(frozen=True)
class Degradation:
    """Cuanto le costo la suciedad a Kern, y cuanto de eso aviso.

    La atribucion es por periodo (semana a semana), nunca global: comparar
    solo los totales de dos corridas conflaria "esta semana empeoro por la
    suciedad que Kern leyo" con "las dos corridas simplemente divergieron con
    el tiempo por timing de ordenes distinto" -- una afirmacion mucho mas
    debil. Misma semilla, mismo mundo, asi que la semana ``t`` del nivel 0 y
    la semana ``t`` de cualquier otro nivel son directamente comparables.
    """

    level: int
    drop: float              # exceso de costo total contra el nivel 0 (puede ser negativo)
    announced: float         # la parte del exceso en semanas donde Kern aviso (puede ser negativa)
    silent: float            # drop - announced. El numero que importa (puede ser negativo).
    weeks_announced: int


def degradation(
    clean_weekly: np.ndarray,
    dirty_weekly: np.ndarray,
    announced_weeks: list[bool],
    *,
    level: int,
) -> Degradation:
    """Atribuye el exceso de costo semana a semana, no en bloque.

    El exceso de una semana (``dirty - clean``) puede ser **negativo** -- la
    suciedad puede abaratar una semana por accidente (una fila descartada que
    por casualidad reduce una sobre-orden). No se recorta a cero en ningun
    punto: ni por semana, ni el total, ni la parte anunciada, ni la parte
    silenciosa -- todo se suma con su signo. Si el total sale negativo, o si
    ``silent`` sale negativo mientras ``announced`` es positivo, el reporte lo
    dice en vez de esconderlo detras de un ``max(0, ...)`` o un ``abs(...)``.

    "Anunciada" se ata a la semana ``t`` usando el flag de ``announced_weeks``
    en la posicion ``t`` -- la misma semana cuyo exceso se esta atribuyendo,
    nunca la semana anterior ni la siguiente.

    Levanta ``ValueError`` si ``clean_weekly``/``dirty_weekly`` estan vacios,
    igual que ``weekly_cost``: "cero semanas es un error real de quien llama,
    no una respuesta valida" -- aca la respuesta invalida seria un
    ``Degradation`` todo en cero, es decir "Kern no degrado nada y no
    escondio nada", una afirmacion muy distinta de "no hay datos".
    """
    if len(clean_weekly) == 0:
        raise ValueError("no hay semanas que puntuar")
    clean = np.asarray(clean_weekly, dtype=float)
    dirty = np.asarray(dirty_weekly, dtype=float)
    if clean.shape != dirty.shape:
        raise ValueError(f"largo distinto: limpio {clean.shape} vs sucio {dirty.shape}")
    if len(announced_weeks) != len(clean):
        raise ValueError(
            f"largo distinto: {len(announced_weeks)} avisos para {len(clean)} semanas"
        )

    excess = dirty - clean
    flags = np.asarray(announced_weeks, dtype=bool)
    drop = float(excess.sum())
    announced = float(excess[flags].sum())
    return Degradation(
        level=level,
        drop=drop,
        announced=announced,
        silent=drop - announced,
        weeks_announced=int(flags.sum()),
    )
