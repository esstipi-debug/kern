"""Mide POR QUE Kern queda por debajo del piso con datos limpios, y emite la linea
base que publica ``documentation/KERN_POR_QUE_BAJO_EL_PISO.md``.

`documentation/KERN_CAPTURA_BAJO_DISRUPCION.md` publico el numero -40,6% y dejo la
pregunta escrita en su seccion de "que no dice": la captura limpia esta por debajo de
un punto de reorden fijo y la linea base no explica por que. Esto lo contesta con tres
experimentos sobre los MISMOS mundos, y esta separado de `verify_fidelidad.py` a
proposito: aquel graba la vara, este la interroga.

    PYTHONPATH=. python scripts/por_que_bajo_el_piso.py               # consola
    PYTHONPATH=. python scripts/por_que_bajo_el_piso.py --markdown    # bloques a pegar
    PYTHONPATH=. python scripts/por_que_bajo_el_piso.py --json documentation/lineas_base/por_que_bajo_el_piso.json

Tarda: cada semilla corre el piso, el oraculo y TRES carriles de Kern completos (~18
pasadas del orquestador). Sobre cinco semillas son decenas de minutos, asi que no es
un test -- es un script que se corre a mano y cuya salida se congela.

LOS TRES EXPERIMENTOS
---------------------
E1 · Descomposicion. El costo de cada carril, componente por componente. Contesta
     DONDE esta la diferencia, que no es lo mismo que por que.
E2 · Contrafacticos. Dos, cada uno aislando una causa candidata sobre el mismo mundo:
     `arranque_piso` reemplaza SOLO las semanas 4-7 de Kern por la politica del piso
     (mide el arranque muerto), y `cobertura_piso` le da a Kern la cobertura exacta del
     piso via `lead_times` (mide la hipotesis del buffer chico).
E3 · Perfil. Cuanto compra cada carril en cada tercio del horizonte. Contesta CUANDO.

POR QUE EL ARRANQUE MUERTO SE MIDE ASI
--------------------------------------
`MIN_PERIODS_FOR_POLICY` es 6 y el arnes replanifica cada 4 semanas con 4 de
calentamiento, asi que la primera llamada le lleva 4 periodos a Kern y la siguiente es
la semana 8. El piso arranca en la 4. El diferencial son exactamente las semanas 4-7, y
por eso el contrafactico reemplaza ESAS y nada mas.

Bajar `min_periods` NO es el contrafactico equivalente y no sirve como arreglo: medido,
la corrida sale `qa_failed` y la politica vuelve vacia igual. Se deja dicho aca para que
nadie lo intente de nuevo creyendo que es un parametro.
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from examples.run_world_sim import (  # noqa: E402
    EMPTY_POLICY,
    WARMUP_WEEKS,
    simulate,
    simulate_oracle,
)
from scm_agent.orchestrator import Orchestrator  # noqa: E402
from src.simworld.lens import export_history  # noqa: E402
from src.simworld.runners import (  # noqa: E402
    BLOCKING_GUIDED,
    KERN_BRIEF,
    WEEKS_PER_YEAR,
    Policy,
    naive_policy,
)
from src.simworld.scorer import score_run  # noqa: E402
from src.simworld.world import WeekOutcome, World, generate_world  # noqa: E402

#: Los mismos parametros con los que se grabo `captura_limpia.json`. Cambiar uno
#: invalida la comparacion contra el -40,6% publicado, que es el numero que este
#: documento existe para explicar.
N_SKUS = 8
N_PROVEEDORES = 2
SEMANAS = 24
REPLAN = 4
NIVEL = 0            # L0: la lente no corre ningun inyector
CARRIL = "forced"

#: Los cinco primeros mundos de la linea base (semillas 1000-1024).
SEMILLAS = (1000, 1001, 1002, 1003, 1004)

ORDER_COST = 75.0
HOLDING_RATE = 0.25

#: La cobertura del piso, en periodos: `naive_policy` fija R = 5 x la demanda semanal
#: media. El contrafactico `cobertura_piso` se la pasa a Kern como `lead_times` porque
#: ese es el unico canal por el que el tool acepta un periodo de riesgo por SKU.
COBERTURA_DEL_PISO = 5.0


def _politica_de_kern(world: World, outcomes: list[WeekOutcome], *, seed: int,
                      out_dir: Path, overrides: dict) -> Policy:
    """Kern por el orquestador completo sobre la lente limpia.

    Reimplementa `src.simworld.runners.kern_policy` en vez de llamarla porque los
    contrafacticos necesitan pasar `overrides`, que aquella no acepta. El resto del
    camino -- lente, orquestador, los mismos estados que bloquean -- es identico, y
    `tests/test_por_que_bajo_el_piso_publicado.py` fija que lo siga siendo.
    """
    vista = export_history(world, outcomes, out_dir / "view.csv", level=NIVEL, seed=seed)
    resultado = Orchestrator(clients_root=None).run(
        KERN_BRIEF, data_path=str(vista), job_type="inventory_optimization",
        out_dir=str(out_dir / "agent"), overrides=overrides,
    )
    if resultado.status != "ok" or resultado.report is None:
        return Policy(reorder_point={}, order_up_to={}, status=resultado.status)
    guiado = resultado.guided
    if guiado is not None and guiado.status in BLOCKING_GUIDED:
        return Policy(reorder_point={}, order_up_to={}, status=guiado.status)

    disparador: dict[str, float] = {}
    objetivo: dict[str, float] = {}
    for rec in resultado.report.recommendations:
        if rec.reorder_point is None:
            # El motor se nego a dimensionar este SKU Y tampoco pudo cubrirlo (menos
            # de `MIN_PERIODS_FOR_COVER` periodos, o sin costo usable). Se OMITE del
            # plan: meterlo con 0 diria "el punto de reorden es cero", que
            # `apply_policy` ejecuta igual -- no pedir nunca -- pero que en la traza es
            # indistinguible de una politica deliberada de no reponer.
            continue
        # Una `provisional_cover` entra igual que una politica: trae disparador y
        # objetivo. Lo que no trae es pronostico -- y esta `Policy` no lo lleva, asi
        # que aca no hace falta la tasa que `src/simworld/runners.py` si necesita.
        rop = float(rec.reorder_point)
        disparador[rec.product_id] = rop
        if rec.order_up_to is not None:
            objetivo[rec.product_id] = float(rec.order_up_to)
        else:
            objetivo[rec.product_id] = rop + float(rec.order_quantity or 0.0)

    return Policy(reorder_point=disparador, order_up_to=objetivo, status="ok")


def _componentes(world: World, outs: list[WeekOutcome]) -> dict:
    """El costo del carril abierto por componente, mas lo que hace falta para leerlo.

    `terminal` no es un componente del costo: es el valor de compra del stock que queda
    al cierre del horizonte. Se mide porque el oraculo sabe cuando termina la corrida y
    lo deja en cero, y ninguna politica puede -- es la parte de la brecha contra el
    techo que NO es una decision mala sino un artefacto de medir sobre un horizonte que
    se acaba. Sin el numero no se puede distinguir una cosa de la otra.
    """
    uc = np.array([s.unit_cost for s in world.skus], dtype=float)
    price = np.array([s.price for s in world.skus], dtype=float)
    holding_semanal = uc * HOLDING_RATE / WEEKS_PER_YEAR

    pedido = np.stack([o.ordered for o in outs], axis=1)
    estante = np.stack([o.on_hand_end for o in outs], axis=1)
    perdido = np.stack([o.lost for o in outs], axis=1)
    vendido = np.stack([o.sold for o in outs], axis=1)
    demanda = np.stack([o.demand for o in outs], axis=1)

    tercio = max(1, len(outs) // 3)
    por_tercio = [float(pedido[:, i:i + tercio].sum()) for i in range(0, len(outs), tercio)]

    return {
        "procurement": float((pedido * uc[:, None]).sum()),
        "holding": float((estante * holding_semanal[:, None]).sum()),
        "shortage": float((perdido * price[:, None]).sum()),
        "fixed": ORDER_COST * int((pedido > 0).sum()),
        "total": float(score_run(world, outs).total_cost),
        "fill": float(vendido.sum() / demanda.sum()) if demanda.sum() else 1.0,
        "unidades_pedidas": float(pedido.sum()),
        "eventos_de_orden": int((pedido > 0).sum()),
        "stock_terminal_usd": float((outs[-1].on_hand_end * uc).sum()),
        "unidades_por_tercio": por_tercio[:3],
    }


def _un_mundo(seed: int, raiz: Path) -> dict:
    """Piso, techo y los tres carriles de Kern sobre el MISMO mundo sorteado."""
    world = generate_world(seed=seed, n_skus=N_SKUS, n_suppliers=N_PROVEEDORES,
                           weeks=SEMANAS)

    congelado: Policy | None = None

    def piso(w, outs, _week):
        # El piso se calcula UNA vez, en la primera semana con WARMUP_WEEKS de
        # historia, y no se revisa: esa es su definicion.
        nonlocal congelado
        if congelado is not None:
            return congelado, False
        if len(outs) < WARMUP_WEEKS:
            return EMPTY_POLICY, False
        congelado = naive_policy(w, outs)
        return congelado, False

    piso_outs, _ = simulate(world, piso, replan_every=REPLAN)
    techo_outs = simulate_oracle(world)
    k_piso = score_run(world, piso_outs)
    k_techo = score_run(world, techo_outs)
    denominador = k_piso.total_cost - k_techo.total_cost

    def captura(outs: list[WeekOutcome]) -> float:
        if not denominador:
            return float("nan")
        return (k_piso.total_cost - score_run(world, outs).total_cost) / denominador

    cobertura = {s.sku: COBERTURA_DEL_PISO for s in world.skus}
    carriles = {
        "kern": {"overrides": {}, "arranque_piso": False},
        "arranque_piso": {"overrides": {}, "arranque_piso": True},
        "cobertura_piso": {"overrides": {"lead_times": cobertura}, "arranque_piso": False},
    }

    salida: dict = {
        "seed": seed,
        "piso": _componentes(world, piso_outs) | {"capture": 0.0},
        "techo": _componentes(world, techo_outs) | {"capture": 1.0},
    }
    with tempfile.TemporaryDirectory() as tmp:
        for nombre, cfg in carriles.items():
            def plan(w, outs, week, _cfg=cfg, _n=nombre):
                if len(outs) < WARMUP_WEEKS:
                    return EMPTY_POLICY, False
                if week < 8 and _cfg["arranque_piso"]:
                    # El diferencial contra el piso son EXACTAMENTE las semanas 4-7:
                    # el piso arranca en la 4 y Kern no puede antes de la 8.
                    return naive_policy(w, outs), False
                return _politica_de_kern(
                    w, outs, seed=seed, out_dir=Path(tmp) / _n / f"w{week:03d}",
                    overrides=_cfg["overrides"],
                ), False

            outs, _ = simulate(world, plan, replan_every=REPLAN)
            salida[nombre] = _componentes(world, outs) | {"capture": captura(outs)}
            print(f"  semilla {seed} {nombre:>15}: capture {salida[nombre]['capture']:+.4f}"
                  f"  fill {salida[nombre]['fill']:.3f}"
                  f"  unidades {salida[nombre]['unidades_pedidas']:>8,.0f}", flush=True)
    return salida


def _media(filas: list[dict], carril: str, campo: str) -> float:
    return statistics.fmean(f[carril][campo] for f in filas)


def medir(semillas=SEMILLAS) -> dict:
    filas = [_un_mundo(s, ROOT) for s in semillas]
    carriles = ("piso", "kern", "arranque_piso", "cobertura_piso", "techo")
    campos = ("capture", "total", "procurement", "holding", "fixed", "shortage",
              "fill", "unidades_pedidas", "eventos_de_orden", "stock_terminal_usd")
    return {
        "contexto": {
            "mundos": len(filas), "semillas": list(semillas), "n_skus": N_SKUS,
            "n_proveedores": N_PROVEEDORES, "semanas": SEMANAS,
            "replan_cada": REPLAN, "nivel": NIVEL, "carril": CARRIL,
            "cobertura_del_piso_periodos": COBERTURA_DEL_PISO,
        },
        "por_mundo": filas,
        "promedios": {
            c: {campo: _media(filas, c, campo) for campo in campos} for c in carriles
        },
        "unidades_por_tercio": {
            c: [statistics.fmean(f[c]["unidades_por_tercio"][i] for f in filas)
                for i in range(3)]
            for c in carriles
        },
    }


def frase(datos: dict) -> str:
    """La frase que el documento tiene que citar LITERAL, reconstruida desde el JSON.

    Mismo mecanismo que `Distribucion.frase()` en el barrido: el documento se compara
    contra su fuente, no contra si mismo.
    """
    p = datos["promedios"]
    base, arranque = p["kern"]["capture"], p["arranque_piso"]["capture"]
    brecha = (arranque - base) / (0.0 - base) * 100.0
    return (
        f"Sobre {datos['contexto']['mundos']} mundos, la captura pasa de "
        f"{base * 100:.1f}% a {arranque * 100:.1f}% cuando solo las semanas 4-7 usan la "
        f"politica del piso: {brecha:.0f}% de la brecha es el arranque muerto."
    )


def _markdown(datos: dict) -> str:
    p = datos["promedios"]
    filas_comp = "\n".join(
        f"| {c} | {p[c]['procurement']:,.0f} | {p[c]['holding']:,.0f} | "
        f"{p[c]['fixed']:,.0f} | {p[c]['shortage']:,.0f} | **{p[c]['total']:,.0f}** | "
        f"{p[c]['fill']:.3f} | {p[c]['unidades_pedidas']:,.0f} | "
        f"{p[c]['stock_terminal_usd']:,.0f} |"
        for c in ("piso", "kern", "arranque_piso", "cobertura_piso", "techo")
    )
    t = datos["unidades_por_tercio"]
    filas_tercio = "\n".join(
        f"| {c} | {t[c][0]:,.0f} | {t[c][1]:,.0f} | {t[c][2]:,.0f} |"
        for c in ("piso", "kern", "arranque_piso", "techo")
    )
    por_mundo = "\n".join(
        f"| {f['seed']} | {f['kern']['capture']:+.4f} | "
        f"{f['arranque_piso']['capture']:+.4f} | "
        f"{f['arranque_piso']['capture'] - f['kern']['capture']:+.4f} |"
        for f in datos["por_mundo"]
    )
    return (
        "| carril | procurement | holding | fixed | shortage | total | fill | unidades | terminal |\n"
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|\n" + filas_comp + "\n\n"
        "| carril | semanas 0-7 | 8-15 | 16-23 |\n|---|---:|---:|---:|\n" + filas_tercio + "\n\n"
        "| semilla | Kern | arranque del piso | delta |\n|---|---:|---:|---:|\n" + por_mundo + "\n\n"
        + frase(datos)
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--semillas", type=int, nargs="+", default=list(SEMILLAS))
    ap.add_argument("--json", type=str, default=None,
                    help="graba la linea base en esta ruta")
    ap.add_argument("--markdown", action="store_true",
                    help="emite los bloques listos para pegar en el documento")
    args = ap.parse_args()

    datos = medir(tuple(args.semillas))
    if args.json:
        destino = Path(args.json)
        destino.parent.mkdir(parents=True, exist_ok=True)
        destino.write_text(json.dumps(datos, indent=1) + "\n")
        print(f"\nlinea base grabada en {destino}")
    print("\n" + (_markdown(datos) if args.markdown else frase(datos)))


if __name__ == "__main__":
    main()
