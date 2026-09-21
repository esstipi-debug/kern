"""Cuanto compra una politica que sabe cuando se acaba de vender un SKU.

`KERN_HORIZONTE_DE_LA_VARA.md` cerro que el efecto de fin de horizonte NO explica el
residuo -- la vara no se lo debe a Kern -- y dejo abierta la unica version DESPLEGABLE de
"horizonte", que es otra cosa: no la fecha en que se acaba el marcador, sino una **fecha
de fin REAL por SKU** (discontinuacion, fin de temporada, cierre de ventana promocional)
que el archivo del cliente tendria que traer. La cadena de un cliente no se acaba; un SKU
si.

Se mide ANTES de construirla, que es la disciplina de toda esta rama: si la palanca no
paga, no se agrega un campo al intake ni una regla al motor.

LA REGLA QUE SE MIDE, EN UNA LINEA
    No acumules por encima de lo que todavia podes vender. Con `restante` = demanda que
    queda hasta la fecha de fin del SKU, se topean las DOS mitades de la politica:
    `order_up_to = min(order_up_to, restante)` y `reorder_point = min(reorder_point,
    restante)`. Topear solo el objetivo dejaria el disparador alto y seguiria pidiendo;
    topear solo el disparador dejaria el objetivo alto y la orden seria igual de grande.

    Es un post-proceso sobre la `Policy` que devuelve el orquestador, NO un cambio de
    motor: lo que se mide es el techo de la idea, no una implementacion.

DOS CARRILES, PORQUE `restante` SE PUEDE SABER DE DOS MANERAS
    - `horizonte_real` (DIAGNOSTICO): la demanda futura verdadera del mundo. Nadie la
      tiene; es la cota superior de lo que la idea puede pagar.
    - `horizonte_estimado` (DESPLEGABLE): la media observada por SKU hasta la semana de
      replanificacion, por las semanas que faltan. Es exactamente lo que un cliente con
      una fecha de discontinuacion puede calcular con su propio historial -- sin mirar
      el futuro.

    La brecha entre los dos ES el valor de la informacion que no se tiene.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
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
from scripts.por_que_bajo_el_piso import _componentes, _politica_de_kern  # noqa: E402
from src.simworld.runners import Policy, naive_policy  # noqa: E402
from src.simworld.scorer import score_run  # noqa: E402
from src.simworld.world import WeekOutcome, World, generate_world  # noqa: E402

SEMILLAS = (1000, 1001, 1002, 1003, 1004)
N_SKUS, N_PROVEEDORES, SEMANAS, REPLAN = 8, 2, 24, 4
SALIDA = ROOT / "documentation" / "lineas_base" / "horizonte_por_sku.json"


def _restante_real(world: World, week: int) -> dict[str, float]:
    """La demanda que de verdad queda por delante. Nadie la tiene: es la cota superior."""
    return {
        sku.sku: float(world.demand[i, week:].sum())
        for i, sku in enumerate(world.skus)
    }


def _restante_estimado(world: World, outs: list[WeekOutcome], week: int) -> dict[str, float]:
    """La media observada por las semanas que faltan -- lo que un cliente SI puede calcular.

    Usa la demanda que ya ocurrio, no la que viene. Un SKU sin historia todavia no entra:
    inventarle una media seria la clase de cero que esta rama persigue en todas partes.
    """
    if not outs:
        return {}
    vista = np.stack([o.demand for o in outs], axis=1)
    faltan = max(0, world.weeks - week)
    return {
        sku.sku: float(vista[i].mean() * faltan)
        for i, sku in enumerate(world.skus)
        if vista[i].size
    }


def _topar(pol: Policy, restante: dict[str, float]) -> Policy:
    """No acumules por encima de lo que todavia podes vender.

    Se topean las DOS mitades. Un SKU sin `restante` calculado se deja INTACTO -- topear
    con un cero que nadie midio seria decirle que no compre nunca mas, que es justo el
    defecto que `tests/test_negativa_no_es_cero.py` fija en el productor.
    """
    tope = lambda d: {  # noqa: E731
        k: (min(v, max(0.0, restante[k])) if k in restante else v) for k, v in d.items()
    }
    return Policy(
        reorder_point=tope(pol.reorder_point),
        order_up_to=tope(pol.order_up_to),
        announced=pol.announced,
        note=pol.note,
        status=pol.status,
        extras=dict(pol.extras),
        forecast=dict(pol.forecast),
    )


#: nombre -> (como calcular `restante`, clase)
CARRILES = {
    "kern": (None, "control"),
    "horizonte_estimado": (
        lambda w, outs, week: _restante_estimado(w, outs, week),
        "desplegable",
    ),
    "horizonte_real": (lambda w, outs, week: _restante_real(w, week), "diagnostico"),
}


def _un_mundo(seed: int) -> dict:
    world = generate_world(seed=seed, n_skus=N_SKUS, n_suppliers=N_PROVEEDORES,
                           weeks=SEMANAS)
    congelado: object | None = None

    def piso(w, outs, _week):
        nonlocal congelado
        if congelado is not None:
            return congelado, False
        if len(outs) < WARMUP_WEEKS:
            return EMPTY_POLICY, False
        congelado = naive_policy(w, outs)
        return congelado, False

    piso_outs, _ = simulate(world, piso, replan_every=REPLAN)
    techo_outs = simulate_oracle(world)
    k_piso = score_run(world, piso_outs).total_cost
    k_techo = score_run(world, techo_outs).total_cost
    den = k_piso - k_techo

    def captura(outs: list[WeekOutcome]) -> float:
        return float("nan") if not den else (k_piso - score_run(world, outs).total_cost) / den

    salida: dict = {
        "seed": seed,
        "piso": _componentes(world, piso_outs) | {"capture": 0.0, "clase": "vara"},
        "techo": _componentes(world, techo_outs) | {"capture": 1.0, "clase": "vara"},
    }
    with tempfile.TemporaryDirectory() as tmp:
        for nombre, (mk, clase) in CARRILES.items():
            def plan(w, outs, week, _mk=mk, _n=nombre):
                if len(outs) < WARMUP_WEEKS:
                    return EMPTY_POLICY, False
                pol = _politica_de_kern(
                    w, outs, seed=seed,
                    out_dir=Path(tmp) / _n / f"w{week:03d}", overrides={},
                )
                return (pol if _mk is None else _topar(pol, _mk(w, outs, week))), False

            outs, _ = simulate(world, plan, replan_every=REPLAN)
            salida[nombre] = _componentes(world, outs) | {
                "capture": captura(outs), "clase": clase,
            }
            print(f"  semilla {seed} {nombre:>18} [{clase:<12}]: capture "
                  f"{salida[nombre]['capture']:+.4f}  fill {salida[nombre]['fill']:.3f}"
                  f"  unidades {salida[nombre]['unidades_pedidas']:>8,.0f}"
                  f"  cierre {salida[nombre]['pagado_no_vendido_usd']:>9,.0f}", flush=True)
    return salida


def medir(semillas=SEMILLAS) -> dict:
    filas = []
    for s in semillas:
        t0 = time.time()
        filas.append(_un_mundo(s))
        print(f"  semilla {s} lista ({time.time() - t0:.0f}s)", flush=True)

    carriles = ["piso", *CARRILES, "techo"]
    campos = ("capture", "fill", "shortage", "procurement", "holding", "fixed", "total",
              "unidades_pedidas", "stock_terminal_usd", "canieria_usd",
              "pagado_no_vendido_usd")
    resumen = {
        c: {campo: statistics.fmean(f[c][campo] for f in filas) for campo in campos}
        for c in carriles
    }
    for c in CARRILES:
        resumen[c]["clase"] = CARRILES[c][1]
        resumen[c]["mejora_vs_kern"] = resumen[c]["capture"] - resumen["kern"]["capture"]
        resumen[c]["mundos_que_mejoran"] = sum(
            1 for f in filas if f[c]["capture"] > f["kern"]["capture"]
        )
    return {
        "parametros": {
            "semillas": list(semillas), "n_skus": N_SKUS,
            "n_proveedores": N_PROVEEDORES, "semanas": SEMANAS, "replan": REPLAN,
            "nivel": 0,
        },
        "por_semilla": filas,
        "resumen": resumen,
    }


def frase(datos: dict) -> str:
    r = datos["resumen"]
    est, real = r["horizonte_estimado"], r["horizonte_real"]
    n = len(datos["parametros"]["semillas"])
    return (
        f"Topear la politica por lo que queda por vender mueve la captura "
        f"{est['mejora_vs_kern']:+.4f} con la media observada (desplegable, mejora en "
        f"{est['mundos_que_mejoran']}/{n} mundos) y {real['mejora_vs_kern']:+.4f} con la "
        f"demanda futura verdadera (diagnostico, {real['mundos_que_mejoran']}/{n}). "
        f"La brecha entre los dos es el valor de la informacion que nadie tiene."
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--semillas", type=int, nargs="+", default=list(SEMILLAS))
    ap.add_argument("--json", type=Path, default=SALIDA)
    args = ap.parse_args()

    datos = medir(tuple(args.semillas))
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(datos, indent=2, sort_keys=True) + "\n")

    r = datos["resumen"]
    print(f"\n{'carril':20}{'clase':>14}{'captura':>10}{'vs kern':>10}{'mejoran':>9}"
          f"{'fill':>8}{'unidades':>10}{'cierre':>11}")
    for c in ["piso", *CARRILES, "techo"]:
        v = r[c]
        print(f"{c:20}{v.get('clase', 'vara'):>14}{v['capture']:>+10.4f}"
              f"{v.get('mejora_vs_kern', float('nan')):>+10.4f}"
              f"{str(v.get('mundos_que_mejoran', '-')):>9}{v['fill']:>8.3f}"
              f"{v['unidades_pedidas']:>10,.0f}{v['pagado_no_vendido_usd']:>11,.0f}")
    print(f"\n{frase(datos)}\nlinea base: {args.json}")


if __name__ == "__main__":
    main()
