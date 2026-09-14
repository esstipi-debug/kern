"""Detect applicable ISO norms for a plant and evaluate floor evidence. Never certify.

    python examples/run_iso_ops.py tests/fixtures/iso/planta.csv --out output/iso_ops
    python examples/run_iso_ops.py tests/fixtures/iso/detectar.ejemplo.json
    python examples/run_iso_ops.py --sectores automotriz --cliente-pide iatf,pcf --out output/iso_ops
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from jobs import iso_ops_job  # noqa: E402


def _split(value: str | None) -> list[str]:
    if not value:
        return []
    return [part.strip() for part in value.replace(";", ",").split(",") if part.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ISO operations floor evidence (not a certificate)")
    parser.add_argument("data", nargs="?", help="JSON ficha or one-row CSV/Excel")
    parser.add_argument("--sectores", default="")
    parser.add_argument("--cliente-pide", default="", dest="cliente_pide")
    parser.add_argument("--logistica", default="")
    parser.add_argument("--planta", default="")
    parser.add_argument("--kern", default="")
    parser.add_argument("--texto", default="")
    parser.add_argument("--ciclo", action="store_true")
    parser.add_argument("--scrap", action="store_true")
    parser.add_argument("--kg", action="store_true")
    parser.add_argument("--tkm", action="store_true")
    parser.add_argument("--out", default="output/iso_ops")
    parser.add_argument("--client", default="Client")
    args = parser.parse_args(argv)

    params = {
        "sectores": _split(args.sectores),
        "clientePide": _split(args.cliente_pide),
        "logistica": _split(args.logistica),
        "planta": _split(args.planta),
        "kern": _split(args.kern),
        "texto": args.texto,
    }
    if args.ciclo:
        params["ciclo"] = True
    if args.scrap:
        params["scrap"] = True
    if args.kg:
        params["kg"] = True
    if args.tkm:
        params["tkm"] = True
    if not iso_ops_job.perfil_presente(params, args.data):
        print("Need a plant ficha (JSON/CSV) or --sectores/--cliente-pide/--texto/--ciclo.")
        return 2

    prepared = iso_ops_job.prepare(args.data, params)
    report = iso_ops_job.run(prepared, params)
    issues = iso_ops_job.verify(report)
    if issues:
        print("QA failed:")
        for issue in issues:
            print(f"  - {issue}")
        return 1

    paths = iso_ops_job.write_operational(report, args.out, args.client)
    deck = iso_ops_job.build_deck(report, client=args.client)
    deck_paths = deck.write_all(args.out)
    print(report.summary)
    print(f"status: {report.outcome.status}")
    print(f"detectadas: {report.n_detectadas}  nativas_orden: {report.n_nativas_orden}  huecos: {report.n_huecos}")
    for name, path in {**paths, **deck_paths}.items():
        print(f"  {name}: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
