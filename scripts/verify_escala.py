"""Corre el pipeline de inventario en cada tamano de empresa y verifica coherencia.

Cierra el hueco entre las dos verificaciones que existian: la bateria juzga el
entregable sobre dos SKU, y la corrida de portafolio corre 34.498 sin juzgar
nada. Esto juzga a los dos tamanos y a los de en medio.

    python scripts/verify_escala.py                    # micro y pyme (rapido)
    python scripts/verify_escala.py --tramos todos     # incluye 5.000 y 50.000
    python scripts/verify_escala.py --tramos grande --semilla 12

Sale con codigo 1 si algun tramo viola un invariante o se pasa de su presupuesto
de tiempo, para que sirva de compuerta y no solo de informe.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobs import inventory_optimization
from src.verify.carteras import TRAMOS, TRAMOS_POR_NOMBRE, composicion, generar_cartera
from src.verify.coherencia import verificar

RAPIDOS = ("micro", "pyme")


def correr_tramo(nombre: str, *, semilla: int, semanas: int, presupuesto: float | None):
    tramo = TRAMOS_POR_NOMBRE[nombre]
    tope = presupuesto if presupuesto is not None else tramo.presupuesto_s

    t0 = time.perf_counter()
    demanda = generar_cartera(tramo.n_skus, semanas=semanas, semilla=semilla)
    t_datos = time.perf_counter() - t0

    t1 = time.perf_counter()
    reporte = inventory_optimization.run(demanda)
    t_job = time.perf_counter() - t1

    coh = verificar(reporte)
    total = t_datos + t_job

    print(f"\n{'=' * 78}")
    print(f"{tramo.nombre.upper()} — {tramo.n_skus:,} SKU · {tramo.descripcion}")
    print(f"{'=' * 78}")
    comp = composicion(tramo.n_skus)
    print("  composicion: " + " · ".join(f"{k}={v}" for k, v in sorted(comp.items())))
    print(f"  datos       {t_datos:7.2f} s   ({len(demanda):,} filas)")
    print(f"  pipeline    {t_job:7.2f} s")
    print(f"  total       {total:7.2f} s   / presupuesto {tope:.0f} s")
    print(f"  inversion   {reporte.final_investment:,.0f} usd · "
          f"{reporte.n_at_risk} en riesgo · {reporte.n_intermittent} intermitentes")
    print(f"  coherencia  {coh.resumen()}")

    for v in coh.violaciones[:15]:
        print(f"      ! {v.invariante} [{v.ambito}] {v.evidencia}")
    if len(coh.violaciones) > 15:
        print(f"      ... y {len(coh.violaciones) - 15} mas")

    dentro = total <= tope
    if not dentro:
        print(f"      ! presupuesto excedido: {total:.2f} s > {tope:.0f} s")
    return coh.coherente and dentro


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--tramos", default="rapidos",
                   help="'rapidos' (por defecto), 'todos', o nombres separados por coma")
    p.add_argument("--semilla", type=int, default=7)
    p.add_argument("--semanas", type=int, default=52)
    p.add_argument("--presupuesto", type=float, default=None,
                   help="anula el presupuesto de tiempo del tramo, en segundos")
    args = p.parse_args()

    if args.tramos == "rapidos":
        nombres = RAPIDOS
    elif args.tramos == "todos":
        nombres = tuple(t.nombre for t in TRAMOS)
    else:
        nombres = tuple(n.strip() for n in args.tramos.split(","))

    desconocidos = set(nombres) - set(TRAMOS_POR_NOMBRE)
    if desconocidos:
        p.error(f"tramos desconocidos: {sorted(desconocidos)}; "
                f"conocidos: {sorted(TRAMOS_POR_NOMBRE)}")

    ok = True
    for nombre in nombres:
        ok &= correr_tramo(nombre, semilla=args.semilla, semanas=args.semanas,
                           presupuesto=args.presupuesto)

    print(f"\n{'=' * 78}")
    print("TODOS LOS TRAMOS COHERENTES Y EN PRESUPUESTO" if ok
          else "HAY TRAMOS CON VIOLACIONES O FUERA DE PRESUPUESTO")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
