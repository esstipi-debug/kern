"""Tests for src/pricing_intel/models.py (Linchpin 3.0 PR-10).

Guarantees under test:
- CompetitorOffer / PricePoint / MatchCandidate / SiteConfig reject invalid
  field values eagerly (fail fast at construction, nothing silently stored);
- offers_to_dataframe -> dataframe_to_offers round-trips every field exactly,
  including Decimal precision, through a real parquet file on disk;
- the same round trip holds through a plain CSV file (the no-parquet-engine
  fallback shape) -- the whole reason every storage column is a string;
- the price_normalized / BASE_CURRENCY convention is proven with one
  hand-verified FX computation;
- validate_offer_frame rejects a malformed bulk frame before anything is
  written.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal

import pandas as pd
import pytest

from src.pricing_intel.models import (
    ACQUISITION_TIERS,
    AVAILABILITY_VALUES,
    BASE_CURRENCY,
    OFFER_COLUMNS,
    CompetitorOffer,
    MatchCandidate,
    OfferFrameValidationError,
    PricePoint,
    SiteConfig,
    dataframe_to_offers,
    offers_to_dataframe,
    validate_offer_frame,
)


def _offer(**overrides: object) -> CompetitorOffer:
    fields: dict[str, object] = dict(
        observed_at=datetime(2026, 7, 12, 10, 30, 0, tzinfo=timezone.utc),
        site="example.com",
        competitor_sku_ref="https://example.com/p/123",
        matched_product_id="SKU-A",
        match_confidence=0.95,
        price=Decimal("19.99"),
        currency="USD",
        price_normalized=Decimal("19.99"),
        shipping=Decimal("4.50"),
        availability="InStock",
        promo_flag=False,
        list_price=Decimal("24.99"),
        acquisition_tier="L1",
        extractor="jsonld",
        extractor_version="extruct==0.18.0",
        extraction_confidence=0.98,
    )
    fields.update(overrides)
    return CompetitorOffer(**fields)


# -- CompetitorOffer construction / validation -------------------------------


def test_valid_offer_constructs_and_holds_the_given_fields():
    offer = _offer()
    assert offer.site == "example.com"
    assert offer.price == Decimal("19.99")
    assert offer.matched_product_id == "SKU-A"


def test_offer_with_none_optional_fields_is_allowed():
    offer = _offer(matched_product_id=None, shipping=None, list_price=None)
    assert offer.matched_product_id is None
    assert offer.shipping is None
    assert offer.list_price is None


@pytest.mark.parametrize(
    "overrides",
    [
        {"observed_at": datetime(2026, 7, 12, 10, 30, 0)},  # naive datetime, no tz
        {"site": ""},
        {"site": "https://example.com"},  # not a bare domain
        {"competitor_sku_ref": ""},
        {"matched_product_id": ""},  # "" is not a legal stand-in for None
        {"match_confidence": 1.5},
        {"match_confidence": -0.1},
        {"price": Decimal("0")},
        {"price": Decimal("-1")},
        {"price": 19.99},  # float, not Decimal
        {"currency": "usd"},  # must be uppercase
        {"currency": ""},
        {"price_normalized": Decimal("0")},
        {"shipping": Decimal("-1")},
        {"availability": "Backordered"},
        {"list_price": Decimal("0")},
        {"acquisition_tier": "L4"},
        {"extractor": ""},
        {"extractor_version": ""},
        {"extraction_confidence": 1.1},
    ],
)
def test_invalid_offer_fields_are_rejected(overrides):
    with pytest.raises((ValueError, TypeError)):
        _offer(**overrides)


def test_all_availability_and_tier_values_are_accepted():
    for availability in AVAILABILITY_VALUES:
        assert _offer(availability=availability).availability == availability
    for tier in ACQUISITION_TIERS:
        assert _offer(acquisition_tier=tier).acquisition_tier == tier


# -- golden round trip: parquet -----------------------------------------------


def test_offers_to_dataframe_round_trips_exactly_through_parquet(tmp_path):
    """Write known CompetitorOffer rows, reload, assert every field -- including
    Decimal precision -- comes back byte-identical, not just numerically equal."""
    offers = [
        _offer(
            competitor_sku_ref="ref-1",
            price=Decimal("19.99"),
            price_normalized=Decimal("19.99"),
            shipping=Decimal("0.00"),
        ),
        _offer(
            competitor_sku_ref="ref-2",
            price=Decimal("123.456"),  # deliberately a different scale than ref-1
            price_normalized=Decimal("123.456"),
            shipping=None,
            matched_product_id=None,
            list_price=None,
            promo_flag=True,
        ),
    ]
    frame = offers_to_dataframe(offers)
    path = tmp_path / "offers.parquet"
    frame.to_parquet(path, index=False)
    back = pd.read_parquet(path)
    reloaded = dataframe_to_offers(back)

    assert reloaded == offers  # frozen dataclass equality: every field matches
    # explicitly nail the Decimal-precision claim: repr, not just ==
    assert repr(reloaded[0].price) == repr(Decimal("19.99")) == "Decimal('19.99')"
    assert repr(reloaded[1].price) == repr(Decimal("123.456")) == "Decimal('123.456')"
    assert reloaded[1].shipping is None
    assert reloaded[1].matched_product_id is None


def test_offers_to_dataframe_round_trips_exactly_through_csv(tmp_path):
    """The no-parquet-engine fallback shape: a plain CSV file. This is the whole
    reason offers_to_dataframe encodes every field as a string -- pandas'
    default numeric-dtype inference on read_csv would otherwise silently
    truncate a Decimal-as-string column to float64."""
    offers = [_offer(competitor_sku_ref="ref-1"), _offer(competitor_sku_ref="ref-2", shipping=None)]
    frame = offers_to_dataframe(offers)
    path = tmp_path / "offers.csv"
    frame.to_csv(path, index=False)
    back = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[])
    reloaded = dataframe_to_offers(back)

    assert reloaded == offers
    assert reloaded[1].shipping is None


def test_dataframe_to_offers_ignores_extra_columns():
    """ledger.py appends its own is_correction bookkeeping column -- models.py
    must tolerate and ignore columns beyond OFFER_COLUMNS (mirrors
    src/state/system_state.py's strict=False convention)."""
    frame = offers_to_dataframe([_offer()])
    frame["is_correction"] = "False"
    frame["some_future_column"] = "whatever"
    reloaded = dataframe_to_offers(frame)
    assert reloaded == [_offer()]


def test_dataframe_to_offers_rejects_a_frame_missing_required_columns():
    frame = offers_to_dataframe([_offer()]).drop(columns=["price"])
    with pytest.raises(ValueError):
        dataframe_to_offers(frame)


def test_offers_to_dataframe_rejects_an_empty_sequence():
    with pytest.raises(ValueError):
        offers_to_dataframe([])


def test_offers_to_dataframe_column_order_matches_offer_columns():
    frame = offers_to_dataframe([_offer()])
    assert list(frame.columns) == list(OFFER_COLUMNS)


# -- price_normalized / BASE_CURRENCY convention, hand-verified --------------


def test_price_normalized_hand_verified_fx_example():
    """Convention (models.py docstring): price_normalized = price * (FX rate to
    BASE_CURRENCY), same "unit" (smallest sellable item) as price itself.

    Worked by hand: MXN 1,234.56 at an FX rate of 0.058 USD/MXN.
      1234.56 * 0.058
        = 1234.56 * 0.05 + 1234.56 * 0.008
        = 61.7280 + 9.87648
        = 71.60448
    So price_normalized must be exactly Decimal('71.60448') USD.
    """
    price_mxn = Decimal("1234.56")
    fx_rate_mxn_to_usd = Decimal("0.058")
    price_normalized = price_mxn * fx_rate_mxn_to_usd

    assert price_normalized == Decimal("71.60448")

    offer = _offer(
        price=price_mxn,
        currency="MXN",
        price_normalized=price_normalized,
    )
    assert offer.price_normalized == Decimal("71.60448")
    assert BASE_CURRENCY == "USD"  # price_normalized is denominated in this


def test_price_point_from_offer_uses_base_currency():
    offer = _offer(price_normalized=Decimal("71.60448"))
    point = PricePoint.from_offer(offer)
    assert point.currency == BASE_CURRENCY == "USD"
    assert point.price_normalized == Decimal("71.60448")
    assert point.matched_product_id == offer.matched_product_id
    assert point.site == offer.site


def test_price_point_rejects_non_positive_price():
    with pytest.raises(ValueError):
        PricePoint(
            matched_product_id="SKU-A",
            site="example.com",
            observed_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
            price_normalized=Decimal("0"),
        )


# -- MatchCandidate / SiteConfig: shape + light validation --------------------


def test_match_candidate_valid_construction():
    candidate = MatchCandidate(
        our_product_id="SKU-A",
        competitor_sku_ref="ref-1",
        site="example.com",
        method="gtin",
        score=0.99,
        status="confirmed",
        reason="exact GTIN match",
        confirmed_by="rule:gtin_exact",
        confirmed_at=datetime(2026, 7, 12, tzinfo=timezone.utc),
    )
    assert candidate.status == "confirmed"


@pytest.mark.parametrize(
    "overrides",
    [
        {"method": "psychic"},
        {"status": "maybe"},
        {"score": 1.2},
        {"score": -0.1},
        {"our_product_id": ""},
    ],
)
def test_match_candidate_rejects_invalid_fields(overrides):
    fields = dict(
        our_product_id="SKU-A",
        competitor_sku_ref="ref-1",
        site="example.com",
        method="fuzzy",
        score=0.6,
        status="suspect",
    )
    fields.update(overrides)
    with pytest.raises(ValueError):
        MatchCandidate(**fields)


def test_site_config_is_approved_reflects_robots_and_tos():
    approved = SiteConfig(
        domain="example.com",
        robots_txt_respected=True,
        robots_checked_at="2026-07-01",
        tos_summary="scraping of public product pages permitted",
        tos_decision="allowed",
        rate_limit_seconds=2.0,
        max_tier_allowed="L1",
    )
    assert approved.is_approved is True

    prohibited = SiteConfig(
        domain="blocked.example.com",
        robots_txt_respected=True,
        robots_checked_at="2026-07-01",
        tos_summary="ToS forbids automated access",
        tos_decision="prohibited",
        rate_limit_seconds=2.0,
        max_tier_allowed="L0",
    )
    assert prohibited.is_approved is False

    robots_blocked = SiteConfig(
        domain="norobots.example.com",
        robots_txt_respected=False,
        robots_checked_at="2026-07-01",
        tos_summary="robots.txt disallows /product/",
        tos_decision="allowed",
        rate_limit_seconds=2.0,
        max_tier_allowed="L1",
    )
    assert robots_blocked.is_approved is False


def test_site_config_rejects_non_none_pii_policy():
    with pytest.raises(ValueError):
        SiteConfig(
            domain="example.com",
            robots_txt_respected=True,
            robots_checked_at="2026-07-01",
            tos_summary="x",
            tos_decision="allowed",
            rate_limit_seconds=1.0,
            max_tier_allowed="L1",
            pii_policy="emails",
        )


def test_site_config_rejects_unknown_tos_decision_and_tier():
    with pytest.raises(ValueError):
        SiteConfig(
            domain="example.com",
            robots_txt_respected=True,
            robots_checked_at="2026-07-01",
            tos_summary="x",
            tos_decision="mostly-fine",
            rate_limit_seconds=1.0,
            max_tier_allowed="L1",
        )
    with pytest.raises(ValueError):
        SiteConfig(
            domain="example.com",
            robots_txt_respected=True,
            robots_checked_at="2026-07-01",
            tos_summary="x",
            tos_decision="allowed",
            rate_limit_seconds=1.0,
            max_tier_allowed="L9",
        )


# -- validate_offer_frame: bulk-frame safety net ------------------------------


def test_validate_offer_frame_accepts_a_well_formed_frame():
    frame = offers_to_dataframe([_offer()])
    validate_offer_frame(frame)  # must not raise


def test_validate_offer_frame_rejects_bad_availability():
    frame = offers_to_dataframe([_offer()])
    frame.loc[0, "availability"] = "Backordered"
    with pytest.raises(OfferFrameValidationError):
        validate_offer_frame(frame)


def test_validate_offer_frame_rejects_empty_required_column():
    frame = offers_to_dataframe([_offer()])
    frame.loc[0, "site"] = ""
    with pytest.raises(OfferFrameValidationError):
        validate_offer_frame(frame)


def test_validate_offer_frame_rejects_non_positive_price():
    frame = offers_to_dataframe([_offer()])
    frame.loc[0, "price"] = "0"
    with pytest.raises(OfferFrameValidationError):
        validate_offer_frame(frame)


def test_validate_offer_frame_rejects_out_of_range_confidence():
    frame = offers_to_dataframe([_offer()])
    frame.loc[0, "match_confidence"] = "1.5"
    with pytest.raises(OfferFrameValidationError):
        validate_offer_frame(frame)
