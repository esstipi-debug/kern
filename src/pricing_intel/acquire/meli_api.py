"""L0 acquisition: MercadoLibre's public Items API (Linchpin 3.0 PR-15, plan
sections 6.1/6.2 -- "MercadoLibre (ICP LatAm -- prioridad 1)").

``MeliApiFetcher`` structurally implements ``acquire.base.Fetcher`` (domain,
tier, ``fetch(sku_ref) -> RawObservation``) -- unlike PR-13's
``pdp_fetcher.fetch_pdp_html`` (a bare function that predates being told to
conform to the protocol), this fetcher owns its ``domain``/``tier`` as
instance attributes, matching the protocol's own docstring literally. Gating
(``require_approved_site``) and circuit-breaker consultation are still the
CALLER's job, not this class's -- the same division of responsibility
``jobs/price_intelligence.py``'s ``_acquire_one`` already established for
``pdp_fetcher.py``, kept consistent here rather than inventing a second
"the fetcher gates itself at construction" convention in the same package.

``fetch()`` NEVER raises on a transport failure (dependency-injected
``httpx.Client``, matching ``pdp_fetcher.py``'s testing convention): it
returns a ``RawObservation`` with ``status_code=None`` instead of
``pdp_fetcher.py``'s separate ``FetchError`` type, which keeps this class's
``fetch()`` a literal, unconditional ``-> RawObservation`` (the Protocol's
exact signature) at the cost of not carrying a human-readable failure
reason -- callers only need "could not complete the request, treat as an
ordinary transient failure, never a blocking signal" (see
``acquire.base.classify_blocking_signal``'s own docstring for why a
transport-level miss must never trip the circuit breaker), which
``status_code is None`` already conveys.

Only the single-item endpoint (``GET /items/{id}``) is implemented --
exactly what a CONFIRMED ``sku_map`` pair needs re-acquired on a schedule
(the id is already known). The bulk ``/sites/{site_id}/search`` endpoint
named in the plan's file-tree comment ("Items/Search API") is NOT wired in
this PR -- it would serve competitor-DISCOVERY (an unconfirmed candidate
listing), which is a different, unbuilt workflow (no caller in this PR needs
it); a future PR can add ``search()`` here without touching this module's
existing shape.

**VERIFICATION CAVEAT (read before pointing this at a live/approved
domain):** this module was built against MercadoLibre's documented public
Items API response shape (``id``, ``site_id``, ``title``, ``price``,
``currency_id``, ``available_quantity``, ``status``, ``permalink``, ...) --
NOT verified against a live, successful response in this PR. Two real,
single, polite requests WERE made while building this PR (2026-07-12):
``GET https://api.mercadolibre.com/robots.txt`` returned
``User-agent: *\\nDisallow: /`` (robots.txt disallows automated access to
the ENTIRE host, including the public search/item endpoints), and an
unauthenticated ``GET /sites/MLA/search`` returned HTTP 403 the same day.
Per this repo's hard rule ("Fetchers respect robots.txt ... NO anti-bot
evasion, ever"), ``config/sites/api.mercadolibre.com.yaml`` records this
domain as **prohibited** (see that file for the full writeup) -- a fetcher
constructed against the REAL domain therefore refuses to run at all
(``require_approved_site`` raises before any network call happens).
``config/sites/meli-api.test.yaml`` is a synthetic, approved ``.test``
fixture standing in for a hypothetical future state where an operator has
resolved this with MercadoLibre directly (their Developers Program / OAuth
app registration is the documented sanctioned path -- a business step, not
a code change); this module's own tests exercise the happy path only
against that synthetic domain, never the real one. **A real, successful
request against a resolved domain is required before this connector is
relied on in production** -- same "needs a real-request verification pass"
caveat this repo's Odoo connector originally carried for its field mapping.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal

import httpx

from ..normalize import PriceNormalizationError, normalize_price_string
from .base import RawObservation

# The one physical API host every MercadoLibre country marketplace's public
# Items/Search API is served from -- one config/sites/*.yaml record (and one
# CircuitBreaker) governs all of them, regardless of which site_id
# (MLA/MLB/MLM/...) a given item belongs to (plan S6.2's ICP LatAm -- this
# API is shared across the whole region, not one host per country).
MELI_DOMAIN = "api.mercadolibre.com"
_BASE_URL = f"https://{MELI_DOMAIN}"

# Identifiable, honest User-Agent (plan S6.0 #5) -- same convention as
# pdp_fetcher.py's USER_AGENT, no browser impersonation, no rotation.
USER_AGENT = "LinchpinPricingIntel/1.0 (+https://kern.example/pricing-intel-bot)"

DEFAULT_TIMEOUT_SECONDS = 10.0

# This module's OWN parsing-logic version (provenance, plan rule 7) -- bump
# whenever parse_meli_item_json's field-extraction/availability-mapping logic
# changes. MercadoLibre's public API response carries no version field of its
# own to report instead (verified against the documented shape) -- same
# "adapter owns its version number" convention as structured.py's
# LDJSON_FALLBACK_VERSION.
MELI_API_VERSION = "1"
MELI_EXTRACTOR = "meli_api"
# Official first-party API JSON, not scraped/heuristic -- higher trust than
# even extract.py's own tier-1 JSON-LD (0.98), which is scraped from a page
# a competitor controls, not returned by their own systems-of-record API.
MELI_API_CONFIDENCE = 1.0


class MeliApiFetcher:
    """``acquire.base.Fetcher`` implementation for MercadoLibre's public
    Items API (L0). See module docstring for the gating/breaker division of
    responsibility and the "never raises" ``fetch()`` contract.
    """

    def __init__(
        self,
        *,
        client: httpx.Client,
        domain: str = MELI_DOMAIN,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self.domain = domain
        self.tier = "L0"
        self._client = client
        self._timeout = timeout

    def fetch(self, sku_ref: str, *, now: datetime | None = None) -> RawObservation:
        """Fetch one MercadoLibre item by its id (``sku_ref``, e.g.
        ``"MLA1234567890"`` -- the plan's own example format, S6.3's
        ``competitor_sku_ref`` docstring). ``RawObservation.html`` carries the
        raw JSON response TEXT (reusing the generic "raw body" field the
        Protocol's own docstring anticipates for "API JSON" fetchers -- see
        ``base.RawObservation``'s docstring: "per-tier fetchers ... carry
        richer native shapes of their own; this is the common denominator"),
        not markup -- callers parse it with :func:`parse_meli_item_json`,
        never ``extract.py``'s HTML cascade (this is L0, not L1).
        """
        now = now or datetime.now(timezone.utc)
        url = f"{_BASE_URL}/items/{sku_ref}"
        try:
            response = self._client.get(url, timeout=self._timeout, headers={"User-Agent": USER_AGENT})
        except httpx.HTTPError:
            # Transport-level failure -- NOT a blocking signal (see module
            # docstring). status_code=None is this class's whole "FetchError"
            # signal; see module docstring for why no separate type exists.
            return RawObservation(sku_ref=sku_ref, fetched_at=now, status_code=None, html=None)
        return RawObservation(sku_ref=sku_ref, fetched_at=now, status_code=response.status_code, html=response.text)


@dataclass(frozen=True)
class MeliItemObservation:
    """One successfully-parsed MercadoLibre item read -- the raw facts a
    caller (``jobs/price_monitor.py``) assembles into a
    ``sanity.RawOfferCandidate`` alongside context this module does not have
    (which of OUR skus it matches -- that's ``sku_map``'s job)."""

    item_id: str
    site_id: str  # MLA/MLB/MLM/... -- which country marketplace this item belongs to
    title: str
    price: Decimal
    currency: str  # ISO 4217, resolved via normalize.py (plan rule: every price funnels through it)
    availability: str  # "InStock" | "OutOfStock" -- models.AVAILABILITY_VALUES subset (see _resolve_availability)
    permalink: str
    fetched_at: datetime


class MeliParseError(Exception):
    """The MELI Items API response could not be parsed into a valid
    :class:`MeliItemObservation` -- malformed JSON, an API error envelope
    (``{"error": "not_found", "status": 404, ...}``), a missing required
    field (``id``/``site_id``/``price``/``currency_id``), or an unparseable
    price. Never a fabricated price (plan S6.4: "un precio dudoso es peor
    que ningun precio" applies to every acquisition tier, not just the HTML
    cascade) -- callers should surface this as an ``extraction_failed``
    event, matching ``extract.ExtractionFailed``'s own contract."""


# MercadoLibre's documented item "status" vocabulary (verified against the
# documented shape, see module docstring's verification caveat). Only
# "active" (with stock) resolves to InStock -- everything else, including a
# status this table has never seen, resolves to OutOfStock. Deliberately
# NOT raising for an unrecognized status: MELI's own ``status`` field is
# reliably present on every real item response, but a conservative "not
# actively purchasable" default is more honest than discarding an otherwise
# valid price read over an unrecognized status string (models.py's
# AVAILABILITY_VALUES has no "unknown" value to fall back to -- unlike
# extract.py's own tier-1/2 mapping, which CAN return None because its
# caller tolerates "availability unknown, defaulted to InStock"
# downstream -- see jobs/price_intelligence.py's own documented business
# assumption for that same default in the other direction).
_MELI_ACTIVE_STATUSES = frozenset({"active"})


def _resolve_availability(*, status: object, available_quantity: object) -> str:
    try:
        qty = int(available_quantity) if available_quantity is not None else None
    except (TypeError, ValueError):
        qty = None
    status_text = str(status).strip().lower() if status else ""
    if status_text in _MELI_ACTIVE_STATUSES and (qty is None or qty > 0):
        return "InStock"
    return "OutOfStock"


def parse_meli_item_json(raw_json_text: str, *, fetched_at: datetime) -> MeliItemObservation:
    """Parse one ``GET /items/{id}`` response body into a
    :class:`MeliItemObservation`. Pure -- no network I/O (the fetch already
    happened; see :class:`MeliApiFetcher`).

    Hand-verified reference example (see
    ``tests/test_pricing_intel_meli_api.py``):
    ``{"id": "MLA1234567890", "site_id": "MLA", "price": 899999.99,
    "currency_id": "ARS", "available_quantity": 5, "status": "active", ...}``
    -> ``price=Decimal("899999.99")``, ``currency="ARS"``,
    ``availability="InStock"``.

    Raises :class:`MeliParseError` on malformed JSON, a non-object body, an
    API error envelope, a missing required field, or an unparseable price --
    never a fabricated observation.
    """
    try:
        data = json.loads(raw_json_text)
    except json.JSONDecodeError as exc:
        raise MeliParseError(f"response body is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise MeliParseError(f"expected a JSON object, got {type(data).__name__}")

    if "error" in data and "id" not in data:
        # MELI's error envelope, e.g. {"message": "...", "error": "not_found",
        # "status": 404, "cause": []} -- a real, well-formed response, just
        # not an item.
        raise MeliParseError(f"MercadoLibre API error response: {data.get('message') or data.get('error')}")

    item_id = data.get("id")
    site_id = data.get("site_id")
    price = data.get("price")
    currency_id = data.get("currency_id")
    missing = [
        name for name, value in (("id", item_id), ("site_id", site_id), ("price", price), ("currency_id", currency_id))
        if value in (None, "")
    ]
    if missing:
        raise MeliParseError(f"MercadoLibre item response is missing required field(s): {missing}")

    try:
        normalized = normalize_price_string(str(price), currency=str(currency_id))
    except PriceNormalizationError as exc:
        raise MeliParseError(f"could not normalize price {price!r} {currency_id!r}: {exc}") from exc

    availability = _resolve_availability(status=data.get("status"), available_quantity=data.get("available_quantity"))

    return MeliItemObservation(
        item_id=str(item_id),
        site_id=str(site_id),
        title=str(data.get("title") or ""),
        price=normalized.amount,
        currency=normalized.currency,
        availability=availability,
        permalink=str(data.get("permalink") or ""),
        fetched_at=fetched_at,
    )
