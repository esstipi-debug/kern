"""Free lead-magnet: "Escaneo de Stock Muerto" - one stock CSV in, one $ headline out.

    python examples/run_free_scan.py --data prospecto_stock.csv --client "Acme Co"
    python examples/run_free_scan.py --demo

Reuses the existing, tested Excess & Obsolete job (jobs/excess_obsolete_job.py)
as-is - no new engine logic. Produces the SHORT, client-facing teaser message for
the acquisition playbook (documentation/ACQUISITION_PLAYBOOK.md) - not the full
paid Diagnostico deliverable (Excel + report + KPIs), which stays a Diagnostico
package deliverable (examples/run_package.py --package diagnostico).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from jobs.excess_obsolete_job import EOReport, prepare_records, run


def _demo_stock() -> pd.DataFrame:
    """A small illustrative stock file - good enough to run the scan with no client data."""
    rows = [
        {"product_id": "SKU-001", "on_hand": 320, "daily_demand": 6.0, "unit_cost": 7.0,
         "days_since_last_sale": 3},
        {"product_id": "SKU-002", "on_hand": 900, "daily_demand": 1.5, "unit_cost": 12.0,
         "days_since_last_sale": 210},
        {"product_id": "SKU-003", "on_hand": 60, "daily_demand": 4.0, "unit_cost": 20.0,
         "days_since_last_sale": 5},
        {"product_id": "SKU-004", "on_hand": 500, "daily_demand": 0.0, "unit_cost": 9.5,
         "days_since_last_sale": 260},
        {"product_id": "SKU-005", "on_hand": 150, "daily_demand": 2.0, "unit_cost": 15.0,
         "days_since_last_sale": 40},
        {"product_id": "SKU-006", "on_hand": 1200, "daily_demand": 3.0, "unit_cost": 5.0,
         "days_since_last_sale": 95},
        {"product_id": "SKU-007", "on_hand": 40, "daily_demand": 8.0, "unit_cost": 30.0,
         "days_since_last_sale": 1},
        {"product_id": "SKU-008", "on_hand": 700, "daily_demand": 0.5, "unit_cost": 11.0,
         "days_since_last_sale": 300},
    ]
    return pd.DataFrame(rows)


def format_scan_message(report: EOReport, client: str) -> str:
    """The short, client-facing teaser (playbook's free-scan lead magnet)."""
    top10 = [e for e in report.lines if e.excess_value > 0][:10]

    lines = [
        f"Escaneo de Stock Muerto - {client}",
        "",
        f"Tienes ${report.eo_value:,.0f} atrapados en stock muerto o excedido "
        f"({report.eo_pct_of_value * 100:.0f}% de tu valor de inventario: "
        f"{report.n_dead} SKU(s) muertos + {report.n_excess} excedidos de "
        f"{report.n_skus} analizados).",
        "",
        "Los SKUs que mas cash tienen atrapado:",
    ]
    for i, e in enumerate(top10, start=1):
        lines.append(
            f"  {i}. {e.product_id} - {e.classification} - ${e.excess_value:,.0f} en riesgo "
            f"({e.on_hand:,.0f} unidades - {e.recommended_action})"
        )
    lines += [
        "",
        "Esto es el escaneo gratis (1 archivo, resultado en menos de 48h). El "
        "Diagnostico de Arranque (USD 1.500-2.500, sprint de 2 semanas) convierte "
        "este numero en un plan de recuperacion priorizado - auditoria de calidad "
        "de datos, clasificacion ABC-XYZ y KPIs financieros del inventario - con "
        "cada numero trazable y una compuerta de QA antes de entregarse.",
    ]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Escaneo gratis de Stock Muerto (lead magnet).")
    parser.add_argument(
        "--data",
        help="stock CSV del prospecto (product_id, on_hand, daily_demand "
             "[+ unit_cost, days_since_last_sale opcionales])",
    )
    parser.add_argument("--client", default="Prospecto", help="nombre del prospecto/cliente para el mensaje")
    parser.add_argument("--out", help="guardar el mensaje en este archivo .txt (listo para pegar en email/DM)")
    parser.add_argument("--demo", action="store_true", help="correr con datos sinteticos, sin archivo de cliente")
    args = parser.parse_args()

    if not args.demo and not args.data:
        parser.error("pasa --data <stock.csv> o --demo")

    df = _demo_stock() if args.demo else pd.read_csv(args.data)
    payload = prepare_records(df)
    report = run(payload)

    message = format_scan_message(report, args.client)
    print(message)

    if args.out:
        Path(args.out).write_text(message + "\n", encoding="utf-8")
        print(f"\n[guardado en {args.out}]")

    return 0


if __name__ == "__main__":
    sys.exit(main())
