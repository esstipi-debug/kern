"""Corre la grilla del marcador: piso, Kern y oraculo sobre el mismo mundo.

    PYTHONPATH=. python examples/run_world_sim.py
    PYTHONPATH=. python examples/run_world_sim.py --skus 120 --weeks 52 --levels 0 1 2 3
    PYTHONPATH=. python examples/run_world_sim.py --no-kern     # solo piso vs oraculo
    PYTHONPATH=. python examples/run_world_sim.py --lanes forced brief

Los valores por defecto de este CLI son deliberadamente chicos (ver
`--skus`/`--weeks` mas abajo): `kern_policy` corre el orquestador real por
cada replanificacion (del orden de 1-2 segundos por llamada a esta escala,
medido) y `oracle_orders` resuelve un MILP por SKU por semana (~50-60ms via
HiGHS, tambien medido, documentado en `src/simworld/runners.py`). La corrida
de produccion completa (120 SKUs x 52 semanas x 4 niveles) es del orden de
minutos, no segundos -- se deja disponible via `--skus`/`--weeks` explicitos,
nunca como default, para que nadie la dispare sin saber cuanto tarda.

Dos funciones de simulacion, no una: `simulate` sostiene una **politica**
entre replanificaciones y la reaplica cada semana con `apply_policy` (lo que
hace la operacion real: el plan del mes se fija una vez y se ejecuta semana a
semana). El oraculo no es un corredor de politica sino un clarividente: decide
cantidades semana a semana mirando el futuro real, y no hay punto de reorden
que lo represente -- forzarlo al mismo molde obligaria a reinterpretar
cantidades como niveles objetivo, que seria incorrecto.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path
from typing import Callable

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import numpy as np  # noqa: E402

from src.simworld.runners import (  # noqa: E402
    NAIVE_LOOKBACK_WEEKS,
    Policy,
    apply_policy,
    kern_policy,
    naive_policy,
    oracle_orders,
)
from src.simworld.scorer import (  # noqa: E402
    capture,
    degradation,
    score_run,
    score_shock,
    weekly_cost,
)
from src.simworld.shocks import (  # noqa: E402
    Shock,
    ShockError,
    apply_shocks,
    combined_window,
    parse_shock,
)
from src.simworld.world import WeekOutcome, World, advance, generate_world, initial_state  # noqa: E402
from src.state.record import (  # noqa: E402
    record_decision,
    record_forecast,
    record_outcome,
    record_stock,
)
from src.state.store import StateStore  # noqa: E402

# El job_type real que kern_policy(forced=True) ya pasa al orquestador -- ver
# docs/superpowers/specs/2026-07-31-marcador-state-bridge-design.md, decision 9.
STATE_BRIDGE_TOOL = "inventory_optimization"

# (world, outcomes-hasta-ahora, semana) -> (Policy, aviso)
Plan = Callable[[World, list[WeekOutcome], int], tuple[Policy, bool]]

DEFAULT_LANES: tuple[str, ...] = ("forced",)
# `naive_policy` solo mira las ultimas NAIVE_LOOKBACK_WEEKS igual, asi que
# tanto el piso como Kern esperan a tener esa misma cantidad de historia
# antes de su primera decision real -- no es una coincidencia de que ambos
# hardcodearan "4", es el mismo limite: comparar corredores desde el mismo
# punto de partida de informacion.
WARMUP_WEEKS = NAIVE_LOOKBACK_WEEKS

EMPTY_POLICY = Policy(
    reorder_point={}, order_up_to={}, announced=False, note="", status="ok",
)


def simulate(
    world: World,
    plan: Plan,
    *,
    replan_every: int = 4,
    state_store: StateStore | None = None,
) -> tuple[list[WeekOutcome], list[bool]]:
    """Corre el horizonte sosteniendo una politica entre replanificaciones.

    La politica se **reaplica** todas las semanas contra la posicion de
    inventario del momento (``apply_policy`` ya cuenta lo que viene en
    camino) -- lo que hace la operacion real: el plan se fija una vez y se
    ejecuta semana a semana, nunca se repite la orden a ciegas.

    El aviso (segundo elemento de la tupla que devuelve ``plan``) se marca
    solo en la semana en que efectivamente se replanifico: es cuando el
    corredor miro los datos y pudo decir algo. Las semanas intermedias
    heredan la politica pero no un aviso nuevo.

    Cuando ``state_store`` no es ``None``, ademas escribe ``stock``,
    ``outcomes`` y ``decisions`` cada semana, y ``forecast`` solo en las
    semanas de replanificacion (ver ``docs/superpowers/specs/
    2026-07-31-marcador-state-bridge-design.md``). ``state_store=None``
    (default) no escribe nada -- el comportamiento existente no cambia.

    Advertencia para quien lea un backtest corrido sobre este puente: un
    MAPE alto en ``decisions`` (``order_qty`` vs. ``actual_qty``) no
    significa que Kern decida mal -- una politica de punto de reorden pide
    en bloque para cubrir varios periodos, no para igualar la demanda
    semana a semana. El marcador honesto de si la decision fue buena sigue
    siendo ``src/simworld/scorer.py`` (captura normalizada contra el
    oraculo).
    """
    if replan_every < 1:
        raise ValueError(f"replan_every debe ser >= 1, no {replan_every}")

    state = initial_state(world)
    outcomes: list[WeekOutcome] = []
    flags: list[bool] = []
    policy = EMPTY_POLICY

    for week in range(world.weeks):
        cycle_id = str(week)
        announced = False
        replanned = week % replan_every == 0
        if replanned:
            policy, announced = plan(world, outcomes, week)
        orders = apply_policy(world, state, policy)
        state, outcome = advance(world, state, orders)
        outcomes.append(outcome)
        flags.append(bool(announced))

        if state_store is not None:
            _record_week(world, outcome, orders, policy, state_store, cycle_id,
                        replanned=replanned, outcomes_so_far=outcomes)
    return outcomes, flags


def _record_week(
    world: World,
    outcome: WeekOutcome,
    orders: dict[str, float],
    policy: Policy,
    state_store: StateStore,
    cycle_id: str,
    *,
    replanned: bool,
    outcomes_so_far: list[WeekOutcome],
) -> None:
    """Escribe los cuatro dominios de ``src.state`` para una semana, cuando
    ``simulate`` recibe un ``state_store``. Separado de ``simulate`` para que
    el bucle principal no cargue con el detalle de columnas de cada dominio."""
    # outcomes.append(outcome) ya corrio en simulate() antes de esta llamada,
    # asi que window tiene como minimo la semana actual -- nunca esta vacio.
    window = outcomes_so_far[-NAIVE_LOOKBACK_WEEKS:]
    record_stock(state_store, cycle_id, [
        {
            "product_id": sku.sku,
            "on_hand": float(outcome.on_hand_end[i]),
            "reorder_point": float(policy.reorder_point.get(sku.sku, 0.0)),
            # promedio semanal / 7: el dominio espera una tasa DIARIA
            # (scm_agent/monitors.py calcula "dias de cobertura" con esto).
            "avg_daily_demand": float(sum(w.sold[i] for w in window) / len(window) / 7.0),
        }
        for i, sku in enumerate(world.skus)
    ])
    record_outcome(state_store, cycle_id, [
        row
        for i, sku in enumerate(world.skus)
        for row in (
            {"product_id": sku.sku, "tool": STATE_BRIDGE_TOOL, "metric": "actual_qty",
             "value": float(outcome.demand[i]), "period": cycle_id},
            {"product_id": sku.sku, "tool": STATE_BRIDGE_TOOL, "metric": "sold_qty",
             "value": float(outcome.sold[i]), "period": cycle_id},
        )
    ])
    record_decision(state_store, cycle_id, [
        {
            "product_id": sku.sku, "tool": STATE_BRIDGE_TOOL,
            "decision": "reponer" if orders.get(sku.sku, 0.0) > 0 else "sin orden",
            "period": cycle_id, "order_qty": float(orders.get(sku.sku, 0.0)),
        }
        for sku in world.skus
    ])
    if replanned and policy.forecast:
        record_forecast(state_store, cycle_id, [
            {"product_id": product_id, "period": cycle_id, "forecast_qty": qty}
            for product_id, qty in policy.forecast.items()
        ])


def simulate_oracle(world: World) -> list[WeekOutcome]:
    """El techo. No es un corredor de politica: decide cantidades cada
    semana mirando la demanda futura verdadera, asi que tiene su propio
    bucle en vez de pasar por ``simulate``. **Nunca ve la lente** -- no toma
    ``level``/``seed`` ni llama a ``export_history`` en ningun punto."""
    state = initial_state(world)
    outcomes: list[WeekOutcome] = []
    for _ in range(world.weeks):
        orders = oracle_orders(world, state)
        state, outcome = advance(world, state, orders)
        outcomes.append(outcome)
    return outcomes


#: Los tipos de shock que solo muerden a traves de una orden EMITIDA. La demanda no:
#: ocurre igual la semana 0.
_SUPPLY_SHOCKS = ("lead_time_spike", "supplier_outage")


def _refuse_shocks_the_grid_cannot_measure(shocks: tuple[Shock, ...]) -> None:
    """Rechaza un shock de suministro que caiga entero dentro del calentamiento.

    Los corredores devuelven ``EMPTY_POLICY`` mientras tienen menos de ``WARMUP_WEEKS``
    de historia, asi que NO SE EMITE NINGUNA ORDEN en esas semanas. Un
    ``lead_time_spike`` o un ``supplier_outage`` solo muerden a traves de una orden
    emitida (``advance`` lee el lead real en el momento de emitir), de modo que una
    ventana que termina dentro del calentamiento perturba una matriz que nadie consulta.

    Medido antes de esta guarda: ``supplier_outage`` en las semanas 0-3 daba
    ``cost_excess_post_shock`` exactamente 0,00 y ``first_out_of_band_week`` ``None`` --
    el reporte decia "absorbido" sobre una disrupcion que no ocurrio. Es el peor resultado
    posible de esta funcionalidad, asi que se rechaza en vez de avisar: un aviso al lado
    de un numero que se ve bien no lo desarma.

    Vive aca y no en ``src/simworld/shocks.py`` a proposito: ``apply_shocks`` es una
    transformacion pura del mundo y no sabe -- ni debe saber -- que politica lo va a
    correr. Quien sabe del calentamiento es la grilla.
    """
    for shock in shocks:
        if shock.kind in _SUPPLY_SHOCKS and shock.end_week <= WARMUP_WEEKS:
            raise ShockError(
                f"{shock.as_cli()} termina en la semana {shock.end_week}, dentro de las "
                f"{WARMUP_WEEKS} semanas de calentamiento: ningun corredor emite ordenes "
                "todavia, asi que el shock no golpearia nada y la corrida saldria "
                f"'absorbido' por construccion. Arrancalo en la semana {WARMUP_WEEKS} o "
                "alargalo para que cruce el calentamiento."
            )


def _run_lanes(
    world: World,
    *,
    seed: int,
    levels: tuple[int, ...],
    lanes: tuple[str, ...],
    replan_every: int,
    include_kern: bool,
    out: Path,
    omit: frozenset[str] | set[str] | tuple[str, ...],
) -> dict:
    """Corre el piso, el techo y la grilla de Kern sobre UN mundo, y devuelve los crudos.

    Extraido de ``run_grid`` para poder correr la MISMA grilla sobre dos mundos -- el
    base y el perturbado por un shock -- sin escribir dos veces el bucle. Que sea el
    mismo codigo es lo que permite atribuir la diferencia al shock: dos implementaciones
    parecidas divergirian en algun detalle y la diferencia mediria eso.

    No arma reporte ni decide nada: devuelve outcomes, KPIs y costos semanales para que
    el llamador componga. El piso se congela por mundo (su definicion: punto de reorden
    fijo, calculado una vez con ``WARMUP_WEEKS`` de historia y nunca revisado), asi que
    cada llamada trae su propia clausura.
    """
    frozen_floor: Policy | None = None

    def naive_plan(w: World, outs: list[WeekOutcome], week: int) -> tuple[Policy, bool]:
        # El piso se calcula una sola vez, en la primera semana con
        # WARMUP_WEEKS de historia, y no se revisa mas: esa es su
        # definicion (punto de reorden fijo, congelado). `simulate()` sigue
        # llamando a esta clausura en CADA replanificacion (no solo en la
        # primera), asi que la congelacion tiene que vivir aca, en estado de
        # clausura, no en `naive_policy` (que es sin estado por diseno): la
        # primera vez que hay suficiente historia se calcula y se guarda en
        # `frozen_floor` via `nonlocal`; toda llamada posterior devuelve esa
        # MISMA `Policy`, sin volver a mirar `outs` ni recalcular nada.
        nonlocal frozen_floor
        if frozen_floor is not None:
            return frozen_floor, False
        if len(outs) < WARMUP_WEEKS:
            return EMPTY_POLICY, False
        frozen_floor = naive_policy(w, outs)
        return frozen_floor, False

    floor_outs, _ = simulate(world, naive_plan, replan_every=replan_every)
    ceil_outs = simulate_oracle(world)
    resultado: dict = {
        "floor_outs": floor_outs,
        "ceil_outs": ceil_outs,
        "floor_k": score_run(world, floor_outs),
        "ceil_k": score_run(world, ceil_outs),
        "lanes": {},
    }
    if not include_kern:
        return resultado

    for lane in lanes:
        forced = lane == "forced"
        for level in levels:
            status_counts: dict[str, int] = {}

            def kern_plan(
                w: World, outs: list[WeekOutcome], week: int,
                _level: int = level, _lane: str = lane, _forced: bool = forced,
                _counts: dict[str, int] = status_counts,
            ) -> tuple[Policy, bool]:
                if len(outs) < WARMUP_WEEKS:
                    return EMPTY_POLICY, False
                pol = kern_policy(
                    w, outs, level=_level, seed=seed,
                    out_dir=out / f"{_lane}-L{_level}" / f"w{week:03d}",
                    forced=_forced, omit=omit,
                )
                _counts[pol.status] = _counts.get(pol.status, 0) + 1
                return pol, pol.announced

            outs, flags = simulate(world, kern_plan, replan_every=replan_every)
            resultado["lanes"][f"{lane}-L{level}"] = {
                "lane": lane, "level": level,
                "outs": outs, "flags": flags,
                "kpis": score_run(world, outs),
                "weekly": weekly_cost(world, outs),
                "status_counts": status_counts,
                # Verdad fisica de "Kern actuo": unidades REALMENTE pedidas
                # (WeekOutcome.ordered, la cantidad que `advance()` acredito
                # ese SKU esa semana), sumadas sobre todas las semanas de la
                # corrida -- no el status que Kern se autoreporta. `ordered`
                # nunca es negativo (`advance()` levanta si algun llamador
                # intenta lo contrario), asi que la suma es 0 unicamente
                # cuando NINGUNA semana ordeno nada.
                "total_ordered": float(sum(o.ordered.sum() for o in outs)),
            }
    return resultado


def run_grid(
    *,
    seed: int = 4,
    n_skus: int = 8,
    n_suppliers: int = 2,
    weeks: int = 24,
    levels: tuple[int, ...] = (0, 1, 2, 3),
    replan_every: int = 4,
    out_dir: str | Path = "deliverables/simworld",
    include_kern: bool = True,
    lanes: tuple[str, ...] = DEFAULT_LANES,
    omit: frozenset[str] | set[str] | tuple[str, ...] = (),
    shocks: tuple[Shock, ...] | list[Shock] = (),
    shock_band: float = 0.02,
    shock_hold: int = 3,
) -> dict:
    """La grilla completa: 1 mundo x len(levels) niveles x len(lanes) carriles
    de Kern, mas el piso y el techo -- todos sobre la MISMA realizacion del
    mundo (una sola llamada a ``generate_world``, reusada para todo lo demas
    en esta funcion). Esa reutilizacion es la garantia de validez central del
    marcador: si algo regenerara el mundo por nivel, las comparaciones entre
    niveles dejarian de ser comparables en silencio.

    ``shocks`` (N2b) perturba ese mundo DESPUES del sorteo
    (``src.simworld.shocks.apply_shocks``) y los tres corredores corren sobre el mundo
    perturbado: el oraculo ve la verdad perturbada -- informacion perfecta, sigue siendo
    un techo genuino --, el piso quedo congelado en las semanas de calentamiento y no lo
    ve (es el piso), y Kern lo ve por la lente cada ``replan_every`` semanas, con su
    nivel de suciedad. Entonces ``capture`` pasa a ser **captura bajo disrupcion**, que
    es el numero que este parametro existe para producir.

    Con shocks, la grilla se corre DOS veces: una sobre el mundo base y otra sobre el
    perturbado. Es el precio de poder atribuir: sin la corrida limpia del mismo carril
    sobre la misma semilla, "el shock le costo X a Kern" no tiene contra que restarse.
    Sin shocks no se paga nada -- el camino es exactamente el de antes, una sola pasada.

    Un cambio de comportamiento del refactor, y es un arreglo: la referencia limpia de la
    degradacion (el nivel 0 del mismo carril) se arma ANTES del bucle, no a medida que
    corre. Antes, ``--levels 3 0`` no producia ninguna degradacion -- cuando le tocaba al
    nivel 3, el nivel 0 todavia no existia -- asi que un numero perfectamente definido se
    omitia por el orden en que el operador tipeo un argumento. Lo fija
    ``tests/test_simworld_e2e.py::test_degradation_no_depende_del_orden_en_que_se_pidieron_los_niveles``.
    """
    world_base = generate_world(
        seed=seed, n_skus=n_skus, n_suppliers=n_suppliers, weeks=weeks,
        regime="seasonal_trend",
    )
    shocks = tuple(shocks)
    _refuse_shocks_the_grid_cannot_measure(shocks)
    world = apply_shocks(world_base, shocks)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    corrida = _run_lanes(
        world, seed=seed, levels=levels, lanes=lanes, replan_every=replan_every,
        include_kern=include_kern, out=out, omit=omit,
    )
    # La referencia limpia solo existe cuando hay shock. Su salida va a otro directorio
    # para que los entregables de las dos pasadas no se pisen y se pueda abrir cualquiera
    # de las dos despues.
    limpia = (
        _run_lanes(
            world_base, seed=seed, levels=levels, lanes=lanes, replan_every=replan_every,
            include_kern=include_kern, out=out / "sin-shock", omit=omit,
        )
        if shocks else None
    )

    floor_k, ceil_k = corrida["floor_k"], corrida["ceil_k"]
    span = floor_k.total_cost - ceil_k.total_cost

    report: dict = {
        "seed": seed, "weeks": weeks, "n_skus": n_skus, "n_suppliers": n_suppliers,
        "regime": world.regime, "replan_every": replan_every,
        "levels": list(levels), "lanes": list(lanes),
        # Que inyectores se saltaron. Va en el reporte para que una corrida de
        # descomposicion no se pueda confundir con una corrida normal al leer
        # el JSON meses despues: un capture alto sin este campo significa otra
        # cosa que un capture alto con tres inyectores omitidos.
        "omit": sorted(omit),
        # Los shocks aplicados, en el formato que `parse_shock` vuelve a leer. Mismo
        # argumento que `omit`: una captura del 40% significa una cosa muy distinta con
        # un corte de proveedor encima, y el JSON tiene que decir cual de las dos es.
        "shocks": [s.as_cli() for s in shocks],
        "floor": asdict(floor_k), "ceiling": asdict(ceil_k), "kern": {},
    }

    if shocks:
        report["shock_floor"] = {
            "capture_note": (
                "el piso y el techo de este reporte son los del mundo CON shock: "
                "la captura de abajo es captura bajo disrupcion"
            ),
            "floor_sin_shock": asdict(limpia["floor_k"]),
            "ceiling_sin_shock": asdict(limpia["ceil_k"]),
        }

    for name, datos in corrida["lanes"].items():
        entry: dict = {
            "lane": datos["lane"], "level": datos["level"],
            "kpis": asdict(datos["kpis"]),
            "replan_status_counts": dict(datos["status_counts"]),
        }
        kpis, total_ordered = datos["kpis"], datos["total_ordered"]

        if span <= 0:
            # No es una fuga de informacion: es que este
            # mundo/escala no discrimina (el piso no cuesta mas que
            # el techo). `capture()` levantaria por esta misma
            # razon -- se evita llamarla para no confundir este caso
            # con el de mas abajo, que SI tiene que ser ruidoso.
            entry["capture"] = None
            entry["capture_note"] = (
                f"span degenerado: el piso ({floor_k.total_cost:,.2f}) no "
                f"cuesta mas que el techo ({ceil_k.total_cost:,.2f}) en "
                "este mundo/escala; la captura no es medible aca"
            )
        elif total_ordered <= 0.0:
            # Kern jamas coloco una orden real esta corrida. Antes
            # esto se inferia mirando `replan_status_counts` (si
            # nunca hubo un replan "ok"), pero eso mide el status
            # AUTOREPORTADO de Kern, no el resultado fisico: un lane
            # puede tener status="ok" en TODOS sus replans (Kern
            # "corrio bien" segun su propio vocabulario) y aun asi no
            # pedir una sola unidad -- p.ej. una politica cuyo
            # reorder_point/order_up_to salen en cero para cada SKU,
            # o cualquier escenario donde `apply_policy` decide
            # legitimamente que no hace falta reponer. El guard viejo
            # no se activaba ahi (el status fue "ok" siempre) y
            # publicaba una captura numerica enganosa (medida en la
            # revision: 0.941, "recupero 94% del hueco piso-techo",
            # de un lane que ordeno cero unidades). Chequear
            # `total_ordered` en vez del status cubre ese caso
            # ademas del caso original (todos los replans reales
            # bloquearon con needs_data/needs_clarification/
            # qa_failed/error/handoff/escalated, que tambien
            # devuelven siempre una Policy vacia y por lo tanto
            # tambien caen en `total_ordered <= 0.0`).
            entry["capture"] = None
            entry["capture_note"] = (
                "Kern no emitio ninguna orden esta corrida (estados "
                f"observados: {dict(datos['status_counts'])}); la captura de "
                "costo no es comparable"
            )
        else:
            # Deliberadamente SIN try/except: si Kern le gana al
            # oraculo (captura > 1), `capture()` levanta a proposito
            # -- eso es una fuga de informacion real del mundo hacia
            # la lente, y hay que pararse a buscarla, no envolverla
            # en un "capture: null" como si fuera un resultado mas.
            entry["capture"] = capture(
                floor_cost=floor_k.total_cost,
                kern_cost=kpis.total_cost,
                oracle_cost=ceil_k.total_cost,
            )
        # Con shock, la captura de arriba ES la captura bajo disrupcion. Se publica
        # ademas con ese nombre para que nadie tenga que deducirlo del campo `shocks`,
        # y porque es la clave que el barrido de N mundos agrega.
        if shocks:
            entry["capture_under_shock"] = entry["capture"]

        report["kern"][name] = entry

    # Degradacion por suciedad: el nivel 0 del MISMO carril es la referencia.
    clean_weekly_by_lane: dict[str, np.ndarray] = {
        d["lane"]: d["weekly"] for d in corrida["lanes"].values() if d["level"] == 0
    }
    for name, datos in corrida["lanes"].items():
        if datos["level"] == 0 or datos["lane"] not in clean_weekly_by_lane:
            continue
        clean = clean_weekly_by_lane[datos["lane"]]
        deg = degradation(clean, datos["weekly"], datos["flags"], level=datos["level"])
        report["kern"][name]["degradation"] = asdict(deg)
        excess = datos["weekly"] - clean
        worst_idx = int(np.argmax(np.abs(excess)))
        report["kern"][name]["degradation_largest_week"] = {
            "week": worst_idx, "value": float(excess[worst_idx]),
        }

    # El impacto del shock, carril por carril, contra la corrida limpia del MISMO carril.
    if shocks:
        ventana = combined_window(shocks)
        for name, datos in corrida["lanes"].items():
            ref = limpia["lanes"].get(name)
            if ref is None:
                continue
            # UNA medicion por corrida, no una por shock: con dos shocks hay un solo
            # mundo perturbado, y la ventana del primero incluiria el dano del segundo.
            # `combined_window` devuelve el shock mismo cuando hay uno solo.
            report["kern"][name]["shock"] = [
                asdict(score_shock(world_base, ref["outs"], datos["outs"], ventana,
                                   band=shock_band, hold=shock_hold))
            ]
            # Lo que el shock le costo a ESTE carril, partido en la parte que Kern aviso
            # y la que no. Es `degradation` tal cual -- la misma atribucion semana a
            # semana que ya se usa para la suciedad, con el mundo perturbado en el lugar
            # del sucio. Las semanas anteriores al shock son identicas en las dos
            # corridas (mismo mundo, misma politica), asi que su exceso es cero y el
            # total es el de la ventana; el corte anunciado/silencioso es lo que agrega:
            # dice si Kern VIO venir el golpe o lo absorbio sin decir nada.
            deg = degradation(ref["weekly"], datos["weekly"], datos["flags"],
                              level=datos["level"])
            report["kern"][name]["shock_degradation"] = asdict(deg)
        for etiqueta, clave in (("floor", "floor_outs"), ("ceiling", "ceil_outs")):
            report["shock_floor"][etiqueta] = [
                asdict(score_shock(world_base, limpia[clave], corrida[clave], ventana,
                                   band=shock_band, hold=shock_hold))
            ]

    (out / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    (out / "report.md").write_text(_markdown(report), encoding="utf-8")
    return report


def _markdown(report: dict) -> str:
    lines = [
        "# Marcador del mundo simulado",
        "",
        f"- Semilla: `{report['seed']}` (toda corrida es reproducible o no vale)",
        f"- Horizonte: {report['weeks']} semanas, {report['n_skus']} SKUs, "
        f"{report['n_suppliers']} proveedores",
        f"- Regimen: {report['regime']}. Replanificacion cada "
        f"{report['replan_every']} semanas.",
        "",
        "| Corredor | Costo total | Fill rate | Latigo | Captura |",
        "|---|---:|---:|---:|---:|",
        f"| Piso (reorden fijo) | {report['floor']['total_cost']:,.0f} | "
        f"{report['floor']['fill_rate']:.3f} | {report['floor']['bullwhip']:.2f} | 0.000 |",
    ]
    for name, entry in report.get("kern", {}).items():
        k = entry["kpis"]
        cap = entry.get("capture")
        cap_txt = f"{cap:.3f}" if cap is not None else "N/D"
        lines.append(
            f"| Kern {name} | {k['total_cost']:,.0f} | {k['fill_rate']:.3f} | "
            f"{k['bullwhip']:.2f} | {cap_txt} |"
        )
    lines.append(
        f"| Techo (oraculo) | {report['ceiling']['total_cost']:,.0f} | "
        f"{report['ceiling']['fill_rate']:.3f} | "
        f"{report['ceiling']['bullwhip']:.2f} | 1.000 |"
    )

    status_rows = [
        (name, entry["replan_status_counts"])
        for name, entry in report.get("kern", {}).items()
        if entry.get("replan_status_counts")
    ]
    if status_rows:
        lines += ["", "## Estados de las replanificaciones de Kern", "",
                  "Un HANDOFF/ESCALATED es Kern decidiendo correctamente no "
                  "adivinar -- ordena cero esa semana en vez de arriesgar una "
                  "cantidad inventada. Es un resultado legitimo del arnes, no "
                  "una falla.", "",
                  "| Carril-Nivel | Estados observados en replanificaciones |",
                  "|---|---|"]
        for name, counts in status_rows:
            txt = ", ".join(f"{k}={v}" for k, v in sorted(counts.items()))
            lines.append(f"| {name} | {txt} |")

    if report.get("shocks"):
        lines += ["", "## Bajo disrupcion", "",
                  "Shocks aplicados: " + ", ".join(f"`{x}`" for x in report["shocks"]) + ".",
                  "",
                  "La captura de la tabla de arriba es **captura bajo disrupcion**: piso, "
                  "techo y Kern corrieron todos sobre el mundo perturbado. El oraculo ve "
                  "la verdad perturbada (sigue siendo un techo genuino); el piso quedo "
                  "congelado en el calentamiento y no la ve; Kern la ve por la lente.",
                  "",
                  "TTR se cuenta desde la primera semana FUERA de la banda, no desde el "
                  "arranque del shock: la holgura que retrasa el golpe no es lentitud de "
                  "deteccion, y cobrarsela al sistema mediria otra cosa.",
                  "",
                  "**Las dos columnas de unidades perdidas miden cosas distintas y las "
                  "dos van:** `perdidas` es demanda del mundo perturbado que quedo sin "
                  "servir; `vs base` es cuanto menos se vendio que sin el shock, CON "
                  "SIGNO -- bajo un surge puede ser negativa (se vendio mas), y ahi la "
                  "primera es la unica que dice algo.",
                  "",
                  "**`Perdidas` y `Fill min` son ABSOLUTOS, no la parte que causo el "
                  "shock.** Un carril que ya servia mal sin disrupcion reporta un minimo "
                  "bajo y cientos de unidades perdidas igual. Las columnas causales -- "
                  "las que se leen como 'esto costo el shock' -- son `vs base` y "
                  "`Exceso de costo`, que son diferencias contra la corrida limpia del "
                  "MISMO carril sobre la MISMA semilla.",
                  "",
                  "| Corredor | Shock | Sale | TTR | Recupero | Fill min | Perdidas | vs base | Exceso de costo |",
                  "|---|---|---:|---:|---|---:|---:|---:|---:|"]
        filas = [(f"Kern {n}", e.get("shock", [])) for n, e in report.get("kern", {}).items()]
        piso = report.get("shock_floor", {})
        filas = ([("Piso", piso.get("floor", []))] + filas
                 + [("Techo", piso.get("ceiling", []))])
        for etiqueta, impactos in filas:
            for imp in impactos:
                ttr = "N/D" if imp["ttr_weeks"] is None else f"{imp['ttr_weeks']:.0f}"
                salida = ("absorbido" if imp["first_out_of_band_week"] is None
                          else str(imp["first_out_of_band_week"]))
                lines.append(
                    f"| {etiqueta} | `{imp['shock_kind']}@{imp['shock_target']}` | "
                    f"{salida} | {ttr} | {'si' if imp['recovered'] else 'NO'} | "
                    f"{imp['min_fill']:.3f} | {imp['lost_units_post_shock']:,.0f} | "
                    f"{imp['lost_vs_baseline']:+,.0f} | "
                    f"{imp['cost_excess_post_shock']:+,.0f} |"
                )
        avisos = [(n, e["shock_degradation"]) for n, e in report.get("kern", {}).items()
                  if "shock_degradation" in e]
        if avisos:
            lines += ["",
                      "### Del costo del shock, cuanto aviso Kern", "",
                      "Misma atribucion semana a semana que la degradacion por suciedad, "
                      "con el mundo perturbado en el lugar del sucio. Las semanas previas "
                      "al shock son identicas en las dos corridas, asi que el total es el "
                      "de la ventana; lo que agrega el corte es si Kern **vio venir** el "
                      "golpe o lo absorbio sin decir nada.", "",
                      "| Carril | Costo del shock | Anunciado | Silencio |",
                      "|---|---:|---:|---:|"]
            for n, d in avisos:
                lines.append(f"| {n} | {d['drop']:,.0f} | {d['announced']:,.0f} | "
                             f"{d['silent']:,.0f} |")

    degs = [(n, e) for n, e in report.get("kern", {}).items() if "degradation" in e]
    if degs:
        lines += ["", "## Degradacion anunciada (contra el nivel 0, mismo carril)", "",
                  "| Nivel | Caida | Anunciado | Silencio | Peor semana |",
                  "|---|---:|---:|---:|---:|"]
        for name, e in degs:
            d = e["degradation"]
            worst = e.get("degradation_largest_week")
            worst_txt = f"sem {worst['week']} ({worst['value']:,.0f})" if worst else "N/D"
            lines.append(
                f"| {name} | {d['drop']:,.0f} | {d['announced']:,.0f} | "
                f"{d['silent']:,.0f} | {worst_txt} |"
            )
        lines += [
            "",
            "Silencio = lo que la suciedad costo sin que Kern dijera nada "
            "ESA semana especifica. Sesgo conocido, deliberado, en contra de "
            "Kern: el aviso solo se marca en semanas de replanificacion real; "
            "una semana gobernada por un aviso emitido en una replanificacion "
            "anterior cuenta igual como 'silenciosa' aca, aunque un operador "
            "real siguiera actuando sobre ese aviso previo. El numero de "
            "'peor semana' existe para que una sola semana atipica no se lea "
            "como si fuera el patron completo -- revisar el JSON para el "
            "desglose si esta columna domina el total.",
        ]

    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Corre la grilla del marcador simulado (piso vs Kern vs oraculo).",
    )
    parser.add_argument("--seed", type=int, default=4)
    parser.add_argument("--skus", type=int, default=8,
                         help="default chico a proposito; usar --skus 120 para la "
                              "escala de produccion (varios minutos, ver el docstring "
                              "del modulo)")
    parser.add_argument("--suppliers", type=int, default=2)
    parser.add_argument("--weeks", type=int, default=24,
                         help="default chico a proposito; usar --weeks 52 para la "
                              "escala de produccion")
    parser.add_argument("--levels", type=int, nargs="+", default=[0, 1, 2, 3])
    parser.add_argument("--lanes", type=str, nargs="+", default=list(DEFAULT_LANES),
                         choices=["forced", "brief"],
                         help="'brief' necesita un proveedor de LLM configurado "
                              "(ANTHROPIC_API_KEY o similar) para no degradar siempre "
                              "a needs_clarification -- ver el reporte de la tarea")
    parser.add_argument("--replan-every", type=int, default=4)
    parser.add_argument("--out", type=str, default="deliverables/simworld")
    parser.add_argument("--no-kern", action="store_true", help="solo piso vs oraculo")
    parser.add_argument(
        "--shock", action="append", default=[], metavar="KIND:TARGET:START:WEEKS[:MAG]",
        help="perturba el mundo DESPUES del sorteo (repetible). "
             "kind: lead_time_spike | supplier_outage | demand_surge. "
             "target: un SKU (SKU-1003), un proveedor (Proveedor-2) o '*'. "
             "Ej: --shock lead_time_spike:SKU-1003:10:3:2. Con shocks la grilla corre "
             "DOS veces (mundo base y perturbado) y el reporte trae 'capture_under_shock'",
    )
    parser.add_argument("--shock-band", type=float, default=0.02,
                        help="banda de fill alrededor del baseline que cuenta como "
                             "'adentro' para el TTR (por defecto 0,02)")
    parser.add_argument("--shock-hold", type=int, default=3,
                        help="semanas seguidas adentro de la banda que cuentan como "
                             "recuperado (por defecto 3)")
    args = parser.parse_args()

    shocks = tuple(parse_shock(t) for t in args.shock)

    if shocks:
        print(f"Shocks: {', '.join(s.as_cli() for s in shocks)}")
        print("Con shock la grilla corre DOS veces (mundo base y perturbado): "
              "sin la corrida limpia no hay contra que restar.")
    print("Generando el mundo y corriendo la grilla (puede tardar minutos a escala grande)...")
    report = run_grid(
        seed=args.seed, n_skus=args.skus, n_suppliers=args.suppliers,
        weeks=args.weeks, levels=tuple(args.levels), replan_every=args.replan_every,
        out_dir=args.out, include_kern=not args.no_kern, lanes=tuple(args.lanes),
        shocks=shocks, shock_band=args.shock_band, shock_hold=args.shock_hold,
    )
    print("")
    print(f"  Piso  (reorden fijo)  costo {report['floor']['total_cost']:>14,.0f}  "
          f"fill {report['floor']['fill_rate']:.3f}")
    for name, entry in report.get("kern", {}).items():
        cap = entry.get("capture")
        cap_txt = f"{cap:.3f}" if cap is not None else "N/D"
        print(f"  Kern  {name:<16} costo {entry['kpis']['total_cost']:>14,.0f}  "
              f"fill {entry['kpis']['fill_rate']:.3f}  captura {cap_txt}  "
              f"estados {entry['replan_status_counts']}")
        for imp in entry.get("shock", []):
            ttr = "N/D" if imp["ttr_weeks"] is None else f"{imp['ttr_weeks']:.0f} sem"
            salida = ("absorbido" if imp["first_out_of_band_week"] is None
                      else f"sale en sem {imp['first_out_of_band_week']}")
            print(f"        shock {imp['shock_kind']}@{imp['shock_target']}: {salida}, "
                  f"TTR {ttr}, fill min {imp['min_fill']:.2f}, "
                  f"perdidas {imp['lost_units_post_shock']:,.0f} u "
                  f"(vs base {imp['lost_vs_baseline']:+,.0f} u)")
    print(f"  Techo (oraculo)       costo {report['ceiling']['total_cost']:>14,.0f}  "
          f"fill {report['ceiling']['fill_rate']:.3f}")
    print("")
    print(f"Reporte en: {Path(args.out).resolve()}")


if __name__ == "__main__":
    main()
