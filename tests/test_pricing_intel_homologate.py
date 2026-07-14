"""Tests for src/pricing_intel/homologate.py (Discovery-Assisted Price Intel
plan, PR-4): the pure orchestration of the existing match cascade (gtin ->
fuzzy -> probabilistic -> adjudicate) that decides, for each product
discovered on a competitor's site, which of OUR products it is -- if any.

Every probabilistic worked example reused below is copied verbatim from
``src/pricing_intel/match/probabilistic.py``'s own module docstring and
``tests/test_pricing_intel_match_probabilistic.py`` (same titles/brands,
same hand-verified scores) -- this file does not invent new numbers.
The one addition beyond those existing fixtures is reading decisive
attributes (``model``, here) off ``DiscoveredProduct.offers`` -- see
``homologate.py``'s own docstring for why that is necessary to exercise the
attribute-conflict path through the real public API at all.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from src.pricing_intel.discover import DiscoveredProduct
from src.pricing_intel.homologate import HomologationRow, homologate
from src.pricing_intel.match.adjudicate import is_in_adjudication_band
from src.pricing_intel.match.fuzzy import ProductAttributes
from src.pricing_intel.match.probabilistic import CONFIRM_THRESHOLD, SUSPECT_THRESHOLD

NOW = datetime(2026, 7, 14, 12, 0, tzinfo=timezone.utc)

VALID_EAN13 = "4006381333931"

# A shared "our catalog" mirroring probabilistic.py's own worked examples --
# reused unchanged across every test below.
OUR_CATALOG = (
    ProductAttributes("our-coke", "Coca-Cola Bottle 2L", "Coca-Cola", {"pack_size": "2l"}),
    ProductAttributes(
        "our-sony-xm5",
        "Sony WH-1000XM5 Wireless Noise Cancelling Headphones",
        "Sony",
        {"model": "xm5"},
    ),
    ProductAttributes("our-samsung-s23", "Samsung Galaxy S23 Smartphone", "Samsung"),
    ProductAttributes("our-acme", "Acme Widget Pro 3000 Deluxe Edition", "Acme"),
)


def _discovered(
    *,
    url: str = "https://competitor.test/p/1",
    site: str = "competitor.test",
    title: str | None,
    brand: str | None,
    gtin: str | None = None,
    offers: tuple[dict, ...] = (),
) -> DiscoveredProduct:
    return DiscoveredProduct(
        url=url, site=site, title=title, brand=brand, gtin=gtin, price_hint=None, offers=offers
    )


# -- gtin exact match -----------------------------------------------------------


def test_gtin_exact_match_confirms() -> None:
    discovered = _discovered(title=None, brand=None, gtin=VALID_EAN13)
    report = homologate(
        [discovered], OUR_CATALOG, our_gtins={"our-coke": VALID_EAN13}, now=NOW
    )

    assert len(report.rows) == 1
    row = report.rows[0]
    assert row.our_product_id == "our-coke"
    assert row.method == "gtin"
    assert row.status == "confirmed"
    assert row.score == 0.99
    assert row.confirmed_by == "auto"
    assert report.n_confirmed == 1
    assert report.n_suspect == 0
    assert report.n_unmatched == 0
    assert report.unmatched == ()


def test_gtin_miss_falls_through_to_fuzzy_probabilistic() -> None:
    # our_gtins present but the discovered gtin doesn't match anything on
    # file -- the cascade must fall through to fuzzy/probabilistic, not
    # silently stop at "no gtin match".
    discovered = _discovered(
        title="Coca-Cola 2L Bottle", brand="Coca-Cola", gtin="4006381333930"  # bad check digit
    )
    report = homologate(
        [discovered], OUR_CATALOG, our_gtins={"our-coke": VALID_EAN13}, now=NOW
    )

    row = report.rows[0]
    assert row.method == "probabilistic"
    assert row.status == "confirmed"
    assert row.our_product_id == "our-coke"


# -- worked example 1: reworded title, matching brand -> confirmed -------------


def test_high_probabilistic_confirms() -> None:
    discovered = _discovered(title="Coca-Cola 2L Bottle", brand="Coca-Cola")
    report = homologate([discovered], OUR_CATALOG, now=NOW)

    row = report.rows[0]
    assert row.our_product_id == "our-coke"
    assert row.method == "probabilistic"
    assert row.status == "confirmed"
    assert row.score == 1.0
    assert row.confirmed_by == "auto"
    assert report.n_confirmed == 1


# -- worked example 3: genuinely ambiguous -> suspect, never auto-confirmed ----


def test_ambiguous_pair_stays_suspect_not_confirmed() -> None:
    discovered = _discovered(title="Samsung Galaxy S23 Ultra Smartphone", brand="Samsung")
    report = homologate([discovered], OUR_CATALOG, now=NOW)

    row = report.rows[0]
    assert row.our_product_id == "our-samsung-s23"
    assert row.score == 0.9484375
    assert SUSPECT_THRESHOLD <= row.score < CONFIRM_THRESHOLD
    assert row.status == "suspect"
    assert row.status != "confirmed"
    assert row.confirmed_by is None
    assert report.n_confirmed == 0
    assert report.n_suspect == 1


# -- worked example 2: decisive attribute conflict -> rejected -----------------


def test_attribute_conflict_rejected() -> None:
    discovered = _discovered(
        title="Sony WH-1000XM4 Wireless Noise Cancelling Headphones",
        brand="Sony",
        offers=(
            {
                "name": "Sony WH-1000XM4 Wireless Noise Cancelling Headphones",
                "model": "xm4",
            },
        ),
    )
    report = homologate([discovered], OUR_CATALOG, now=NOW)

    row = report.rows[0]
    assert row.our_product_id == "our-sony-xm5"
    assert row.method == "probabilistic"
    assert row.score == 0.45
    assert row.status == "rejected"
    assert row.confirmed_by is None
    assert "attribute_conflict" in row.reason
    assert "model" in row.reason
    assert report.n_confirmed == 0
    assert report.n_suspect == 0


# -- golden rule 14: matches nothing -> reported in unmatched, never dropped ---


def test_discovered_product_matching_nothing_is_reported() -> None:
    discovered = _discovered(title="Nonexistent Gadget Widget 9000", brand="Nobrand Inc")
    report = homologate([discovered], [], now=NOW)  # empty catalog -- no block candidates possible

    assert len(report.rows) == 1
    row = report.rows[0]
    assert row.our_product_id is None
    assert row.status == "rejected"
    assert row.reason == "no_block_candidates"
    assert report.n_unmatched == 1
    assert report.unmatched == (row,)
    assert report.n_confirmed == 0
    assert report.n_suspect == 0


def test_discovered_product_missing_title_or_brand_is_reported_unmatched() -> None:
    # No title/brand at all (and no gtin) -- there is nothing to block or
    # score against; this must still be reported, not silently skipped.
    discovered = _discovered(title=None, brand=None)
    report = homologate([discovered], OUR_CATALOG, now=NOW)

    row = report.rows[0]
    assert row.our_product_id is None
    assert row.status == "rejected"
    assert row.reason == "missing_title_or_brand"
    assert report.n_unmatched == 1


# -- llm=None must defer, never fabricate a same/different verdict -------------


def test_no_llm_defers_never_fabricates() -> None:
    # This pair's real probabilistic score lands inside adjudicate.py's
    # literal ADJUDICATION_BAND [0.5, 0.85) -- verified against the real
    # score_pair below, not assumed.
    discovered = _discovered(title="Acme Widget 3000", brand="Acme")
    report = homologate([discovered], OUR_CATALOG, llm=None, now=NOW)

    row = report.rows[0]
    assert SUSPECT_THRESHOLD <= row.score < CONFIRM_THRESHOLD
    assert is_in_adjudication_band(row.score)
    assert row.status == "suspect"
    assert row.confirmed_by is None
    assert "adjudication_deferred" in row.reason
    assert "no_llm_provider_configured" in row.reason


# -- report aggregation across multiple discovered products --------------------


def test_homologate_report_aggregates_counts_across_products() -> None:
    products = [
        _discovered(url="https://competitor.test/p/coke", title="Coca-Cola 2L Bottle", brand="Coca-Cola"),
        _discovered(
            url="https://competitor.test/p/s23u",
            title="Samsung Galaxy S23 Ultra Smartphone",
            brand="Samsung",
        ),
        _discovered(url="https://competitor.test/p/nothing", title="Zzzzz Nonexistent", brand="Zzzzz"),
    ]
    report = homologate(products, OUR_CATALOG, now=NOW)

    assert len(report.rows) == 3
    assert report.n_confirmed == 1
    assert report.n_suspect == 1
    assert report.n_unmatched == 1
    assert len(report.unmatched) == 1


# -- HomologationRow structural safety guard ------------------------------------


def test_homologation_row_rejects_confirmed_without_confirmed_by() -> None:
    with pytest.raises(ValueError):
        HomologationRow(
            our_product_id="our-1",
            competitor_sku_ref="https://competitor.test/p/1",
            site="competitor.test",
            method="gtin",
            score=0.99,
            status="confirmed",
            reason="x",
            confirmed_by=None,
        )


def test_homologation_row_rejects_confirmed_by_on_non_confirmed_status() -> None:
    with pytest.raises(ValueError):
        HomologationRow(
            our_product_id="our-1",
            competitor_sku_ref="https://competitor.test/p/1",
            site="competitor.test",
            method="probabilistic",
            score=0.7,
            status="suspect",
            reason="x",
            confirmed_by="auto",  # forbidden -- suspect must never carry confirmed_by
        )
