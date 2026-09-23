"""Cuanto vale que Kern deje de ajustar la demanda contra ventas CENSURADAS.

`KERN_CAPTURA_BAJO_DISRUPCION.md` cerro el colapso de los niveles sucios y dejo escrito
lo unico que quedaba de aquel hallazgo y NO era un defecto de lectura:

    `lens.history_frame` exporta `out.sold`, no la demanda. Politica mala -> quiebre ->
    el historico se CENSURA -> la politica siguiente es mas chica. El piso es inmune por
    ser congelado (`naive_policy`, "sin revisar nunca"): el bucle solo muerde al carril
    que replanifica.

Ese parrafo describe un mecanismo y termina diciendo que **no esta medido cuanto vale**.
Esto lo mide, antes de construir nada -- la misma disciplina que
`scripts/horizonte_por_sku.py`: si la palanca no paga, no se agrega una columna al intake
ni una regla al motor.

QUE TAN GRANDE ES EL AGUJERO
    Sobre los 25 mundos, el archivo con el que Kern replanifica NO DECLARA el 23,2% de la
    demanda, y el 34,8% de sus filas salen de una semana que cerro el estante en cero. No
    es un detalle de borde, es casi un cuarto del dato. Y el sesgo tiene una sola
    direccion -- la censura solo puede restar --, asi que no se promedia con nada.

LO QUE SE MIDE, EN UNA LINEA
    Una fila del historico es una (SKU, semana). Una fila quedo CENSURADA cuando el
    estante cerro esa semana en cero: lo que ahi dice `quantity` es lo que se alcanzo a
    vender, no lo que se queria comprar. Los carriles cambian que hace Kern con esas
    filas -- y nada mas: no se toca el motor, ni el piso, ni el techo, ni el marcador,
    que sigue puntuando contra la demanda VERDADERA del mundo aunque Kern haya leido otra
    cosa.

LOS CUATRO CARRILES
    - `kern` (CONTROL). El archivo tal como sale hoy. Tiene que reproducir el carril
      `kern` de `horizonte_por_sku.json` al ultimo digito: es la prueba de que esto es el
      mismo experimento y lo unico que se movio es la censura.
    - `censura_omitida` (DESPLEGABLE). La fila censurada se BORRA. Es la declaracion de
      ausencia que esta rama aplica en todas partes: no se sabe cuanta demanda hubo, asi
      que no se inventa un numero. Cuesta periodos, y `MIN_PERIODS_FOR_POLICY` es 6.
    - `censura_corregida` (DESPLEGABLE). La fila censurada se LEVANTA a
      `max(vendido, media de las filas NO censuradas de ese SKU)`. Es el "unconstraining"
      mas barato que existe y el unico que se puede defender sin un supuesto de
      distribucion: de una semana que se quedo sin stock se sabe que la demanda fue AL
      MENOS lo vendido, y una semana que se agoto no tiene por que haber sido mas floja
      que una tipica. Un SKU sin ninguna fila limpia se deja INTACTO -- no hay con que
      estimar, y esa ausencia tambien se declara.
    - `sin_censura` (DIAGNOSTICO). La lente exporta `out.demand`. Nadie tiene esto: es la
      cota superior de lo que la idea puede pagar. La brecha contra los desplegables ES
      el valor de la informacion que no se tiene.

DE DONDE SALE LA MARCA, Y POR QUE NO ES HACER TRAMPA
    Los dos carriles desplegables marcan la fila con `on_hand_end == 0` -- el estante
    cerro vacio --, que es lo que un ERP SI tiene: `jobs.intake` ya conoce el vocabulario
    de esa columna (`stock_position` lee `on_hand`, aunque hoy solo la ULTIMA fila por
    SKU). Lo que NO se usa en ningun carril desplegable es `out.lost`: la venta perdida
    es exactamente lo que el cliente no puede ver, y marcaria con la respuesta. Un
    estante en cero es un sintoma observable; una unidad perdida es el diagnostico.
    Confundirlos volveria desplegable a un carril que no lo es.

    El precio de esa honestidad se paga y se mide: `on_hand_end == 0` marca tambien la
    semana que cerro justo en cero habiendo servido toda la demanda. Esa fila no estaba
    censurada y se la trata como si lo estuviera. Es ruido en la direccion correcta --
    sobre-marcar hace al carril mas conservador, no mas optimista.

    PYTHONPATH=. python scripts/ventas_censuradas.py
    PYTHONPATH=. python scripts/ventas_censuradas.py --semillas 1000

Tarda ~41 s por semilla (el piso, el oraculo y CUATRO carriles de Kern completos), o sea
~17 minutos los 25 mundos. No es un test -- es un script que se corre a mano y cuya salida
se congela en `documentation/lineas_base/ventas_censuradas.json`.

Y se corre con 25, no con cinco: sobre las cinco semillas canonicas esta palanca sale
+0,0930 en 4/5 y sobre las 25 sale +0,0267 en 13/25 -- 3,5 veces mas chica. Las canonicas
contienen TRES de los ocho mundos donde Kern va peor, que es justo la condicion que infla
esta medicion: H1 del documento explica por que (la palanca paga donde la politica ya esta
mal) y H3 lo mide. `--semillas` acepta menos para una sonda, pero el titular se publica con
los 25.
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
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from examples.run_world_sim import (  # noqa: E402
    EMPTY_POLICY,
    WARMUP_WEEKS,
    simulate,
    simulate_oracle,
)
from scripts.por_que_bajo_el_piso import (  # noqa: E402
    NIVEL,
    _componentes,
    _politica_desde_vista,
)
from src.simworld.lens import EPOCH, apply_dirt, history_frame  # noqa: E402
from src.simworld.runners import Policy, naive_policy  # noqa: E402
from src.simworld.scorer import score_run  # noqa: E402
from src.simworld.world import WeekOutcome, World, generate_world  # noqa: E402

#: Las cinco de `horizonte_por_sku.py` y `por_que_bajo_el_piso.py`. NO son las que publica
#: el titular: son el subconjunto contra el que se chequea el control campo por campo, y el
#: que `resumen_canonico` promedia aparte para que esta palanca se pueda comparar con
#: aquellas sobre los MISMOS mundos.
SEMILLAS = (1000, 1001, 1002, 1003, 1004)

#: Las 25 de `captura_limpia.json`, que son las que publica el titular. Ver el cierre del
#: encabezado: con cinco esta palanca se lee 3,5 veces mas grande de lo que es.
SEMILLAS_PUBLICADAS = tuple(range(1000, 1025))

N_SKUS, N_PROVEEDORES, SEMANAS, REPLAN = 8, 2, 24, 4
SALIDA = ROOT / "documentation" / "lineas_base" / "ventas_censuradas.json"


def _marcas(world: World, outs: list[WeekOutcome]) -> pd.DataFrame:
    """Una fila por (SKU, semana) con lo que cada carril necesita saber de ella.

    ``agotado`` es la marca OBSERVABLE (el estante cerro en cero) y es la unica que
    pueden mirar los carriles desplegables. ``verdad`` es la demanda del mundo y solo la
    mira el carril de diagnostico. Van juntas en un solo frame porque el merge contra el
    historico tiene que ser el mismo para los tres -- si cada carril armara su propia
    clave, una diferencia de formato de fecha se leeria como una diferencia de palanca.
    """
    filas = []
    for out in outs:
        fecha = (EPOCH + pd.Timedelta(weeks=out.week)).strftime("%Y-%m-%d")
        for i, sku in enumerate(world.skus):
            filas.append(
                {
                    "date": fecha,
                    "product_id": sku.sku,
                    "agotado": bool(out.on_hand_end[i] <= 0.0),
                    "verdad": float(out.demand[i]),
                }
            )
    return pd.DataFrame(filas)


def _sin_censura(df: pd.DataFrame, marcas: pd.DataFrame) -> pd.DataFrame:
    """DIAGNOSTICO: la cantidad es la demanda verdadera, censurada o no."""
    out = df.merge(marcas, on=["date", "product_id"], how="left", validate="one_to_one")
    out["quantity"] = out["verdad"]
    return out.drop(columns=["agotado", "verdad"])


def _censura_omitida(df: pd.DataFrame, marcas: pd.DataFrame) -> pd.DataFrame:
    """DESPLEGABLE: la fila censurada se borra. No se sabe, no se inventa."""
    out = df.merge(marcas, on=["date", "product_id"], how="left", validate="one_to_one")
    out = out[~out["agotado"].fillna(False)]
    return out.drop(columns=["agotado", "verdad"]).reset_index(drop=True)


def _censura_corregida(df: pd.DataFrame, marcas: pd.DataFrame) -> pd.DataFrame:
    """DESPLEGABLE: la fila censurada sube a ``max(vendido, media de las limpias)``.

    ``max`` y no la media a secas porque lo vendido en una semana que se agoto es una
    COTA INFERIOR de la demanda de esa semana: bajarla a la media perderia informacion
    que si se tiene. Un SKU sin ninguna fila limpia queda intacto -- no hay media que
    citar, y rellenar con un promedio global mezclaria SKUs de escalas distintas.
    """
    out = df.merge(marcas, on=["date", "product_id"], how="left", validate="one_to_one")
    agotado = out["agotado"].fillna(False)
    limpias = out.loc[~agotado].groupby("product_id")["quantity"].mean()
    piso = out["product_id"].map(limpias)
    out.loc[agotado, "quantity"] = np.maximum(
        out.loc[agotado, "quantity"], piso[agotado].fillna(-np.inf)
    )
    return out.drop(columns=["agotado", "verdad"])


#: nombre -> (transformacion del historico, clase)
CARRILES = {
    "kern": (None, "control"),
    "censura_omitida": (_censura_omitida, "desplegable"),
    "censura_corregida": (_censura_corregida, "desplegable"),
    "sin_censura": (_sin_censura, "diagnostico"),
}


def _vista(world: World, outs: list[WeekOutcome], transformar, ruta: Path) -> Path:
    """Escribe el archivo que Kern va a leer, con la censura tratada segun el carril.

    Pasa por ``apply_dirt`` aunque el nivel sea 0 para recorrer EXACTAMENTE el mismo
    camino que ``lens.export_history`` -- a nivel 0 es la identidad, y depender de eso
    implicitamente seria una diferencia silenciosa contra el carril de control el dia que
    alguien mida esto a nivel 1.
    """
    df = history_frame(world, outs)
    if transformar is not None:
        df = transformar(df, _marcas(world, outs))
    df = apply_dirt(df, level=NIVEL, seed=0)
    ruta.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(ruta, index=False)
    return ruta


def _censura(world: World, outs: list[WeekOutcome]) -> dict:
    """Cuanto del historico que produjo este carril esta censurado.

    Es el mecanismo hecho visible: un carril que quiebra menos se lee a si mismo mejor la
    vuelta siguiente. ``unidades_ocultas`` son las que la demanda tuvo y el archivo no
    declara -- la brecha entre lo que Kern cree haber visto y lo que paso.
    """
    estante = np.stack([o.on_hand_end for o in outs], axis=1)
    perdido = np.stack([o.lost for o in outs], axis=1)
    demanda = np.stack([o.demand for o in outs], axis=1)
    return {
        "fraccion_filas_agotadas": float((estante <= 0.0).mean()),
        "unidades_ocultas": float(perdido.sum()),
        "fraccion_demanda_oculta": float(perdido.sum() / demanda.sum())
        if demanda.sum()
        else 0.0,
    }


def _un_mundo(seed: int) -> dict:
    world = generate_world(seed=seed, n_skus=N_SKUS, n_suppliers=N_PROVEEDORES,
                           weeks=SEMANAS)
    congelado: Policy | None = None

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
        "piso": _componentes(world, piso_outs) | _censura(world, piso_outs)
        | {"capture": 0.0, "clase": "vara"},
        "techo": _componentes(world, techo_outs) | _censura(world, techo_outs)
        | {"capture": 1.0, "clase": "vara"},
    }
    with tempfile.TemporaryDirectory() as tmp:
        for nombre, (transformar, clase) in CARRILES.items():
            def plan(w, outs, week, _t=transformar, _n=nombre):
                if len(outs) < WARMUP_WEEKS:
                    return EMPTY_POLICY, False
                carpeta = Path(tmp) / _n / f"w{week:03d}"
                vista = _vista(w, outs, _t, carpeta / "view.csv")
                return _politica_desde_vista(vista, out_dir=carpeta, overrides={}), False

            outs, _ = simulate(world, plan, replan_every=REPLAN)
            salida[nombre] = _componentes(world, outs) | _censura(world, outs) | {
                "capture": captura(outs), "clase": clase,
            }
            v = salida[nombre]
            print(f"  semilla {seed} {nombre:>18} [{clase:<12}]: capture "
                  f"{v['capture']:+.4f}  fill {v['fill']:.3f}"
                  f"  unidades {v['unidades_pedidas']:>8,.0f}"
                  f"  oculto {v['fraccion_demanda_oculta']:.3f}", flush=True)
    return salida


CAMPOS = ("capture", "fill", "shortage", "procurement", "holding", "fixed", "total",
          "unidades_pedidas", "stock_terminal_usd", "canieria_usd",
          "pagado_no_vendido_usd", "fraccion_filas_agotadas", "unidades_ocultas",
          "fraccion_demanda_oculta")


def resumen_de(filas: list[dict]) -> dict:
    """Promedia los carriles sobre las filas que se le den. Ver `medir` para por que
    esta separado de el."""
    carriles = ["piso", *CARRILES, "techo"]
    resumen = {
        c: {campo: statistics.fmean(f[c][campo] for f in filas) for campo in CAMPOS}
        for c in carriles
    }
    for c in CARRILES:
        resumen[c]["clase"] = CARRILES[c][1]
        resumen[c]["mundos"] = len(filas)
        resumen[c]["mejora_vs_kern"] = resumen[c]["capture"] - resumen["kern"]["capture"]
        resumen[c]["mundos_que_mejoran"] = sum(
            1 for f in filas if f[c]["capture"] > f["kern"]["capture"]
        )
    return resumen


def medir(semillas=SEMILLAS_PUBLICADAS) -> dict:
    """Corre los carriles y emite DOS agregados sobre las mismas filas.

    `resumen` promedia todas las semillas que se corrieron: es el titular, y con 25
    mundos es el unico que aguanta una afirmacion de signo.

    `resumen_canonico` promedia SOLO las cinco semillas 1000-1004 cuando estan, que son
    las que midieron `KERN_RESIDUO_DE_LA_BRECHA.md` y `KERN_HORIZONTE_POR_SKU.md`. Existe
    porque comparar esta palanca contra aquellas exige los MISMOS mundos -- un +16,2
    medido en cinco y un numero medido en veinticinco no se restan. Cuando las cinco no
    estan en la corrida, sale `None` en vez de un promedio de otra cosa.
    """
    filas = []
    for s in semillas:
        t0 = time.time()
        filas.append(_un_mundo(s))
        print(f"  semilla {s} lista ({time.time() - t0:.0f}s)", flush=True)

    canonicas = [f for f in filas if f["seed"] in SEMILLAS]
    return {
        "parametros": {
            "semillas": list(semillas), "n_skus": N_SKUS,
            "n_proveedores": N_PROVEEDORES, "semanas": SEMANAS, "replan": REPLAN,
            "nivel": NIVEL, "semillas_canonicas": list(SEMILLAS),
        },
        "por_semilla": filas,
        "resumen": resumen_de(filas),
        "resumen_canonico": (
            resumen_de(canonicas) if len(canonicas) == len(SEMILLAS) else None
        ),
    }


def frase(datos: dict) -> str:
    r = datos["resumen"]
    n = r["kern"]["mundos"]
    techo = r["sin_censura"]
    mejor = max(
        (c for c in CARRILES if CARRILES[c][1] == "desplegable"),
        key=lambda c: r[c]["mejora_vs_kern"],
    )
    return (
        f"Kern replanifica sobre un archivo al que le falta el "
        f"{r['kern']['fraccion_demanda_oculta']:.1%} de la demanda. Devolverle la "
        f"demanda verdadera mueve la captura {techo['mejora_vs_kern']:+.4f} "
        f"({techo['mundos_que_mejoran']}/{n} mundos) -- esa es la cota superior, y nadie "
        f"la tiene. El mejor carril desplegable ({mejor}) mueve "
        f"{r[mejor]['mejora_vs_kern']:+.4f} ({r[mejor]['mundos_que_mejoran']}/{n})."
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--semillas", type=int, nargs="+", default=list(SEMILLAS_PUBLICADAS))
    ap.add_argument("--json", type=Path, default=SALIDA)
    args = ap.parse_args()

    datos = medir(tuple(args.semillas))
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(datos, indent=2, sort_keys=True) + "\n")

    r = datos["resumen"]
    print(f"\n{'carril':20}{'clase':>14}{'captura':>10}{'vs kern':>10}{'mejoran':>9}"
          f"{'fill':>8}{'unidades':>10}{'oculto':>9}{'cierre':>11}")
    for c in ["piso", *CARRILES, "techo"]:
        v = r[c]
        print(f"{c:20}{v.get('clase', 'vara'):>14}{v['capture']:>+10.4f}"
              f"{v.get('mejora_vs_kern', float('nan')):>+10.4f}"
              f"{str(v.get('mundos_que_mejoran', '-')):>9}{v['fill']:>8.3f}"
              f"{v['unidades_pedidas']:>10,.0f}{v['fraccion_demanda_oculta']:>9.3f}"
              f"{v['pagado_no_vendido_usd']:>11,.0f}")
    print(f"\n{frase(datos)}\nlinea base: {args.json}")


if __name__ == "__main__":
    main()
