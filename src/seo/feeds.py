"""S2 product feeds (Linchpin 3.0 PR-23, plan section 8 "Track B -- SEO", S2
row: ``feeds.py``) -- turns the SAME client catalog ``schema_gen.py``
consumes into two machine-readable feed formats.

**Format choice (documented per the PR instruction to pick one and say
why):**
  - XML: RSS 2.0 with the Google Shopping "g:" namespace
    (``http://base.google.com/ns/1.0``) -- the long-standing feed shape
    Google Merchant Center, Bing Merchant Center, and Meta/Facebook Catalog
    all accept (or closely mirror) as a "scheduled fetch" URL, with zero API
    integration. Chosen over Google's newer Content API JSON-upload format
    because a static feed URL is the deliverable an SEO/growth engagement
    can hand off without also wiring API credentials -- matching this repo's
    offline-first, credential-free-until-there-is-a-client doctrine (plan
    risk #8).
  - JSON: a plain ``{"feed_info": {...}, "products": [...]}`` object.
    NEITHER Google's Content-API product resource shape NOR jsonfeed.org's
    blogging-oriented spec fits a product catalog cleanly, so this is the
    repo's own small, documented shape (``format: "kern-generic-product-
    feed-v1"``) for any downstream consumer (a client's own site search, a
    partner integration, a quick diff) that wants the same data without an
    XML parser.

Reuses ``schema_gen.CatalogItem``/``missing_required_fields``/
``derive_availability`` rather than a second product-row type or a second
required-field policy (Golden Rule 5, DRY) -- and adds exactly ONE
feed-specific requirement on top: a shopping-feed entry needs a clickable
``url`` to be useful, so ``missing_required_feed_fields`` requires it even
though a bare JSON-LD ``Product`` does not (schema_gen's own ``url`` stays
optional/recommended-only). ``ExcludedCatalogItem`` is ``schema_gen``'s own
dataclass, reused verbatim so a SKU excluded from BOTH the JSON-LD and a
feed for the same underlying gap is reported identically in both places.

Availability/condition tokens are translated from schema.org's PascalCase
vocabulary (``schema_gen``'s output vocabulary, reused here via
``derive_availability``/``normalize_condition_token``) down to Google
Merchant's own lowercase feed vocabulary -- see
``_MERCHANT_AVAILABILITY``/``_MERCHANT_CONDITION`` below, including the
documented approximations for the two schema.org states
(``LimitedAvailability``, ``Discontinued``) that have no distinct Merchant
equivalent. This module does not claim exhaustive Google Merchant Center
policy compliance (e.g. category-specific required attributes) -- it
documents the fields it does and does not populate rather than overselling
precision it doesn't have (same honesty norm as ``jobs/seo_audit.py``'s
ranking heuristic).

No network I/O (HARD RULE) -- this module only builds strings/dicts from an
already-loaded ``CatalogItem`` sequence and writes them to a LOCAL path; the
real feed consumers (Merchant Center's scheduled fetch, a partner's polling
job) read the resulting static file over HTTP themselves, outside this repo.
"""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from xml.dom import minidom

import pandas as pd

from src.deliverable import DEFAULT_BRANDING, Branding
from src.export import write_summary_csv
from src.seo.schema_gen import (
    CatalogItem,
    ExcludedCatalogItem,
    derive_availability,
    missing_required_fields,
    normalize_condition_token,
)

GOOGLE_MERCHANT_NS = "http://base.google.com/ns/1.0"
ET.register_namespace("g", GOOGLE_MERCHANT_NS)

# Attribution for these two generated ARTIFACTS (not for the client's own
# product data -- see schema_gen.py's module docstring for why the JSON-LD
# output itself carries no such watermark). Configurable via the `branding`
# parameter on build_merchant_feed_xml/build_generic_json_feed below
# (src.deliverable.Branding); defaults to Kern's own identity.
FEED_GENERATOR = f"{DEFAULT_BRANDING.name} SEO Feed Generator"

# schema.org token (schema_gen's vocabulary) -> Google Merchant feed token.
# Merchant's g:availability only has four officially documented values; the
# two schema.org states with no distinct Merchant equivalent are mapped to
# their closest practical bucket, DOCUMENTED here rather than silently
# collapsed (Golden Rule 14).
_MERCHANT_AVAILABILITY: dict[str, str] = {
    "InStock": "in stock",
    "OutOfStock": "out of stock",
    "PreOrder": "preorder",
    "BackOrder": "backorder",
    "LimitedAvailability": "in stock",  # documented approximation: still purchasable
    "Discontinued": "out of stock",  # documented approximation: no longer purchasable
}

_MERCHANT_CONDITION: dict[str, str] = {
    "NewCondition": "new",
    "RefurbishedCondition": "refurbished",
    "UsedCondition": "used",
    "DamagedCondition": "used",  # documented approximation: closest Merchant-supported bucket
}


@dataclass(frozen=True)
class FeedItemResult:
    """One included SKU's exact field:value pairs as written to a feed --
    kept alongside the serialized feed so a caller (or a test) can verify a
    round-trip without re-parsing XML/JSON."""

    product_id: str
    fields: dict


@dataclass(frozen=True)
class MerchantFeedReport:
    xml: str
    items: tuple[FeedItemResult, ...]
    excluded: tuple[ExcludedCatalogItem, ...]
    n_included: int
    n_excluded: int
    summary: str


@dataclass(frozen=True)
class GenericJsonFeedReport:
    feed: dict
    items: tuple[FeedItemResult, ...]
    excluded: tuple[ExcludedCatalogItem, ...]
    n_included: int
    n_excluded: int
    summary: str


def missing_required_feed_fields(item: CatalogItem) -> list[str]:
    """``schema_gen.missing_required_fields`` plus one feed-only requirement
    (see module docstring): a shopping-feed entry with no ``url`` cannot be
    clicked through to the product."""
    missing = missing_required_fields(item)
    if not item.url or not item.url.strip():
        missing.append("url")
    return missing


def _split_included_excluded(
    items: Sequence[CatalogItem],
) -> tuple[list[CatalogItem], list[ExcludedCatalogItem]]:
    included: list[CatalogItem] = []
    excluded: list[ExcludedCatalogItem] = []
    for item in items:
        pid = item.product_id if item.product_id and item.product_id.strip() else "<missing product_id>"
        missing = missing_required_feed_fields(item)
        if missing:
            excluded.append(ExcludedCatalogItem(pid, tuple(missing)))
            continue
        included.append(item)
    return included, excluded


def _merchant_availability(item: CatalogItem) -> str:
    token, _ = derive_availability(item)
    # Every item reaching here already passed missing_required_feed_fields,
    # which requires a resolvable availability signal -- token is never None
    # in practice; the "out of stock" default is a defensive fallback only.
    return _MERCHANT_AVAILABILITY.get(token, "out of stock") if token else "out of stock"


def _merchant_condition(item: CatalogItem) -> str | None:
    token = normalize_condition_token(item.condition)
    return _MERCHANT_CONDITION.get(token) if token else None


def _g(tag: str) -> str:
    return f"{{{GOOGLE_MERCHANT_NS}}}{tag}"


def _build_merchant_item_fields(item: CatalogItem) -> dict:
    fields: dict = {
        "id": item.product_id,
        "title": item.title,
        "link": item.url,
        "price": f"{round(float(item.price), 2):.2f} {item.currency}",
        "availability": _merchant_availability(item),
    }
    if item.description and item.description.strip():
        fields["description"] = item.description
    if item.image_url and item.image_url.strip():
        fields["image_link"] = item.image_url
    if item.brand and item.brand.strip():
        fields["brand"] = item.brand
    if item.gtin and item.gtin.strip():
        fields["gtin"] = item.gtin
    if item.mpn and item.mpn.strip():
        fields["mpn"] = item.mpn
    condition = _merchant_condition(item)
    if condition is not None:
        fields["condition"] = condition
    return fields


def _serialize_xml(root: ET.Element) -> str:
    rough = ET.tostring(root, encoding="unicode")
    pretty = minidom.parseString(rough).toprettyxml(indent="  ")
    lines = [line for line in pretty.splitlines() if line.strip()]
    return "\n".join(lines) + "\n"


def build_merchant_feed_xml(
    items: Sequence[CatalogItem],
    *,
    feed_title: str,
    feed_link: str,
    feed_description: str,
    branding: Branding = DEFAULT_BRANDING,
) -> MerchantFeedReport:
    """Build a Google Merchant Center-style RSS 2.0 + ``g:`` feed. See module
    docstring for the format choice rationale and the availability/condition
    token translation."""
    included, excluded = _split_included_excluded(items)

    rss = ET.Element("rss", attrib={"version": "2.0"})
    channel = ET.SubElement(rss, "channel")
    ET.SubElement(channel, "title").text = feed_title
    ET.SubElement(channel, "link").text = feed_link
    ET.SubElement(channel, "description").text = feed_description
    ET.SubElement(channel, "generator").text = f"{branding.name} SEO Feed Generator"

    results: list[FeedItemResult] = []
    for item in included:
        fields = _build_merchant_item_fields(item)
        entry = ET.SubElement(channel, "item")
        for key, value in fields.items():
            ET.SubElement(entry, _g(key)).text = str(value)
        results.append(FeedItemResult(item.product_id, fields))

    xml_str = _serialize_xml(rss)
    summary = f"Merchant feed: {len(included)} item(s) included, {len(excluded)} excluded for missing required field(s)."
    return MerchantFeedReport(
        xml=xml_str, items=tuple(results), excluded=tuple(excluded),
        n_included=len(included), n_excluded=len(excluded), summary=summary,
    )


def build_generic_json_feed(
    items: Sequence[CatalogItem],
    *,
    feed_title: str,
    feed_link: str,
    feed_description: str,
    branding: Branding = DEFAULT_BRANDING,
) -> GenericJsonFeedReport:
    """Build this repo's own simple, documented product-feed JSON shape (see
    module docstring for why neither Google's nor jsonfeed.org's shape was
    reused)."""
    included, excluded = _split_included_excluded(items)

    products: list[dict] = []
    results: list[FeedItemResult] = []
    for item in included:
        fields = {
            "id": item.product_id,
            "title": item.title,
            "link": item.url,
            "price": round(float(item.price), 2),
            "currency": item.currency,
            "availability": _merchant_availability(item),
        }
        if item.description and item.description.strip():
            fields["description"] = item.description
        if item.image_url and item.image_url.strip():
            fields["image_link"] = item.image_url
        if item.brand and item.brand.strip():
            fields["brand"] = item.brand
        if item.gtin and item.gtin.strip():
            fields["gtin"] = item.gtin
        if item.mpn and item.mpn.strip():
            fields["mpn"] = item.mpn
        condition = _merchant_condition(item)
        if condition is not None:
            fields["condition"] = condition
        products.append(fields)
        results.append(FeedItemResult(item.product_id, fields))

    feed = {
        "feed_info": {
            "title": feed_title,
            "link": feed_link,
            "description": feed_description,
            "generated_by": branding.name,
            "format": "kern-generic-product-feed-v1",
        },
        "products": products,
    }
    summary = (
        f"Generic JSON feed: {len(included)} item(s) included, {len(excluded)} excluded "
        "for missing required field(s)."
    )
    return GenericJsonFeedReport(
        feed=feed, items=tuple(results), excluded=tuple(excluded),
        n_included=len(included), n_excluded=len(excluded), summary=summary,
    )


def write_merchant_feed_xml(report: MerchantFeedReport, out_path: str | Path) -> Path:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(report.xml, encoding="utf-8")
    return path


def write_generic_json_feed(report: GenericJsonFeedReport, out_path: str | Path) -> Path:
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.feed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def write_excluded_csv(excluded: Sequence[ExcludedCatalogItem], out_path: str | Path) -> Path:
    """Shared writer for either feed's excluded-item list -- mirrors
    ``schema_gen.write_catalog_jsonld``'s own excluded-CSV shape (Golden
    Rule 14: an exclusion is always reported, never silent)."""
    path = Path(out_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if excluded:
        rows = [{"product_id": e.product_id, "reasons": "; ".join(e.reasons)} for e in excluded]
        return write_summary_csv(rows, path)
    pd.DataFrame(columns=["product_id", "reasons"]).to_csv(path, index=False)
    return path
