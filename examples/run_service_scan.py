"""Servicio al cliente REALMENTE entregado, leido de un conector.

Corre la cadena entera: conector -> ``scan_service`` -> el job de inventario -> fill rate,
OTIF saliente (SCOR RL.1.1) y venta perdida por SKU, mas las notas de que campos el backend
del cliente NO pudo dar.

Offline, sin credenciales, contra la tienda simulada::

    python examples/run_service_scan.py --demo

Contra una cuenta Unleashed viva (solo lectura)::

    export UNLEASHED_API_ID=...     # en PowerShell: $env:UNLEASHED_API_ID = "..."
    export UNLEASHED_API_KEY=...
    python examples/run_service_scan.py --since 2026-01-01

La clave privada FIRMA cada request y no viaja en ninguna cabecera; se lee del entorno y
nunca se imprime.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

_RAIZ = Path(__file__).resolve().parents[1]
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from jobs.inventory_optimization import run as run_inventory  # noqa: E402
from src.connectors.service_scan import scan_service  # noqa: E402


def _fuente(args):
    """La tienda simulada, o un conector Unleashed armado desde el entorno."""
    if args.demo:
        from src.connectors.simulator import demo_store

        return demo_store()
    api_id = os.environ.get("UNLEASHED_API_ID", "").strip()
    api_key = os.environ.get("UNLEASHED_API_KEY", "").strip()
    if not api_id or not api_key:
        raise SystemExit(
            "Faltan UNLEASHED_API_ID / UNLEASHED_API_KEY en el entorno. "
            "Para probar sin credenciales: python examples/run_service_scan.py --demo"
        )
    from src.connectors.unleashed import UnleashedConnector, UnleashedCredentials, live_http

    creds = UnleashedCredentials(api_id=api_id, api_key=api_key)
    return UnleashedConnector(live_http(creds), creds)


def _pct(valor) -> str:
    """Un porcentaje, o 'no medido'. Nunca un 0% inventado sobre una ausencia."""
    return "no medido" if valor is None else f"{valor * 100:.1f}%"


def _plata(valor) -> str:
    return "no medida" if valor is None else f"${valor:,.0f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--demo", action="store_true",
                        help="correr contra la tienda simulada, sin credenciales")
    parser.add_argument("--since", default=None,
                        help="fecha ISO desde la que leer (se filtra en el servidor)")
    parser.add_argument("--top", type=int, default=15, help="cuantos SKU listar")
    args = parser.parse_args()

    fuente = _fuente(args)
    scan = scan_service(fuente, since=args.since)

    print(f"Backend: {scan.source_name}")
    print(f"Ordenes leidas: {len(scan.demand)} lineas de pedido")
    print()

    if not scan.records:
        print("Sin ordenes en la ventana pedida. No hay servicio que medir todavia.")
        for nota in scan.notes:
            print(f"  - {nota}")
        return 0

    print("SERVICIO MEDIDO POR SKU")
    print(f"  {'sku':<16}{'fill rate':>12}{'otif':>10}{'venta perdida':>16}{'lineas':>9}")
    for r in sorted(scan.records, key=lambda x: x.product_id)[: args.top]:
        print(f"  {r.product_id:<16}{_pct(r.fill_rate):>12}{_pct(r.otif):>10}"
              f"{_plata(r.lost_sales_value):>16}{r.lines_scored:>9}")

    p = scan.portfolio
    print()
    print("CARTERA (ponderada por evidencia, no por cantidad de SKU)")
    print(f"  fill rate {_pct(p.fill_rate)} | otif {_pct(p.otif)} | "
          f"venta perdida {_plata(p.lost_sales_value)} | provenance {p.provenance}")

    if scan.notes:
        print()
        print("LO QUE ESTE BACKEND NO PUEDE DECIR (o dice distinto de lo que parece)")
        for nota in scan.notes:
            print(f"  - {nota}")

    # Y la misma lectura, entrando al job de inventario: es el argumento order_lines que
    # hasta ahora ningun llamador pasaba, o sea el motivo por el que cada corrida reportaba
    # not_measured sobre historiales que si tenian con que medirse.
    print()
    try:
        report = run_inventory(scan.demand, order_lines=scan.order_lines, period_freq="D")
    except (ValueError, KeyError) as exc:
        print(f"El job de inventario no pudo correr sobre esta demanda: {exc}")
        return 0
    medidos = sum(
        1 for rec in report.recommendations
        if rec.realized_service is not None and rec.realized_service.provenance == "observed"
    )
    print(f"JOB DE INVENTARIO: {report.n_skus} SKU analizados, "
          f"{medidos} con servicio OBSERVADO en el entregable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
