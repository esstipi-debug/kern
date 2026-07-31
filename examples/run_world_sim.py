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
from src.simworld.scorer import capture, degradation, score_run, weekly_cost  # noqa: E402
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
) -> dict:
    """La grilla completa: 1 mundo x len(levels) niveles x len(lanes) carriles
    de Kern, mas el piso y el techo -- todos sobre la MISMA realizacion del
    mundo (una sola llamada a ``generate_world``, reusada para todo lo demas
    en esta funcion). Esa reutilizacion es la garantia de validez central del
    marcador: si algo regenerara el mundo por nivel, las comparaciones entre
    niveles dejarian de ser comparables en silencio.
    """
    world = generate_world(
        seed=seed, n_skus=n_skus, n_suppliers=n_suppliers, weeks=weeks,
        regime="seasonal_trend",
    )
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

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
    floor_k = score_run(world, floor_outs)
    ceil_k = score_run(world, ceil_outs)
    # Mismos kwargs por defecto que `oracle_orders` (ninguno se pasa
    # explicitamente aca, a proposito): si algun dia este CLI necesita
    # exponer order_cost/holding_rate/shortage_penalty_rate como flags,
    # TIENEN que aplicarse a `oracle_orders` y a `score_run`/`weekly_cost`
    # a la vez o el techo deja de ser un techo genuino contra lo medido.
    span = floor_k.total_cost - ceil_k.total_cost

    report: dict = {
        "seed": seed, "weeks": weeks, "n_skus": n_skus, "n_suppliers": n_suppliers,
        "regime": world.regime, "replan_every": replan_every,
        "levels": list(levels), "lanes": list(lanes),
        "floor": asdict(floor_k), "ceiling": asdict(ceil_k), "kern": {},
    }

    if include_kern:
        clean_weekly_by_lane: dict[str, np.ndarray] = {}
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
                        forced=_forced,
                    )
                    _counts[pol.status] = _counts.get(pol.status, 0) + 1
                    return pol, pol.announced

                outs, flags = simulate(world, kern_plan, replan_every=replan_every)
                kpis = score_run(world, outs)
                wk = weekly_cost(world, outs)

                entry: dict = {
                    "lane": lane, "level": level,
                    "kpis": asdict(kpis),
                    "replan_status_counts": dict(status_counts),
                }

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
                elif status_counts.get("ok", 0) == 0:
                    # Kern nunca llego a un replan "ok" en toda la corrida:
                    # todos los replans reales (si hubo alguno) bloquearon
                    # (needs_data/needs_clarification/qa_failed/error/
                    # handoff/escalated) -- ningun estado no-"ok" de
                    # `kern_policy` devuelve nunca una politica con
                    # cantidades reales, asi que esta corrida jamas emitio
                    # una orden. Una captura numerica aca se leeria como un
                    # resultado real (p.ej. "60% del hueco piso-techo"),
                    # cuando en realidad compara el costo de un corredor que
                    # no actuo contra un piso y un techo que si ordenan algo
                    # -- un numero que parece un logro y no lo es.
                    entry["capture"] = None
                    entry["capture_note"] = (
                        "Kern no emitio ninguna orden esta corrida (estados "
                        f"observados: {dict(status_counts)}); la captura de "
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

                if level == 0:
                    clean_weekly_by_lane[lane] = wk
                elif lane in clean_weekly_by_lane:
                    clean = clean_weekly_by_lane[lane]
                    deg = degradation(clean, wk, flags, level=level)
                    entry["degradation"] = asdict(deg)
                    excess = wk - clean
                    worst_idx = int(np.argmax(np.abs(excess)))
                    entry["degradation_largest_week"] = {
                        "week": worst_idx, "value": float(excess[worst_idx]),
                    }

                report["kern"][f"{lane}-L{level}"] = entry

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
    args = parser.parse_args()

    print("Generando el mundo y corriendo la grilla (puede tardar minutos a escala grande)...")
    report = run_grid(
        seed=args.seed, n_skus=args.skus, n_suppliers=args.suppliers,
        weeks=args.weeks, levels=tuple(args.levels), replan_every=args.replan_every,
        out_dir=args.out, include_kern=not args.no_kern, lanes=tuple(args.lanes),
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
    print(f"  Techo (oraculo)       costo {report['ceiling']['total_cost']:>14,.0f}  "
          f"fill {report['ceiling']['fill_rate']:.3f}")
    print("")
    print(f"Reporte en: {Path(args.out).resolve()}")


if __name__ == "__main__":
    main()
