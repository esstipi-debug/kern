"""Discovery-Assisted Price Watch (end-to-end, R1-R6 acceptance) -- the CLI that
runs the WHOLE discovery-assisted competitor price intelligence pipeline for one
never-seen competitor URL, with zero human intervention:

    python examples/run_price_watch.py --url <competitor_category_url> --catalog <our_catalog.csv>
    python examples/run_price_watch.py --demo

Pipeline (each stage is an already-tested `jobs/price_watch.py` /
`jobs/price_priority.py` / `jobs/price_intelligence.py` step -- this CLI only
chains them, it invents no new business logic):

  1. prepare       auto-onboard the seed URL's domain via robots.txt ONLY, then
                   crawl + discover its product pages (Tasks 1/2/3).
  2. homologate    match the discovered products against your own catalog and
                   persist the confirmed/suspect pairs (Tasks 4/5) ->
                   homologation_table.csv (+ homologation_unmatched.csv).
  3. watch cycle   re-acquire the CURRENT competitor price for every confirmed
                   pair via the L1 structured-data PDP path (Task 6) ->
                   price_watch_cycle.csv. An optional per-SKU scaling desire is
                   routed through the R5 guard (Tasks 8/9): a tier raise beyond
                   the approved ceiling is surfaced as a pending-approval
                   GuidedOutcome and applied NOWHERE (never EXECUTED alone).
  4. price position from the watch cycle's fresh ledger observations crossed with
                   your own prices -> price_position_matrix.xlsx (+ ledger_export.csv).
  5. price priority the ABC-XYZ x price-position value plan (Task 10) ->
                   price_priority.csv (+ price_priority_excluded.csv).

READ-ONLY OBSERVATION (NON-GOAL 4): this tool is 100% read-only. It NEVER writes
back a price to any competitor, marketplace, or your own catalog anywhere in the
pipeline -- every action in price_priority.csv is a RECOMMENDATION for a human to
act on. AUTO-ONBOARDING IS ROBOTS-ONLY + ALWAYS `limited`, NEVER `allowed`
(NON-GOAL 2): robots.txt says nothing about a site's Terms of Service, so the
self-written config/sites/<domain>.yaml is always tos_decision `limited` (never
`allowed`) and max_tier_allowed L1 (auto-onboarding never self-grants a higher
acquisition tier). A robots.txt disallow writes no config, crawls nothing, and
reports an honest reason.

The whole pipeline runs offline-deterministically under injected fixtures (a
mock crawl callable + `httpx.MockTransport` + a robots_reader stub), which is
exactly what `tests/test_price_watch_e2e.py` drives; `--demo` wires the same
seams against the repo's frozen extraction goldens so the CLI itself needs no
network and no client files.
"""

from __future__ import annotations

import argparse
import statistics
import sys
import tempfile
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path

import httpx
import pandas as pd

from jobs import price_intelligence, price_priority, price_watch, price_watch_deliverable, qa
from jobs.price_intelligence import PriceIntelReport, RowOutcome
from jobs.price_priority import PricePriorityReport
from jobs.price_watch import PriceWatchCycleReport
from jobs.price_watch_scaling import SkuScalingRequest
from scm_agent.events import EventLedger
from src.pricing_intel.acquire.auto_approve import OnboardingResult
from src.pricing_intel.acquire.pdp_fetcher import USER_AGENT
from src.pricing_intel.homologate import HomologationReport
from src.pricing_intel.ledger import PriceLedger, default_ledger
from src.pricing_intel.match.fuzzy import ProductAttributes
from src.pricing_intel.match.sku_map import SkuMap, SkuMapEntry, default_sku_map
from src.pricing_intel.models import SiteConfig

DEFAULT_SLA_HOURS = price_intelligence.DEFAULT_SLA_HOURS

_PRODUCT_COLS = ("product_id", "ProductID", "sku", "SKU", "Product", "item")
_TITLE_COLS = ("title", "name", "product_name", "Title", "Name")
_BRAND_COLS = ("brand", "Brand", "manufacturer", "Manufacturer")
_GTIN_COLS = ("gtin", "gtin13", "ean", "EAN", "upc", "UPC", "GTIN", "barcode")
_OUR_PRICE_COLS = ("our_price", "client_price", "current_price", "list_price", "price")


@dataclass(frozen=True)
class PriceWatchPipelineResult:
    """Everything one full-pipeline run produced -- the honest, complete record
    the CLI prints and the E2E test asserts on. A gate refusal (robots disallow,
    unresolvable URL, not-approved config) sets ``skipped_reason`` and leaves the
    downstream reports ``None`` and ``written`` empty (nothing was produced)."""

    domain: str | None
    skipped_reason: str | None
    onboarding: OnboardingResult | None
    site_config: SiteConfig | None
    homologation: HomologationReport | None
    cycle: PriceWatchCycleReport | None
    price_report: PriceIntelReport | None
    priority: PricePriorityReport | None
    qa_issues: tuple[str, ...]
    written: dict[str, Path]
    summary: str


def _pick_column(df: pd.DataFrame, candidates: Sequence[str]) -> str | None:
    return next((c for c in candidates if c in df.columns), None)


def _clean_gtin(value: object) -> str:
    """A GTIN cell as a bare digit string. A catalog whose GTIN column has any
    blank cells makes pandas read the whole column as ``float64``, turning
    ``4006381333931`` into ``"4006381333931.0"`` -- strip that spurious
    ``.0`` so the check-digit validator sees the real code (every GTIN-8..14
    is well within float64's exact-integer range)."""
    text = str(value).strip()
    if text.endswith(".0") and text[:-2].isdigit():
        return text[:-2]
    return text


def _load_catalog(
    df: pd.DataFrame,
) -> tuple[list[ProductAttributes], dict[str, str], dict[str, Decimal]]:
    """Parse the catalog CSV into the three shapes the pipeline needs: the
    ``ProductAttributes`` list homologation matches against, the
    ``our_product_id -> gtin`` map that drives the exact-GTIN confirm path, and
    the ``our_product_id -> our_price`` map the price-position step crosses
    against competitor reads. Title/brand fall back to safe non-empty
    placeholders only when a row omits them (``ProductAttributes`` requires
    both) -- never fabricated for matching, just structurally valid."""
    product_col = _pick_column(df, _PRODUCT_COLS)
    if product_col is None:
        raise ValueError(f"catalog needs a product id column (one of {_PRODUCT_COLS}); saw {list(df.columns)[:10]}")
    title_col = _pick_column(df, _TITLE_COLS)
    brand_col = _pick_column(df, _BRAND_COLS)
    gtin_col = _pick_column(df, _GTIN_COLS)
    price_col = _pick_column(df, _OUR_PRICE_COLS)

    catalog: list[ProductAttributes] = []
    our_gtins: dict[str, str] = {}
    our_prices: dict[str, Decimal] = {}
    for _, row in df.iterrows():
        pid = str(row[product_col]).strip()
        if not pid:
            continue
        title = str(row[title_col]).strip() if title_col and pd.notna(row[title_col]) else ""
        brand = str(row[brand_col]).strip() if brand_col and pd.notna(row[brand_col]) else ""
        catalog.append(ProductAttributes(pid, title or pid, brand or "(unspecified brand)", {}))
        if gtin_col and pd.notna(row[gtin_col]) and str(row[gtin_col]).strip():
            our_gtins[pid] = _clean_gtin(row[gtin_col])
        if price_col and pd.notna(row[price_col]) and str(row[price_col]).strip():
            try:
                our_prices[pid] = Decimal(str(row[price_col]))
            except (InvalidOperation, ValueError):
                pass  # a non-numeric price cell just means no our-price for this SKU
    return catalog, our_gtins, our_prices


def _price_report_from_confirmed(
    confirmed_pairs: Sequence[SkuMapEntry],
    our_prices: dict[str, Decimal],
    ledger: PriceLedger,
    *,
    now: datetime,
    sla_hours: float,
) -> PriceIntelReport:
    """Assemble a :class:`PriceIntelReport` from the watch cycle's OWN fresh
    ledger observations (never a second acquisition run) -- the "minimal
    price-position input" built from the homologation results crossed with our
    own prices. Reuses the exact ``CompetitorOffer`` rows the cycle appended;
    the only competitor-vs-our price math (``position_index``) lives in the
    reused ``price_intelligence`` / ``price_priority`` consumers, never here --
    this function only collects offers and rolls up honest bookkeeping counts.
    A confirmed pair with no accepted observation this cycle is recorded as a
    skipped row (golden rule 14), never silently absent."""
    offers = []
    rows: list[RowOutcome] = []
    products = {e.our_product_id for e in confirmed_pairs}
    for entry in confirmed_pairs:
        record = ledger.latest_by_sku(entry.site, entry.competitor_sku_ref)
        if record is not None and record.offer.matched_product_id:
            offers.append(record.offer)
            rows.append(RowOutcome(
                product_id=entry.our_product_id, site=entry.site,
                competitor_url=entry.competitor_sku_ref, status="accepted", reason="ok", offer=record.offer,
            ))
        else:
            rows.append(RowOutcome(
                product_id=entry.our_product_id, site=entry.site,
                competitor_url=entry.competitor_sku_ref, status="skipped",
                reason="no_accepted_observation_this_cycle",
            ))

    covered = {o.matched_product_id for o in offers if o.matched_product_id}
    n_products = len(products)
    coverage_pct = (len(covered) / n_products) if n_products else 0.0
    if offers:
        avg_freshness = statistics.fmean((now - o.observed_at).total_seconds() / 3600.0 for o in offers)
    else:
        avg_freshness = 0.0
    tier_mix = dict(Counter(o.acquisition_tier for o in offers))
    summary = (
        f"Discovery price position across {n_products} confirmed pair(s): "
        f"{len(covered)} product(s) have a current competitor read from this watch cycle."
    )
    return PriceIntelReport(
        n_products=n_products, n_products_covered=len(covered), coverage_pct=coverage_pct,
        offers=tuple(offers), our_prices=dict(our_prices), rows=tuple(rows),
        quarantine_rate=0.0, avg_freshness_hours=avg_freshness, sla_hours=sla_hours,
        tier_mix=tier_mix, stale_events=(), now=now, summary=summary,
    )


def run_pipeline(
    *,
    seed_url: str,
    catalog_path: str | Path,
    out_dir: str | Path,
    client: str = "Client",
    config_dir: str | Path | None = None,
    robots_reader: Callable[[str, str], bool] | None = None,
    crawl_domain: Callable[..., pd.DataFrame] | None = None,
    http_client: httpx.Client | None = None,
    sku_map: SkuMap | None = None,
    ledger: PriceLedger | None = None,
    event_ledger: EventLedger | None = None,
    scaling_request_for: Callable[[SkuMapEntry], SkuScalingRequest | None] | None = None,
    sla_hours: float = DEFAULT_SLA_HOURS,
    now: datetime | None = None,
) -> PriceWatchPipelineResult:
    """Run the full discovery-assisted price-watch pipeline for one ``seed_url``.

    Every side-effecting dependency is an injectable seam (``crawl_domain``,
    ``http_client``, ``robots_reader``, the three stores) so the whole run is
    offline-deterministic under test/demo fixtures. ``crawl_domain`` (when
    given) temporarily replaces ``price_watch._crawl_domain`` -- the module's
    only crawl seam -- for the duration of this call, the same seam every
    ``price_watch`` test monkeypatches.

    Lifecycle: ``sku_map``/``ledger`` are NEVER closed here (a caller-supplied
    or process-wide singleton must outlive this call -- mirrors
    ``run_price_watch_cycle``'s own discipline); an ``event_ledger``/
    ``http_client`` this function constructs itself IS closed on exit, while a
    caller-supplied one is left open (the caller's lifecycle)."""
    now = now or datetime.now(timezone.utc)
    out_dir = Path(out_dir)

    owns_event_ledger = event_ledger is None
    owns_http_client = http_client is None
    sku_map = sku_map if sku_map is not None else default_sku_map()
    ledger = ledger if ledger is not None else default_ledger()
    event_ledger = event_ledger if event_ledger is not None else EventLedger()
    http_client = http_client if http_client is not None else httpx.Client(headers={"User-Agent": USER_AGENT})

    prepare_params: dict = {}
    if config_dir is not None:
        prepare_params["config_dir"] = config_dir
    if robots_reader is not None:
        prepare_params["robots_reader"] = robots_reader

    original_crawl = price_watch._crawl_domain
    if crawl_domain is not None:
        price_watch._crawl_domain = crawl_domain
    try:
        # 1. prepare: auto-onboard (robots-only) + hard gate + crawl + discover
        payload = price_watch.prepare(seed_url, prepare_params)
        if payload["skipped_reason"]:
            summary = f"Skipped {payload['domain']}: {payload['skipped_reason']} -- no config, no crawl, no fetch."
            return PriceWatchPipelineResult(
                domain=payload["domain"], skipped_reason=payload["skipped_reason"],
                onboarding=payload["onboarding"], site_config=payload["site_config"],
                homologation=None, cycle=None, price_report=None, priority=None,
                qa_issues=(), written={}, summary=summary,
            )

        catalog, our_gtins, our_prices = _load_catalog(pd.read_csv(catalog_path))
        written: dict[str, Path] = {}

        # 2. homologate + publish the table (persists confirmed/suspect pairs)
        homologation = price_watch.run_homologation(
            {**payload, "our_catalog": catalog, "our_gtins": our_gtins}, sku_map=sku_map, now=now,
        )
        w_hom = price_watch.write_homologation(homologation, out_dir, client)
        written["homologation_table"] = w_hom["csv"]
        written["homologation_unmatched"] = w_hom["unmatched_csv"]

        # 3. one watch cycle: re-acquire the confirmed pairs' current prices (L1)
        cycle = price_watch.run_price_watch_cycle(
            sku_map=sku_map, ledger=ledger, event_ledger=event_ledger, http_client=http_client,
            sites_config_dir=config_dir, scaling_request_for=scaling_request_for, now=now,
        )
        written["watch_cycle"] = price_watch_deliverable.write_operational(cycle, out_dir, client)["csv"]
        qa_issues = tuple(qa.verify_price_watch(cycle))

        # 4. price-position matrix from the cycle's fresh ledger reads + our prices
        confirmed_pairs = sku_map.list_all_confirmed()
        report_prices = {
            e.our_product_id: our_prices[e.our_product_id]
            for e in confirmed_pairs if e.our_product_id in our_prices
        }
        price_report = _price_report_from_confirmed(
            confirmed_pairs, report_prices, ledger, now=now, sla_hours=sla_hours,
        )
        w_matrix = price_intelligence.write_operational(price_report, out_dir, client)
        written["price_position_matrix"] = w_matrix["matrix"]
        written["ledger_export"] = w_matrix["ledger_csv"]

        # 5. per-SKU price priority plan (ABC-XYZ x price-position value join)
        pp_payload = price_priority.prepare(str(catalog_path), {"price_report": price_report})
        priority = price_priority.run(pp_payload)
        w_pp = price_priority.write_operational(priority, out_dir, client)
        written["price_priority"] = w_pp["csv"]
        written["price_priority_excluded"] = w_pp["excluded_csv"]

        summary = (
            f"{payload['domain']} approved ({payload['site_config'].tos_decision}, "
            f"tier<={payload['site_config'].max_tier_allowed}); "
            f"{len(payload['discovered'])} product page(s) discovered, "
            f"{homologation.n_confirmed} confirmed / {homologation.n_suspect} suspect. "
            f"{cycle.summary} {priority.summary}"
        )
        return PriceWatchPipelineResult(
            domain=payload["domain"], skipped_reason=None, onboarding=payload["onboarding"],
            site_config=payload["site_config"], homologation=homologation, cycle=cycle,
            price_report=price_report, priority=priority, qa_issues=qa_issues,
            written=written, summary=summary,
        )
    finally:
        if crawl_domain is not None:
            price_watch._crawl_domain = original_crawl
        if owns_http_client:
            http_client.close()
        if owns_event_ledger:
            event_ledger.close()


# -- offline demo (frozen fixtures, no network, no client files) --------------

_DEMO_FIXTURES = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "pricing_intel"
_DEMO_DOMAIN = "demo-retailer.test"
_DEMO_GTIN = "4006381333931"  # the standard GS1/IFA demo EAN-13 (check-digit valid)


def _demo_discovery_html() -> str:
    return (
        '<html><head><title>Acme Widget Pro 3000</title>'
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"Product",'
        '"name":"Acme Widget Pro 3000 Deluxe Edition","brand":"Acme",'
        '"offers":{"@type":"Offer","price":"249.00","priceCurrency":"USD",'
        f'"gtin13":"{_DEMO_GTIN}","availability":"https://schema.org/InStock"}}}}'
        '</script></head><body><h1>Acme Widget Pro 3000</h1></body></html>'
    )


def _demo_catalog_df() -> pd.DataFrame:
    return pd.DataFrame([
        {"product_id": "our-acme", "title": "Acme Widget Pro 3000 Deluxe Edition", "brand": "Acme",
         "gtin": _DEMO_GTIN, "our_price": 260.00, "demand": 120, "unit_cost": 180.0},
        {"product_id": "our-gadget", "title": "Generic Gadget", "brand": "Generic",
         "gtin": "", "our_price": 55.00, "demand": 30, "unit_cost": 40.0},
        {"product_id": "our-trinket", "title": "Small Trinket", "brand": "Trinket Co",
         "gtin": "", "our_price": 9.00, "demand": 10, "unit_cost": 5.0},
    ])


def _run_demo(out_dir: str | Path, client: str) -> PriceWatchPipelineResult:
    """Fully offline demo: a never-seen ``.test`` domain auto-onboarded into a
    throwaway temp config dir, a fixed one-page crawl, and an
    ``httpx.MockTransport`` serving the frozen tier-1 extraction golden -- the
    same seams the E2E test uses, no network and no client files."""
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=(_DEMO_FIXTURES / "jsonld_clean.html").read_text(encoding="utf-8"))

    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        catalog_path = tmp_path / "demo_catalog.csv"
        _demo_catalog_df().to_csv(catalog_path, index=False)
        # Isolated stores under the temp dir so the demo never touches (or is
        # polluted by) the process-wide default sku_map/ledger under data/.
        sku_map = SkuMap(tmp_path / "sku_map")
        ledger = PriceLedger(tmp_path / "ledger")
        event_ledger = EventLedger(tmp_path / "events.sqlite3")
        client_http = httpx.Client(transport=httpx.MockTransport(handler))
        try:
            return run_pipeline(
                seed_url=f"https://{_DEMO_DOMAIN}/category/widgets",
                catalog_path=catalog_path,
                out_dir=out_dir,
                client=client,
                config_dir=tmp_path / "sites",
                robots_reader=lambda robots_url, user_agent: True,
                crawl_domain=lambda seed, **kwargs: pd.DataFrame([{
                    "url": f"https://{_DEMO_DOMAIN}/p/aw-3000", "status": 200,
                    "title": "Acme Widget Pro 3000", "page_html": _demo_discovery_html(),
                }]),
                http_client=client_http,
                sku_map=sku_map,
                ledger=ledger,
                event_ledger=event_ledger,
            )
        finally:
            client_http.close()
            event_ledger.close()
            ledger.close()
            sku_map.close()


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Discovery-Assisted Price Watch (end-to-end): auto-onboard a competitor URL "
                    "(robots.txt only, limited tier), homologate + watch it, and produce the full "
                    "read-only deliverable set. No price is ever written back anywhere.",
    )
    parser.add_argument("--url", help="competitor category/product-listing seed URL")
    parser.add_argument("--catalog", help="our catalog CSV: product_id, title, brand [, gtin, our_price, demand, unit_cost]")
    parser.add_argument("--out", default="deliverables/price_watch", help="output directory")
    parser.add_argument("--client", default="Client")
    parser.add_argument("--sla-hours", type=float, default=DEFAULT_SLA_HOURS, help="freshness SLA in hours")
    parser.add_argument("--demo", action="store_true", help="run against bundled offline fixtures, no client files")
    args = parser.parse_args(argv)

    if not args.demo and not (args.url and args.catalog):
        parser.error("pass --url <competitor_url> --catalog <our_catalog.csv>, or --demo")

    if args.demo:
        result = _run_demo(args.out, args.client)
    else:
        result = run_pipeline(
            seed_url=args.url, catalog_path=args.catalog, out_dir=args.out,
            client=args.client, sla_hours=args.sla_hours,
        )

    if result.skipped_reason:
        print(f"Site gate refused: {result.skipped_reason}", file=sys.stderr)
        print("No config was written, no page was crawled, no price was fetched.", file=sys.stderr)
        return 1

    print(result.summary)
    if result.qa_issues:
        print("QA issues (surfaced, not silently dropped):", file=sys.stderr)
        for issue in result.qa_issues:
            print("  - " + issue, file=sys.stderr)
    if result.cycle is not None and result.cycle.pending_escalations:
        print(f"{len(result.cycle.pending_escalations)} pending ceiling-raise request(s) need human approval "
              "(never applied automatically).")
    for kind, path in result.written.items():
        print(f"  {kind:24s} -> {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
