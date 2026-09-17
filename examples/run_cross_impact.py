"""Run cross_impact end to end: one proposed decision -> who would object.

    python examples/run_cross_impact.py --data data/demanda.csv --sku SKU-1 \
        --current-q 200 --current-sl 0.95 --proposed-q 400 --proposed-sl 0.95 \
        --proposed-by comprador --client "Acme Corp"

Thresholds come from the client profile (clients/<slug>/profile.json). Areas
without one come out no_evaluable and the verdict is 'indeterminado' - that is
the honest answer, not a failure.

`transporte` has a second way to be no_evaluable: it needs a unit weight (a
weight_kg column in the CSV) and a lane length (--distance-km). Without both it
cannot be measured at all, and the verdict stays 'indeterminado' no matter how
many thresholds are declared -- nobody can say transport would not object to a
quantity nobody priced the freight of.
"""

from __future__ import annotations

import argparse
import sys

from scm_agent.orchestrator import Orchestrator
from src.hats import HAT_KEYS


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Cross-area impact of a proposed decision")
    p.add_argument("--data", required=True, help="weekly demand CSV")
    p.add_argument("--sku", required=True)
    p.add_argument("--current-q", type=float, required=True, dest="current_q",
                   help="order quantity the client runs TODAY (no default)")
    p.add_argument("--current-sl", type=float, required=True, dest="current_sl",
                   help="service level the client runs TODAY (no default)")
    p.add_argument("--proposed-q", type=float, required=True, dest="proposed_q")
    p.add_argument("--proposed-sl", type=float, required=True, dest="proposed_sl")
    p.add_argument("--proposed-by", required=True, dest="proposed_by", choices=list(HAT_KEYS))
    p.add_argument("--distance-km", type=float, default=None, dest="distance_km",
                   help="largo de la ruta; sin esto (o sin columna weight_kg) "
                        "transporte sale no_evaluable")
    p.add_argument("--max-transit-days", type=float, default=None, dest="max_transit_days",
                   help="ventana de entrega: descarta modos mas lentos")
    p.add_argument("--client", default="Client")
    p.add_argument("--out", default="deliverables/cross_impact")
    p.add_argument("--strict-params", action="store_true", dest="strict_params",
                   help="block when the client profile has no objection thresholds")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    overrides: dict = {
        "sku": args.sku,
        "current": {"q": args.current_q, "sl": args.current_sl},
        "proposed": {"q": args.proposed_q, "sl": args.proposed_sl},
        "proposed_by": args.proposed_by,
    }
    if args.distance_km is not None:
        overrides["distance_km"] = args.distance_km
    if args.max_transit_days is not None:
        overrides["max_transit_days"] = args.max_transit_days
    result = Orchestrator().run(
        brief=f"impacto cruzado de la decision propuesta por {args.proposed_by}",
        data_path=args.data,
        job_type="cross_impact",
        client=args.client,
        strict_params=args.strict_params,
        overrides=overrides,
        out_dir=args.out,
    )
    print(f"status: {result.status}")
    print(result.summary)
    for c in result.clarifications:
        print(f"  - {c}")
    if result.guided is not None:
        print("\nopciones:")
        for o in result.guided.options:
            mark = "*" if o.recommended else " "
            print(f" {mark} {o.label}  (score {o.score:.3f})")
    for name, path in result.deliverables.items():
        print(f"  {name}: {path}")
    return 0 if result.status == "ok" else 1


if __name__ == "__main__":
    sys.exit(main())
