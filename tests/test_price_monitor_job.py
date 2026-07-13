"""Tests for jobs/price_monitor.py (Linchpin 3.0 PR-15 -- the last PR of Fase
B: L0 MercadoLibre acquisition + continuous scheduled monitoring, wiring
F0's scheduler and the Tower's event/autonomy machinery onto the titan).

No real network call ever happens here -- every httpx.Client is built on
httpx.MockTransport (same convention as tests/test_pricing_intel_meli_api.py
and tests/test_price_intelligence_job.py). ``meli-api.test`` is PR-15's own
committed synthetic, approved config/sites/*.yaml fixture (see
config/sites/meli-api.test.yaml); the real api.mercadolibre.com fixture
(also committed) is used specifically to prove the compliance gate refuses
to run against it.

Guarantees under test:
- accept_observation() runs a candidate through sanity -> ledger -> the
  shared market-signal detector, and reports accepted/quarantined/discarded
  correctly (never silently dropping a candidate);
- run_price_monitor_cycle() is a plain, synchronous, all-default-kwargs
  function -- runnable directly, and via jobs.scheduler.JobRegistry.run_once()
  with NO daemon/sleeping (golden rule 9);
- a confirmed MELI sku_map pair with a genuinely-changed price -> an
  accepted observation appended to the ledger AND a real price_move Event
  recorded on the EventLedger, with a hand-verified delta_pct;
- the real, currently-prohibited api.mercadolibre.com domain is honestly
  reported as skipped for every confirmed pair against it, never silently
  dropped from the cycle (golden rule 14);
- a run of 403s trips the circuit breaker mid-cycle and a later pair on the
  SAME domain is skipped as circuit_open, without ever reaching the network;
- a synthetic price_move Event routes end-to-end through
  scm_agent.event_intent.handle_event() to a REAL price_intelligence tool
  run via the real Orchestrator, producing a QA-gated, STATUS_OK JobResult
  -- same integration-test pattern as Fase A PR-5's end-to-end monitor
  tests (tests/test_event_intent.py).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path

import httpx

from jobs.price_monitor import (
    DEFAULT_CADENCE_HOURS,
    PRICE_MONITOR_JOB,
    AcceptOutcome,
    accept_observation,
    run_price_monitor_cycle,
)
from jobs.scheduler import JobRegistry
from scm_agent import event_intent as event_intent_module
from scm_agent import llm, tools
from scm_agent.event_intent import DEFAULT_ROUTING_PATH, handle_event
from scm_agent.events import Event, EventLedger
from scm_agent.orchestrator import Orchestrator
from scm_agent.types import STATUS_OK
from src.pricing_intel.ledger import PriceLedger
from src.pricing_intel.match.sku_map import AUTO_CONFIRMED_BY, SkuMap
from src.pricing_intel.models import CompetitorOffer, MatchCandidate
from src.pricing_intel.sanity import RawOfferCandidate

NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)
EARLIER = NOW - timedelta(hours=4)
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pricing_intel"

MELI_TEST_DOMAIN = "meli-api.test"
MELI_ITEM_ID = "MLA1234567890"


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _meli_item_json(*, price: float, status: str = "active", available_quantity: int = 5) -> dict:
    return {
        "id": MELI_ITEM_ID, "site_id": "MLA", "title": "Notebook Ejemplo",
        "price": price, "currency_id": "USD", "available_quantity": available_quantity,
        "status": status, "permalink": f"https://articulo.mercadolibre.com.ar/{MELI_ITEM_ID}",
    }


def _seed_confirmed_pair(sku_map: SkuMap, *, our_product_id: str = "SKU-100", site: str = MELI_TEST_DOMAIN,
                          competitor_sku_ref: str = MELI_ITEM_ID) -> None:
    sku_map.record(
        MatchCandidate(
            our_product_id=our_product_id, competitor_sku_ref=competitor_sku_ref, site=site,
            method="gtin", score=0.99, status="confirmed", reason="gtin_exact_match:hand-verified-fixture",
            confirmed_by=AUTO_CONFIRMED_BY, confirmed_at=NOW,
        ),
        now=NOW,
    )


def _seed_previous_offer(ledger: PriceLedger, *, price: str, site: str = MELI_TEST_DOMAIN,
                          competitor_sku_ref: str = MELI_ITEM_ID, matched_product_id: str = "SKU-100",
                          availability: str = "InStock") -> None:
    price_dec = Decimal(price)
    offer = CompetitorOffer(
        observed_at=EARLIER, site=site, competitor_sku_ref=competitor_sku_ref,
        matched_product_id=matched_product_id, match_confidence=1.0,
        price=price_dec, currency="USD", price_normalized=price_dec, shipping=None,
        availability=availability, promo_flag=False, list_price=None,
        acquisition_tier="L0", extractor="meli_api", extractor_version="1", extraction_confidence=1.0,
    )
    ledger.append([offer], now=EARLIER)


def _raw_candidate(**overrides: object) -> RawOfferCandidate:
    defaults: dict[str, object] = dict(
        observed_at=NOW, site="shop.example.com", competitor_sku_ref="https://shop.example.com/p/1",
        matched_product_id="SKU-100", match_confidence=1.0,
        price=Decimal("19.99"), currency="USD", price_normalized=None,
        shipping=None, availability="InStock", promo_flag=False, list_price=None,
        acquisition_tier="L2", extractor="changedetection_io_webhook", extractor_version="1",
        extraction_confidence=0.85,
    )
    defaults.update(overrides)
    return RawOfferCandidate(**defaults)  # type: ignore[arg-type]


# -- accept_observation() --------------------------------------------------------


def test_accept_observation_accepts_a_first_ever_reading_with_no_market_events(tmp_path) -> None:
    ledger = PriceLedger(tmp_path / "ledger")
    outcome = accept_observation(_raw_candidate(), ledger=ledger, event_ledger=None)

    assert isinstance(outcome, AcceptOutcome)
    assert outcome.status == "accepted"
    assert outcome.offer is not None
    assert outcome.offer.price == Decimal("19.99")
    assert outcome.events == ()  # nothing to compare a "move" against yet

    record = ledger.latest_by_sku("shop.example.com", "https://shop.example.com/p/1")
    assert record is not None
    ledger.close()


def test_accept_observation_fires_price_move_on_a_second_different_reading(tmp_path) -> None:
    ledger = PriceLedger(tmp_path / "ledger")
    event_ledger = EventLedger(tmp_path / "events.sqlite3")
    accept_observation(_raw_candidate(price=Decimal("100.00"), observed_at=EARLIER), ledger=ledger, event_ledger=event_ledger)

    outcome = accept_observation(_raw_candidate(price=Decimal("80.00"), observed_at=NOW), ledger=ledger, event_ledger=event_ledger)

    assert outcome.status == "accepted"
    assert len(outcome.events) == 1
    assert outcome.events[0].type == "price_move"
    assert Decimal(outcome.events[0].payload["delta_pct"]) == Decimal("-0.20")
    ledger.close()
    event_ledger.close()


def test_accept_observation_discards_invalid_price(tmp_path) -> None:
    ledger = PriceLedger(tmp_path / "ledger")
    outcome = accept_observation(_raw_candidate(price=Decimal("0")), ledger=ledger, event_ledger=None)
    assert outcome.status == "discarded"
    assert outcome.reason == "invalid_price"
    assert outcome.offer is None
    ledger.close()


def test_accept_observation_quarantines_an_unconfirmed_large_jump(tmp_path) -> None:
    ledger = PriceLedger(tmp_path / "ledger")
    accept_observation(_raw_candidate(price=Decimal("100.00"), observed_at=EARLIER), ledger=ledger, event_ledger=None)

    # +50% intraday jump, no promo_flag -> quarantined (sanity.py rule 2)
    outcome = accept_observation(_raw_candidate(price=Decimal("150.00"), observed_at=NOW), ledger=ledger, event_ledger=None)

    assert outcome.status == "quarantined"
    assert outcome.reason == "intraday_delta_unconfirmed"
    assert outcome.offer is None  # never appended to the ledger
    ledger.close()


def test_accept_observation_new_pair_emits_new_competitor_listing_only_when_requested(tmp_path) -> None:
    ledger = PriceLedger(tmp_path / "ledger")
    event_ledger = EventLedger(tmp_path / "events.sqlite3")

    without_flag = accept_observation(_raw_candidate(), ledger=ledger, event_ledger=event_ledger, detect_new_listing=False)
    assert without_flag.events == ()

    ledger2 = PriceLedger(tmp_path / "ledger2")
    event_ledger2 = EventLedger(tmp_path / "events2.sqlite3")
    with_flag = accept_observation(_raw_candidate(), ledger=ledger2, event_ledger=event_ledger2, detect_new_listing=True)
    assert len(with_flag.events) == 1
    assert with_flag.events[0].type == "new_competitor_listing"

    ledger.close()
    event_ledger.close()
    ledger2.close()
    event_ledger2.close()


# -- run_price_monitor_cycle() ---------------------------------------------------


def test_cycle_with_no_confirmed_pairs_is_a_clean_no_op(tmp_path) -> None:
    sku_map = SkuMap(tmp_path / "sku_map")
    ledger = PriceLedger(tmp_path / "ledger")
    event_ledger = EventLedger(tmp_path / "events.sqlite3")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("no confirmed pairs -- the network must never be touched")

    report = run_price_monitor_cycle(
        sku_map=sku_map, ledger=ledger, event_ledger=event_ledger, http_client=_client(handler),
        meli_domain=MELI_TEST_DOMAIN, now=NOW,
    )

    assert report.pairs_checked == 0
    assert report.outcomes == ()
    assert report.events == ()
    assert "no confirmed" in report.summary.lower()
    sku_map.close()
    ledger.close()
    event_ledger.close()


def test_cycle_against_the_real_meli_domain_is_honestly_skipped_never_silently_dropped(tmp_path) -> None:
    """The real api.mercadolibre.com domain is recorded PROHIBITED (see
    config/sites/api.mercadolibre.com.yaml) -- a confirmed pair against it
    must be reported, not vanish from the cycle (golden rule 14)."""
    sku_map = SkuMap(tmp_path / "sku_map")
    _seed_confirmed_pair(sku_map, site="api.mercadolibre.com")
    ledger = PriceLedger(tmp_path / "ledger")
    event_ledger = EventLedger(tmp_path / "events.sqlite3")

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("a prohibited domain must never be fetched")

    report = run_price_monitor_cycle(
        sku_map=sku_map, ledger=ledger, event_ledger=event_ledger, http_client=_client(handler),
        meli_domain="api.mercadolibre.com", now=NOW,
    )

    assert report.pairs_checked == 1
    assert report.outcomes[0].status == "skipped"
    assert "site_not_approved" in report.outcomes[0].reason
    sku_map.close()
    ledger.close()
    event_ledger.close()


def test_cycle_happy_path_accepts_a_price_change_and_emits_price_move(tmp_path) -> None:
    sku_map = SkuMap(tmp_path / "sku_map")
    _seed_confirmed_pair(sku_map)
    ledger = PriceLedger(tmp_path / "ledger")
    _seed_previous_offer(ledger, price="100.00")
    event_ledger = EventLedger(tmp_path / "events.sqlite3")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/items/{MELI_ITEM_ID}"
        return httpx.Response(200, json=_meli_item_json(price=85.00))

    report = run_price_monitor_cycle(
        sku_map=sku_map, ledger=ledger, event_ledger=event_ledger, http_client=_client(handler),
        meli_domain=MELI_TEST_DOMAIN, now=NOW,
    )

    assert report.pairs_checked == 1
    outcome = report.outcomes[0]
    assert outcome.status == "accepted"
    assert outcome.site == MELI_TEST_DOMAIN
    assert outcome.matched_product_id == "SKU-100"
    assert len(outcome.events) == 1
    price_move = outcome.events[0]
    assert price_move.type == "price_move"
    assert price_move.payload["old_price_normalized"] == "100.00"
    assert Decimal(price_move.payload["new_price_normalized"]) == Decimal("85.00")
    assert Decimal(price_move.payload["delta_pct"]) == Decimal("-0.15")
    assert report.events == (price_move,)
    assert "1 accepted" in report.summary

    record = ledger.latest_by_sku(MELI_TEST_DOMAIN, MELI_ITEM_ID)
    assert record is not None
    assert record.offer.price == Decimal("85")
    assert record.offer.acquisition_tier == "L0"

    sku_map.close()
    ledger.close()
    event_ledger.close()


def test_cycle_out_of_stock_transition_emits_competitor_oos(tmp_path) -> None:
    sku_map = SkuMap(tmp_path / "sku_map")
    _seed_confirmed_pair(sku_map)
    ledger = PriceLedger(tmp_path / "ledger")
    _seed_previous_offer(ledger, price="100.00", availability="InStock")
    event_ledger = EventLedger(tmp_path / "events.sqlite3")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=_meli_item_json(price=100.00, status="paused", available_quantity=0))

    report = run_price_monitor_cycle(
        sku_map=sku_map, ledger=ledger, event_ledger=event_ledger, http_client=_client(handler),
        meli_domain=MELI_TEST_DOMAIN, now=NOW,
    )

    events_by_type = {e.type for e in report.events}
    assert "competitor_oos" in events_by_type
    sku_map.close()
    ledger.close()
    event_ledger.close()


def test_cycle_extraction_failure_emits_extraction_failed_never_crashes(tmp_path) -> None:
    sku_map = SkuMap(tmp_path / "sku_map")
    _seed_confirmed_pair(sku_map)
    ledger = PriceLedger(tmp_path / "ledger")
    event_ledger = EventLedger(tmp_path / "events.sqlite3")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="{not valid json")

    report = run_price_monitor_cycle(
        sku_map=sku_map, ledger=ledger, event_ledger=event_ledger, http_client=_client(handler),
        meli_domain=MELI_TEST_DOMAIN, now=NOW,
    )

    assert report.outcomes[0].status == "skipped"
    assert report.outcomes[0].reason == "extraction_failed"
    recorded = event_ledger.list_by_type("extraction_failed")
    assert len(recorded) == 1
    sku_map.close()
    ledger.close()
    event_ledger.close()


def test_cycle_repeated_blocking_trips_breaker_and_a_later_pair_is_circuit_open(tmp_path) -> None:
    sku_map = SkuMap(tmp_path / "sku_map")
    for i in range(4):
        _seed_confirmed_pair(sku_map, our_product_id=f"SKU-{i}", competitor_sku_ref=f"MLA{i:010d}")
    ledger = PriceLedger(tmp_path / "ledger")
    event_ledger = EventLedger(tmp_path / "events.sqlite3")

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(403, text="blocked")

    report = run_price_monitor_cycle(
        sku_map=sku_map, ledger=ledger, event_ledger=event_ledger, http_client=_client(handler),
        meli_domain=MELI_TEST_DOMAIN, now=NOW,
    )

    # default CircuitBreaker.for_site failure_threshold=3: the first 3 pairs
    # each get a real (mocked) 403 attempt and trip the breaker on the 3rd;
    # the 4th pair never reaches the network at all.
    statuses = [o.reason for o in report.outcomes]
    assert statuses[:3] == ["blocked:blocked_403"] * 3
    assert statuses[3] == "circuit_open"

    degraded = event_ledger.list_by_type("site_degraded")
    assert len(degraded) == 1
    sku_map.close()
    ledger.close()
    event_ledger.close()


def test_cycle_is_batch_degradable_via_job_registry_run_once(tmp_path, monkeypatch) -> None:
    """Golden rule 9: no daemon, no sleeping -- JobRegistry.run_once() calls
    the exact same synchronous function PRICE_MONITOR_JOB registers.
    Redirects the module's default-singleton getters to isolated tmp_path
    instances (same monkeypatch idiom tests/test_digest_job.py uses for
    jobs.digest_job's own EventLedger default) so this test never touches
    the real data/pricing_intel/* paths -- default_ledger()/default_sku_map()
    are process-wide CACHED singletons (see run_price_monitor_cycle's own
    docstring), so an env-var override alone would be too late here: their
    DEFAULT_BASE_PATH is already bound at import time, long before this
    test runs."""
    import jobs.price_monitor as price_monitor_module

    monkeypatch.setattr(price_monitor_module, "default_sku_map", lambda: SkuMap(tmp_path / "sku_map"))
    monkeypatch.setattr(price_monitor_module, "default_ledger", lambda: PriceLedger(tmp_path / "ledger"))
    monkeypatch.setattr(price_monitor_module, "EventLedger", lambda: EventLedger(tmp_path / "events.sqlite3"))

    registry = JobRegistry()
    registry.register(PRICE_MONITOR_JOB)
    assert PRICE_MONITOR_JOB.id == "price_monitor_cycle"
    assert PRICE_MONITOR_JOB.trigger_args == {"hours": DEFAULT_CADENCE_HOURS}

    result = registry.run_once("price_monitor_cycle")
    report = result["price_monitor_cycle"]
    assert report.pairs_checked == 0  # no sku_map seeded via the isolated tmp_path store above


# -- end to end: a synthetic price_move Event routes through handle_event() -----


def _test_orchestrator() -> Orchestrator:
    return Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback(), clients_root=None)


def test_handle_event_routes_a_synthetic_price_move_event_end_to_end(tmp_path, monkeypatch) -> None:
    """A price_move Event (this PR's own emission shape) -> routed via
    config/event_routing.yaml -> price_intel_refresh_from_event -> the REAL
    price_intelligence tool -> a QA-gated JobResult. Uses an html_path
    fixture (no network) so the refreshed report actually has something to
    accept and passes QA -- same "html_path, no real fetch" idiom
    tests/test_price_intelligence_job.py itself uses."""
    monkeypatch.setattr(event_intent_module, "notify", lambda message, **kwargs: True)

    html_path = str(FIXTURES / "jsonld_clean.html")
    event = Event(
        type="price_move", severity="medium", source="jobs.price_monitor",
        dedup_key="price_move:example-retailer.test:https://example-retailer.test/p/aw-3000:x",
        sku="SKU-100",
        payload={
            "site": "example-retailer.test",
            "competitor_sku_ref": "https://example-retailer.test/p/aw-3000",
            "matched_product_id": "SKU-100",
            "html_path": html_path,
            "old_price_normalized": "180.00",
            "new_price_normalized": "199.99",
            "delta_pct": "0.111",
        },
    )

    routed = handle_event(event, routing_path=DEFAULT_ROUTING_PATH, orchestrator=_test_orchestrator(), out_dir=tmp_path)

    assert routed.route.tool == "price_intelligence"
    assert routed.route.autonomy_tier == "T2"
    assert routed.result.status == STATUS_OK
    assert routed.result.qa_issues == []


def test_handle_event_routes_a_synthetic_competitor_oos_event_end_to_end(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(event_intent_module, "notify", lambda message, **kwargs: True)
    html_path = str(FIXTURES / "jsonld_clean.html")
    event = Event(
        type="competitor_oos", severity="medium", source="jobs.price_monitor",
        dedup_key="competitor_oos:example-retailer.test:https://example-retailer.test/p/aw-3000",
        sku="SKU-100",
        payload={
            "site": "example-retailer.test",
            "competitor_sku_ref": "https://example-retailer.test/p/aw-3000",
            "matched_product_id": "SKU-100",
            "html_path": html_path,
        },
    )

    routed = handle_event(event, routing_path=DEFAULT_ROUTING_PATH, orchestrator=_test_orchestrator(), out_dir=tmp_path)

    assert routed.route.tool == "price_intelligence"
    assert routed.result.status == STATUS_OK
