"""Run N4 (tension map) and N5 (settlement) side by side over one demand CSV.

ONE command, BOTH outputs plus the $ value table (spec sec 8). ASCII-only output
(Windows cp1252 console). Without --sku it runs every SKU (the full demo).

    python examples/run_hats.py --sku SKU-A
    python examples/run_hats.py --all --weights cfo=0.4,planner=0.3,comprador=0.2,comercial=0.1
    python examples/run_hats.py --all --distance-km 900    # + el sombrero transporte

El quinto sombrero (transporte) solo entra cuando el CSV trae peso por unidad
(columna weight_kg) Y se pasa --distance-km. Sin eso el consejo corre con cuatro
areas y lo dice; no se inventa un peso.
"""
from __future__ import annotations

import argparse

from jobs import hats_job
from src.hat_council import agreement_at_1, settle, tension_map, top1_by_judge, value_row
from src.hats import HAT_KEYS, headline_kpi


def _print_tension(inp, tmap) -> None:
    tag = "  tarifario (assumed)" if inp.price_breaks_assumed else ""
    if inp.freight is not None and inp.freight.rates_assumed:
        tag += "  flete (tarifario ilustrativo)"
    print(f"[{inp.sku}]{tag}")
    for k, e in tmap.ideals.items():
        kpi = headline_kpi(k)
        print(f"  {k:<10} Q={e.candidate.order_quantity:>9,.0f}  "
              f"SL={e.candidate.service_level:.1%}  {kpi}={e.kpis[kpi]:,.2f}")
    if tmap.excluded_hats:
        print(f"  FUERA DE LA MESA: {', '.join(tmap.excluded_hats)} "
              "(sin insumos; no es que no objete)")
    top3 = " | ".join(
        f"{c.hat_a} vs {c.hat_b}: {c.delta_capital_usd:+,.0f} usd" for c in tmap.clashes[:3])
    print(f"  choques top-3: {top3}")


def _print_value_table(rows, pairs) -> None:
    # The transporte column appears only if some SKU could be scored on it; a
    # column of "n/d" for every row would be noise, one of zeros would be a lie.
    with_t = any(r.c_transporte is not None for r in rows)
    t_head = f"{'C_transporte':>14}" if with_t else ""
    print(f"{'SKU':<10}{'C_baseline (a)':>16}{'C_comprador':>14}{'C_planner':>12}"
          f"{'C_cfo':>12}{'C_comercial (b)':>17}{t_head}{'C_N5 (c)':>12}"
          f"{'Delta $ = a - c':>17}")
    for r in rows:
        t_cell = ""
        if with_t:
            t_cell = f"{r.c_transporte:>14,.0f}" if r.c_transporte is not None else f"{'n/d':>14}"
        print(f"{r.sku:<10}{r.c_baseline:>16,.0f}{r.c_comprador:>14,.0f}"
              f"{r.c_planner:>12,.0f}{r.c_cfo:>12,.0f}{r.c_comercial:>17,.0f}{t_cell}"
              f"{r.c_n5:>12,.0f}{r.delta_usd:>+17,.0f}")
    t_total = ""
    if with_t:
        measured = [r.c_transporte for r in rows if r.c_transporte is not None]
        # Summed over the SKUs that HAVE the number, never over all of them: a
        # total that silently treats "n/d" as 0 understates the column.
        t_total = f"{sum(measured):>14,.0f}"
    print(f"{'TOTAL':<10}{sum(r.c_baseline for r in rows):>16,.0f}"
          f"{sum(r.c_comprador for r in rows):>14,.0f}"
          f"{sum(r.c_planner for r in rows):>12,.0f}"
          f"{sum(r.c_cfo for r in rows):>12,.0f}"
          f"{sum(r.c_comercial for r in rows):>17,.0f}{t_total}"
          f"{sum(r.c_n5 for r in rows):>12,.0f}"
          f"{sum(r.delta_usd for r in rows):>+17,.0f}")
    if with_t and any(r.c_transporte is None for r in rows):
        missing = [r.sku for r in rows if r.c_transporte is None]
        print(f"  n/d en C_transporte: {', '.join(missing)} "
              "(sin peso por unidad o sin distancia; el TOTAL los excluye)")
    hits = sum(1 for a, b in pairs
               if a.order_quantity == b.order_quantity and a.service_level == b.service_level)
    print(f"agreement@1 (top-1 N4 por juez == settlement N5): "
          f"{agreement_at_1(pairs):.0%} ({hits}/{len(pairs)})")


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Sombreros N4 (tension) y N5 (settlement) sobre la decision (Q, SL)")
    ap.add_argument("--csv", default="data/sample_demand_portfolio.csv")
    ap.add_argument("--sku", default=None, help="un SKU puntual; sin --sku corre todos (demo)")
    ap.add_argument("--all", action="store_true", help="explicito: todos los SKUs del CSV")
    ap.add_argument("--weights", default=None,
                    help="ej: cfo=0.4,planner=0.3,comprador=0.2,comercial=0.1 (default: iguales)")
    ap.add_argument("--wacc", type=float, default=0.12)
    ap.add_argument("--margin", type=float, default=0.30, dest="margin")
    ap.add_argument("--sl-target", type=float, default=0.95, dest="sl_target")
    ap.add_argument("--distance-km", type=float, default=None, dest="distance_km",
                    help="largo de la ruta; sin esto transporte queda fuera de la mesa")
    ap.add_argument("--max-transit-days", type=float, default=None, dest="max_transit_days",
                    help="ventana de entrega: descarta modos mas lentos")
    ap.add_argument("--moq", type=float, default=None,
                    help="minimo de pedido del proveedor para todos los SKUs sin columna moq")
    ap.add_argument("--order-multiple", type=float, default=None, dest="order_multiple",
                    help="multiplo de caja (ej. 30: vende de a 30, 60, 90) para los SKUs "
                         "sin columna order_multiple; la grilla solo usa lotes legales")
    args = ap.parse_args()

    params: dict = {"wacc": args.wacc, "gross_margin_rate": args.margin,
                    "sl_target": args.sl_target}
    if args.distance_km is not None:
        params["distance_km"] = args.distance_km
    if args.max_transit_days is not None:
        params["max_transit_days"] = args.max_transit_days
    if args.weights:
        params["weights"] = args.weights
    if args.moq is not None:
        params["minimum_order_quantity"] = args.moq
    if args.order_multiple is not None:
        params["order_multiple"] = args.order_multiple
    if args.sku and not args.all:
        params["sku"] = args.sku
    payload = hats_job.prepare(args.csv, params)
    inputs, weights = payload["inputs"], payload["weights"]

    print("== NIVEL 4: MAPA DE TENSION ==")
    maps = {}
    for inp in inputs:
        tmap = tension_map(inp)
        maps[inp.sku] = tmap
        _print_tension(inp, tmap)

    print("")
    print("== NIVEL 5: SETTLEMENT ==")
    settlements = {}
    for inp in inputs:
        s = settle(inp, weights)
        settlements[inp.sku] = s
        acta = "; ".join(f"{e.hat_key} cede {e.concesion:.2f}" for e in s.acta)
        print(f"[{inp.sku}] Q*={s.chosen.order_quantity:,.0f} "
              f"SL*={s.chosen.service_level:.1%} | {acta}")
    print("pesos (politica del operador, no consenso objetivo): "
          + ", ".join(f"{k}={weights[k]:.2f}" for k in HAT_KEYS if k in weights))

    print("")
    print("== VALOR ==")
    rows, pairs = [], []
    for inp in inputs:
        tmap, s = maps[inp.sku], settlements[inp.sku]
        rows.append(value_row(inp, tmap, s))
        pairs.append((top1_by_judge(inp, tmap), s.chosen))
    _print_value_table(rows, pairs)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
