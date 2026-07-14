"""Tests for ``src/pricing_intel/acquire/l1.py`` -- the shared L1 gate ->
tier -> breaker -> fetch -> classify -> extract prefix factored out of
``jobs/price_intelligence.py``'s ``_acquire_one`` and ``jobs/price_watch.py``'s
``_check_one_pair`` (final whole-branch review, Finding 2).

No real network call ever happens here -- every ``httpx.Client`` is built on
``httpx.MockTransport`` (the same convention every other pricing-intel test
file uses). ``SiteConfig``/``CircuitBreaker`` instances are pre-seeded
directly into the ``site_configs``/``breakers`` caches so these tests never
touch the repo's real ``config/sites`` directory (a fresh-resolution path is
covered separately via an isolated ``tmp_path`` config dir).

Guarantees under test:
- ``site=None`` skips straight to the id-ref reason, no cache/network touch;
- an unconfigured/unapproved domain resolves via ``require_approved_site``
  from a real (tmp-path) YAML file, cached across repeated calls;
- a tier above the domain's ``max_tier_allowed`` is skipped, never fetched;
- an open circuit breaker skips without touching the network;
- a 403/429/captcha/empty-DOM signal degrades the breaker (records failure,
  emits ``site_degraded`` once threshold is hit) and is NEVER retried within
  one call (NON-GOAL 1) -- a plain transport-level ``FetchError`` does NOT
  trip the breaker;
- a successful fetch with an unparseable page returns ``extraction_failed``
  with the raw ``attempts`` tuple, WITHOUT emitting any event itself (that
  stays the caller's job -- proven by asserting the passed ``EventLedger``
  received nothing from this call alone);
- a successful extraction returns an ``AcquiredOffer`` with a well-formed
  ``RawOfferCandidate`` (site/competitor_ref/matched_product_id/match_confidence
  carried through verbatim, tier always "L1");
- ``html_path`` bypasses the breaker/fetch entirely (no network call) but the
  tier-ceiling check still applies first;
- ``currency_hint`` is passed straight through to ``extract_price``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import httpx

from scm_agent.events import EventLedger
from src.pricing_intel.acquire import base
from src.pricing_intel.acquire.l1 import AcquiredOffer, AcquisitionSkipped, acquire_l1_offer
from src.pricing_intel.acquire.pdp_fetcher import USER_AGENT
from src.pricing_intel.models import SiteConfig

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pricing_intel"
NOW = datetime(2026, 7, 14, 12, 0, 0, tzinfo=timezone.utc)
SITE = "shop.example.test"
REF = "https://shop.example.test/p/1"


def _site_config(max_tier: str = "L1") -> SiteConfig:
    return SiteConfig(
        domain=SITE, robots_txt_respected=True, robots_checked_at="2026-07-01",
        tos_summary="test fixture", tos_decision="limited", rate_limit_seconds=1.0,
        max_tier_allowed=max_tier,
    )


def _seeded_caches(max_tier: str = "L1") -> tuple[dict, dict]:
    config = _site_config(max_tier)
    site_configs: dict[str, object] = {SITE: config}
    breakers: dict[str, base.CircuitBreaker] = {SITE: base.CircuitBreaker.for_site(config)}
    return site_configs, breakers


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def _jsonld_html() -> str:
    return (FIXTURES / "jsonld_clean.html").read_text(encoding="utf-8")


def _call(
    *,
    site: str | None = SITE,
    site_configs: dict | None = None,
    breakers: dict | None = None,
    handler=None,
    event_ledger: EventLedger | None = None,
    html_path: str | None = None,
    currency_hint: str | None = None,
    sites_config_dir: str | Path | None = None,
) -> AcquiredOffer | AcquisitionSkipped:
    if site_configs is None or breakers is None:
        site_configs, breakers = _seeded_caches()

    def _default_handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=_jsonld_html())

    client = _client(handler or _default_handler)
    try:
        return acquire_l1_offer(
            site=site, competitor_ref=REF, matched_product_id="SKU-1", match_confidence=1.0,
            client=client, now=NOW, site_configs=site_configs, breakers=breakers,
            sites_config_dir=sites_config_dir, event_ledger=event_ledger,
            html_path=html_path, currency_hint=currency_hint,
        )
    finally:
        client.close()


# -- id-only ref (price_intelligence-only case) -------------------------------


def test_site_none_skips_without_touching_cache_or_network():
    def must_not_run(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must never fetch when site is None")

    result = _call(site=None, handler=must_not_run)

    assert isinstance(result, AcquisitionSkipped)
    assert result.reason == "id_ref_requires_l0_api_not_yet_available"


# -- site gate + tier ceiling -------------------------------------------------


def test_unconfigured_domain_is_skipped_resolved_via_real_config_dir(tmp_path):
    def must_not_run(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must never fetch an unconfigured domain")

    result = _call(site_configs={}, breakers={}, handler=must_not_run, sites_config_dir=tmp_path)

    assert isinstance(result, AcquisitionSkipped)
    assert result.reason == "site_not_approved:SiteNotConfiguredError"


def test_configured_but_prohibited_domain_is_skipped(tmp_path):
    (tmp_path / f"{SITE}.yaml").write_text(
        "domain: shop.example.test\nrobots_txt_respected: true\n"
        "robots_checked_at: '2026-07-01'\ntos_summary: prohibited fixture\n"
        "tos_decision: prohibited\nrate_limit_seconds: 1.0\nmax_tier_allowed: L1\n",
        encoding="utf-8",
    )

    def must_not_run(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must never fetch a prohibited domain")

    result = _call(site_configs={}, breakers={}, handler=must_not_run, sites_config_dir=tmp_path)

    assert isinstance(result, AcquisitionSkipped)
    assert result.reason == "site_not_approved:SiteNotApprovedError"


def test_site_resolution_is_cached_across_repeated_calls(tmp_path):
    (tmp_path / f"{SITE}.yaml").write_text(
        "domain: shop.example.test\nrobots_txt_respected: true\n"
        "robots_checked_at: '2026-07-01'\ntos_summary: fixture\n"
        "tos_decision: limited\nrate_limit_seconds: 1.0\nmax_tier_allowed: L1\n",
        encoding="utf-8",
    )
    site_configs: dict = {}
    breakers: dict = {}

    first = _call(site_configs=site_configs, breakers=breakers, sites_config_dir=tmp_path)
    assert isinstance(first, AcquiredOffer)
    assert SITE in site_configs and SITE in breakers

    # Delete the file -- if the second call re-reads it, it would now fail;
    # a cached call must not touch disk again.
    (tmp_path / f"{SITE}.yaml").unlink()
    second = _call(site_configs=site_configs, breakers=breakers, sites_config_dir=tmp_path)
    assert isinstance(second, AcquiredOffer)


def test_tier_above_ceiling_is_skipped_never_fetched():
    site_configs, breakers = _seeded_caches(max_tier="L0")

    def must_not_run(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must never fetch a domain approved only to L0")

    result = _call(site_configs=site_configs, breakers=breakers, handler=must_not_run)

    assert isinstance(result, AcquisitionSkipped)
    assert result.reason == "tier_not_approved"


# -- circuit breaker -----------------------------------------------------------


def test_open_circuit_skips_without_touching_network():
    site_configs, breakers = _seeded_caches()
    breakers[SITE].record_failure(reason="blocked_403", now=NOW)
    breakers[SITE].record_failure(reason="blocked_403", now=NOW)
    breakers[SITE].record_failure(reason="blocked_403", now=NOW)  # trips at threshold=3

    def must_not_run(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must never fetch while the breaker is open")

    result = _call(site_configs=site_configs, breakers=breakers, handler=must_not_run)

    assert isinstance(result, AcquisitionSkipped)
    assert result.reason == "circuit_open"


def test_403_degrades_the_breaker_never_retried_within_one_call():
    site_configs, breakers = _seeded_caches()
    call_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(403, text="blocked")

    event_ledger = EventLedger(":memory:")
    for _ in range(3):  # default failure_threshold=3
        result = _call(site_configs=site_configs, breakers=breakers, handler=handler, event_ledger=event_ledger)
        assert isinstance(result, AcquisitionSkipped)
        assert result.reason == "blocked:blocked_403"

    assert call_count == 3  # exactly one attempt per call -- never a retry
    degraded = event_ledger.list_by_type("site_degraded")
    assert len(degraded) == 1
    assert degraded[0].payload["reason"] == "blocked_403"
    event_ledger.close()


def test_transport_failure_does_not_trip_the_breaker():
    site_configs, breakers = _seeded_caches()

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=request)

    result = _call(site_configs=site_configs, breakers=breakers, handler=handler)

    assert isinstance(result, AcquisitionSkipped)
    assert result.reason.startswith("fetch_error:")
    assert breakers[SITE].state == base.CircuitBreakerState.CLOSED
    assert breakers[SITE].consecutive_failures == 0


def test_non_200_status_is_skipped_as_fetch_failed():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="server error")

    result = _call(handler=handler)

    assert isinstance(result, AcquisitionSkipped)
    assert result.reason == "fetch_failed:status_500"


# -- extraction failure: no event emitted here --------------------------------


def test_extraction_failure_returns_attempts_and_emits_no_event_itself():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text=(FIXTURES / "no_price_anywhere.html").read_text(encoding="utf-8"))

    event_ledger = EventLedger(":memory:")
    result = _call(handler=handler, event_ledger=event_ledger)

    assert isinstance(result, AcquisitionSkipped)
    assert result.reason == "extraction_failed"
    assert result.extraction_attempts
    # The shared prefix never emits extraction_failed itself -- both callers
    # keep their own source/payload-shaped emission (see module docstring).
    assert event_ledger.list_by_type("extraction_failed") == []
    event_ledger.close()


# -- successful extraction ------------------------------------------------------


def test_successful_extraction_returns_a_well_formed_candidate():
    result = _call()

    assert isinstance(result, AcquiredOffer)
    candidate = result.candidate
    assert candidate.site == SITE
    assert candidate.competitor_sku_ref == REF
    assert candidate.matched_product_id == "SKU-1"
    assert candidate.match_confidence == 1.0
    assert candidate.price == Decimal("199.99")
    assert candidate.acquisition_tier == "L1"
    assert candidate.availability == "InStock"


def test_match_confidence_is_carried_through_verbatim():
    site_configs, breakers = _seeded_caches()
    client = _client(lambda request: httpx.Response(200, text=_jsonld_html()))
    try:
        result = acquire_l1_offer(
            site=SITE, competitor_ref=REF, matched_product_id="SKU-9", match_confidence=0.87,
            client=client, now=NOW, site_configs=site_configs, breakers=breakers,
        )
    finally:
        client.close()

    assert isinstance(result, AcquiredOffer)
    assert result.candidate.match_confidence == 0.87


def test_fetch_uses_the_identifiable_user_agent():
    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["ua"] = request.headers.get("user-agent")
        return httpx.Response(200, text=_jsonld_html())

    result = _call(handler=handler)

    assert isinstance(result, AcquiredOffer)
    assert captured["ua"] == USER_AGENT


# -- html_path bypass (price_intelligence-only case) --------------------------


def test_html_path_bypasses_network_entirely(tmp_path):
    html_file = tmp_path / "snapshot.html"
    html_file.write_text(_jsonld_html(), encoding="utf-8")

    def must_not_run(request: httpx.Request) -> httpx.Response:
        raise AssertionError("html_path must bypass the network entirely")

    result = _call(handler=must_not_run, html_path=str(html_file))

    assert isinstance(result, AcquiredOffer)
    assert result.candidate.price == Decimal("199.99")


def test_html_path_still_honors_the_tier_ceiling():
    site_configs, breakers = _seeded_caches(max_tier="L0")

    def must_not_run(request: httpx.Request) -> httpx.Response:
        raise AssertionError("must never reach html read past a tier refusal")

    result = _call(site_configs=site_configs, breakers=breakers, handler=must_not_run, html_path="/nonexistent.html")

    assert isinstance(result, AcquisitionSkipped)
    assert result.reason == "tier_not_approved"


def test_html_path_unreadable_is_skipped_honestly():
    result = _call(html_path=str(Path("does") / "not" / "exist.html"))

    assert isinstance(result, AcquisitionSkipped)
    assert result.reason.startswith("html_path_unreadable:")


# -- currency_hint passthrough -------------------------------------------------


def test_currency_hint_is_passed_through_to_extraction():
    # text_only.html states a price but NO currency -- tier 4 cannot resolve
    # a bare "$" without a hint (see test_pricing_intel_extract.py's own
    # test_all_tiers_fail_without_a_currency_hint...); with a hint supplied,
    # the extracted candidate's currency is exactly that hint, proving it
    # genuinely reaches extract_price rather than being ignored.
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            text=(FIXTURES / "text_only.html").read_text(encoding="utf-8"),
        )

    without_hint = _call(handler=handler)
    assert isinstance(without_hint, AcquisitionSkipped)
    assert without_hint.reason == "extraction_failed"

    with_hint = _call(handler=handler, currency_hint="EUR")
    assert isinstance(with_hint, AcquiredOffer)
    assert with_hint.candidate.currency == "EUR"
