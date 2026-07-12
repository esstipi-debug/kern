"""L1 acquisition: JSON-LD / microdata / OpenGraph product metadata
extraction from an already-fetched PDP HTML string (Linchpin 3.0 PR-11, plan
section 6.1: ``structured.py`` -- "extruct tras adapter", section 6.4 tier 1/2).

No network I/O lives here -- this module is a pure function over an HTML
string a (future) fetcher already retrieved. It is the plan's "adapter" that
isolates the rest of the titan from *which* structured-data library does the
parsing: ``extract_product_metadata`` tries extruct==0.18.0 first, and falls
back to a hand-rolled JSON-LD-only parser (lxml XPath + ``json.loads``, with
chompjs for malformed JSON) when extruct is unavailable or errors out --
"asi el titan no se rompe" (plan section 6.4). Only JSON-LD has a hand-rolled
fallback (matches the plan's own scope, section 6.4 point 1); microdata and
OpenGraph have no equivalent hand-rolled parser -- if extruct cannot be used
at all, those two fields simply come back empty and ``extract.py``'s cascade
falls through to tier 3/4, exactly like "this page has no microdata" would.
That is an honest degrade, never fabricated data.

Verified failure mode (worth documenting -- this is *why* the fallback
exists, not a hypothetical): extruct 0.18.0's JSON-LD parser
(``jstyleson.loads``) raises ``json.JSONDecodeError`` on a malformed
``<script type="application/ld+json">`` block instead of skipping it, and
that exception propagates out of ``extruct.extract()`` for the *entire*
call -- including syntaxes requested alongside ``"json-ld"`` -- so one bad
script tag on a page that also has perfectly good microdata would otherwise
blind us to the microdata too. ``extract_product_metadata`` retries extruct
without ``"json-ld"`` when the first call raises, specifically to avoid that.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:  # primary path (plan section 6.4: "extruct==0.18.0 primero")
    import extruct

    _HAS_EXTRUCT = True
except ImportError:  # pragma: no cover -- exercised via monkeypatch in tests
    extruct = None
    _HAS_EXTRUCT = False

try:  # used both by extruct (transitively) and this module's own fallback
    import lxml.html as _lxml_html

    _HAS_LXML = True
except ImportError:  # pragma: no cover -- lxml ships with extruct; only
    # missing in a hypothetical partial/broken install.
    _lxml_html = None
    _HAS_LXML = False

try:  # malformed-JSON recovery for the hand-rolled fallback (plan 6.4)
    import chompjs

    _HAS_CHOMPJS = True
except ImportError:  # pragma: no cover -- pricing-intel extra installs it
    chompjs = None
    _HAS_CHOMPJS = False

try:
    EXTRUCT_VERSION = _pkg_version("extruct")
except PackageNotFoundError:  # pragma: no cover -- defensive
    EXTRUCT_VERSION = "0.18.0"  # pyproject's pin

# Bump this whenever the hand-rolled fallback parser's *logic* changes --
# it is this module's own extractor_version when extruct is unavailable
# (plan rule 7: extractor + version travels with every observation).
LDJSON_FALLBACK_VERSION = "1"

_LDJSON_XPATH = '//script[@type="application/ld+json"]'


@dataclass(frozen=True)
class ProductMetadata:
    """Structured-data extraction result for one PDP HTML page -- the L1/L2
    payload ``extract.py``'s cascade tiers 1 and 2 consume.

    Provenance is tracked *separately* for JSON-LD (which can come from two
    different engines: extruct, or this module's own ld+json-only fallback)
    versus microdata/OpenGraph (extruct only -- no hand-rolled equivalent,
    see module docstring). This lets ``extract.py`` attribute
    ``extractor``/``extractor_version`` correctly per tier even when, say,
    extruct's microdata parse succeeded but its JSON-LD parse had to fall
    back (the verified failure mode described above).

    Any field may be empty/``None`` when that syntax simply was not present
    on the page -- an all-empty ``ProductMetadata`` is not an error;
    ``extract.py``'s tiers treat "nothing found here" as "fall through to
    the next tier", never as a failure of this module.
    """

    json_ld_offers: tuple[dict, ...]
    json_ld_source: str  # "extruct" | "ldjson_fallback" | "none"
    json_ld_source_version: str
    microdata_offers: tuple[dict, ...]
    opengraph_price: dict | None  # {"price": "...", "priceCurrency": "..." | None} or None
    structured_source: str  # "extruct" | "none" -- microdata/opengraph have no fallback
    structured_source_version: str


def extract_product_metadata(html: str) -> ProductMetadata:
    """Parse ``html`` for JSON-LD ``Offer`` nodes, microdata ``Offer`` nodes,
    and an OpenGraph ``product:price:*`` pair. Never raises on malformed or
    empty input -- worst case is an all-empty ``ProductMetadata`` (see class
    docstring)."""
    if not html or not html.strip():
        return ProductMetadata((), "none", "n/a", (), None, "none", "n/a")

    json_ld_offers: tuple[dict, ...] = ()
    json_ld_source = "none"
    json_ld_source_version = "n/a"
    microdata_offers: tuple[dict, ...] = ()
    opengraph_price: dict | None = None
    structured_source = "none"
    structured_source_version = "n/a"

    extruct_data = _try_extruct(html, syntaxes=["json-ld", "microdata", "opengraph"])
    if extruct_data is None and _HAS_EXTRUCT:
        # The combined call raised (e.g. a malformed <script> block -- see
        # module docstring's "verified failure mode"). Retry without
        # json-ld so a bad script tag doesn't also blind us to good
        # microdata/opengraph on the same page.
        extruct_data = _try_extruct(html, syntaxes=["microdata", "opengraph"])

    if extruct_data is not None:
        found_json_ld = _iter_jsonld_offer_nodes(extruct_data.get("json-ld", []))
        if found_json_ld:
            json_ld_offers = tuple(found_json_ld)
            json_ld_source = "extruct"
            json_ld_source_version = EXTRUCT_VERSION
        microdata_offers = tuple(_iter_microdata_offer_props(extruct_data.get("microdata", [])))
        opengraph_price = _opengraph_price(extruct_data.get("opengraph", []))
        structured_source = "extruct"
        structured_source_version = EXTRUCT_VERSION

    if not json_ld_offers:
        fallback_offers = _ldjson_fallback(html)
        if fallback_offers:
            json_ld_offers = fallback_offers
            json_ld_source = "ldjson_fallback"
            json_ld_source_version = LDJSON_FALLBACK_VERSION

    return ProductMetadata(
        json_ld_offers=json_ld_offers,
        json_ld_source=json_ld_source,
        json_ld_source_version=json_ld_source_version,
        microdata_offers=microdata_offers,
        opengraph_price=opengraph_price,
        structured_source=structured_source,
        structured_source_version=structured_source_version,
    )


def _try_extruct(html: str, *, syntaxes: list[str]) -> dict | None:
    if not _HAS_EXTRUCT:
        return None
    try:
        return extruct.extract(html, syntaxes=syntaxes)
    except Exception:
        # extruct is "semi-dormant" per plan section 14 risk #3 -- it can
        # raise on malformed markup/JSON rather than degrading internally.
        # Never let a parsing quirk on one page crash the cascade; the
        # caller falls back.
        return None


# -- JSON-LD (schema.org "@type": "Offer", possibly nested under a Product
# or an AggregateOffer, possibly inside "@graph") ---------------------------


def _iter_jsonld_offer_nodes(top_level_nodes: list) -> list[dict]:
    results: list[dict] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            node_type = node.get("@type")
            is_offer = (isinstance(node_type, str) and node_type.rsplit("/", 1)[-1].lower() == "offer") or (
                isinstance(node_type, list) and any(str(t).rsplit("/", 1)[-1].lower() == "offer" for t in node_type)
            )
            if is_offer:
                results.append(node)
            for value in node.values():
                walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for node in top_level_nodes:
        walk(node)
    return results


# -- microdata (extruct's own {"type": ..., "properties": {...}} shape) -----


def _iter_microdata_offer_props(top_level_nodes: list) -> list[dict]:
    results: list[dict] = []

    def walk(node: object) -> None:
        if isinstance(node, dict):
            item_type = node.get("type")
            if isinstance(item_type, str) and item_type.rsplit("/", 1)[-1].lower() == "offer":
                props = node.get("properties")
                if isinstance(props, dict):
                    results.append(props)
            props = node.get("properties")
            if isinstance(props, dict):
                for value in props.values():
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for node in top_level_nodes:
        walk(node)
    return results


# -- OpenGraph product:price:* --------------------------------------------


def _opengraph_price(top_level_nodes: list) -> dict | None:
    for node in top_level_nodes:
        if not isinstance(node, dict):
            continue
        props = node.get("properties")
        if not props:
            continue
        # extruct emits opengraph "properties" as a list of (key, value)
        # pairs (a page can repeat a property), not a dict.
        prop_map = dict(props)
        amount = prop_map.get("product:price:amount")
        if amount is None:
            continue
        return {"price": amount, "priceCurrency": prop_map.get("product:price:currency")}
    return None


# -- hand-rolled JSON-LD-only fallback (extruct unavailable or errored) -----


def _ldjson_fallback(html: str) -> tuple[dict, ...]:
    texts = _find_ldjson_script_texts(html)
    blocks = _parse_ldjson_blocks(texts)
    return tuple(_iter_jsonld_offer_nodes(blocks))


def _find_ldjson_script_texts(html: str) -> list[str]:
    if _HAS_LXML:
        try:
            doc = _lxml_html.fromstring(html)
            return [node.text_content() for node in doc.xpath(_LDJSON_XPATH)]
        except Exception:
            pass  # fall through to the regex last resort below
    return _find_ldjson_script_texts_regex(html)


_LDJSON_SCRIPT_RE = re.compile(
    r'<script\b[^>]*type\s*=\s*["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.IGNORECASE | re.DOTALL,
)


def _find_ldjson_script_texts_regex(html: str) -> list[str]:
    # Absolute last resort if lxml itself is unavailable/broken -- a plain
    # stdlib regex over <script type="application/ld+json"> blocks,
    # tolerant of attribute order and extra attributes.
    return [m.group(1) for m in _LDJSON_SCRIPT_RE.finditer(html)]


def _parse_ldjson_blocks(texts: list[str]) -> list[object]:
    parsed: list[object] = []
    for text in texts:
        text = (text or "").strip()
        if not text:
            continue
        try:
            parsed.append(json.loads(text))
            continue
        except json.JSONDecodeError:
            pass
        if _HAS_CHOMPJS:
            try:
                parsed.append(chompjs.parse_js_object(text))
                continue
            except Exception:
                pass
        # Unrecoverable block: skipped, never fabricated (plan: "un precio
        # dudoso es peor que ningun precio" applies to structure too).
    return parsed
