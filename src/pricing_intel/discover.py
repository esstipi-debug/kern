"""Discovery page filter (Discovery-Assisted Price Intel plan, PR-2): given
already-crawled pages (a URL plus the raw HTML a (future) crawler already
fetched -- see ``jobs/price_watch.py``, PR-3), keep only the ones that carry
real JSON-LD/microdata ``Product``/``Offer`` structured data, and reduce each
survivor to the light ``DiscoveredProduct`` record PR-4's homologation
cascade (``gtin`` -> ``fuzzy`` -> ``probabilistic`` -> ``adjudicate``)
consumes.

Pure module, no network I/O -- mirrors ``acquire/structured.py``'s own
invariant (see that module's docstring). Every byte of structured data this
module reads comes from ``structured.extract_product_metadata``, reused
as-is, never reimplemented: this module's only job is (1) deciding whether a
page is worth keeping at all, and (2) pulling title/brand/gtin/price_hint
out of whatever Offer-shaped dicts that function already found.

Why OpenGraph alone does not qualify a page (only JSON-LD/microdata Offer
nodes do): ``ProductMetadata.opengraph_price`` is a bare ``{"price",
"priceCurrency"}`` pair with no product identity attached (no title, no
brand, no gtin) -- there is nothing in it for PR-4's homologation cascade to
match against our catalog. A page with only OpenGraph pricing is dropped
here even though ``extract.py``'s later price-extraction cascade (tier 2)
can still read a price from it once a product has already been homologated
some other way.

Golden rule 14 applied to structure, same discipline as ``structured.py``:
title/brand/gtin are pulled only from schema.org fields actually present on
an extracted Offer node (some real-world JSON-LD skips a separate ``Product``
node and puts identity fields -- ``name``, ``brand``, ``gtin13`` and its
siblings are all legitimate Offer properties, not just Product ones --
directly on the Offer itself). A field that is not there stays ``None``,
never guessed from surrounding text.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from .acquire.structured import ProductMetadata, extract_product_metadata

# schema.org GTIN encodings, most-specific first. All are legitimate Offer
# (not just Product) identifiers -- ``mpn``/``sku`` are deliberately excluded
# here since they identify the manufacturer part / seller listing, not the
# product's global trade item number, and conflating them would fabricate a
# gtin that was never actually stated.
_GTIN_FIELDS: tuple[str, ...] = ("gtin13", "gtin", "gtin12", "gtin8", "gtin14")


@runtime_checkable
class CrawledPageLike(Protocol):
    """The attribute-style half of the duck type ``filter_product_pages``
    accepts -- anything exposing ``.url``/``.html``, e.g.
    ``seo_audit.crawl_audit.CrawledPage``. A plain ``{"url", "html"}`` dict
    is accepted too (see ``_page_field``'s ``Mapping`` branch); a dict has no
    attribute access so it does not structurally satisfy this Protocol --
    ``filter_product_pages``'s own parameter type spells out both halves.
    """

    url: str
    html: str | None


@dataclass(frozen=True)
class DiscoveredProduct:
    """One crawled page confirmed to carry real Product/Offer structured
    data -- the L1 discovery record PR-4's homologation cascade consumes.

    ``title``/``brand``/``gtin``/``price_hint`` are best-effort signals
    pulled straight from the underlying Offer node(s); any of them may be
    ``None`` when the page's structured data simply didn't state it (never
    fabricated). ``offers`` carries every raw JSON-LD/microdata Offer dict
    found on the page, for provenance -- the actual price-extraction cascade
    (``extract.py``) re-parses the page's HTML directly rather than reading
    this field.
    """

    url: str
    site: str
    title: str | None
    brand: str | None
    gtin: str | None
    price_hint: str | None
    offers: tuple[dict, ...]


def filter_product_pages(
    pages: Iterable[CrawledPageLike | Mapping[str, object]], *, site: str
) -> list[DiscoveredProduct]:
    """Keep only ``pages`` whose HTML carries at least one JSON-LD or
    microdata ``Offer`` node (per ``extract_product_metadata``); every other
    page -- no structured data, OpenGraph-only, empty/missing HTML -- is
    dropped silently. Not an error: an honest "this is not a product page".

    Each element of ``pages`` is ``CrawledPageLike``: a plain
    ``{"url", "html"}`` dict, or any object exposing ``.url``/``.html``
    attributes (e.g. ``seo_audit.crawl_audit.CrawledPage``) -- so callers
    never need a live crawl to exercise this function.
    """
    discovered: list[DiscoveredProduct] = []
    for page in pages:
        url = _page_field(page, "url") or ""
        html = _page_field(page, "html") or ""
        if not html.strip():
            continue

        meta = extract_product_metadata(html)
        offers = meta.json_ld_offers + meta.microdata_offers
        if not offers:
            continue

        discovered.append(
            DiscoveredProduct(
                url=url,
                site=site,
                title=_title_from(meta),
                brand=_brand_from(meta),
                gtin=_gtin_from(meta),
                price_hint=_price_hint_from(meta),
                offers=offers,
            )
        )
    return discovered


def _page_field(page: CrawledPageLike | Mapping[str, object], name: str) -> str | None:
    """Duck-typed ``.url``/``.html`` access -- a plain dict (or any other
    ``Mapping``) reads by key, anything else (a dataclass, a namedtuple, ...)
    reads by attribute. Missing either way resolves to ``None``, never a
    ``KeyError``/``AttributeError`` -- callers of this pure module should
    never see it raise on a shape mismatch."""
    if isinstance(page, Mapping):
        return page.get(name)
    return getattr(page, name, None)


def _offer_candidates(meta: ProductMetadata) -> tuple[dict, ...]:
    """The Offer-shaped dicts worth reading title/brand/gtin/price_hint off
    of. OpenGraph's price pair is deliberately excluded -- it structurally
    cannot carry those fields (see module docstring)."""
    return meta.json_ld_offers + meta.microdata_offers


def _scalar(value: object) -> str | None:
    """Resolve one Offer field value to a plain string, or ``None`` if it
    was never stated. Handles the two non-string shapes schema.org fields
    show up in: a JSON-LD ``{"@value": ...}`` wrapper, and a nested node
    (e.g. ``brand: {"@type": "Brand", "name": "Acme"}``) -- mirrors
    ``extract.py``'s own ``_extract_offer_fields._scalar`` idiom for the
    ``@value`` case, extended to also unwrap a nested ``name``."""
    if isinstance(value, dict):
        value = value.get("name") or value.get("@value")
    if value in (None, ""):
        return None
    return str(value)


def _first_field(offers: tuple[dict, ...], keys: tuple[str, ...]) -> str | None:
    """The first non-empty value found for any of ``keys``, scanning each
    offer's own keys (in ``keys`` order) before moving to the next offer --
    so an earlier Offer node's own field always wins over a later one's."""
    for offer in offers:
        for key in keys:
            resolved = _scalar(offer.get(key))
            if resolved is not None:
                return resolved
    return None


def _title_from(meta: ProductMetadata) -> str | None:
    return _first_field(_offer_candidates(meta), ("name",))


def _brand_from(meta: ProductMetadata) -> str | None:
    return _first_field(_offer_candidates(meta), ("brand",))


def _gtin_from(meta: ProductMetadata) -> str | None:
    return _first_field(_offer_candidates(meta), _GTIN_FIELDS)


def _price_hint_from(meta: ProductMetadata) -> str | None:
    return _first_field(_offer_candidates(meta), ("price",))
