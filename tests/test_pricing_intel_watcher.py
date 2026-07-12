"""Tests for src/pricing_intel/acquire/watcher.py (Linchpin 3.0 PR-15).

No network I/O in this module at all (a webhook receiver PARSES a POST body
someone else's server sent -- there is nothing to mock). Guarantees under
test:
- a realistic changedetection.io "Re-stock & Price detection" webhook
  payload (this module's own documented JSON contract) parses into a
  correct RawOfferCandidate (hand-verified price/currency/site);
- 'watch_url' becomes 'site' via normalize_domain and 'competitor_sku_ref'
  verbatim;
- in_stock=false resolves OutOfStock; in_stock omitted (None) defaults
  InStock (documented business assumption, same as jobs/price_intelligence.py);
- notification_timestamp (unix epoch) resolves observed_at; a missing one
  falls back to `now`;
- missing/invalid watch_url, price, or currency each raise
  ChangeDetectionWebhookError with an actionable message, never a
  fabricated observation.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pytest

from src.pricing_intel.acquire.watcher import (
    CHANGEDETECTION_ADAPTER_VERSION,
    CHANGEDETECTION_CONFIDENCE,
    CHANGEDETECTION_EXTRACTOR,
    ChangeDetectionWebhookError,
    parse_changedetection_webhook,
)
from src.pricing_intel.sanity import RawOfferCandidate

FIXED_NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)

_REALISTIC_PAYLOAD = {
    "uuid": "a1b2c3d4-0000-0000-0000-000000000000",
    "watch_url": "https://www.competitor-shop.example.com/p/widget-9000",
    "watch_title": "Widget 9000",
    "price": "199.99",
    "previous_price": "219.99",
    "currency": "USD",
    "in_stock": True,
    "notification_timestamp": 1783814400,  # 2026-07-12T00:00:00Z
}


def test_parses_a_realistic_payload_into_a_correct_raw_offer_candidate() -> None:
    candidate = parse_changedetection_webhook(_REALISTIC_PAYLOAD, matched_product_id="SKU-100")
    assert isinstance(candidate, RawOfferCandidate)
    assert candidate.site == "competitor-shop.example.com"  # www. stripped, via normalize_domain
    assert candidate.competitor_sku_ref == "https://www.competitor-shop.example.com/p/widget-9000"
    assert candidate.price == Decimal("199.99")
    assert candidate.currency == "USD"
    assert candidate.availability == "InStock"
    assert candidate.acquisition_tier == "L2"
    assert candidate.extractor == CHANGEDETECTION_EXTRACTOR
    assert candidate.extractor_version == CHANGEDETECTION_ADAPTER_VERSION
    assert candidate.extraction_confidence == CHANGEDETECTION_CONFIDENCE
    assert candidate.matched_product_id == "SKU-100"
    assert candidate.observed_at == datetime(2026, 7, 12, 0, 0, 0, tzinfo=timezone.utc)


def test_matched_product_id_defaults_to_none_when_unmatched() -> None:
    candidate = parse_changedetection_webhook(_REALISTIC_PAYLOAD)
    assert candidate.matched_product_id is None


def test_in_stock_false_resolves_out_of_stock() -> None:
    payload = {**_REALISTIC_PAYLOAD, "in_stock": False}
    candidate = parse_changedetection_webhook(payload)
    assert candidate.availability == "OutOfStock"


def test_in_stock_omitted_defaults_in_stock() -> None:
    payload = {k: v for k, v in _REALISTIC_PAYLOAD.items() if k != "in_stock"}
    candidate = parse_changedetection_webhook(payload)
    assert candidate.availability == "InStock"


def test_missing_notification_timestamp_falls_back_to_now() -> None:
    payload = {k: v for k, v in _REALISTIC_PAYLOAD.items() if k != "notification_timestamp"}
    candidate = parse_changedetection_webhook(payload, now=FIXED_NOW)
    assert candidate.observed_at == FIXED_NOW


def test_unparseable_notification_timestamp_falls_back_to_now() -> None:
    payload = {**_REALISTIC_PAYLOAD, "notification_timestamp": "not-a-number"}
    candidate = parse_changedetection_webhook(payload, now=FIXED_NOW)
    assert candidate.observed_at == FIXED_NOW


def test_missing_watch_url_raises() -> None:
    payload = {k: v for k, v in _REALISTIC_PAYLOAD.items() if k != "watch_url"}
    with pytest.raises(ChangeDetectionWebhookError, match="watch_url"):
        parse_changedetection_webhook(payload)


def test_non_url_watch_url_raises() -> None:
    payload = {**_REALISTIC_PAYLOAD, "watch_url": "not-a-url"}
    with pytest.raises(ChangeDetectionWebhookError, match="watch_url"):
        parse_changedetection_webhook(payload)


@pytest.mark.parametrize("missing_price", [None, ""])
def test_missing_or_empty_price_raises(missing_price) -> None:
    payload = {**_REALISTIC_PAYLOAD, "price": missing_price}
    with pytest.raises(ChangeDetectionWebhookError, match="price"):
        parse_changedetection_webhook(payload)


def test_missing_currency_raises() -> None:
    payload = {k: v for k, v in _REALISTIC_PAYLOAD.items() if k != "currency"}
    with pytest.raises(ChangeDetectionWebhookError, match="currency"):
        parse_changedetection_webhook(payload)


def test_unparseable_price_raises() -> None:
    payload = {**_REALISTIC_PAYLOAD, "price": "not-a-number"}
    with pytest.raises(ChangeDetectionWebhookError):
        parse_changedetection_webhook(payload)


def test_non_dict_payload_raises() -> None:
    with pytest.raises(ChangeDetectionWebhookError, match="JSON object"):
        parse_changedetection_webhook(["not", "a", "dict"])  # type: ignore[arg-type]


def test_promo_flag_and_list_price_are_never_fabricated() -> None:
    # This contract carries no list/regular-price signal (see module
    # docstring) -- promo_flag/list_price must never be guessed.
    candidate = parse_changedetection_webhook(_REALISTIC_PAYLOAD)
    assert candidate.promo_flag is False
    assert candidate.list_price is None
