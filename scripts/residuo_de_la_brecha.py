"""Mide QUE QUEDA de la brecha contra el piso despues de cerrar el arranque muerto.

`documentation/KERN_POR_QUE_BAJO_EL_PISO.md` contesto por que Kern quedaba en -40,6% y
midio el arreglo: la cobertura provisional llevo la captura de -47,0% a -27,6% sobre
cinco mundos. Lo que dejo escrito, y no resuelto, es el residuo:

    > Con el arranque arreglado Kern sigue en -27,6%: gasta casi lo mismo que el piso y
    > aun asi pierde mas venta y deja mas stock muerto. Eso ya es una ineficiencia de
    > asignacion entre SKU y semanas, sin diagnosticar.

Esto lo diagnostica, sobre los MISMOS cinco mundos y los MISMOS parametros -- medir con
otros explicaria otra corrida.

    PYTHONPATH=. python scripts/residuo_de_la_brecha.py
    PYTHONPATH=. python scripts/residuo_de_la_brecha.py --markdown
    PYTHONPATH=. python scripts/residuo_de_la_brecha.py --json documentation/lineas_base/residuo_de_la_brecha.json

LOS CARRILES, Y CUALES SON DESPLEGABLES
---------------------------------------
La distincion importa mas que los numeros: un carril que le pasa a Kern algo que un
cliente no puede darle mide una CAUSA, no un arreglo, y presentarlo como arreglo seria
la misma confusion que este repositorio persigue en todas partes.

DESPLEGABLES -- lo que sale de las propias recepciones del cliente. `plazo_obs`,
`sigma_obs` y `proveedor_medido` usan SOLO las ordenes que ya se emitieron Y ya
llegaron antes de la semana de replanificacion. Nada del futuro, y cuando un SKU
todavia no vio llegar ninguna orden NO se le inventa un numero: se lo omite del
override, que es la regla de ausencia declarada de siempre.

DIAGNOSTICO -- `servicio_econ` le pasa a Kern la razon critica por SKU, que necesita el
PRECIO. La lente del marcador exporta `unit_cost` y no exporta precio, asi que hoy Kern
no puede calcularla de lo que lee; `jobs/intake.py` si sabe leer `revenue`, o sea que
la brecha es de canal, no de motor. Mide cuanto vale la pena cerrarla, no la cierra.

POR QUE `sigma_obs` VALE UN CARRIL PROPIO
-----------------------------------------
El periodo de riesgo es `sigma_x = sqrt(tau * sigma_d^2 + sigma_L^2 * mu_d^2)` y la
lente escribe `lead_time_std = 0.0` -- fiel a lo que trae un ERP, que guarda el plazo
PROMETIDO del proveedor y no su varianza. El motor lo lee como cero dispersion y pierde
el termino `sigma_L^2 * mu_d^2`, que es el DOMINANTE cuando la demanda es grande frente
a su propia dispersion. Al escribir este script el carril salia identico al de control,
y la causa no era el modelo: `scm_agent/tools.py` aceptaba `lead_time_stds` y no lo
reenviaba al job. Ese arreglo (y su guard, `tests/test_overrides_no_se_tiran.py`) salio
de correr este experimento.

Tarda: cada semilla corre el piso, el oraculo y CINCO carriles de Kern completos.
No es un test -- es un script que se corre a mano y cuya salida se congela.
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

from examples.run_world_sim import EMPTY_POLICY, WARMUP_WEEKS, simulate, simulate_oracle  # noqa: E402
from scripts.por_que_bajo_el_piso import (  # noqa: E402
    HOLDING_RATE,
    N_PROVEEDORES,
    N_SKUS,
    NIVEL,
    REPLAN,
    SEMANAS,
    SEMILLAS,
    _componentes,
    _politica_de_kern,
)
from src.simworld.runners import WEEKS_PER_YEAR, naive_policy  # noqa: E402
from src.simworld.scorer import score_run  # noqa: E402
from src.simworld.world import WeekOutcome, World, generate_world  # noqa: E402

#: El tope de la razon critica. Un `service_level` de 1,0 manda `z` a infinito y el
#: buffer con el; 0,9999 es z ~ 3,72, que es un numero y no una asintota.
MAX_SERVICE_LEVEL = 0.9999


def _plazo_observado(world: World, outs: list[WeekOutcome], week: int
                     ) -> tuple[dict[str, float], dict[str, float]]:
    """Media y desvio del plazo de las ordenes que YA LLEGARON, por SKU.

    Observable de verdad: una orden emitida en la semana `t` con plazo real `L` se ve
    recien cuando aterriza, o sea si `t + L <= week`. Un SKU al que todavia no le llego
    ninguna orden NO entra en el dict -- inventarle una media seria exactamente el
    defecto que el resto de la rama cierra --, y con una sola llegada hay media pero no
    desvio, asi que tampoco entra en el segundo.
    """
    medias: dict[str, float] = {}
    desvios: dict[str, float] = {}
    for i, sku in enumerate(world.skus):
        vistos = [
            float(world.real_lead_weeks[i, o.week])
            for o in outs
            if o.ordered[i] > 0 and o.week + world.real_lead_weeks[i, o.week] <= week
        ]
        if not vistos:
            continue
        medias[sku.sku] = float(np.mean(vistos))
        if len(vistos) > 1:
            desvios[sku.sku] = float(np.std(vistos, ddof=1))
    return medias, desvios


def _razon_critica(world: World) -> dict[str, float]:
    """Nivel de servicio economico por SKU.

    Perder una unidad cuesta `price` -- es como `src/simworld/scorer.py` valua la venta
    perdida desde el 2026-09-18 -- y guardarla una semana cuesta
    `unit_cost * HOLDING_RATE / 52`. La razon critica es `Cu / (Cu + Co)`, y sobre esta
    economia da ~0,998, no 0,95.
    """
    return {
        s.sku: min(MAX_SERVICE_LEVEL,
                   s.price / (s.price + s.unit_cost * HOLDING_RATE / WEEKS_PER_YEAR))
        for s in world.skus
    }


#: Cada carril: como se arma su `overrides` en cada replanificacion, y si un cliente
#: podria correrlo hoy. `None` en `despliegue` significa que es el control.
CARRILES = {
    "kern": (lambda w, outs, week, cr: {}, "control"),
    "plazo_obs": (
        lambda w, outs, week, cr: {"lead_times": _plazo_observado(w, outs, week)[0]},
        "desplegable",
    ),
    "sigma_obs": (
        lambda w, outs, week, cr: {"lead_time_stds": _plazo_observado(w, outs, week)[1]},
        "desplegable",
    ),
    "proveedor_medido": (
        lambda w, outs, week, cr: dict(
            zip(("lead_times", "lead_time_stds"), _plazo_observado(w, outs, week), strict=True)
        ),
        "desplegable",
    ),
    "servicio_econ": (lambda w, outs, week, cr: {"service_levels": cr}, "diagnostico"),
    "todo": (
        lambda w, outs, week, cr: dict(
            zip(("lead_times", "lead_time_stds"), _plazo_observado(w, outs, week), strict=True)
        ) | {"service_levels": cr},
        "diagnostico",
    ),
}


def _un_mundo(seed: int) -> dict:
    """Piso, techo y los cinco carriles de Kern sobre el MISMO mundo sorteado."""
    world = generate_world(seed=seed, n_skus=N_SKUS, n_suppliers=N_PROVEEDORES,
                           weeks=SEMANAS)
    congelado: "object | None" = None

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
    denominador = k_piso - k_techo

    def captura(outs: list[WeekOutcome]) -> float:
        if not denominador:
            return float("nan")
        return (k_piso - score_run(world, outs).total_cost) / denominador

    critica = _razon_critica(world)
    salida: dict = {
        "seed": seed,
        "nivel_suciedad": NIVEL,
        "razon_critica_media": statistics.fmean(critica.values()),
        "piso": _componentes(world, piso_outs) | {"capture": 0.0},
        "techo": _componentes(world, techo_outs) | {"capture": 1.0},
    }
    with tempfile.TemporaryDirectory() as tmp:
        for nombre, (mk, clase) in CARRILES.items():
            def plan(w, outs, week, _mk=mk, _n=nombre):
                if len(outs) < WARMUP_WEEKS:
                    return EMPTY_POLICY, False
                return _politica_de_kern(
                    w, outs, seed=seed, out_dir=Path(tmp) / _n / f"w{week:03d}",
                    overrides=_mk(w, outs, week, critica),
                ), False

            outs, _ = simulate(world, plan, replan_every=REPLAN)
            salida[nombre] = _componentes(world, outs) | {
                "capture": captura(outs), "clase": clase,
            }
            print(f"  semilla {seed} {nombre:>17} [{clase:<12}]: capture "
                  f"{salida[nombre]['capture']:+.4f}  fill {salida[nombre]['fill']:.3f}"
                  f"  quiebre {salida[nombre]['shortage']:>9,.0f}"
                  f"  term {salida[nombre]['stock_terminal_usd']:>8,.0f}", flush=True)
    return salida


def _media(filas: list[dict], carril: str, campo: str) -> float:
    return statistics.fmean(f[carril][campo] for f in filas)


def medir(semillas=SEMILLAS) -> dict:
    filas = [_un_mundo(s) for s in semillas]
    carriles = ["piso", *CARRILES, "techo"]
    resumen = {
        c: {
            campo: _media(filas, c, campo)
            for campo in ("capture", "fill", "shortage", "procurement", "holding",
                          "fixed", "total", "unidades_pedidas", "stock_terminal_usd",
                          "canieria_usd", "pagado_no_vendido_usd")
        }
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
            "n_skus": N_SKUS, "n_proveedores": N_PROVEEDORES, "semanas": SEMANAS,
            "replan": REPLAN, "nivel": NIVEL, "semillas": list(semillas),
            "holding_rate": HOLDING_RATE, "max_service_level": MAX_SERVICE_LEVEL,
        },
        "por_semilla": filas,
        "resumen": resumen,
    }


def frase(datos: dict) -> str:
    """La linea citable, derivada de la medicion y nunca escrita a mano."""
    r = datos["resumen"]
    mejor = max(CARRILES, key=lambda c: r[c]["capture"])
    return (
        f"Sobre {len(datos['parametros']['semillas'])} mundos, el residuo de -"
        f"{abs(r['kern']['capture']) * 100:.1f}% se mueve a {r[mejor]['capture'] * 100:+.1f}% "
        f"con el carril '{mejor}' ({r[mejor]['clase']}), que mejora en "
        f"{r[mejor]['mundos_que_mejoran']} de {len(datos['parametros']['semillas'])} mundos."
    )


def _tabla(datos: dict) -> str:
    r = datos["resumen"]
    filas = [
        "| carril | clase | captura | fill | quiebre | compra | terminal | unidades |",
        "|---|---|---:|---:|---:|---:|---:|---:|",
    ]
    for c in ["piso", *CARRILES, "techo"]:
        clase = r[c].get("clase", "vara")
        filas.append(
            f"| {c} | {clase} | {r[c]['capture'] * 100:+.1f}% | {r[c]['fill']:.3f} "
            f"| {r[c]['shortage']:,.0f} | {r[c]['procurement']:,.0f} "
            f"| {r[c]['stock_terminal_usd']:,.0f} | {r[c]['unidades_pedidas']:,.0f} |"
        )
    return "\n".join(filas)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", type=Path, help="graba la medicion cruda")
    ap.add_argument("--markdown", action="store_true", help="emite la tabla a pegar")
    ap.add_argument("--semillas", type=int, nargs="+", default=list(SEMILLAS))
    args = ap.parse_args()

    datos = medir(tuple(args.semillas))
    print("\n" + _tabla(datos))
    print("\n" + frase(datos))
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(datos, indent=2, sort_keys=True) + "\n")
        print(f"\nlinea base -> {args.json}")
    if args.markdown:
        print("\n---\n" + _tabla(datos))


if __name__ == "__main__":
    main()
