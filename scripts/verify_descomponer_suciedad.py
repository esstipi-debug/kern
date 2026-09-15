"""Cual de los inyectores de un nivel se come la ventaja de Kern.

El barrido de 25 mundos midio que la captura cae de 10,6% en el nivel 2 a 0,6%
en el nivel 3 -- o sea, a cero: con datos muy sucios Kern deja de batir a un
punto de reorden fijo. El nivel 3 tiene cuatro inyectores y el numero agregado
no dice cual de los cuatro lo hace.

Esto corre, sobre los MISMOS mundos:

    control        el nivel completo
    sin X          el nivel completo menos el inyector X, para cada X

Si quitar X devuelve la captura, X es el culpable. Si quitar cualquiera la
devuelve por igual, el dano es del conjunto y no de una pieza.

    python scripts/verify_descomponer_suciedad.py --nivel 3 --mundos 10

> **Leer una sola corrida como aislamiento exacto es un error.** El ``rng`` de
> la lente es uno solo, asi que omitir un inyector cambia el sorteo de los
> siguientes: una corrida con ``omit`` es otra realizacion de la misma
> naturaleza, no "la misma menos una pieza". Por eso esto barre varios mundos
> y compara medias -- la diferencia de stream es ruido, y el ruido se promedia.
> Con pocos mundos la tabla no concluye nada, y la columna de error estandar
> esta puesta para que eso se vea.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from examples.run_world_sim import run_grid
from src.simworld.lens import nombres_de_inyectores
from src.verify.barrido import agregar

CONTROL = "<nivel completo>"


def barrer(*, semillas, nivel, omit, n_skus, n_proveedores, semanas,
           replan_cada, out) -> tuple[float | None, float | None, int, int]:
    """Media y error estandar de la captura sobre esas semillas."""
    reportes = []
    for semilla in semillas:
        reportes.append(run_grid(
            seed=semilla, n_skus=n_skus, n_suppliers=n_proveedores,
            weeks=semanas, levels=(nivel,), replan_every=replan_cada,
            out_dir=out / f"seed{semilla}", omit=omit,
        ))
    dists = agregar(reportes)
    if not dists:
        return None, None, len(semillas), 0
    d = dists[0]
    if d.media is None:
        return None, None, d.n_mundos, 0
    se = (d.desviacion / (d.n_medibles ** 0.5)) if d.desviacion is not None else None
    return d.media, se, d.n_mundos, d.n_medibles


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--nivel", type=int, default=3)
    p.add_argument("--mundos", type=int, default=10)
    p.add_argument("--semilla-inicial", type=int, default=2000)
    p.add_argument("--n-skus", type=int, default=16)
    p.add_argument("--n-proveedores", type=int, default=2)
    p.add_argument("--semanas", type=int, default=52)
    p.add_argument("--replan-cada", type=int, default=4)
    p.add_argument("--out", default="deliverables/descomposicion")
    p.add_argument("--inyectores", default=None, metavar="A,B",
                   help="restringe el barrido a estos inyectores (por defecto, "
                        "todos los del nivel). Sirve para gastar la muestra donde "
                        "hace falta: una primera pasada sobre los cuatro senala un "
                        "sospechoso, y confirmarlo con el doble de mundos cuesta la "
                        "mitad si solo se corren control y sospechoso")
    args = p.parse_args()

    inyectores = nombres_de_inyectores(args.nivel)
    if args.inyectores:
        pedidos = tuple(n.strip() for n in args.inyectores.split(","))
        desconocidos = set(pedidos) - set(inyectores)
        if desconocidos:
            p.error(f"inyectores desconocidos en el nivel {args.nivel}: "
                    f"{sorted(desconocidos)}; conocidos: {list(inyectores)}")
        inyectores = pedidos
    semillas = tuple(range(args.semilla_inicial, args.semilla_inicial + args.mundos))
    raiz = Path(args.out)

    configs: list[tuple[str, tuple[str, ...]]] = [(CONTROL, ())]
    configs += [(f"sin {n}", (n,)) for n in inyectores]

    print(f"Descomponiendo el nivel {args.nivel} ({len(inyectores)} inyectores) "
          f"sobre {len(semillas)} mundos · {args.n_skus} SKU · {args.semanas} semanas")
    print(f"{len(configs)} configuraciones × {len(semillas)} mundos "
          f"= {len(configs) * len(semillas)} corridas\n")

    filas = []
    t0 = time.perf_counter()
    for etiqueta, omit in configs:
        t = time.perf_counter()
        media, se, n, medibles = barrer(
            semillas=semillas, nivel=args.nivel, omit=omit,
            n_skus=args.n_skus, n_proveedores=args.n_proveedores,
            semanas=args.semanas, replan_cada=args.replan_cada,
            out=raiz / etiqueta.replace(" ", "_").replace("<", "").replace(">", ""),
        )
        filas.append((etiqueta, media, se, n, medibles))
        visto = "--" if media is None else f"{media * 100:.1f}%"
        print(f"  {etiqueta:<34} {visto:>8}   ({time.perf_counter() - t:.0f} s)")

    total = time.perf_counter() - t0
    control = next(f for f in filas if f[0] == CONTROL)

    print(f"\n{'=' * 78}")
    print(f"NIVEL {args.nivel} DESCOMPUESTO · {len(semillas)} mundos · {total:.0f} s")
    print(f"{'=' * 78}")
    print(f"{'configuracion':<34}{'captura':>10}{'err.est':>10}"
          f"{'delta':>10}{'medibles':>10}")
    for etiqueta, media, se, n, medibles in filas:
        if media is None:
            print(f"{etiqueta:<34}{'--':>10}{'--':>10}{'--':>10}{medibles:>7}/{n}")
            continue
        if etiqueta == CONTROL or control[1] is None:
            delta = "--"
        else:
            delta = f"{(media - control[1]) * 100:+.1f}%"
        se_txt = "--" if se is None else f"{se * 100:.1f}%"
        print(f"{etiqueta:<34}{media * 100:>9.1f}%{se_txt:>10}{delta:>10}"
              f"{medibles:>7}/{n}")

    if control[1] is None:
        print("\nEl control no fue medible: sin referencia no hay descomposicion.")
        return 1

    # Un culpable se declara solo si su delta supera dos errores estandar de la
    # diferencia. Con pocos mundos eso casi nunca ocurre, y es lo correcto: la
    # alternativa es publicar un culpable elegido por ruido.
    culpables = []
    for etiqueta, media, se, _n, _m in filas:
        if etiqueta == CONTROL or media is None or se is None or control[2] is None:
            continue
        margen = 2.0 * (se ** 2 + control[2] ** 2) ** 0.5
        if media - control[1] > margen:
            culpables.append((etiqueta, media - control[1], margen))

    print()
    if culpables:
        print("Quitar estos inyectores RECUPERA captura por encima del ruido:")
        for etiqueta, delta, margen in sorted(culpables, key=lambda c: -c[1]):
            print(f"  · {etiqueta}: {delta * 100:+.1f}% (margen {margen * 100:.1f}%)")
        print("\nAhi esta el dano. Es un bug de robustez de ingesta, no una tarea "
              "de producto.")
    else:
        print("Ningun inyector solo explica la caida por encima del ruido de "
              f"{len(semillas)} mundos.")
        print("Dos lecturas posibles, y hay que distinguirlas antes de actuar:")
        print("  a) el dano es del conjunto, no de una pieza -> arreglar uno no basta")
        print("  b) la muestra es corta -> subir --mundos antes de concluir")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
