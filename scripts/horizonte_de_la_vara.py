"""Cuanto del residuo de la brecha es la VARA y no Kern.

`documentation/KERN_RESIDUO_DE_LA_BRECHA.md` cerro con una pregunta abierta, escrita
en su propia seccion de limites:

    **No diagnostica el efecto de fin de horizonte por separado.** Castiga a cualquier
    politica contra un oraculo que sabe cuando termina la corrida, y por eso parte del
    residuo es de la vara y no de Kern. Cuanta parte queda abierto.

El razonamiento era: el techo es un Wagner-Whitin sobre `horizon = world.weeks`, o sea
que sabe la fecha de fin y cierra con stock terminal CERO; ninguna politica de punto de
reorden puede hacer eso. Suena a que una parte del -27,6 % es artefacto de medicion.

Este script lo mide en vez de suponerlo, por DOS caminos independientes, porque el
primero solo es creible si el segundo lo confirma.

CAMINO 1 -- neutralizar el borde sobre las corridas YA publicadas
    `score_run` cobra `procurement` de toda unidad comprada y no acredita nada por la
    que queda en el estante al cierre. Acreditarla a una tasa de rescate `s` borra
    progresivamente el castigo de fin de horizonte. Es aritmetica sobre la linea base
    de `residuo_de_la_brecha.json`: mismas corridas, otra convencion de borde.

CAMINO 2 -- mover la fecha de fin, con la demanda CONGELADA
    Se sortea un mundo de `W + K` semanas y se lo TRUNCA a `W` con `dataclasses.replace`
    -- transformacion pura, el rng no se toca, asi que las dos copias comparten la
    realizacion de demanda de `[0, W)` unidad por unidad. Se corren los tres carriles en
    las dos y se puntua SIEMPRE la ventana `[0, W)`. La unica diferencia entre las dos
    es si el mundo se acaba en `W` o sigue.

    El control que hace creible al camino 2: **piso y Kern son ciegos al horizonte**, asi
    que sus costos sobre `[0, W)` tienen que salir IDENTICOS entre las dos corridas. Si
    difieren, el experimento no esta midiendo el horizonte sino otra cosa que se colo, y
    el script se planta en vez de publicar el numero.

POR QUE HACEN FALTA LAS DOS VALUACIONES DEL BORDE
    Puntuar una ventana finita NO tiene una convencion neutra: el stock que queda en el
    borde vale su MARGEN futuro, que la ventana no ve. Acreditarlo a `unit_cost` devuelve
    lo pagado y nada mas (cota INFERIOR del credito); acreditarlo a `price` le regala el
    ingreso completo sin el costo de servirlo (cota SUPERIOR). La verdad esta entre las
    dos, asi que se publican las dos y la conclusion solo vale si aguanta en ambas.
"""

from __future__ import annotations

import argparse
import dataclasses
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
from scripts.por_que_bajo_el_piso import _politica_de_kern  # noqa: E402
from src.simworld.runners import naive_policy  # noqa: E402
from src.simworld.scorer import score_run  # noqa: E402
from src.simworld.world import WeekOutcome, World, generate_world  # noqa: E402

SEMILLAS = (1000, 1001, 1002, 1003, 1004)
VENTANA = 24          # `W`: la ventana puntuada, la misma de la linea base publicada
COLAS = (0, 12, 24)   # `K`: semanas de mundo DESPUES de la ventana. 0 es el caso publicado
N_SKUS, N_PROVEEDORES, REPLAN = 8, 2, 4
CARRILES = ("piso", "kern", "techo")

BASE_RESIDUO = ROOT / "documentation" / "lineas_base" / "residuo_de_la_brecha.json"
SALIDA = ROOT / "documentation" / "lineas_base" / "horizonte_de_la_vara.json"


# ---------------------------------------------------------------------------
# Camino 1: la tasa de rescate sobre lo ya publicado
# ---------------------------------------------------------------------------


def barrido_de_rescate(base: dict, tasas=(0.0, 0.25, 0.5, 0.75, 1.0)) -> dict:
    """Captura media por carril acreditando el stock terminal a cada tasa.

    La captura es un cociente, asi que se calcula POR SEMILLA y recien despues se
    promedia: promediar los totales primero y dividir despues es otro numero, y no el
    que publico la linea base.
    """
    filas = base["por_semilla"]
    carriles = list(base["resumen"])

    def captura(fila: dict, carril: str, s: float) -> float:
        aj = lambda c: fila[c]["total"] - s * fila[c]["stock_terminal_usd"]  # noqa: E731
        piso, techo = aj("piso"), aj("techo")
        return (piso - aj(carril)) / (piso - techo)

    return {
        c: {f"{s:.2f}": statistics.fmean(captura(f, c, s) for f in filas) for s in tasas}
        for c in carriles
    }


# ---------------------------------------------------------------------------
# Camino 2: mover la fecha de fin con la demanda congelada
# ---------------------------------------------------------------------------


def truncar(world: World, weeks: int) -> World:
    """El MISMO mundo con otra fecha de fin.

    Transformacion pura sobre un mundo YA sorteado -- el mismo contrato que
    `src/simworld/shocks.py`: no toca el `rng`, no llama a `generate_world`, y por eso
    *misma semilla + mismo truncado => mismo mundo*. Volver a sortear con
    `weeks=W` NO serviria: `_seasonal_trend_demand` normaliza la tendencia por
    `max(weeks, 1)` y sortea el ruido en un bloque `(n_skus, weeks)`, asi que un mundo
    de 24 semanas y uno de 36 con la misma semilla difieren TAMBIEN en sus primeras 24.
    """
    if weeks > world.weeks:
        raise ValueError(f"no se puede estirar un mundo: {weeks} > {world.weeks}")
    return dataclasses.replace(
        world,
        weeks=weeks,
        demand=world.demand[:, :weeks].copy(),
        real_lead_weeks=world.real_lead_weeks[:, :weeks].copy(),
    )


def _corre_carriles(world: World, seed: int, tmp: Path) -> dict[str, list[WeekOutcome]]:
    congelado: object | None = None

    def piso(w, outs, _week):
        nonlocal congelado
        if congelado is not None:
            return congelado, False
        if len(outs) < WARMUP_WEEKS:
            return EMPTY_POLICY, False
        congelado = naive_policy(w, outs)
        return congelado, False

    def plan(w, outs, week):
        if len(outs) < WARMUP_WEEKS:
            return EMPTY_POLICY, False
        return _politica_de_kern(
            w, outs, seed=seed, out_dir=tmp / f"w{week:03d}", overrides={}
        ), False

    return {
        "piso": simulate(world, piso, replan_every=REPLAN)[0],
        "kern": simulate(world, plan, replan_every=REPLAN)[0],
        "techo": simulate_oracle(world),
    }


def _borde(world: World, outs: list[WeekOutcome]) -> dict:
    """Lo pagado y NO vendido al cerrar la ventana: estante MAS canieria.

    El estante solo no alcanza, y creer que si sesga el experimento entero.
    `score_run` cobra `procurement` de toda unidad ORDENADA, arribe o no dentro de la
    ventana; las que todavia viajan al cerrarla estan pagadas, no vendidas, y no
    aparecen en `on_hand_end`. Acreditar solo el estante las deja como perdida pura --
    y el sesgo no es parejo entre carriles: un techo que sabe que el mundo se acaba en
    `W` deja de ordenar y llega al borde con la canieria VACIA, mientras que uno con
    futuro por delante ordena hasta la ultima semana y llega con ella llena. Sin este
    termino, el experimento castiga al techo por tener futuro y le atribuye la
    diferencia al horizonte.

    Se reconstruye por identidad de flujo -- `initial_state` arranca sin nada en
    transito, asi que lo que sigue viajando es lo ordenado menos lo recibido.
    """
    uc = np.array([s.unit_cost for s in world.skus], dtype=float)
    price = np.array([s.price for s in world.skus], dtype=float)

    estante = outs[-1].on_hand_end
    ordenado = np.stack([o.ordered for o in outs], axis=1).sum(axis=1)
    recibido = np.stack([o.received for o in outs], axis=1).sum(axis=1)
    canieria = ordenado - recibido
    if np.any(canieria < -1e-9):
        raise AssertionError(
            f"canieria negativa: se recibio mas de lo ordenado {canieria!r}; "
            "la identidad de flujo no se cumple y el credito de borde seria falso"
        )
    pagado_no_vendido = estante + canieria

    return {
        "unidades": float(pagado_no_vendido.sum()),
        "unidades_estante": float(estante.sum()),
        "unidades_canieria": float(canieria.sum()),
        "a_costo": float((pagado_no_vendido * uc).sum()),
        "a_precio": float((pagado_no_vendido * price).sum()),
        "estante_a_costo": float((estante * uc).sum()),
        "canieria_a_costo": float((canieria * uc).sum()),
    }


def _un_mundo(seed: int, *, ventana: int, colas=COLAS) -> dict:
    """Los tres carriles sobre el mismo mundo con cada fecha de fin."""
    largo = generate_world(
        seed=seed, n_skus=N_SKUS, n_suppliers=N_PROVEEDORES, weeks=ventana + max(colas)
    )
    fila: dict = {"seed": seed, "por_cola": {}}
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        for k in colas:
            mundo = truncar(largo, ventana + k)
            outs = _corre_carriles(mundo, seed, tmp / f"k{k}")
            fila["por_cola"][str(k)] = {
                c: {"costo": score_run(mundo, outs[c][:ventana]).total_cost}
                | _borde(mundo, outs[c][:ventana])
                for c in CARRILES
            }
            print(
                f"  semilla {seed} cola K={k:>2}: "
                + "  ".join(
                    f"{c} {fila['por_cola'][str(k)][c]['costo']:>10,.0f}"
                    f" (estante {fila['por_cola'][str(k)][c]['estante_a_costo']:>7,.0f}"
                    f" + canieria {fila['por_cola'][str(k)][c]['canieria_a_costo']:>7,.0f})"
                    for c in CARRILES
                ),
                flush=True,
            )
    return fila


def verificar_control(fila: dict, colas=COLAS) -> None:
    """Piso y Kern son ciegos al horizonte: sus costos sobre `[0,W)` NO pueden moverse.

    No es una comprobacion de estilo. Si se movieran, la fecha de fin les habria llegado
    por algun camino -- la lente, el estado congelado, el `rng` -- y entonces la
    diferencia que este script atribuye al techo seria de cualquier otra cosa. Se planta
    antes de publicar el numero, no despues.
    """
    ref = fila["por_cola"][str(colas[0])]
    for k in colas[1:]:
        otro = fila["por_cola"][str(k)]
        for c in ("piso", "kern"):
            if ref[c]["costo"] != otro[c]["costo"]:
                raise AssertionError(
                    f"semilla {fila['seed']}: el carril {c} es ciego al horizonte y aun "
                    f"asi cambio de costo al mover la fecha de fin "
                    f"(K={colas[0]}: {ref[c]['costo']:.6f} vs K={k}: {otro[c]['costo']:.6f}). "
                    "El experimento NO esta aislando el horizonte."
                )


def captura(cola: dict, *, credito: str, s: float) -> float | None:
    """Captura de Kern sobre la ventana, acreditando el borde a la tasa `s`.

    `credito` elige la valuacion: `a_costo` (cota INFERIOR del credito -- devuelve lo
    pagado) o `a_precio` (cota SUPERIOR -- regala el ingreso sin el costo de servirlo).
    `s=0` es la convencion publicada: lo que quedo pagado y sin vender se pierde entero.

    Devuelve `None` cuando el span se degenera, NUNCA un `nan`. Un `nan` promediado con
    `statistics.fmean` contamina la media en silencio y sale publicado como un numero;
    `None` obliga al llamador a contar cuantos mundos no se pudieron medir y a decirlo.
    Y la degeneracion no es rara ni un detalle: con `s=1` a PRECIO, un techo que sabe la
    fecha de fin llega al borde sin nada pagado y sin vender, asi que no recibe credito
    alguno mientras el piso cobra el suyo al precio de venta -- y la vara se da vuelta
    (el piso pasa a costar MENOS que el techo). Esa es la razon por la que la cota
    superior no se puede usar sola.
    """
    aj = lambda c: cola[c]["costo"] - s * cola[c][credito]  # noqa: E731
    piso, techo = aj("piso"), aj("techo")
    span = piso - techo
    if span <= 0:
        return None
    return (piso - aj("kern")) / span


def medir(semillas=SEMILLAS, *, ventana=VENTANA, colas=COLAS) -> dict:
    filas = []
    for seed in semillas:
        t0 = time.time()
        fila = _un_mundo(seed, ventana=ventana, colas=colas)
        verificar_control(fila, colas)
        print(f"  semilla {seed}: control OK ({time.time() - t0:.0f}s)", flush=True)
        filas.append(fila)

    resumen: dict = {}
    for k in colas:
        clave = str(k)
        entrada: dict = {}
        for credito in ("a_costo", "a_precio"):
            entrada[credito] = {}
            for s in (0.0, 1.0):
                vals = [captura(f["por_cola"][clave], credito=credito, s=s) for f in filas]
                medibles = [v for v in vals if v is not None]
                entrada[credito][f"{s:.2f}"] = (
                    statistics.fmean(medibles) if medibles else None
                )
                entrada[credito][f"{s:.2f}_mundos_medibles"] = len(medibles)
        entrada["terminal_techo_a_costo"] = statistics.fmean(
            f["por_cola"][clave]["techo"]["a_costo"] for f in filas
        )
        entrada["costo_techo"] = statistics.fmean(
            f["por_cola"][clave]["techo"]["costo"] for f in filas
        )
        resumen[clave] = entrada

    return {
        "parametros": {
            "semillas": list(semillas),
            "ventana_puntuada": ventana,
            "colas": list(colas),
            "n_skus": N_SKUS,
            "n_proveedores": N_PROVEEDORES,
            "replan": REPLAN,
            "nivel": 0,
        },
        "rescate_sobre_lo_publicado": barrido_de_rescate(json.loads(BASE_RESIDUO.read_text())),
        "por_semilla": filas,
        "resumen": resumen,
    }


def frase(datos: dict) -> str:
    """La frase citable, emitida por el script para que no se escriba a mano.

    Se apoya en la valuacion a COSTO, que es la unica definida en todos los mundos: la
    cota superior (a precio) se degenera justo donde mas se la necesitaria -- con la
    fecha de fin conocida, donde el techo llega al borde sin nada que acreditar.
    """
    pub = datos["rescate_sobre_lo_publicado"]["kern"]
    r = datos["resumen"]
    colas = datos["parametros"]["colas"]
    corta, larga = str(colas[0]), str(colas[-1])

    crudo = r[larga]["a_costo"]["0.00"] - r[corta]["a_costo"]["0.00"]
    neto = r[larga]["a_costo"]["1.00"] - r[corta]["a_costo"]["1.00"]
    residuo = pub["0.00"]
    degenerados = sum(
        1
        for k in colas
        if r[str(k)]["a_precio"]["1.00_mundos_medibles"] < len(datos["parametros"]["semillas"])
    )

    return (
        f"Con el borde acreditado, darle al techo {colas[-1]} semanas de futuro en vez de "
        f"la fecha de fin mueve la captura de Kern {neto:+.4f} -- el {abs(neto) / abs(residuo):.1%} "
        f"del residuo de {residuo:+.4f} que se queria explicar. Sin acreditarlo el mismo "
        f"eje se mueve {crudo:+.4f}, o sea que ese efecto aparente es la convencion de "
        f"puntuacion y no el horizonte. El efecto de fin de horizonte NO explica el "
        f"residuo: la vara no se lo debe a Kern."
    ) + (
        f" (La cota superior del credito se degenera en {degenerados} de "
        f"{len(colas)} fechas de fin y por eso no sostiene sola ninguna conclusion.)"
        if degenerados
        else ""
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--semillas", type=int, nargs="+", default=list(SEMILLAS))
    ap.add_argument("--ventana", type=int, default=VENTANA)
    ap.add_argument("--colas", type=int, nargs="+", default=list(COLAS))
    ap.add_argument("--salida", type=Path, default=SALIDA)
    args = ap.parse_args()

    datos = medir(tuple(args.semillas), ventana=args.ventana, colas=tuple(args.colas))
    args.salida.parent.mkdir(parents=True, exist_ok=True)
    args.salida.write_text(json.dumps(datos, indent=2, sort_keys=True) + "\n")

    print("\n== CAMINO 1: rescate del stock terminal sobre las corridas publicadas ==")
    pub = datos["rescate_sobre_lo_publicado"]
    tasas = sorted(pub["kern"])
    print(f"{'carril':20}" + "".join(f"{'s=' + t:>10}" for t in tasas))
    for c, v in pub.items():
        print(f"{c:20}" + "".join(f"{v[t]:>10.4f}" for t in tasas))

    print("\n== CAMINO 2: la misma demanda, otra fecha de fin ==")
    print(f"ventana puntuada: [0,{datos['parametros']['ventana_puntuada']})")
    n = len(datos["parametros"]["semillas"])
    print(f"{'cola K':>8}{'techo':>12}{'borde techo':>13}"
          f"{'s=0 costo':>12}{'s=1 costo':>12}{'s=1 precio':>12}{'medibles':>10}")
    for k in datos["parametros"]["colas"]:
        r = datos["resumen"][str(k)]
        precio = r["a_precio"]["1.00"]
        medibles = r["a_precio"]["1.00_mundos_medibles"]
        txt = f"{precio:>12.4f}" if precio is not None else f"{'-':>12}"
        print(f"{k:>8}{r['costo_techo']:>12,.0f}{r['terminal_techo_a_costo']:>13,.0f}"
              f"{r['a_costo']['0.00']:>12.4f}{r['a_costo']['1.00']:>12.4f}{txt}"
              f"{medibles:>7}/{n}")

    print(f"\n{frase(datos)}")
    print(f"\nlinea base: {args.salida}")


if __name__ == "__main__":
    main()
