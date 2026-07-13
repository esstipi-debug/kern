"""Tests for src/pricing_intel/acquire/base.py (Linchpin 3.0 PR-12): the
Fetcher protocol, the per-domain compliance gate (plan S6.7), and the
circuit breaker (plan S6.6 rule 5).

Guarantees under test:
- a domain with NO config/sites/<domain>.yaml is refused outright
  (SiteNotConfiguredError) -- the hard "sin YAML aprobado, el fetcher se
  niega a correr" gate, never a silent proceed;
- a domain with a config whose tos_decision is "prohibited" is refused too,
  but with a DIFFERENT, more specific exception (SiteNotApprovedError);
- an approved domain's SiteConfig loads with the right fields;
- a blocking-signal simulation (three consecutive 403s) trips the breaker,
  degrades its effective tier (L3 config -> L2 effective), and short-circuits
  the next fetch attempt (allow_request returns False) rather than letting a
  caller hammer the blocking site;
- the breaker only allows a single HALF_OPEN probe after cooldown, and a
  success does NOT auto-restore the degraded tier.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from src.pricing_intel.acquire.base import (
    CircuitBreaker,
    CircuitBreakerState,
    Fetcher,
    RawObservation,
    SiteNotApprovedError,
    SiteNotConfiguredError,
    classify_blocking_signal,
    load_site_config,
    normalize_domain,
    require_approved_site,
)

SITES_DIR = "config/sites"
NOW = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)


# -- Fetcher protocol ----------------------------------------------------------


class _FakeFetcher:
    domain = "example-retailer.test"
    tier = "L1"

    def fetch(self, sku_ref: str) -> RawObservation:
        return RawObservation(sku_ref=sku_ref, fetched_at=NOW, status_code=200, html="<html></html>")


def test_fetcher_protocol_is_satisfied_by_duck_typed_implementation() -> None:
    assert isinstance(_FakeFetcher(), Fetcher)


# -- per-domain compliance gate (plan S6.7) -----------------------------------


def test_unconfigured_domain_is_refused_outright() -> None:
    with pytest.raises(SiteNotConfiguredError):
        require_approved_site("this-domain-has-no-config.test", config_dir=SITES_DIR)


def test_approved_domain_loads_and_is_approved() -> None:
    config = require_approved_site("example-retailer.test", config_dir=SITES_DIR)
    assert config.domain == "example-retailer.test"
    assert config.tos_decision == "limited"
    assert config.max_tier_allowed == "L2"
    assert config.is_approved is True


def test_prohibited_domain_is_configured_but_not_approved() -> None:
    # Configured (loads fine) but explicitly refused -- a different, more
    # specific failure mode than "no config at all".
    config = load_site_config("example-blocked.test", config_dir=SITES_DIR)
    assert config.is_approved is False

    with pytest.raises(SiteNotApprovedError):
        require_approved_site("example-blocked.test", config_dir=SITES_DIR)


def test_domain_filename_mismatch_is_rejected(tmp_path: Path) -> None:
    (tmp_path / "mismatch.test.yaml").write_text(
        "domain: not-the-filename.test\n"
        "robots_txt_respected: true\n"
        "robots_checked_at: '2026-07-01'\n"
        "tos_summary: 'x'\n"
        "tos_decision: limited\n"
        "rate_limit_seconds: 1.0\n"
        "max_tier_allowed: L1\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_site_config("mismatch.test", config_dir=tmp_path)


def test_unsafe_domain_string_is_rejected_before_touching_disk(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        load_site_config("../../etc/passwd", config_dir=tmp_path)


# -- classify_blocking_signal --------------------------------------------------


@pytest.mark.parametrize(
    "kwargs,expected",
    [
        ({"status_code": 403}, "blocked_403"),
        ({"status_code": 429}, "blocked_429"),
        ({"html": "   \n  "}, "empty_dom"),
        ({"html": "<html>Please verify you are human</html>"}, "captcha_detected"),
        ({"identical_price_streak": True}, "identical_price_streak"),
        ({"status_code": 200, "html": "<html>all good</html>"}, None),
        ({"status_code": 500}, None),  # ordinary transient failure -- NOT a blocking signal
    ],
)
def test_classify_blocking_signal(kwargs: dict, expected: str | None) -> None:
    assert classify_blocking_signal(**kwargs) == expected


# -- circuit breaker (plan S6.6 rule 5) ---------------------------------------


def test_breaker_trips_after_threshold_and_degrades_tier() -> None:
    breaker = CircuitBreaker("example-retailer.test", "L3", failure_threshold=3, cooldown_seconds=900.0)
    assert breaker.state == CircuitBreakerState.CLOSED
    assert breaker.effective_tier == "L3"

    assert breaker.record_failure(reason="blocked_403", now=NOW) is None
    assert breaker.record_failure(reason="blocked_403", now=NOW) is None
    assert breaker.state == CircuitBreakerState.CLOSED  # not tripped yet (2 < threshold 3)

    event = breaker.record_failure(reason="blocked_403", now=NOW)
    assert event is not None
    assert event.type == "site_degraded"
    assert event.payload["previous_tier"] == "L3"
    assert event.payload["effective_tier"] == "L2"
    assert breaker.state == CircuitBreakerState.OPEN
    assert breaker.effective_tier == "L2"


def test_breaker_short_circuits_further_requests_while_open() -> None:
    breaker = CircuitBreaker("example-retailer.test", "L3", failure_threshold=1, cooldown_seconds=900.0)
    breaker.record_failure(reason="blocked_429", now=NOW)
    assert breaker.state == CircuitBreakerState.OPEN

    # Immediately after tripping -- still inside cooldown -- no network call
    # should even be attempted.
    assert breaker.allow_request(NOW + timedelta(seconds=1)) is False
    # Calling again doesn't change anything either (proves this is a check,
    # not a side-effecting "consume one retry" counter).
    assert breaker.allow_request(NOW + timedelta(seconds=2)) is False


def test_breaker_allows_exactly_one_half_open_probe_after_cooldown() -> None:
    breaker = CircuitBreaker("example-retailer.test", "L3", failure_threshold=1, cooldown_seconds=60.0)
    breaker.record_failure(reason="blocked_403", now=NOW)

    after_cooldown = NOW + timedelta(seconds=61)
    assert breaker.allow_request(after_cooldown) is True
    assert breaker.state == CircuitBreakerState.HALF_OPEN
    # A second check before the probe resolves allows nothing further.
    assert breaker.allow_request(after_cooldown + timedelta(seconds=1)) is False


def test_breaker_success_closes_but_does_not_restore_tier() -> None:
    breaker = CircuitBreaker("example-retailer.test", "L3", failure_threshold=1, cooldown_seconds=60.0)
    breaker.record_failure(reason="blocked_403", now=NOW)
    assert breaker.effective_tier == "L2"

    after_cooldown = NOW + timedelta(seconds=61)
    breaker.allow_request(after_cooldown)  # -> HALF_OPEN, probe allowed
    breaker.record_success()

    assert breaker.state == CircuitBreakerState.CLOSED
    assert breaker.consecutive_failures == 0
    assert breaker.effective_tier == "L2"  # NOT auto-restored to L3


def test_breaker_half_open_probe_failure_reopens_without_double_degrade() -> None:
    breaker = CircuitBreaker("example-retailer.test", "L3", failure_threshold=1, cooldown_seconds=60.0)
    breaker.record_failure(reason="blocked_403", now=NOW)
    assert breaker.effective_tier == "L2"

    after_cooldown = NOW + timedelta(seconds=61)
    breaker.allow_request(after_cooldown)  # -> HALF_OPEN
    event = breaker.record_failure(reason="blocked_403", now=after_cooldown)

    assert event is None  # re-opening a half-open probe is not a NEW trip
    assert breaker.state == CircuitBreakerState.OPEN
    assert breaker.effective_tier == "L2"  # unchanged -- one step, not two
    assert breaker.allow_request(after_cooldown + timedelta(seconds=1)) is False


def test_breaker_l0_has_nowhere_lower_to_degrade() -> None:
    breaker = CircuitBreaker("example-retailer.test", "L0", failure_threshold=1)
    breaker.record_failure(reason="blocked_403", now=NOW)
    assert breaker.effective_tier == "L0"


def test_breaker_rejects_invalid_construction_args() -> None:
    with pytest.raises(ValueError):
        CircuitBreaker("d", "L9")
    with pytest.raises(ValueError):
        CircuitBreaker("d", "L1", failure_threshold=0)
    with pytest.raises(ValueError):
        CircuitBreaker("d", "L1", cooldown_seconds=-1)


class _CountingFetcher:
    """A minimal Fetcher whose fetch() increments a counter -- used to prove
    a caller gated by CircuitBreaker.allow_request() never actually invokes
    the network call while the breaker is OPEN."""

    domain = "example-retailer.test"
    tier = "L1"

    def __init__(self) -> None:
        self.calls = 0

    def fetch(self, sku_ref: str) -> RawObservation:
        self.calls += 1
        return RawObservation(sku_ref=sku_ref, fetched_at=NOW, status_code=403, html="")


def test_gated_caller_never_touches_network_once_breaker_is_open() -> None:
    breaker = CircuitBreaker("example-retailer.test", "L3", failure_threshold=1, cooldown_seconds=900.0)
    fetcher = _CountingFetcher()

    def attempt(sku_ref: str, now: datetime) -> RawObservation | None:
        if not breaker.allow_request(now):
            return None  # short-circuited -- fetcher.fetch() is never called
        observation = fetcher.fetch(sku_ref)
        reason = classify_blocking_signal(status_code=observation.status_code, html=observation.html)
        if reason is not None:
            breaker.record_failure(reason=reason, now=now)
        else:
            breaker.record_success()
        return observation

    first = attempt("sku-1", NOW)
    assert first is not None
    assert fetcher.calls == 1
    assert breaker.state == CircuitBreakerState.OPEN

    # Three more attempts inside the cooldown window -- none should reach
    # the network.
    for i in range(3):
        result = attempt("sku-1", NOW + timedelta(seconds=i + 1))
        assert result is None
    assert fetcher.calls == 1  # still just the one real call that tripped it


# -- normalize_domain (Linchpin 3.0 PR-15) ---------------------------------------


def test_normalize_domain_strips_scheme_and_www() -> None:
    assert normalize_domain("https://www.shop.example.com/p/1") == "shop.example.com"


def test_normalize_domain_lowercases() -> None:
    assert normalize_domain("https://Shop.Example.COM/p/1") == "shop.example.com"


def test_normalize_domain_no_www_prefix_left_untouched() -> None:
    assert normalize_domain("http://shop.example.com") == "shop.example.com"


def test_normalize_domain_returns_none_for_a_bare_id() -> None:
    assert normalize_domain("MLA123456") is None


def test_normalize_domain_returns_none_for_non_http_scheme() -> None:
    assert normalize_domain("ftp://old.example.com") is None


def test_normalize_domain_returns_none_for_empty_string() -> None:
    assert normalize_domain("") is None


# -- the REAL MercadoLibre domain compliance record (Linchpin 3.0 PR-15) --------
#
# Proves the compliance gate actually holds for a real, currently-live
# domain -- not just synthetic .test fixtures. See
# config/sites/api.mercadolibre.com.yaml for the live robots.txt/403
# verification this record documents.


def test_real_meli_domain_is_recorded_as_not_approved() -> None:
    config = load_site_config("api.mercadolibre.com", config_dir=SITES_DIR)
    assert config.is_approved is False
    assert config.tos_decision == "prohibited"
    assert config.robots_txt_respected is False


def test_real_meli_domain_gate_refuses_to_run() -> None:
    with pytest.raises(SiteNotApprovedError):
        require_approved_site("api.mercadolibre.com", config_dir=SITES_DIR)


def test_synthetic_meli_test_fixture_is_approved_for_l0() -> None:
    config = require_approved_site("meli-api.test", config_dir=SITES_DIR)
    assert config.is_approved is True
    assert config.max_tier_allowed == "L0"
