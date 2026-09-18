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
from src.simworld.shocks import parse_shock
from src.verify.barrido import (
    agregar,
    comparar_con_linea_base,
    degrada_con_la_suciedad,
    guardar_linea_base,
)


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
    p.add_argument("--grabar-linea-base", metavar="RUTA", default=None,
                   help="graba esta corrida como referencia futura")
    p.add_argument("--contra-linea-base", metavar="RUTA", default=None,
                   help="falla si la captura cayo respecto de esa referencia")
    p.add_argument("--shock", action="append", default=[],
                   metavar="KIND:TARGET:START:WEEKS[:MAG]",
                   help="perturba cada mundo despues del sorteo (repetible). Con esto, "
                        "lo que se agrega es la CAPTURA BAJO DISRUPCION: cada mundo "
                        "corre dos veces (base y perturbado), asi que tarda el doble")
    args = p.parse_args()

    shocks = tuple(parse_shock(t) for t in args.shock)
    # La clave que se agrega depende de si hubo shock. No se mezclan nunca: una media
    # que promediara capturas limpias y bajo disrupcion no seria de ninguna de las dos.
    metrica = "capture_under_shock" if shocks else "capture"

    niveles = tuple(int(n) for n in args.niveles.split(","))
    semillas = tuple(range(args.semilla_inicial, args.semilla_inicial + args.mundos))

    print(f"Barriendo {len(semillas)} mundos · {args.n_skus} SKU · "
          f"{args.semanas} semanas · niveles {niveles}")
    if shocks:
        print("Shocks: " + ", ".join(s.as_cli() for s in shocks))
        print("Se agrega `capture_under_shock`: los tres corredores corren sobre el "
              "mundo perturbado, y cada mundo corre ademas limpio para poder atribuir.")
    print("Cada mundo corre el piso, el oraculo y Kern en cada nivel. "
          "Es lento a proposito: el oraculo optimiza contra el futuro.\n")

    reportes = []
    t0 = time.perf_counter()
    for i, semilla in enumerate(semillas, 1):
        t = time.perf_counter()
        rep = run_grid(
            seed=semilla, n_skus=args.n_skus, n_suppliers=args.n_proveedores,
            weeks=args.semanas, levels=niveles, replan_every=args.replan_cada,
            out_dir=Path(args.out) / f"seed{semilla}", shocks=shocks,
        )
        reportes.append(rep)
        print(f"  mundo {i:>3}/{len(semillas)}  semilla={semilla}  "
              f"{time.perf_counter() - t:6.1f} s")
    total = time.perf_counter() - t0

    dists = agregar(reportes, metrica=metrica)
    carriles = sorted({d.carril for d in dists})

    titulo = "DISTRIBUCION DE CAPTURA BAJO DISRUPCION" if shocks else "DISTRIBUCION DE CAPTURA"
    print(f"\n{'=' * 78}")
    print(f"{titulo} · {len(semillas)} mundos · {total:.0f} s")
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

    if shocks:
        # Bajo disrupcion, una captura negativa NO es una anomalia que haya que
        # investigar como un bug: es la pregunta que este barrido existe para
        # contestar -- "¿Kern le gana a un punto de reorden fijo cuando el mundo se
        # rompe?" -- y la investigacion dice que ese es justo el resultado que hay
        # que querer ver antes que un cliente lo vea. Se nombra aca para que nadie
        # lo lea como un fallo del arnes.
        peores = [d for d in dists if d.media is not None and d.media < 0]
        if peores:
            print("\nCAPTURA NEGATIVA BAJO DISRUPCION (no es un bug del arnes):")
            for d in peores:
                print(f"  · {d.carril} nivel {d.nivel}: media {d.media * 100:.1f}% "
                      f"-- Kern decide PEOR que el punto de reorden fijo con este shock")

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

    contexto = {
        "mundos": len(semillas), "semilla_inicial": args.semilla_inicial,
        "n_skus": args.n_skus, "n_proveedores": args.n_proveedores,
        "semanas": args.semanas, "replan_cada": args.replan_cada,
        "niveles": list(niveles),
        # Sin esto, una linea base grabada con shock y otra sin el se comparan entre si
        # y la diferencia se lee como una regresion del producto. Van los shocks EN EL
        # FORMATO que `parse_shock` vuelve a leer, y la metrica que se agrego.
        "shocks": [x.as_cli() for x in shocks],
        "metrica": metrica,
    }
    if args.grabar_linea_base:
        ruta = guardar_linea_base(dists, args.grabar_linea_base, contexto=contexto)
        print(f"\nLinea base grabada en {ruta}")

    if args.contra_linea_base:
        regresiones, avisos = comparar_con_linea_base(
            dists, args.contra_linea_base, contexto=contexto)
        for a in avisos:
            print(f"  aviso: {a}")
        if regresiones:
            print("\nREGRESION DE DECISION contra la linea base:")
            for r in regresiones:
                print(f"  ! {r}")
            print("\nKern decide peor que cuando se grabo la referencia. No es un "
                  "fallo de tests: es que el producto empeoro.")
            return 1
        print("\nSin regresion contra la linea base.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
