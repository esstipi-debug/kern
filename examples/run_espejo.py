"""El espejo -- the free diagnostic, running on the prospect's machine.

    python examples/run_espejo.py --archivo ventas.xlsx
    python examples/run_espejo.py --archivo ventas.csv --guardar informe.txt
    python examples/run_espejo.py --ejemplo
    python examples/run_espejo.py --demo

Takes the prospect's export as their system dumped it -- CSV or Excel -- and
returns four answers: how few SKUs make 80% of sales, how much of the catalog
is dead, how much sales leak from stockouts (as a RANGE), and real OTIF / fill
rate when the file has the columns to measure them.

**Runs local and does not open a socket.** That is not an implementation note:
it is half the sale. The objection "I will not upload my customer base to a
stranger's cloud" is correct, and this CLI exists so the prospect can watch
the screen on THEIR machine, wifi off if they want.

This CLI does not invent logic: it chains ``jobs/prospect_diagnostic_job.py``
(pandas-only prepare) into ``src/prospect_diagnostic.py``. ``--demo`` feeds
synthetic rows straight to the engine so a first conversation needs no file.
``--ejemplo`` runs the committed real-shaped fixture
(``tests/fixtures/espejo/prospect_sales.csv`` -- Spanish headers, no PII).
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from jobs.prospect_diagnostic_job import EXAMPLE_FIXTURE, prepare, run  # noqa: E402
from src.prospect_diagnostic import diagnose, render_report  # noqa: E402


def _demo() -> list[dict]:
    """A synthetic catalog with the three pathologies the mirror looks for.

    Exists so we can show the output without asking anyone for a file -- which
    is the right order in a first conversation.
    """
    arranque = date(2025, 1, 6)
    filas: list[dict] = []

    for i, (media, amplitud) in enumerate([(120, 60), (90, 45), (70, 30)]):
        for semana in range(52):
            filas.append({
                "date": arranque + timedelta(weeks=semana),
                "product_id": f"ESTRELLA-{i + 1}",
                "quantity": float(media + (amplitud if semana % 2 else -amplitud)),
                "unit_cost": 25.0 + i * 5,
            })

    for i in range(20):
        for semana in range(52):
            filas.append({
                "date": arranque + timedelta(weeks=semana),
                "product_id": f"COLA-{i:02d}",
                "quantity": float(1 + (semana + i) % 3),
                "unit_cost": 8.0,
            })

    for i in range(9):
        for semana in range(10):
            filas.append({
                "date": arranque + timedelta(weeks=semana),
                "product_id": f"MUERTO-{i:02d}",
                "quantity": 3.0,
                "unit_cost": 12.0,
            })
    return filas


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="run_espejo",
        description=(
            "Inventory diagnostic on the prospect's file. Runs local. "
            "ASCII console output (Windows cp1252 safe)."
        ),
    )
    origen = parser.add_mutually_exclusive_group(required=True)
    origen.add_argument("--archivo", type=Path, help="CSV or Excel from the prospect")
    origen.add_argument("--demo", action="store_true", help="Synthetic catalog, no file needed")
    origen.add_argument(
        "--ejemplo", action="store_true",
        help="Committed real-shaped fixture (Spanish headers, no PII)",
    )
    parser.add_argument("--guardar", type=Path, help="Write the report to a file")
    parser.add_argument(
        "--ventana-muerto", type=int, default=180,
        help="Days without a sale to call a SKU dead (default: 180)",
    )
    parser.add_argument(
        "--plazo", type=float, default=14.0,
        help="Replenishment lead time in days if the file has none (default: 14)",
    )
    args = parser.parse_args(argv)

    params = {"window_days": args.ventana_muerto, "lead_time_days": args.plazo}

    if args.demo:
        diag = diagnose(_demo(), window_days=args.ventana_muerto, lead_time_days=args.plazo)
        informe = render_report(diag)
    else:
        fuente = EXAMPLE_FIXTURE if args.ejemplo else args.archivo
        if fuente is None or not fuente.exists():
            print(f"ERROR: file not found: {fuente}", file=sys.stderr)
            return 2
        try:
            filas = prepare(str(fuente), params)
        except (ValueError, FileNotFoundError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        informe = render_report(run(filas, params).diagnostic)

    print(informe)
    if args.guardar:
        args.guardar.write_text(informe, encoding="utf-8")
        print(f"\nInforme guardado en {args.guardar}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
