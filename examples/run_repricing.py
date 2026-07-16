"""Operator entry point for the multichannel repricing playbook (jobs/repricing.py).

Stages a per-channel price change through the safe-staging writeback plane
(guardrail-gated, citation-backed), shows the diff, and -- only with --apply --
runs the full stage -> approve -> apply -> verify cycle. Runs fully offline
against an in-memory stand-in for the chosen channel (Shopify / MercadoLibre /
Odoo), so the whole flow is demonstrable with no credentials.

This is the operator entry point the repricing module's own docstring flagged as
"a later PR's concern" -- before this, jobs/repricing.py was reachable only from
tests. Live channels need the client's own credentials (a Shopify Admin token, a
MercadoLibre OAuth token for the seller's OWN listings, or ODOO_* creds) and a
real per-channel RPC; wiring that live transport is the operator's integration
step (see src/connectors/{shopify,meli,odoo}_prices.py). This CLI exercises the
offline stand-in that proves the pipeline end to end, and NEVER auto-applies:
staging is a dry run, and even --apply requires a named approver (the human).

Usage:
    python examples/run_repricing.py --channel shopify
    python examples/run_repricing.py --channel odoo --prices "SKU-1=19.0,SKU-2=45.0" --reason "Q3 markdown"
    python examples/run_repricing.py --channel meli --apply --approved-by "Ana"   # applies to the stand-in
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from jobs.repricing import (  # noqa: E402
    RepricingGuardrailBlocked,
    RepricingVerificationFailed,
    run_channel_repricing,
    stage_repricing,
)
from src import writeback  # noqa: E402
from src.connectors.meli_prices import MeliPriceStore, demo_meli  # noqa: E402
from src.connectors.odoo import demo_odoo  # noqa: E402
from src.connectors.odoo_prices import OdooPriceStore  # noqa: E402
from src.connectors.shopify_prices import ShopifyPriceStore, demo_shopify  # noqa: E402

# demo_*() seed SKU-1=20, SKU-2=50, SKU-3=8, so these produce a real diff.
_DEMO_PRICES = {"SKU-1": 18.0, "SKU-2": 45.0}
# A reason the pricing guardrail gate accepts (mirrors the repricing test's REASON).
_DEMO_REASON = "Repricing to close a margin gap vs landed cost (elasticity-driven)."


def _demo_store(channel: str) -> object:
    """Build the channel's offline stand-in store (the exact construction the
    repricing tests use). Live construction from real credentials is the
    operator's integration step -- see src/connectors/<channel>_prices.py."""
    if channel == "shopify":
        return ShopifyPriceStore(demo_shopify())
    if channel == "meli":
        return MeliPriceStore(demo_meli())
    if channel == "odoo":
        return OdooPriceStore(demo_odoo())
    raise ValueError(f"unknown channel {channel!r}")


def _parse_prices(raw: str) -> dict[str, float]:
    prices: dict[str, float] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair:
            continue
        sku, sep, value = pair.partition("=")
        if not sep or not sku.strip() or not value.strip():
            raise SystemExit(f"bad --prices item {pair!r}; expected SKU=price")
        try:
            prices[sku.strip()] = float(value)
        except ValueError:
            raise SystemExit(f"bad price in {pair!r}; expected a number")
    if not prices:
        raise SystemExit("--prices parsed to nothing; expected e.g. 'SKU-1=18.0,SKU-2=45.0'")
    return prices


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Stage/apply a per-channel repricing against an offline stand-in."
    )
    parser.add_argument("--channel", choices=["shopify", "meli", "odoo"], required=True)
    parser.add_argument("--prices", default=None, help="comma-separated SKU=price (default: demo prices)")
    parser.add_argument("--reason", default=None,
                        help="human explanation, guardrail-required (default: a demo reason)")
    parser.add_argument("--approved-by", default="operator",
                        help="who approves the staged diff (audit trail)")
    parser.add_argument("--apply", action="store_true",
                        help="run the full stage->approve->apply->verify cycle (default: dry-run, stage only)")
    args = parser.parse_args(argv)

    prices = _parse_prices(args.prices) if args.prices else dict(_DEMO_PRICES)
    reason = (args.reason or "").strip() or _DEMO_REASON
    store = _demo_store(args.channel)
    channel_label = f"{args.channel}:demo"
    idem = f"cli-repricing-{args.channel}"

    print(f"\n=== Repricing playbook ({args.channel}, offline stand-in) ===")
    print(f"  reason     : {reason}")
    print(f"  price plan : {', '.join(f'{s}={p:g}' for s, p in prices.items())}")

    try:
        changeset = stage_repricing(store, channel_label, prices, idempotency_key=idem, reason=reason)
    except RepricingGuardrailBlocked as exc:
        print(f"\n  BLOCKED by pricing guardrails (nothing staged, nothing written): {exc}")
        return 2

    print(f"\n  Staged (dry-run, nothing written yet): {changeset.summary()}")
    print(f"  risk tier  : {changeset.risk_tier}")

    if not args.apply:
        print("\n  Dry run. Re-run with --apply to stage -> approve -> apply -> verify.")
        return 0

    try:
        result = run_channel_repricing(
            store, channel_label, prices,
            idempotency_key=idem, reason=reason, approved_by=args.approved_by,
        )
    except RepricingGuardrailBlocked as exc:
        print(f"\n  BLOCKED by pricing guardrails: {exc}")
        return 2
    except writeback.WritebackRefused as exc:
        print(f"\n  Apply refused (approval missing/expired/mismatched): {exc}")
        return 3
    except RepricingVerificationFailed as exc:
        print(f"\n  Verification FAILED (a staged write did not take): {exc}")
        return 4

    print(f"\n  Approved by {args.approved_by!r} -> applied={result.apply_result.applied}, "
          f"verified={result.verified}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
