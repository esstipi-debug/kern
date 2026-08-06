"""Barrido de mundos sobre ``simworld``: convierte la anecdota en distribucion.

``examples/run_world_sim.py`` ya tiene el entorno de simulacion completo -- mundo
con semilla, piso naive congelado, techo de oraculo que ve el futuro, cuatro
niveles de suciedad acumulativa en los datos que Kern ve, y la captura
normalizada entre piso y techo. Lo que le falta no es fidelidad de modelo: es
muestra. Corre un mundo, y los tests fijan cinco semillas concretas.

Con un mundo se puede decir "en la semilla 4 Kern capturo el 58%", que es una
propiedad de la semilla 4. Con doscientos se puede decir "captura 58% +/- 9%, y
su peor mundo fue 31%", que es una propiedad de Kern y es lo que un comprador
compra: el peor caso, no la media.

    python scripts/verify_fidelidad.py --mundos 20
    python scripts/verify_fidelidad.py --mundos 200 --n-skus 40 --semanas 52
    python scripts/verify_fidelidad.py --mundos 30 --niveles 0,3

Sale con codigo 1 si ensuciar los datos MEJORA la captura en algun carril: eso
no es un resultado bueno, es el sintoma de que el nivel de suciedad esta
entrando en la decision por una via que nadie diseno.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from examples.run_world_sim import run_grid
from src.verify.barrido import agregar, degrada_con_la_suciedad


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--mundos", type=int, default=20,
                   help="cuantas semillas barrer (por defecto 20)")
    p.add_argument("--semilla-inicial", type=int, default=1000)
    p.add_argument("--n-skus", type=int, default=8)
    p.add_argument("--n-proveedores", type=int, default=2)
    p.add_argument("--semanas", type=int, default=24)
    p.add_argument("--replan-cada", type=int, default=4)
    p.add_argument("--niveles", default="0,1,2,3",
                   help="niveles de suciedad, separados por coma")
    p.add_argument("--out", default="deliverables/fidelidad")
    args = p.parse_args()

    niveles = tuple(int(n) for n in args.niveles.split(","))
    semillas = tuple(range(args.semilla_inicial, args.semilla_inicial + args.mundos))

    print(f"Barriendo {len(semillas)} mundos · {args.n_skus} SKU · "
          f"{args.semanas} semanas · niveles {niveles}")
    print("Cada mundo corre el piso, el oraculo y Kern en cada nivel. "
          "Es lento a proposito: el oraculo optimiza contra el futuro.\n")

    reportes = []
    t0 = time.perf_counter()
    for i, semilla in enumerate(semillas, 1):
        t = time.perf_counter()
        rep = run_grid(
            seed=semilla, n_skus=args.n_skus, n_suppliers=args.n_proveedores,
            weeks=args.semanas, levels=niveles, replan_every=args.replan_cada,
            out_dir=Path(args.out) / f"seed{semilla}",
        )
        reportes.append(rep)
        print(f"  mundo {i:>3}/{len(semillas)}  semilla={semilla}  "
              f"{time.perf_counter() - t:6.1f} s")
    total = time.perf_counter() - t0

    dists = agregar(reportes)
    carriles = sorted({d.carril for d in dists})

    print(f"\n{'=' * 78}")
    print(f"DISTRIBUCION DE CAPTURA · {len(semillas)} mundos · {total:.0f} s")
    print(f"{'=' * 78}")
    print(f"{'carril':<10}{'niv':>4}{'mundos':>8}{'medibles':>10}"
          f"{'media':>9}{'sd':>8}{'p10':>8}{'peor':>8}")
    for d in dists:
        if d.media is None:
            print(f"{d.carril:<10}{d.nivel:>4}{d.n_mundos:>8}{d.n_medibles:>10}"
                  f"{'--':>9}{'--':>8}{'--':>8}{'--':>8}")
            continue
        sd = f"{d.desviacion * 100:.1f}%" if d.desviacion is not None else "--"
        print(f"{d.carril:<10}{d.nivel:>4}{d.n_mundos:>8}{d.n_medibles:>10}"
              f"{d.media * 100:>8.1f}%{sd:>8}"
              f"{d.p10 * 100:>7.1f}%{d.peor * 100:>7.1f}%")

    print("\nLo que se puede afirmar:")
    for d in dists:
        print(f"  · {d.frase()}")

    anomalias: list[str] = []
    for carril in carriles:
        anomalias.extend(degrada_con_la_suciedad(dists, carril))
    if anomalias:
        print("\nANOMALIAS -- ensuciar los datos mejoro el resultado:")
        for a in anomalias:
            print(f"  ! {a}")
        print("\nEso no es una buena noticia: el nivel de suciedad esta entrando")
        print("en la decision por una via no disenada. Investigar antes de citar")
        print("ningun numero de esta tabla.")
        return 1

    print("\nSin anomalias: mas suciedad nunca mejoro la captura.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
