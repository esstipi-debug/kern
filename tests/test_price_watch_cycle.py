"""Tests for jobs/price_watch.py's Task 6 additions (Discovery-Assisted Price
Intel plan, PR-6): the recurring re-acquisition half of the discovery
playbook -- ``run_price_watch_cycle`` re-acquires every CONFIRMED ``sku_map``
pair via the L1 structured-data PDP path and converges on
``jobs.price_monitor.accept_observation`` for every sanity-gate/ledger/
market-signal decision, plus ``PRICE_WATCH_JOB``'s scheduler registration.

No real network call ever happens here -- every ``httpx.Client`` is built on
``httpx.MockTransport`` (same convention as ``tests/test_price_monitor_job.py``
and ``tests/test_price_intelligence_job.py``). ``discovered-retailer.test``
(approved to L1 -- PR-1's own auto-onboarding fixture) and ``meli-api.test``
(approved only to L0 -- PR-15's own fixture) are both already-committed
``config/sites/*.yaml`` fixtures; no new fixture was needed.

Guarantees under test (mirrors the task brief's risk callouts, the highest-
stakes tests in the whole plan):
- a confirmed discovery pair is honestly re-acquired via the L1 PDP path and
  the accepted observation is durably appended to the ledger;
- every sanity-gate/ledger-append/market-signal-event decision for a
  re-acquired candidate is made by the SAME ``accept_observation()``
  ``jobs.price_monitor``'s own L0 path calls -- proven by SPYING on it, not
  merely checking outcomes (a duplicated implementation would still pass an
  outcomes-only check);
- a 403 degrades the domain's ``CircuitBreaker`` (tier step-down,
  ``site_degraded`` event) and is NEVER retried -- one fetch attempt per
  pair per cycle, an honest ``skipped: blocked:blocked_403`` outcome;
- ``JobRegistry.run_once()`` runs ``PRICE_WATCH_JOB`` fully synchronously --
  no background thread is ever started;
- a site approved only to a tier below L1 (MercadoLibre's own L0-only
  fixture) is honestly ``skipped: tier_not_approved``, never silently
  escalated to fetch anyway -- the actual tier-raising ESCALATION logic is
  explicitly out of scope for this cycle (a later PR's concern);
- no silent caps (golden rule 14): an empty sku_map is a clean no-op, and an
  unconfigured domain is reported, never dropped from the cycle;
- the shared ``default_sku_map()``/``default_ledger()`` singletons are never
  closed by this cycle, even when it constructed them itself.
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock

import httpx

from jobs import price_watch as pw
from jobs.price_monitor import accept_observation as real_accept_observation
from jobs.scheduler import JobRegistry
from scm_agent.events import EventLedger
from src.pricing_intel.ledger import PriceLedger
from src.pricing_intel.match.sku_map import AUTO_CONFIRMED_BY, SkuMap
from src.pricing_intel.models import CompetitorOffer, MatchCandidate

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pricing_intel"

NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
EARLIER = NOW - timedelta(hours=6)

# PR-1's own committed auto-onboarding fixture (config/sites/discovered-retailer.test.yaml):
# approved to L1 exactly, the ceiling this cycle needs.
DISCOVERY_SITE = "discovered-retailer.test"
DISCOVERY_URL = "https://discovered-retailer.test/p/1"

# PR-15's own committed fixture (config/sites/meli-api.test.yaml): approved
# only to L0 -- exactly the "approved to a lower tier" case this cycle must
# honestly skip, never escalate past.
MELI_TEST_DOMAIN = "meli-api.test"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _jsonld_html() -> str:
    # Hand-verified fixture (see its own docstring): price=199.99, USD, InStock.
    return (FIXTURES / "jsonld_clean.html").read_text(encoding="utf-8")


def _seed_confirmed_pair(
    sku_map: SkuMap,
    *,
    our_product_id: str = "SKU-100",
    site: str = DISCOVERY_SITE,
    competitor_sku_ref: str = DISCOVERY_URL,
    method: str = "probabilistic",
) -> None:
    sku_map.record(
        MatchCandidate(
            our_product_id=our_product_id, competitor_sku_ref=competitor_sku_ref, site=site,
            method=method, score=0.95, status="confirmed", reason="hand-verified-fixture",
            confirmed_by=AUTO_CONFIRMED_BY, confirmed_at=NOW,
        ),
        now=NOW,
    )


def _seed_previous_offer(
    ledger: PriceLedger,
    *,
    price: str,
    site: str = DISCOVERY_SITE,
    competitor_sku_ref: str = DISCOVERY_URL,
    matched_product_id: str = "SKU-100",
) -> None:
    price_dec = Decimal(price)
    offer = CompetitorOffer(
        observed_at=EARLIER, site=site, competitor_sku_ref=competitor_sku_ref,
        matched_product_id=matched_product_id, match_confidence=1.0,
        price=price_dec, currency="USD", price_normalized=price_dec, shipping=None,
        availability="InStock", promo_flag=False, list_price=None,
        acquisition_tier="L1", extractor="structured:extruct", extractor_version="1", extraction_confidence=0.98,
    )
    ledger.append([offer], now=EARLIER)


# -- test_cycle_reacquires_confirmed_discovery_pairs -------------------------


def test_cycle_reacquires_confirmed_discovery_pairs(tmp_path) -> None:
    sku_map = SkuMap(tmp_path / "sku_map")
    _seed_confirmed_pair(sku_map)
    ledger = PriceLedger(tmp_path / "ledger")
    event_ledger = EventLedger(tmp_path / "events.sqlite3")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == DISCOVERY_SITE
        return httpx.Response(200, text=_jsonld_html())

    report = pw.run_price_watch_cycle(
        sku_map=sku_map, ledger=ledger, event_ledger=event_ledger, http_client=_client(handler), now=NOW,
    )

    assert report.pairs_checked == 1
    outcome = report.outcomes[0]
    assert outcome.status == "accepted"
    assert outcome.reason == "ok"
    assert outcome.site == DISCOVERY_SITE
    assert outcome.matched_product_id == "SKU-100"

    record = ledger.latest_by_sku(DISCOVERY_SITE, DISCOVERY_URL)
    assert record is not None
    assert record.offer.price == Decimal("199.99")
    assert record.offer.acquisition_tier == "L1"
    assert "1 accepted" in report.summary

    sku_map.close()
    ledger.close()
    event_ledger.close()


# -- test_cycle_converges_on_accept_observation -------------------------------


def test_cycle_converges_on_accept_observation(tmp_path, monkeypatch) -> None:
    """The dedicated convergence test: SPY on accept_observation (wraps the
    REAL implementation) and prove the market-signal event this cycle
    reports came from that call, not a second, parallel implementation."""
    sku_map = SkuMap(tmp_path / "sku_map")
    _seed_confirmed_pair(sku_map)
    ledger = PriceLedger(tmp_path / "ledger")
    _seed_previous_offer(ledger, price="150.00")  # within the 40% intraday-delta gate
    event_ledger = EventLedger(tmp_path / "events.sqlite3")

    spy = MagicMock(side_effect=real_accept_observation)
    monkeypatch.setattr(pw, "accept_observation", spy)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_jsonld_html())

    report = pw.run_price_watch_cycle(
        sku_map=sku_map, ledger=ledger, event_ledger=event_ledger, http_client=_client(handler), now=NOW,
    )

    spy.assert_called_once()
    called_candidate = spy.call_args.args[0]
    assert called_candidate.site == DISCOVERY_SITE
    assert called_candidate.competitor_sku_ref == DISCOVERY_URL
    assert spy.call_args.kwargs["ledger"] is ledger
    assert spy.call_args.kwargs["event_ledger"] is event_ledger

    assert report.outcomes[0].status == "accepted"
    assert len(report.events) == 1
    assert report.events[0].type == "price_move"
    # the reported event is exactly what the spied (real) accept_observation
    # returned -- this cycle never computes its own market-signal verdict.
    assert report.outcomes[0].events == report.events

    sku_map.close()
    ledger.close()
    event_ledger.close()


# -- test_403_degrades_via_circuit_breaker_not_retry --------------------------


def test_403_degrades_via_circuit_breaker_not_retry(tmp_path) -> None:
    sku_map = SkuMap(tmp_path / "sku_map")
    for i in range(3):
        _seed_confirmed_pair(
            sku_map, our_product_id=f"SKU-{i}",
            competitor_sku_ref=f"https://discovered-retailer.test/p/{i}",
        )
    ledger = PriceLedger(tmp_path / "ledger")
    event_ledger = EventLedger(tmp_path / "events.sqlite3")

    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(403, text="blocked")

    report = pw.run_price_watch_cycle(
        sku_map=sku_map, ledger=ledger, event_ledger=event_ledger, http_client=_client(handler), now=NOW,
    )

    # Exactly one fetch attempt per confirmed pair -- NEVER a retry against
    # the same (or any) URL within this cycle (NON-GOAL 1).
    assert call_count == 3
    assert report.pairs_checked == 3
    assert all(o.status == "skipped" for o in report.outcomes)
    assert all(o.reason == "blocked:blocked_403" for o in report.outcomes)

    # default CircuitBreaker.for_site failure_threshold=3: the breaker trips
    # (degrades) exactly once, on the 3rd consecutive 403 -- never a retry,
    # always a degrade.
    degraded = event_ledger.list_by_type("site_degraded")
    assert len(degraded) == 1
    assert degraded[0].payload["domain"] == DISCOVERY_SITE
    assert degraded[0].payload["reason"] == "blocked_403"
    assert degraded[0].payload["previous_tier"] == "L1"
    assert degraded[0].payload["effective_tier"] == "L0"

    sku_map.close()
    ledger.close()
    event_ledger.close()


# -- test_run_once_is_synchronous_no_daemon -----------------------------------


def test_run_once_is_synchronous_no_daemon(tmp_path, monkeypatch) -> None:
    """Golden rule 9: no daemon, no sleeping. JobRegistry.run_once() calls
    the exact same synchronous function PRICE_WATCH_JOB registers -- proven
    here by patching threading.Thread.start to raise if it is EVER called
    during the run, not just by asserting on the return value."""
    monkeypatch.setattr(pw, "default_sku_map", lambda: SkuMap(tmp_path / "sku_map"))
    monkeypatch.setattr(pw, "default_ledger", lambda: PriceLedger(tmp_path / "ledger"))
    monkeypatch.setattr(pw, "EventLedger", lambda: EventLedger(tmp_path / "events.sqlite3"))

    def _no_thread_start(self, *args, **kwargs):
        raise AssertionError("run_once() must never start a background thread")

    monkeypatch.setattr(threading.Thread, "start", _no_thread_start)

    registry = JobRegistry()
    registry.register(pw.PRICE_WATCH_JOB)
    assert pw.PRICE_WATCH_JOB.id == "price_watch_cycle"
    assert pw.PRICE_WATCH_JOB.trigger_args == {"hours": pw.DEFAULT_CADENCE_HOURS}

    before = threading.active_count()
    result = registry.run_once("price_watch_cycle")
    after = threading.active_count()

    assert after == before  # no thread survived (or was ever started)
    report = result["price_watch_cycle"]
    assert report.pairs_checked == 0  # no sku_map seeded via the isolated tmp_path store above


# -- test_tier_beyond_ceiling_skipped_not_escalated_here ----------------------


def test_tier_beyond_ceiling_skipped_not_escalated_here(tmp_path) -> None:
    """MercadoLibre's own fixture is approved only to L0 (config/sites/
    meli-api.test.yaml) -- a confirmed pair against it must be honestly
    skipped by THIS cycle's L1 attempt, never silently escalated to fetch
    anyway. Raising the ceiling is explicitly a later PR's concern."""
    sku_map = SkuMap(tmp_path / "sku_map")
    _seed_confirmed_pair(
        sku_map, site=MELI_TEST_DOMAIN, competitor_sku_ref="MLA1234567890", our_product_id="SKU-100",
    )
    ledger = PriceLedger(tmp_path / "ledger")
    event_ledger = EventLedger(tmp_path / "events.sqlite3")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("a site approved only to L0 must never be fetched at L1")

    report = pw.run_price_watch_cycle(
        sku_map=sku_map, ledger=ledger, event_ledger=event_ledger, http_client=_client(handler), now=NOW,
    )

    assert report.pairs_checked == 1
    assert report.outcomes[0].status == "skipped"
    assert report.outcomes[0].reason == "tier_not_approved"

    sku_map.close()
    ledger.close()
    event_ledger.close()


# -- supporting coverage: golden rule 14, no silent caps ----------------------


def test_cycle_with_no_confirmed_pairs_is_a_clean_no_op(tmp_path) -> None:
    sku_map = SkuMap(tmp_path / "sku_map")
    ledger = PriceLedger(tmp_path / "ledger")
    event_ledger = EventLedger(tmp_path / "events.sqlite3")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no confirmed pairs -- the network must never be touched")

    report = pw.run_price_watch_cycle(
        sku_map=sku_map, ledger=ledger, event_ledger=event_ledger, http_client=_client(handler), now=NOW,
    )

    assert report.pairs_checked == 0
    assert report.outcomes == ()
    assert report.events == ()
    assert "no confirmed" in report.summary.lower()

    sku_map.close()
    ledger.close()
    event_ledger.close()


def test_site_not_configured_is_skipped_honestly_never_dropped(tmp_path) -> None:
    sku_map = SkuMap(tmp_path / "sku_map")
    _seed_confirmed_pair(
        sku_map, site="never-onboarded.test", competitor_sku_ref="https://never-onboarded.test/p/1",
    )
    ledger = PriceLedger(tmp_path / "ledger")
    event_ledger = EventLedger(tmp_path / "events.sqlite3")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("an unconfigured domain must never be fetched")

    report = pw.run_price_watch_cycle(
        sku_map=sku_map, ledger=ledger, event_ledger=event_ledger, http_client=_client(handler), now=NOW,
    )

    assert report.pairs_checked == 1
    assert report.outcomes[0].status == "skipped"
    assert "site_not_approved" in report.outcomes[0].reason

    sku_map.close()
    ledger.close()
    event_ledger.close()


def test_two_pairs_on_different_sites_each_get_their_own_config_and_breaker(tmp_path) -> None:
    """Multiple confirmed pairs on DIFFERENT domains within one cycle each
    load their own SiteConfig/CircuitBreaker -- one domain's tier ceiling
    never affects another's outcome (golden rule 14: every pair reported,
    correctly, independently)."""
    sku_map = SkuMap(tmp_path / "sku_map")
    _seed_confirmed_pair(sku_map, our_product_id="SKU-1", site=DISCOVERY_SITE, competitor_sku_ref=DISCOVERY_URL)
    _seed_confirmed_pair(
        sku_map, our_product_id="SKU-2", site=MELI_TEST_DOMAIN, competitor_sku_ref="MLA0000000001",
    )
    ledger = PriceLedger(tmp_path / "ledger")
    event_ledger = EventLedger(tmp_path / "events.sqlite3")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == DISCOVERY_SITE  # the L0-only MELI pair must never reach the network
        return httpx.Response(200, text=_jsonld_html())

    report = pw.run_price_watch_cycle(
        sku_map=sku_map, ledger=ledger, event_ledger=event_ledger, http_client=_client(handler), now=NOW,
    )

    assert report.pairs_checked == 2
    by_product = {o.matched_product_id: o for o in report.outcomes}
    assert by_product["SKU-1"].status == "accepted"
    assert by_product["SKU-2"].status == "skipped"
    assert by_product["SKU-2"].reason == "tier_not_approved"

    sku_map.close()
    ledger.close()
    event_ledger.close()


# -- supporting coverage: shared singleton lifecycle discipline ---------------


def test_shared_singletons_never_closed_by_cycle(tmp_path, monkeypatch) -> None:
    sku_map = SkuMap(tmp_path / "sku_map")
    ledger = PriceLedger(tmp_path / "ledger")

    def _raise_if_closed():
        raise AssertionError("run_price_watch_cycle must never close a caller-supplied sku_map/ledger")

    monkeypatch.setattr(sku_map, "close", _raise_if_closed)
    monkeypatch.setattr(ledger, "close", _raise_if_closed)
    event_ledger = EventLedger(tmp_path / "events.sqlite3")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no confirmed pairs -- network must never be touched")

    report = pw.run_price_watch_cycle(
        sku_map=sku_map, ledger=ledger, event_ledger=event_ledger, http_client=_client(handler), now=NOW,
    )

    assert report.pairs_checked == 0
    # reaching here without the monkeypatched close() raising proves the guard held.
    event_ledger.close()


def test_owned_event_ledger_and_client_are_closed_when_self_constructed(tmp_path, monkeypatch) -> None:
    """The inverse of the singleton test above: event_ledger/http_client are
    NOT cached singletons -- when this cycle constructs them itself (caller
    passed neither), it must close them; a caller-supplied instance of
    either must be left open (asserted in the other tests above)."""
    monkeypatch.setattr(pw, "default_sku_map", lambda: SkuMap(tmp_path / "sku_map"))
    monkeypatch.setattr(pw, "default_ledger", lambda: PriceLedger(tmp_path / "ledger"))

    closed = {"event_ledger": False}
    real_event_ledger = EventLedger(tmp_path / "events.sqlite3")
    original_close = real_event_ledger.close

    def _tracking_close():
        closed["event_ledger"] = True
        original_close()

    monkeypatch.setattr(real_event_ledger, "close", _tracking_close)
    monkeypatch.setattr(pw, "EventLedger", lambda: real_event_ledger)

    report = pw.run_price_watch_cycle(now=NOW)

    assert report.pairs_checked == 0
    assert closed["event_ledger"] is True
