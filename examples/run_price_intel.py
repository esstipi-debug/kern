"""Price Position Diagnostic: a client refs CSV (product_id + competitor URL,
one-shot mode -- plan section 3.1 "El Atajo de Revenue") -> price-position
matrix + report + ledger export.

    python examples/run_price_intel.py --refs competitors.csv --client "Acme Co"
    python examples/run_price_intel.py --demo
    python examples/run_price_intel.py --refs competitors.csv --client "Acme" --lang en

Refs CSV columns: product_id, competitor_url (+ optional our_price, currency,
html_path, competitor_site). One row per (product, competitor) pair -- a
product with several competitors is just several rows sharing the same
product_id. The URL<->SKU mapping IS the match (plan S6.5's one-shot
exemption); PR-14's fuzzy/probabilistic matching pipeline is not needed here.

Pipeline: intake (jobs.price_intelligence.prepare, its OWN CSV reader, not
jobs/intake.py) -> acquire (PR-11's extraction cascade, gated by PR-12's
site-approval/circuit-breaker machinery) -> sanity (PR-12's quarantine gate)
-> QA (jobs.qa.verify_price_intel: >=60% coverage or no deliverable) ->
deliverable (price_position_matrix.xlsx + report.md + ledger_export.csv,
E4/E5/E6 -- lang, gated L3 citations, branding).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

from jobs import price_intelligence as pi
from jobs.qa import verify_price_intel
from src.deliverable import Branding
from src.pricing_intel.ledger import PriceLedger

_DEMO_FIXTURES = (
    Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "pricing_intel"
)


def _demo_refs() -> pd.DataFrame:
    """A small illustrative refs file, entirely offline -- reuses the exact
    frozen HTML fixtures the pricing-intel test suite ships (PR-11's own
    extraction goldens), against the synthetic, PR-12-approved
    ``example-retailer.test`` domain (see config/sites/example-retailer.test.yaml).
    Good enough to demo the full pipeline with zero network access and zero
    client files."""
    return pd.DataFrame([
        {"product_id": "SKU-100", "competitor_url": "https://example-retailer.test/p/aw-3000",
         "our_price": 210.00, "html_path": str(_DEMO_FIXTURES / "jsonld_clean.html")},
        {"product_id": "SKU-200", "competitor_url": "https://example-retailer.test/p/microdata-item",
         "our_price": 360.00, "html_path": str(_DEMO_FIXTURES / "microdata_only.html")},
    ])


def main() -> int:
    parser = argparse.ArgumentParser(description="Price Position Diagnostic (one-shot, plan section 3.1/6.9).")
    parser.add_argument("--refs", help="refs CSV: product_id, competitor_url [+ our_price, currency, html_path]")
    parser.add_argument("--client", default="Client")
    parser.add_argument("--out", default="deliverables/price_intel", help="output directory")
    parser.add_argument("--lang", default="es", choices=("es", "en"), help="report language (E4)")
    parser.add_argument("--brand-name", help="white-label the deck under this name instead of Kern (E6)")
    parser.add_argument("--sla-hours", type=float, default=pi.DEFAULT_SLA_HOURS,
                        help="freshness SLA in hours for the QA gate")
    parser.add_argument("--demo", action="store_true", help="run against bundled offline fixtures, no client file")
    args = parser.parse_args()

    if not args.demo and not args.refs:
        parser.error("pass --refs <competitors.csv> or --demo")

    if args.demo:
        df = _demo_refs()
        base_dir = None  # html_path is already absolute in the demo frame
    else:
        df = pd.read_csv(args.refs)
        base_dir = Path(args.refs).resolve().parent

    payload = pi.prepare_records(df, {"sla_hours": args.sla_hours}, base_dir=base_dir)
    print(f"Intake: {len(payload['refs'])} ref(s) across "
          f"{len({r.product_id for r in payload['refs']})} product(s) from "
          f"{'demo fixtures' if args.demo else args.refs}")

    ledger = PriceLedger()
    try:
        report = pi.run(payload, ledger=ledger)
    finally:
        ledger.close()

    issues = verify_price_intel(report)
    if issues:
        print("QA FAILED - deliverables not written:", file=sys.stderr)
        for i in issues:
            print("  - " + i, file=sys.stderr)
        return 1

    branding = Branding(name=args.brand_name) if args.brand_name else None
    brief = f"price position diagnostic for {args.client}: donde estoy caro respecto a la competencia"
    written = pi.write_deliverable(
        report, out_dir=args.out, client=args.client, brief=brief,
        lang=args.lang, branding=branding,
    )
    print(f"QA passed. {report.summary}")
    for kind, path in written.items():
        print(f"  {kind:12s} -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
