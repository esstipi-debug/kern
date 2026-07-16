"""The extraction cascade (Linchpin 3.0 PR-11, plan section 6.4): turns one
already-fetched PDP HTML page into a validated (price, currency,
availability, list_price, confidence, extractor provenance) result, trying
progressively cheaper/less reliable sources and STOPPING at the first one
that produces a valid price.

Order and confidence are load-bearing and verbatim from the plan (not
tunable per call):
  1. JSON-LD ``Offer.price``/``priceCurrency``/``availability``       -- 0.98
  2. Microdata/OpenGraph ``product:price:amount``                     -- 0.90
  3. Versioned CSS selector (``SiteConfig`` stand-in, see below)      -- 0.80
  4. price-parser over visible candidate text near the title          -- 0.60
  5. LLM extractor (schema-strict, budget-capped)                     -- 0.60, DEFERRED

"Un precio dudoso es peor que ningun precio" (plan section 6.4): if every
tier fails, this module raises ``ExtractionFailed`` rather than returning a
fabricated or best-guess price. A later PR's ``jobs/price_intelligence.py``
is expected to catch it and emit the plan's ``extraction_failed`` event
(``src/pricing_intel/events.py`` does not exist yet -- out of this PR's
scope, this module has no event-bus/I-O dependency).

Every candidate funnels its raw price text through ``normalize.py``'s
``normalize_price_string`` (plan: "TODO precio... pasa por price-parser
hacia Decimal") -- even the JSON-LD/microdata tiers whose "19.99"-style
numbers look pre-clean -- one normalizer, one set of edge cases, instead of
duplicating ad hoc ``Decimal(...)`` calls per tier. A malformed price on one
candidate (e.g. one ``Offer`` node in a JSON-LD array, or one text node near
the title) is skipped in favor of the next candidate at the *same* tier
before falling through to the next tier -- a single bad node never
disqualifies an otherwise-good tier.

Tier 3 (selector) is a deliberate stand-in for PR-12's ``SiteConfig``-backed,
versioned selector registry (``config/sites/*.yaml`` -- not wired yet, plan
section 6.1/6.7): this PR accepts a plain ``selector``/``selector_version``
pair so the cascade's shape and confidence ordering are already correct and
testable; PR-12 is expected to pass ``site_config.selectors_version`` and a
selector string it loads from YAML, not to change this function's contract.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from .acquire.structured import extract_product_metadata
from .normalize import PRICE_PARSER_VERSION, PriceNormalizationError, normalize_price_string

TIER_CONFIDENCE: dict[str, float] = {
    "json_ld": 0.98,
    "microdata": 0.90,
    "selector": 0.80,
    "price_parser": 0.60,
    "llm": 0.60,
}

# schema.org Offer.availability values (bare token or full
# "https://schema.org/InStock"-style URI) mapped down to the three values
# models.CompetitorOffer.availability accepts (AVAILABILITY_VALUES).
# Anything not in this table resolves to None -- "unknown", never guessed.
_AVAILABILITY_MAP: dict[str, str] = {
    "instock": "InStock",
    "limitedavailability": "InStock",
    "instorenow": "InStock",
    "onlineonly": "InStock",
    "outofstock": "OutOfStock",
    "backorder": "OutOfStock",
    "soldout": "OutOfStock",
    "discontinued": "OutOfStock",
    "preorder": "Preorder",
    "presale": "Preorder",
}

# Heuristic signals for tier 4's "text node near a currency symbol" search
# (plan: "nodos con simbolo de moneda cerca del titulo"). Small and
# deliberately conservative -- a false-positive candidate just fails to
# parse in normalize_price_string and the scan moves to the next node.
_CURRENCY_SYMBOLS = ("$", "€", "£", "¥")
_ISO_CODES_IN_TEXT = ("USD", "EUR", "MXN", "BRL", "CLP", "GBP", "JPY", "COP", "ARS", "CAD", "PEN")


class ExtractionFailed(Exception):
    """Every cascade tier (1-5) failed to produce a valid price for this
    page. Never fabricated -- callers should surface this as the plan's
    ``extraction_failed`` event, not swallow it."""

    def __init__(self, attempts: tuple[str, ...]) -> None:
        self.attempts = attempts
        super().__init__(f"all extraction tiers failed: {', '.join(attempts) if attempts else 'none attempted'}")


class ExtractionDependencyMissing(RuntimeError):
    """The selector/price-parser tiers need BeautifulSoup (the ``bs4`` package),
    which only the optional ``pricing-intel`` extra installs -- the base
    ``.[web,mcp]`` production install does not. Distinct from
    ``ExtractionFailed`` ("the page had no recoverable price") and deliberately
    NOT a subclass of it, so the cascade's per-candidate
    ``except PriceNormalizationError`` never silently swallows a missing-
    dependency environment error: it propagates loudly with an actionable
    "install the extra" message instead of masquerading as "no price found"."""


def _load_beautifulsoup():
    """Import ``BeautifulSoup`` on demand so this module stays import-safe on
    the app's boot chain (``pricing_intel/__init__`` -> ``extract``) even when
    the optional ``pricing-intel`` extra is absent. Behavior is unchanged when
    ``bs4`` is installed (dev/CI/tests); only a runtime call into the HTML-
    parsing tiers without it raises."""
    try:
        from bs4 import BeautifulSoup
    except ImportError as exc:  # pragma: no cover - exercised only in a prod-like env without the extra
        raise ExtractionDependencyMissing(
            "the selector / price-parser extraction tiers require BeautifulSoup; "
            "install the pricing-intel extra: pip install '.[pricing-intel]'"
        ) from exc
    return BeautifulSoup


@dataclass(frozen=True)
class ExtractionResult:
    """One successful cascade result -- the raw facts a later PR's job
    assembles into a ``models.CompetitorOffer`` alongside context this
    module does not have (site, competitor_sku_ref, observed_at, match).
    """

    price: Decimal
    currency: str
    availability: str | None  # None when the source didn't state it (unknown, not "missing")
    list_price: Decimal | None
    tier: str  # "json_ld" | "microdata" | "selector" | "price_parser" | "llm"
    confidence: float
    extractor: str
    extractor_version: str


def extract_price(
    html: str,
    *,
    currency_hint: str | None = None,
    selector: str | None = None,
    selector_version: str | None = None,
) -> ExtractionResult:
    """Run the 5-level cascade against ``html``; return the first tier that
    produces a valid price.

    ``currency_hint`` is an ISO 4217 code the caller already knows for this
    domain (e.g. a future ``SiteConfig``'s declared market) -- consulted
    only when a tier's own data doesn't state a currency (tiers 1/2 prefer
    their own stated ISO code over the hint; tiers 3/4 have no currency of
    their own and rely on it entirely).

    ``selector``/``selector_version`` stand in for PR-12's SiteConfig-backed
    selector registry (see module docstring) -- omitting ``selector`` skips
    straight from tier 2 to tier 4.

    Raises ``ExtractionFailed`` if no tier produces a valid price.
    """
    attempts: list[str] = []
    meta = extract_product_metadata(html)

    for offer in meta.json_ld_offers:
        result = _offer_dict_to_result(
            offer,
            tier="json_ld",
            extractor=f"structured:{meta.json_ld_source}",
            extractor_version=meta.json_ld_source_version,
            currency_hint=currency_hint,
        )
        if result is not None:
            return result
    attempts.append("json_ld")

    tier2_candidates = list(meta.microdata_offers)
    if meta.opengraph_price is not None:
        tier2_candidates.append(meta.opengraph_price)
    for offer in tier2_candidates:
        result = _offer_dict_to_result(
            offer,
            tier="microdata",
            extractor=f"structured:{meta.structured_source}",
            extractor_version=meta.structured_source_version,
            currency_hint=currency_hint,
        )
        if result is not None:
            return result
    attempts.append("microdata")

    if selector:
        result = _try_selector(html, selector, selector_version, currency_hint=currency_hint)
        if result is not None:
            return result
    attempts.append("selector")

    result = _try_price_parser_candidate(html, currency_hint=currency_hint)
    if result is not None:
        return result
    attempts.append("price_parser")

    result = _extract_via_llm(html)
    if result is not None:
        return result
    attempts.append("llm")

    raise ExtractionFailed(tuple(attempts))


# -- tier 1/2: structured-data Offer dicts (JSON-LD, microdata, OpenGraph) --


def _extract_offer_fields(offer: dict) -> tuple[str | None, str | None, str | None, str | None]:
    """Pull (price_text, currency, availability_text, list_price_text) out
    of one Offer-shaped dict. Any field may be ``None`` -- a dict with a
    price but no currency is still worth trying (a ``currency_hint`` may
    cover it); a dict with no price at all yields ``price_text=None`` so the
    caller moves on to the next candidate."""

    def _scalar(value: object) -> str | None:
        if isinstance(value, dict):
            value = value.get("@value")
        if value in (None, ""):
            return None
        return str(value)

    price_text = _scalar(offer.get("price"))
    currency = _scalar(offer.get("priceCurrency"))
    availability = _scalar(offer.get("availability"))
    list_price_text = _scalar(offer.get("listPrice") or offer.get("originalPrice") or offer.get("regularPrice"))
    return price_text, currency, availability, list_price_text


def _normalize_availability(raw: str | None) -> str | None:
    if not raw:
        return None
    token = raw.rsplit("/", 1)[-1].strip().lower()
    return _AVAILABILITY_MAP.get(token)


def _offer_dict_to_result(
    offer: dict,
    *,
    tier: str,
    extractor: str,
    extractor_version: str,
    currency_hint: str | None = None,
) -> ExtractionResult | None:
    price_text, currency, availability_text, list_price_text = _extract_offer_fields(offer)
    if price_text is None:
        return None
    try:
        normalized = normalize_price_string(price_text, currency=currency or currency_hint)
    except PriceNormalizationError:
        return None

    list_price: Decimal | None = None
    if list_price_text is not None:
        try:
            list_price = normalize_price_string(list_price_text, currency=currency or currency_hint).amount
        except PriceNormalizationError:
            list_price = None  # a malformed list_price never blocks a good price

    return ExtractionResult(
        price=normalized.amount,
        currency=normalized.currency,
        availability=_normalize_availability(availability_text),
        list_price=list_price,
        tier=tier,
        confidence=TIER_CONFIDENCE[tier],
        extractor=extractor,
        extractor_version=extractor_version,
    )


# -- tier 3: versioned CSS selector (SiteConfig stand-in) -------------------


def _try_selector(
    html: str, selector: str, selector_version: str | None, *, currency_hint: str | None
) -> ExtractionResult | None:
    soup = _load_beautifulsoup()(html, "html.parser")
    node = soup.select_one(selector)
    if node is None:
        return None
    text = node.get_text(strip=True)
    if not text:
        return None
    try:
        normalized = normalize_price_string(text, currency=currency_hint)
    except PriceNormalizationError:
        return None
    return ExtractionResult(
        price=normalized.amount,
        currency=normalized.currency,
        availability=None,
        list_price=None,
        tier="selector",
        confidence=TIER_CONFIDENCE["selector"],
        extractor="selector",
        extractor_version=selector_version or "unversioned",
    )


# -- tier 4: price-parser over visible candidate text ------------------------


def _looks_like_price_text(text: str) -> bool:
    if not any(ch.isdigit() for ch in text):
        return False
    if any(symbol in text for symbol in _CURRENCY_SYMBOLS):
        return True
    upper = text.upper()
    return any(code in upper for code in _ISO_CODES_IN_TEXT)


def _try_price_parser_candidate(html: str, *, currency_hint: str | None) -> ExtractionResult | None:
    soup = _load_beautifulsoup()(html, "html.parser")
    for text in soup.stripped_strings:
        if not _looks_like_price_text(text):
            continue
        try:
            normalized = normalize_price_string(text, currency=currency_hint)
        except PriceNormalizationError:
            continue
        return ExtractionResult(
            price=normalized.amount,
            currency=normalized.currency,
            availability=None,
            list_price=None,
            tier="price_parser",
            confidence=TIER_CONFIDENCE["price_parser"],
            extractor="price_parser",
            extractor_version=PRICE_PARSER_VERSION,
        )
    return None


# -- tier 5: LLM extractor -- DEFERRED (plan rule 10, budget cap, pydantic) --


def _extract_via_llm(html: str) -> ExtractionResult | None:
    """Tier 5 stub (plan section 6.4 point 5). NOT WIRED in this PR: a real
    implementation needs a strict pydantic schema, a daily budget cap, and
    the plan rule-10 cross-verification marker (an ``extractor="llm"``
    result gets re-checked deterministically on its next read) -- none of
    which exist yet for pricing-intel. Always returns ``None`` so the
    cascade degrades to ``ExtractionFailed`` exactly like "tried the LLM and
    it found nothing" would. Do not call this expecting a result; it is a
    documented no-op until a future PR wires an LLM provider here."""
    return None
