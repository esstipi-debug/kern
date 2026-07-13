"""Tests for src/pricing_intel/sanity.py (Linchpin 3.0 PR-12, plan S6.6).

One test per quarantine/discard rule, each with a concrete, hand-verifiable
before/after example (see sanity.py's own docstrings for the worked-by-hand
numbers these tests assert against):
  1. basic validity: invalid price / unknown currency / contradictory
     availability all discard with an event; a clean candidate accepts.
  2. intraday delta: a >40% jump without promo_flag quarantines; the SAME
     jump WITH promo_flag=True does not.
  3. MAD outlier over a small hand-computable trailing window.
  4. staleness: past 2x SLA emits stale_feed; not yet past it does not.
  5. to_competitor_offer: the final assembly step and its own defense-in-depth.

Event-ledger dedup is exercised once (basic validity) to prove sanity.py
follows scm_agent/monitors.py's exact "._emit_one" convention rather than a
parallel mechanism.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from scm_agent.events import EventLedger
from src.pricing_intel.models import CompetitorOffer
from src.pricing_intel.sanity import (
    CONFIRMATION_WINDOW,
    RawOfferCandidate,
    SanityStatus,
    check_basic_validity,
    check_intraday_delta,
    check_mad_outlier,
    check_staleness,
    resolve_pending_confirmation,
    to_competitor_offer,
)

OBSERVED_AT = datetime(2026, 7, 12, 12, 0, tzinfo=timezone.utc)


def _candidate(**overrides: object) -> RawOfferCandidate:
    defaults: dict[str, object] = dict(
        observed_at=OBSERVED_AT,
        site="example-retailer.test",
        competitor_sku_ref="https://example-retailer.test/p/123",
        matched_product_id="SKU-1",
        match_confidence=0.95,
        price=Decimal("19.99"),
        currency="USD",
        price_normalized=Decimal("19.99"),
        shipping=None,
        availability="InStock",
        promo_flag=False,
        list_price=None,
        acquisition_tier="L1",
        extractor="structured:extruct",
        extractor_version="0.18.0",
        extraction_confidence=0.98,
        availability_conflict=False,
    )
    defaults.update(overrides)
    return RawOfferCandidate(**defaults)  # type: ignore[arg-type]


# -- rule 1: basic validity ---------------------------------------------------


def test_basic_validity_accepts_a_clean_candidate() -> None:
    verdict = check_basic_validity(_candidate())
    assert verdict.status == SanityStatus.ACCEPT
    assert verdict.reason == "basic_validity_passed"
    assert verdict.event is None


def test_basic_validity_discards_zero_or_negative_price() -> None:
    zero = check_basic_validity(_candidate(price=Decimal("0")))
    negative = check_basic_validity(_candidate(price=Decimal("-5.00")))
    for verdict in (zero, negative):
        assert verdict.status == SanityStatus.DISCARD
        assert verdict.reason == "invalid_price"
        assert verdict.event is not None
        assert verdict.event.type == "offer_discarded"
        assert verdict.event.payload["reason"] == "invalid_price"


def test_basic_validity_discards_unknown_currency() -> None:
    verdict = check_basic_validity(_candidate(currency="XYZ"))
    assert verdict.status == SanityStatus.DISCARD
    assert verdict.reason == "unknown_currency"
    assert verdict.event.payload["currency"] == "XYZ"


def test_basic_validity_discards_contradictory_availability() -> None:
    verdict = check_basic_validity(_candidate(availability_conflict=True))
    assert verdict.status == SanityStatus.DISCARD
    assert verdict.reason == "contradictory_availability"


def test_basic_validity_dedups_repeat_discard_through_shared_ledger() -> None:
    ledger = EventLedger(":memory:", dedup_window_seconds=3600.0)
    candidate = _candidate(price=Decimal("-1"))

    first = check_basic_validity(candidate, ledger=ledger)
    second = check_basic_validity(candidate, ledger=ledger)

    # Both calls still DISCARD the data -- dedup only suppresses the
    # repeat NOTIFICATION, never the data-quality outcome itself.
    assert first.status == SanityStatus.DISCARD
    assert second.status == SanityStatus.DISCARD
    assert first.event is not None  # newly recorded
    assert second.event is None  # same dedup_key inside the window -- suppressed
    assert len(ledger.list_all()) == 1


# -- rule 2: intraday delta ---------------------------------------------------


def test_intraday_delta_quarantines_a_45_percent_jump_without_promo_flag() -> None:
    candidate = _candidate(price_normalized=Decimal("145"), promo_flag=False)
    verdict = check_intraday_delta(candidate, Decimal("100"))
    assert verdict.status == SanityStatus.QUARANTINE
    assert verdict.reason == "intraday_delta_unconfirmed"
    assert verdict.pending is not None
    assert verdict.pending.delta_pct == Decimal("45") / Decimal("100")
    assert verdict.pending.confirm_by == OBSERVED_AT + CONFIRMATION_WINDOW
    assert verdict.event.type == "offer_quarantined"


def test_intraday_delta_same_45_percent_jump_with_promo_flag_accepts() -> None:
    candidate = _candidate(price_normalized=Decimal("145"), promo_flag=True)
    verdict = check_intraday_delta(candidate, Decimal("100"))
    assert verdict.status == SanityStatus.ACCEPT
    assert verdict.reason == "large_delta_promo_flagged"
    assert verdict.pending is None


def test_intraday_delta_35_percent_jump_is_within_threshold() -> None:
    candidate = _candidate(price_normalized=Decimal("135"), promo_flag=False)
    verdict = check_intraday_delta(candidate, Decimal("100"))
    assert verdict.status == SanityStatus.ACCEPT
    assert verdict.reason == "within_delta_threshold"


def test_intraday_delta_with_no_previous_reading_accepts() -> None:
    candidate = _candidate(price_normalized=Decimal("145"))
    verdict = check_intraday_delta(candidate, None)
    assert verdict.status == SanityStatus.ACCEPT
    assert verdict.reason == "no_previous_reading"


def test_resolve_pending_confirmation_accepts_when_second_read_confirms() -> None:
    candidate = _candidate(price_normalized=Decimal("145"), promo_flag=False)
    pending = check_intraday_delta(candidate, Decimal("100")).pending
    assert pending is not None

    verdict = resolve_pending_confirmation(pending, Decimal("144"), OBSERVED_AT + timedelta(minutes=30))
    assert verdict.status == SanityStatus.ACCEPT
    assert verdict.reason == "intraday_delta_confirmed"


def test_resolve_pending_confirmation_discards_when_second_read_reverts() -> None:
    candidate = _candidate(price_normalized=Decimal("145"), promo_flag=False)
    pending = check_intraday_delta(candidate, Decimal("100")).pending
    assert pending is not None

    # The second read reverts back toward the OLD price -- the "jump" was a
    # transient glitch, not a real price change.
    verdict = resolve_pending_confirmation(pending, Decimal("101"), OBSERVED_AT + timedelta(minutes=30))
    assert verdict.status == SanityStatus.DISCARD
    assert verdict.reason == "intraday_delta_not_confirmed"


def test_resolve_pending_confirmation_discards_when_window_expires() -> None:
    candidate = _candidate(price_normalized=Decimal("145"), promo_flag=False)
    pending = check_intraday_delta(candidate, Decimal("100")).pending
    assert pending is not None

    # Confirmatory read arrives 2h later -- past the 1h confirmation window.
    verdict = resolve_pending_confirmation(pending, Decimal("145"), OBSERVED_AT + timedelta(hours=2))
    assert verdict.status == SanityStatus.DISCARD
    assert verdict.reason == "intraday_delta_confirmation_expired"


# -- rule 3: MAD outlier -------------------------------------------------------

# Hand-computed: sorted [98, 99, 100, 100, 101, 101, 102] -> median = 100;
# abs deviations sorted [0, 0, 1, 1, 1, 2, 2] -> MAD = 1.
_TRAILING_WINDOW = [Decimal(v) for v in ("100", "101", "99", "102", "98", "100", "101")]


def test_mad_outlier_flags_a_price_20_mad_z_scores_away() -> None:
    candidate = _candidate(price_normalized=Decimal("130"))
    verdict = check_mad_outlier(candidate, _TRAILING_WINDOW)
    assert verdict.status == SanityStatus.QUARANTINE
    assert verdict.reason == "mad_outlier"
    assert verdict.event.payload["window_median"] == "100"
    assert verdict.event.payload["window_mad"] == "1"


def test_mad_outlier_accepts_a_price_within_threshold() -> None:
    candidate = _candidate(price_normalized=Decimal("101"))
    verdict = check_mad_outlier(candidate, _TRAILING_WINDOW)
    assert verdict.status == SanityStatus.ACCEPT
    assert verdict.reason == "within_mad_threshold"


def test_mad_outlier_accepts_with_insufficient_history() -> None:
    candidate = _candidate(price_normalized=Decimal("999"))
    verdict = check_mad_outlier(candidate, _TRAILING_WINDOW[:3])
    assert verdict.status == SanityStatus.ACCEPT
    assert verdict.reason == "insufficient_history"


def test_mad_outlier_zero_variance_window() -> None:
    flat_window = [Decimal("100")] * 6
    matches = check_mad_outlier(_candidate(price_normalized=Decimal("100")), flat_window)
    deviates = check_mad_outlier(_candidate(price_normalized=Decimal("105")), flat_window)
    assert matches.status == SanityStatus.ACCEPT
    assert matches.reason == "matches_stable_history"
    assert deviates.status == SanityStatus.QUARANTINE
    assert deviates.reason == "mad_outlier_zero_variance"


# -- rule 4: staleness ---------------------------------------------------------


def test_staleness_emits_stale_feed_past_2x_sla() -> None:
    now = OBSERVED_AT + timedelta(hours=13)
    event = check_staleness(
        site="example-retailer.test",
        competitor_sku_ref="https://example-retailer.test/p/123",
        matched_product_id="SKU-1",
        last_observed_at=OBSERVED_AT,
        sla_hours=6.0,
        now=now,
    )
    assert event is not None
    assert event.type == "stale_feed"
    assert event.payload["hours_since_last_observed"] == pytest.approx(13.0)
    assert event.payload["threshold_hours"] == pytest.approx(12.0)


def test_staleness_does_not_fire_before_2x_sla() -> None:
    now = OBSERVED_AT + timedelta(hours=10)
    event = check_staleness(
        site="example-retailer.test",
        competitor_sku_ref="https://example-retailer.test/p/123",
        matched_product_id="SKU-1",
        last_observed_at=OBSERVED_AT,
        sla_hours=6.0,
        now=now,
    )
    assert event is None


def test_staleness_rejects_nonpositive_sla() -> None:
    with pytest.raises(ValueError):
        check_staleness(
            site="s",
            competitor_sku_ref="ref",
            matched_product_id=None,
            last_observed_at=OBSERVED_AT,
            sla_hours=0,
            now=OBSERVED_AT,
        )


# -- final assembly ------------------------------------------------------------


def test_to_competitor_offer_builds_a_valid_offer() -> None:
    offer = to_competitor_offer(_candidate())
    assert isinstance(offer, CompetitorOffer)
    assert offer.price == Decimal("19.99")
    assert offer.currency == "USD"
    assert offer.site == "example-retailer.test"


def test_to_competitor_offer_rejects_a_candidate_missing_price() -> None:
    candidate = replace(_candidate(), price=None)
    with pytest.raises(ValueError):
        to_competitor_offer(candidate)
