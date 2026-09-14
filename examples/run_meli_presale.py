"""Operator entry point for MercadoLibre preventa / catalog product search.

Read-only. Searches the official catalog, classifies which hits can carry
MANUFACTURING_TIME (MercadoLibre's documented preventa term), and writes a
CSV + markdown deck. Never POSTs /items and never asks for secrets.

    python examples/run_meli_presale.py --demo
    python examples/run_meli_presale.py --csv queries.csv --out deliverables/meli-presale

Live catalog reads need the seller's own Developers Program OAuth token
wired onto an httpx client (same deploy-time step as
src/connectors/meli_prices.py). This CLI does not start that flow. Without
credentials it only runs --demo against the in-memory catalog.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jobs.meli_presale_job import (  # noqa: E402
    build_deliverable,
    prepare,
    run,
    verify,
    write_report_csv,
)
from src.connectors.meli_catalog import demo_meli_catalog  # noqa: E402
from src.meli_presale import PresaleQuery  # noqa: E402


def _demo_queries() -> list[PresaleQuery]:
    return [
        PresaleQuery(q="Auriculares inalambricos demo", sku="SKU-PRE-1"),
        PresaleQuery(q="Cable USB-C demo", sku="SKU-CAB-2"),
        PresaleQuery(product_identifier="7790000000003", sku="SKU-OFF-3"),
        PresaleQuery(q="Cargador ya publicado demo", sku="SKU-OWN-4"),
        PresaleQuery(q="producto que no existe en el catalogo"),
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="MercadoLibre preventa / catalog search (read-only)")
    parser.add_argument("--demo", action="store_true", help="run against the offline demo catalog")
    parser.add_argument("--csv", type=Path, help="query CSV (q/title and/or gtin/ean)")
    parser.add_argument("--out", type=Path, default=Path("deliverables/meli-presale"),
                        help="directory for CSV + markdown")
    parser.add_argument("--client", default="Demo seller", help="name on the deck")
    args = parser.parse_args(argv)

    if not args.demo and args.csv is None:
        print("error: pass --demo or --csv <file>. Live OAuth is a deploy-time step.", file=sys.stderr)
        return 2
    if args.demo:
        queries = _demo_queries()
        rpc = demo_meli_catalog()
        demo = True
    else:
        queries = prepare(args.csv)
        # A live csv without credentials still must not hit the real host.
        print(
            "error: a live catalog scan needs a Bearer token on an httpx client "
            "(MercadoLibre Developers Program). This CLI will not invent one. "
            "Use --demo, or wire MeliCatalogClient in your own operator script.",
            file=sys.stderr,
        )
        return 2

    report = run(queries, rpc, demo=demo)
    problems = verify(report)
    if problems:
        print("QA failed -- no deliverable:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        return 1

    out = args.out
    out.mkdir(parents=True, exist_ok=True)
    csv_path = write_report_csv(report, out / "meli_presale.csv")
    deck = build_deliverable(report, client=args.client, prepared="demo" if demo else "")
    md_path = out / "meli_presale.md"
    md_path.write_text(deck.to_markdown(), encoding="utf-8")

    print(f"queries:        {report.n_queries}")
    print(f"presale_ready:  {report.n_presale_ready}")
    print(f"catalog_only:   {report.n_catalog_only}")
    print(f"already_listed: {report.n_already_listed}")
    print(f"no_match:       {report.n_no_match}")
    print("published:      0 (this CLI never POSTs /items)")
    print(f"csv:            {csv_path}")
    print(f"deck:           {md_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
