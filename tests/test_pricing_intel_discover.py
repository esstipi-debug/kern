"""Tests for src/pricing_intel/discover.py (Discovery-Assisted Price Intel
plan, PR-2): the pure filter that keeps only already-crawled pages carrying
real JSON-LD/microdata Product+Offer structured data, and pulls a light
title/brand/gtin/price_hint signal out of them for downstream homologation
(PR-4). No network I/O -- this module only ever sees HTML strings a (future)
crawler already fetched.

Guarantees under test:
- a page with a valid JSON-LD Offer is kept as one ``DiscoveredProduct``,
  with title/brand/gtin parsed from whatever schema.org fields the Offer
  node itself actually carries (never fabricated -- see module docstring);
- a page with no structured data at all is dropped silently, not an error;
- ``gtin`` is populated when a ``gtin13``-style identifier is present on the
  Offer node, and stays ``None`` (never guessed) when it is not;
- a malformed <script type="application/ld+json"> block degrades honestly
  (reuses structured.py's own fallback/retry machinery) -- the page is still
  kept if sibling microdata carries a usable Offer, with all-``None``
  identity fields rather than a crash;
- empty and missing ``html`` are dropped, not errors;
- a page whose only structured signal is OpenGraph (no JSON-LD/microdata
  Offer node) is dropped -- OpenGraph alone carries no product identity to
  homologate against, only a bare price pair (see structured.py);
- both a plain ``{"url", "html"}`` dict AND a duck-typed object exposing
  ``.url``/``.html`` (e.g. ``seo_audit.crawl_audit.CrawledPage``) are
  accepted, so tests never need a live crawl.
"""

from __future__ import annotations

from pathlib import Path

from src.pricing_intel.discover import DiscoveredProduct, filter_product_pages
from src.seo.crawl_audit import CrawledPage

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "pricing_intel"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# A JSON-LD Offer node that (realistically -- some sites skip the separate
# Product node and put identity fields directly on the Offer, and gtin13 is
# a legitimate Offer property per schema.org, not just a Product one) also
# carries name/brand/gtin13 -- unlike PR-11's frozen fixtures, which were
# built only to exercise price/currency/availability and never populate
# these fields. Kept inline (like structured.py's own chompjs test) since no
# existing fixture covers this combination.
_JSONLD_WITH_IDENTITY_HTML = """
<html>
<head><title>Acme Widget 3000</title></head>
<body>
<script type="application/ld+json">
{
  "@context": "https://schema.org",
  "@type": "Product",
  "name": "Acme Widget 3000",
  "offers": {
    "@type": "Offer",
    "name": "Acme Widget 3000",
    "brand": {"@type": "Brand", "name": "Acme"},
    "gtin13": "0012345678905",
    "price": "199.99",
    "priceCurrency": "USD",
    "availability": "https://schema.org/InStock"
  }
}
</script>
</body>
</html>
"""


def test_keeps_page_with_jsonld_offer() -> None:
    pages = [{"url": "https://example.test/p/aw-3000", "html": _JSONLD_WITH_IDENTITY_HTML}]

    discovered = filter_product_pages(pages, site="example.test")

    assert len(discovered) == 1
    product = discovered[0]
    assert product == DiscoveredProduct(
        url="https://example.test/p/aw-3000",
        site="example.test",
        title="Acme Widget 3000",
        brand="Acme",
        gtin="0012345678905",
        price_hint="199.99",
        offers=product.offers,  # shape asserted separately below
    )
    assert len(product.offers) == 1
    assert product.offers[0]["price"] == "199.99"


def test_drops_page_without_structured_data() -> None:
    pages = [{"url": "https://example.test/about", "html": _load("text_only.html")}]

    discovered = filter_product_pages(pages, site="example.test")

    assert discovered == []


def test_extracts_gtin_when_present_else_none() -> None:
    with_gtin = {"url": "https://example.test/p/with-gtin", "html": _JSONLD_WITH_IDENTITY_HTML}
    # jsonld_clean.html's Offer node has price/currency/availability but no
    # gtin13 (or any gtin variant) at all -- an honest "not present", not an
    # error; the page is still kept since it has a valid Offer node.
    without_gtin = {"url": "https://example.test/p/no-gtin", "html": _load("jsonld_clean.html")}

    discovered = filter_product_pages([with_gtin, without_gtin], site="example.test")

    assert len(discovered) == 2
    by_url = {p.url: p for p in discovered}
    assert by_url["https://example.test/p/with-gtin"].gtin == "0012345678905"
    assert by_url["https://example.test/p/no-gtin"].gtin is None


def test_handles_malformed_ldjson_via_fallback() -> None:
    # malformed_jsonld_with_microdata.html (PR-11 fixture): an unrecoverable
    # <script type="application/ld+json"> block sits next to a perfectly
    # good microdata Offer for the same product. structured.py's own
    # extruct-retry-without-json-ld path (and, with extruct unavailable, its
    # chompjs/regex fallback) keeps the microdata Offer alive -- discover.py
    # must not crash on the broken block, and must still keep the page
    # (microdata alone is a real Offer node), with identity fields honestly
    # None since this microdata Offer carries no name/brand/gtin itemprops.
    pages = [{"url": "https://example.test/p/desk-lamp", "html": _load("malformed_jsonld_with_microdata.html")}]

    discovered = filter_product_pages(pages, site="example.test")

    assert len(discovered) == 1
    product = discovered[0]
    assert product.title is None
    assert product.brand is None
    assert product.gtin is None
    assert len(product.offers) == 1
    assert product.offers[0]["price"] == "15.75"


def test_empty_and_missing_html_are_dropped_not_errors() -> None:
    pages = [
        {"url": "https://example.test/empty", "html": ""},
        {"url": "https://example.test/whitespace", "html": "   \n  "},
        {"url": "https://example.test/missing"},  # no "html" key at all
    ]

    discovered = filter_product_pages(pages, site="example.test")

    assert discovered == []


def test_drops_page_whose_only_signal_is_opengraph() -> None:
    # OpenGraph's product:price:* pair carries a bare price, no product
    # identity (no title/brand/gtin to homologate against) -- discovery
    # deliberately requires the stronger JSON-LD/microdata Offer signal, so
    # an OpenGraph-only page is dropped here even though extract.py's later
    # price cascade (tier 2) would still be able to read a price from it.
    pages = [{"url": "https://example.test/p/grill", "html": _load("opengraph_only.html")}]

    discovered = filter_product_pages(pages, site="example.test")

    assert discovered == []


def test_accepts_duck_typed_crawled_page_not_just_a_dict() -> None:
    # Proves CrawledPageLike really is structural -- seo_audit.crawl_audit's
    # own frozen dataclass (attribute access, not dict subscripting) works
    # with no adapter code and no live crawl.
    page = CrawledPage(url="https://example.test/p/aw-3000", html=_JSONLD_WITH_IDENTITY_HTML)

    discovered = filter_product_pages([page], site="example.test")

    assert len(discovered) == 1
    assert discovered[0].url == "https://example.test/p/aw-3000"
    assert discovered[0].title == "Acme Widget 3000"


def test_site_is_attached_verbatim_not_derived_from_url() -> None:
    pages = [{"url": "https://sub.example.test/p/x", "html": _JSONLD_WITH_IDENTITY_HTML}]

    discovered = filter_product_pages(pages, site="example.test")

    assert discovered[0].site == "example.test"
