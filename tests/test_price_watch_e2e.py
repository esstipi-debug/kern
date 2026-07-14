"""End-to-end acceptance tests for the discovery-assisted competitor price
intelligence feature (Task 12 / PR-12) -- the final integration checkpoint of
the whole 12-PR plan.

Every prior task (1-11) built and individually verified one piece; this file
proves the three headline acceptance criteria hold when the WHOLE pipeline
runs together through ``examples/run_price_watch.py``'s ``run_pipeline`` -- the
same core the CLI's ``main()`` drives -- with zero human intervention:

R1/R6 (``test_single_url_produces_full_deliverable_set``): one never-seen
   ``.test`` URL -> auto-onboarded (robots.txt only, ``limited`` tier, never
   ``allowed``) -> crawl+discover -> homologate -> one watch cycle acquires
   the confirmed pair's current competitor price -> the FULL deliverable set:
   a ``homologation_table.csv``, a ``price_position_matrix.xlsx``, AND a
   per-SKU ``price_priority.csv``.

Acceptance 2 (``test_robots_disallow_yields_reason_no_config_no_fetch``): a
   robots.txt disallow writes NO ``config/sites/<domain>.yaml``, NEVER invokes
   the crawl, NEVER fetches, and reports an honest machine-readable reason.
   The crawl adapter and the HTTP transport are BOTH spied to raise if called
   -- a genuine assertion, not a status-string check.

Acceptance 3/R5 (``test_ceiling_raise_surfaces_as_pending_option_not_executed``):
   a high-value SKU wanting a tier beyond the auto-approved L1 ceiling surfaces
   a pending-approval ``GuidedOutcome`` (via the real Task 8/9 escalation
   chain), never ``EXECUTED``, and NO higher-tier acquisition is ever attempted.

Fully offline and deterministic: the advertools crawl is injected as a plain
callable returning a fixed DataFrame, and every ``httpx.Client`` is built on
``httpx.MockTransport`` (the same conventions ``tests/test_price_watch_*.py``
already use); auto-onboarding's single network touch is stubbed via its
``robots_reader`` seam. No test here ever touches the network or the repo's
real ``config/sites`` directory.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import httpx
import pandas as pd

from examples import run_price_watch as cli
from jobs.price_watch_scaling import SkuScalingRequest
from scm_agent.events import EventLedger
from src.guided import EXECUTED, HANDOFF
from src.pricing_intel.ledger import PriceLedger
from src.pricing_intel.match.sku_map import SkuMap

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pricing_intel"

NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)

# The standard GS1/IFA demo EAN-13 (reused verbatim from
# tests/test_price_watch_homologation.py -- a real, check-digit-valid code, not
# a new number invented here). The discovery HTML carries it on its Offer node
# and the catalog maps our-acme to it, so homologation auto-confirms via the
# exact-GTIN path (deterministic, no fuzzy/probabilistic threshold to tune).
VALID_EAN13 = "4006381333931"

DOMAIN = "newcomp.test"  # never-seen: no committed config/sites/*.yaml exists
SEED_URL = f"https://{DOMAIN}/category/widgets"
PDP_URL = f"https://{DOMAIN}/p/aw-3000"


def _discovery_html() -> str:
    """One crawled product page carrying real JSON-LD Product/Offer structured
    data, with a check-digit-valid ``gtin13`` on the Offer node (discover.py
    reads gtin off the Offer node) so the homologation cascade auto-confirms
    it against the catalog via the exact-GTIN path."""
    return (
        '<html><head><title>Acme Widget Pro 3000</title>'
        '<script type="application/ld+json">'
        '{"@context":"https://schema.org","@type":"Product",'
        '"name":"Acme Widget Pro 3000 Deluxe Edition","brand":"Acme",'
        '"offers":{"@type":"Offer","price":"249.00","priceCurrency":"USD",'
        f'"gtin13":"{VALID_EAN13}","availability":"https://schema.org/InStock"}}}}'
        '</script></head><body><h1>Acme Widget Pro 3000</h1></body></html>'
    )


def _pdp_html() -> str:
    """The PDP the watch cycle re-fetches for the confirmed pair -- the frozen
    tier-1 extraction golden (price 199.99 USD, InStock)."""
    return (FIXTURES / "jsonld_clean.html").read_text(encoding="utf-8")


def _crawl_frame(domain: str) -> pd.DataFrame:
    """A one-page crawl result, exactly the shape ``prepare`` ->
    ``pages_from_crawl_dataframe`` consumes (same columns the discovery tests
    use: url, status, title, page_html)."""
    return pd.DataFrame(
        [{"url": PDP_URL, "status": 200, "title": "Acme Widget Pro 3000", "page_html": _discovery_html()}]
    )


def _catalog_df() -> pd.DataFrame:
    """Our own catalog -- one row per SKU, doubling as the ABC-XYZ demand
    source for the price_priority step (product_id/demand/unit_cost columns).
    our-acme carries the matching GTIN so it auto-confirms; the other two SKUs
    never match a discovered product, so they land honestly in
    price_priority_excluded.csv (golden rule 14), never a fabricated action."""
    return pd.DataFrame(
        [
            {"product_id": "our-acme", "title": "Acme Widget Pro 3000 Deluxe Edition", "brand": "Acme",
             "gtin": VALID_EAN13, "our_price": 260.00, "demand": 120, "unit_cost": 180.0},
            {"product_id": "our-gadget", "title": "Generic Gadget", "brand": "Generic",
             "gtin": "", "our_price": 55.00, "demand": 30, "unit_cost": 40.0},
            {"product_id": "our-trinket", "title": "Small Trinket", "brand": "Trinket Co",
             "gtin": "", "our_price": 9.00, "demand": 10, "unit_cost": 5.0},
        ]
    )


def _catalog_path(tmp_path: Path) -> Path:
    path = tmp_path / "our_catalog.csv"
    _catalog_df().to_csv(path, index=False)
    return path


def _mock_client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


class _Resources:
    """The offline seams a full-pipeline run needs, all isolated under tmp_path
    -- caller owns their lifecycle (run_pipeline never closes a supplied one)."""

    def __init__(self, tmp_path: Path, handler) -> None:
        self.config_dir = tmp_path / "sites"
        self.out_dir = tmp_path / "out"
        self.catalog_path = _catalog_path(tmp_path)
        self.sku_map = SkuMap(tmp_path / "sku_map")
        self.ledger = PriceLedger(tmp_path / "ledger")
        self.event_ledger = EventLedger(tmp_path / "events.sqlite3")
        self.http_client = _mock_client(handler)

    def close(self) -> None:
        self.sku_map.close()
        self.ledger.close()
        self.event_ledger.close()
        self.http_client.close()


# -- R1/R6: one never-seen URL -> the full deliverable set, zero intervention --


def test_single_url_produces_full_deliverable_set(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == DOMAIN  # only the auto-approved domain is ever fetched
        return httpx.Response(200, text=_pdp_html())

    res = _Resources(tmp_path, handler)
    try:
        result = cli.run_pipeline(
            seed_url=SEED_URL,
            catalog_path=res.catalog_path,
            out_dir=res.out_dir,
            client="Acme Corp",
            config_dir=res.config_dir,
            robots_reader=lambda robots_url, user_agent: True,
            crawl_domain=lambda seed, **kwargs: _crawl_frame(kwargs["hostname"]),
            http_client=res.http_client,
            sku_map=res.sku_map,
            ledger=res.ledger,
            event_ledger=res.event_ledger,
            now=NOW,
        )

        # --- auto-onboarded with ZERO human intervention: robots-only, limited ---
        assert result.skipped_reason is None
        assert result.domain == DOMAIN
        assert result.onboarding is not None
        assert result.onboarding.reason == "auto_approved_via_robots_txt"
        assert result.site_config is not None
        assert result.site_config.tos_decision == "limited"  # never "allowed"
        assert result.site_config.max_tier_allowed == "L1"  # never self-grants higher

        # the config was actually self-written, and its text can never claim a
        # human ToS clearance ("allowed").
        config_file = res.config_dir / f"{DOMAIN}.yaml"
        assert config_file.exists()
        config_text = config_file.read_text(encoding="utf-8")
        assert "tos_decision: limited" in config_text
        assert "tos_decision: allowed" not in config_text

        # --- site approved + homologated (confirmed via exact GTIN) ---
        assert result.homologation is not None
        assert result.homologation.n_confirmed == 1

        # --- one watch cycle acquired the confirmed pair's current price ---
        assert result.cycle is not None
        assert result.cycle.pairs_checked == 1
        assert result.cycle.outcomes[0].status == "accepted"
        record = res.ledger.latest_by_sku(DOMAIN, PDP_URL)
        assert record is not None
        assert record.offer.acquisition_tier == "L1"

        # --- the FULL deliverable set exists on disk ---
        table_path = res.out_dir / "homologation_table.csv"
        matrix_path = res.out_dir / "price_position_matrix.xlsx"
        priority_path = res.out_dir / "price_priority.csv"
        assert table_path.exists()
        assert matrix_path.exists()
        assert priority_path.exists()

        # homologation_table.csv actually names our confirmed SKU/competitor pair
        table = pd.read_csv(table_path)
        assert list(table.columns) == ["my_sku", "competitor_product", "method", "confidence", "status"]
        assert "our-acme" in table["my_sku"].astype(str).tolist()
        assert table.iloc[0]["status"] == "confirmed"

        # per-SKU price_priority.csv carries our-acme with a genuine competitor
        # read (confirmed) and one of the honest enumerated actions -- never a
        # fabricated position for a SKU with no competitor observation.
        priority = pd.read_csv(priority_path)
        acme_rows = priority[priority["product_id"] == "our-acme"]
        assert len(acme_rows) == 1
        acme = acme_rows.iloc[0]
        assert acme["competitor_read"] == "confirmed"
        valid_actions = {"igualar_precio", "oportunidad_subir", "vigilar", "ignorar_bajo_valor"}
        assert acme["action"] in valid_actions

        # --- zero human intervention: the QA gate passed with no open issues ---
        assert result.qa_issues == ()
    finally:
        res.close()


# -- Acceptance 2: robots disallow -> no config, no crawl, no fetch, honest reason --


def test_robots_disallow_yields_reason_no_config_no_fetch(tmp_path: Path) -> None:
    def _crawl_must_not_run(seed, **kwargs):
        raise AssertionError("crawl adapter must never run when robots.txt disallows")

    def _transport_must_not_run(request: httpx.Request) -> httpx.Response:
        raise AssertionError("the HTTP transport must never be touched when robots.txt disallows")

    res = _Resources(tmp_path, _transport_must_not_run)
    try:
        result = cli.run_pipeline(
            seed_url=SEED_URL,
            catalog_path=res.catalog_path,
            out_dir=res.out_dir,
            config_dir=res.config_dir,
            robots_reader=lambda robots_url, user_agent: False,  # robots.txt disallow
            crawl_domain=_crawl_must_not_run,  # spied: raises if the crawl is ever attempted
            http_client=res.http_client,  # spied: raises if a fetch is ever attempted
            sku_map=res.sku_map,
            ledger=res.ledger,
            event_ledger=res.event_ledger,
            now=NOW,
        )

        # honest, machine-readable reason -- never a bare empty result
        assert result.skipped_reason is not None
        assert "robots_disallow" in result.skipped_reason
        assert result.domain == DOMAIN

        # NO config was written for the disallowed domain -- a genuine file check
        assert not (res.config_dir / f"{DOMAIN}.yaml").exists()

        # the pipeline stopped at the gate -- no homologation, no cycle, no deliverables
        assert result.homologation is None
        assert result.cycle is None
        assert result.written == {}
        assert not (res.out_dir / "homologation_table.csv").exists()
        assert not (res.out_dir / "price_priority.csv").exists()
    finally:
        res.close()


# -- Acceptance 3 / R5: a tier raise beyond the ceiling is pending, never applied --


def test_ceiling_raise_surfaces_as_pending_option_not_executed(tmp_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        # The cycle only ever fetches at L1 (the approved ceiling); there is no
        # L2 code path. A fetch to any other domain would be a bug.
        assert request.url.host == DOMAIN
        return httpx.Response(200, text=_pdp_html())

    # A high-value SKU wants L2 -- one tier ABOVE the auto-approved L1 ceiling.
    def scaling_request_for(entry):
        return SkuScalingRequest(desired_tier="L2", desired_cadence_hours=1.0, sku_value_rank="A")

    res = _Resources(tmp_path, handler)
    try:
        result = cli.run_pipeline(
            seed_url=SEED_URL,
            catalog_path=res.catalog_path,
            out_dir=res.out_dir,
            config_dir=res.config_dir,
            robots_reader=lambda robots_url, user_agent: True,
            crawl_domain=lambda seed, **kwargs: _crawl_frame(kwargs["hostname"]),
            http_client=res.http_client,
            sku_map=res.sku_map,
            ledger=res.ledger,
            event_ledger=res.event_ledger,
            scaling_request_for=scaling_request_for,
            now=NOW,
        )

        assert result.cycle is not None
        # the ceiling raise is surfaced for a human, applied nowhere
        assert len(result.cycle.pending_escalations) == 1
        guided = result.cycle.pending_escalations[0]
        assert guided.status == HANDOFF
        assert guided.status != EXECUTED  # R5: a tier raise is NEVER executed alone
        assert guided.handoffs  # a real, human-executable ceiling-raise step travels through

        # nothing was scaled within the ceiling (the desire was a TIER raise) ...
        assert result.cycle.scaled_watches == ()
        # ... and the pair was still observed at L1 only -- no higher tier attempted.
        assert result.cycle.outcomes[0].status == "accepted"
        record = res.ledger.latest_by_sku(DOMAIN, PDP_URL)
        assert record is not None
        assert record.offer.acquisition_tier == "L1"

        # the QA gate agrees: a pending escalation reported as EXECUTED would fail here
        from jobs import qa

        assert qa.verify_price_watch(result.cycle) == []
    finally:
        res.close()


# -- read-only + robots-only-limited: the docs promise is stated where users read it --


def test_cli_docstring_states_readonly_and_robots_only_limited() -> None:
    """NON-GOAL 2/4 must be stated in prose where an operator reads it. The CLI
    module docstring is the user-facing entry point -- assert it says the tool
    is read-only observation and that auto-onboarding is robots-only + limited
    (never 'allowed')."""
    doc = (cli.__doc__ or "").lower()
    assert "read-only" in doc
    assert "robots" in doc
    assert "limited" in doc
    assert "never" in doc and "allowed" in doc
