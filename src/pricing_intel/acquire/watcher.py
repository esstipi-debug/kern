"""L2 acquisition adapter: changedetection.io webhook receiver (Linchpin 3.0
PR-15, plan sections 6.1/6.2's L2 tier, 6.9's webhook route S8).

changedetection.io (self-hosted, Apache-2.0, v0.55.x per the plan's own
verification) is NOT deployed by this repo -- an operator runs their own
instance (plan S6.2: "segundo process-group en la misma maquina Fly", or
anywhere else) and points its "Re-stock & Price detection" watch
notifications at this product's ``POST /api/watch`` (``webapp/app.py``).
This module is ONLY the parser for that webhook POST body -- no network I/O
at all, matching every other module in this package's "no I/O beyond an
injected client" convention (here: no client, because there is no outbound
request to make -- the request arrives, it does not get sent).

Webhook body contract (verified 2026-07-12 against
``github.com/dgtlmoon/changedetection.io@master``,
``changedetectionio/notification_service.py``'s ``NotificationContextData``
for the base tokens and
``changedetectionio/processors/restock_diff/__init__.py``'s
``Watch.extra_notification_token_placeholder_info()`` for the
``restock.*`` tokens the "Re-stock & Price detection" processor adds): this
is OUR OWN contract, not something changedetection.io imposes -- an operator
configures ITS notification body template (JSON content type) to emit
exactly this shape, using changedetection.io's real, documented Jinja2
tokens::

    {
      "uuid": "{{uuid}}",
      "watch_url": "{{watch_url}}",
      "watch_title": "{{watch_title}}",
      "price": {{ restock.price }},
      "previous_price": {{ restock.previous_price }},
      "currency": "{{restock.currency}}",
      "in_stock": {{ 'true' if restock.in_stock else 'false' }},
      "notification_timestamp": {{ notification_timestamp }}
    }

Required for a usable observation: ``watch_url`` (a real ``http``/``https``
URL -- the competitor PDP being watched; becomes ``competitor_sku_ref`` and,
via ``acquire.base.normalize_domain``, the config/sites gate lookup key),
``price`` (changedetection.io's ``restock.price`` -- ``None``/empty means
this check's price-detection selector found nothing, not a legitimate zero),
and ``currency`` (``restock.currency`` -- populated from the watched page's
OWN JSON-LD ``priceCurrency`` when present; an operator whose watched page
has none must hardcode a literal ISO code into the notification template for
that specific watch, since changedetection.io exposes no other currency
signal). ``in_stock``/``notification_timestamp`` are optional (documented
defaults below).

**VERIFICATION CAVEAT:** this contract was designed by reading
changedetection.io's real source (the URLs above), not by pair-testing
against a running instance -- no changedetection.io deployment exists in
this environment to POST a real webhook from. Same "needs a real-request
verification pass before production use" caveat this PR's ``meli_api.py``
carries for MercadoLibre: an operator standing up a real changedetection.io
instance should configure ONE watch against this contract and confirm the
notification body renders as expected before relying on this in production.
"""

from __future__ import annotations

from datetime import datetime, timezone

from ..normalize import PriceNormalizationError, normalize_price_string
from ..sanity import RawOfferCandidate
from .base import normalize_domain

# This module's OWN adapter-logic version (provenance, plan rule 7) -- bump
# whenever parse_changedetection_webhook's field-extraction logic changes.
# changedetection.io's webhook body carries no schema version of its own
# (we define the body shape, see module docstring) -- same "adapter owns its
# version number" convention as structured.py's LDJSON_FALLBACK_VERSION and
# meli_api.py's MELI_API_VERSION.
CHANGEDETECTION_ADAPTER_VERSION = "1"
CHANGEDETECTION_EXTRACTOR = "changedetection_io_webhook"
# A 3rd-party watcher's own internal extraction is a black box we trust but
# do not control or independently verify per-read (unlike our own tier-1
# JSON-LD parse, extract.py's 0.98) -- a deliberately slightly lower,
# documented judgment call, not a measured figure.
CHANGEDETECTION_CONFIDENCE = 0.85


class ChangeDetectionWebhookError(ValueError):
    """The webhook POST body could not be parsed into a usable price
    observation -- not a JSON object, missing ``watch_url``/``price``/
    ``currency``, ``watch_url`` is not a real ``http``/``https`` URL, or the
    price could not be normalized. Never a fabricated observation (plan
    S6.4: "un precio dudoso es peor que ningun precio")."""


def _parse_timestamp(value: object) -> datetime | None:
    """``notification_timestamp`` is a Unix epoch number (changedetection.io's
    own ``time.time()`` -- see ``NotificationContextData``). Tolerant of a
    missing/unparseable value -- the caller falls back to "now" rather than
    rejecting an otherwise-good observation over a cosmetic timestamp."""
    if value in (None, ""):
        return None
    try:
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def parse_changedetection_webhook(
    payload: dict,
    *,
    matched_product_id: str | None = None,
    match_confidence: float = 1.0,
    now: datetime | None = None,
) -> RawOfferCandidate:
    """Parse one changedetection.io webhook POST body (see module docstring
    for the exact JSON contract) into a
    :class:`~src.pricing_intel.sanity.RawOfferCandidate` -- the SAME
    pre-sanity-gate shape every other acquisition tier in this package
    produces, so the caller (``webapp/app.py``'s ``POST /api/watch``) runs it
    through the identical ``sanity.py`` gate and ``PriceLedger`` every other
    tier does, never a shortcut.

    ``matched_product_id`` is the caller's job to resolve (a ``sku_map``
    reverse lookup by ``(site, competitor_sku_ref)`` -- this module has no
    ``sku_map`` dependency, matching ``src/`` "no I/O side effects beyond
    what's explicitly specified" convention) and defaults to ``None``
    ("observed, not yet matched to one of our skus" -- a legitimate,
    honestly-unmatched state, not an error).

    Raises :class:`ChangeDetectionWebhookError` -- see that class's
    docstring for every rejection reason.
    """
    if not isinstance(payload, dict):
        raise ChangeDetectionWebhookError(f"expected a JSON object, got {type(payload).__name__}")

    watch_url = payload.get("watch_url")
    if not isinstance(watch_url, str) or not watch_url.strip():
        raise ChangeDetectionWebhookError("payload is missing 'watch_url'")
    site = normalize_domain(watch_url)
    if site is None:
        raise ChangeDetectionWebhookError(f"'watch_url' is not a fetchable http(s) URL: {watch_url!r}")

    raw_price = payload.get("price")
    if raw_price in (None, ""):
        raise ChangeDetectionWebhookError(
            "payload is missing 'price' (restock.price was empty -- this watch's "
            "price-detection selector found nothing on this check)"
        )
    currency = payload.get("currency")
    if not currency:
        raise ChangeDetectionWebhookError(
            "payload is missing 'currency' (restock.currency) -- the watched page's own "
            "JSON-LD had no priceCurrency; configure it explicitly for this watch"
        )

    try:
        normalized = normalize_price_string(str(raw_price), currency=str(currency))
    except PriceNormalizationError as exc:
        raise ChangeDetectionWebhookError(f"could not normalize price {raw_price!r} {currency!r}: {exc}") from exc

    in_stock = payload.get("in_stock")
    # None (changedetection.io has not determined stock status for this
    # watch) defaults to InStock -- the SAME documented business assumption
    # jobs/price_intelligence.py's own L1 acquire step makes for "a price was
    # successfully read but availability was not stated" (see that module's
    # _acquire_one docstring): a secondary field defaulting is not the same
    # bar as "never fabricate a price".
    availability = "InStock" if in_stock or in_stock is None else "OutOfStock"

    observed_at = _parse_timestamp(payload.get("notification_timestamp")) or now or datetime.now(timezone.utc)

    return RawOfferCandidate(
        observed_at=observed_at,
        site=site,
        competitor_sku_ref=watch_url,
        matched_product_id=matched_product_id,
        match_confidence=match_confidence,
        price=normalized.amount,
        currency=normalized.currency,
        price_normalized=None,
        shipping=None,
        availability=availability,
        promo_flag=False,  # no list/regular-price signal in this contract -- see module docstring
        list_price=None,
        acquisition_tier="L2",
        extractor=CHANGEDETECTION_EXTRACTOR,
        extractor_version=CHANGEDETECTION_ADAPTER_VERSION,
        extraction_confidence=CHANGEDETECTION_CONFIDENCE,
    )
