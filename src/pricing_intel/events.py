"""Market-signal event detection for the pricing titan (Linchpin 3.0 PR-15,
plan section 6.1 file tree: "events.py emision a scm_agent.events (ver
6.8)"; section 6.8's event list: ``price_move`` / ``competitor_oos`` /
``promo_detected`` / ``new_competitor_listing``).

Pure detection over two ALREADY-ACQUIRED, ALREADY-SANITY-GATED
:class:`~src.pricing_intel.models.CompetitorOffer` readings for the SAME
``(site, competitor_sku_ref)`` pair -- ``offer`` (this cycle's accepted
reading) versus ``previous_offer`` (the ``PriceLedger``'s prior latest
reading, or ``None`` on a pair's first-ever reading). Mirrors
``scm_agent/monitors.py``'s own "pure function -> candidate Events ->
optional ``EventLedger.emit()`` dedup" convention exactly (see that module's
docstring) -- NOT a parallel event mechanism. This module never constructs
its own ``EventLedger``; the SAME ledger every other titan health event
(``extraction_failed``/``site_degraded``/``stale_feed``, already emitted by
``sanity.py``/``acquire/base.py``) flows through is what a caller passes in
here too.

Both acquisition paths PR-15 wires -- ``jobs/price_monitor.py``'s scheduled
L0 MercadoLibre poll AND ``webapp/app.py``'s ``POST /api/watch`` L2
changedetection.io receiver -- call this SAME module (via
``jobs/price_monitor.py``'s ``accept_observation``, the one place both paths
converge) rather than each re-deriving their own "is this a meaningful
change" logic, so a price move looks identical in the Tower regardless of
which acquisition tier observed it.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from scm_agent.events import Event, EventLedger

from .models import CompetitorOffer

SOURCE = "pricing_intel.events"

# abs(delta_pct) at/above this fraction is a "high" severity price_move,
# below it "medium" -- there is no "low": any price change that survived
# sanity.py's own gates (a QUARANTINEd, unconfirmed >40% jump never reaches
# this module at all -- see jobs/price_monitor.py's accept_observation) is
# already a confirmed, worth-a-look market signal, not noise.
_HIGH_SEVERITY_DELTA = Decimal("0.10")


def _dedup_key(event_type: str, offer: CompetitorOffer) -> str:
    return f"{event_type}:{offer.site}:{offer.competitor_sku_ref}"


def _emit(candidates: list[Event], ledger: EventLedger | None) -> list[Event]:
    """Same convention as ``scm_agent.monitors._emit`` / ``sanity._emit_one``:
    with a ledger, keep only the ones actually (newly) recorded; with
    ``ledger=None``, return every candidate unfiltered (what makes this
    module trivially unit-testable without SQLite)."""
    if ledger is None:
        return candidates
    return [e for e in candidates if ledger.emit(e)]


def detect_market_signal_events(
    offer: CompetitorOffer,
    previous_offer: CompetitorOffer | None,
    *,
    source: str = SOURCE,
    ledger: EventLedger | None = None,
) -> list[Event]:
    """``price_move`` / ``competitor_oos`` / ``promo_detected`` candidates
    comparing ``offer`` against ``previous_offer``. Returns ``[]`` when
    ``previous_offer`` is ``None`` -- a pair's first-ever reading has
    nothing to compare a "move" against yet, so nothing fires (this is
    NOT a gap: the offer itself was already durably appended to the ledger
    by the caller before this function runs).

    ``competitor_oos``/``promo_detected`` fire on a TRANSITION (was not ->
    is now), not on every cycle a competitor stays out of stock / stays on
    promo -- ``EventLedger``'s own dedup window would eventually collapse a
    naive "every cycle" re-fire anyway, but a transition-based check is a
    more honest "detected" framing and throttles even across dedup-window
    boundaries.

    Reference example (see ``tests/test_pricing_intel_events.py``):
    previous ``price_normalized=Decimal("100.00")``, new
    ``price_normalized=Decimal("92.00")`` -> ``delta_pct=Decimal("-0.08")``
    -> one ``price_move`` event, severity "medium" (``abs(delta_pct) <
    0.10``). previous ``availability="InStock"``, new ``="OutOfStock"`` ->
    one ``competitor_oos`` event. previous ``promo_flag=False``, new
    ``=True`` -> one ``promo_detected`` event. All three payloads carry
    ``site``/``competitor_sku_ref``/``matched_product_id`` -- procedence
    (plan rule 7) travels with a market-signal event exactly like it does
    with the offer itself.
    """
    if previous_offer is None:
        return []

    candidates: list[Event] = []

    if offer.price_normalized != previous_offer.price_normalized:
        delta_pct = (offer.price_normalized - previous_offer.price_normalized) / previous_offer.price_normalized
        severity = "high" if abs(delta_pct) >= _HIGH_SEVERITY_DELTA else "medium"
        candidates.append(Event(
            type="price_move",
            severity=severity,
            source=source,
            dedup_key=_dedup_key("price_move", offer),
            sku=offer.matched_product_id,
            payload={
                "site": offer.site,
                "competitor_sku_ref": offer.competitor_sku_ref,
                "matched_product_id": offer.matched_product_id,
                "old_price_normalized": str(previous_offer.price_normalized),
                "new_price_normalized": str(offer.price_normalized),
                "delta_pct": str(delta_pct),
                "acquisition_tier": offer.acquisition_tier,
                "message": (
                    f"{offer.matched_product_id or offer.competitor_sku_ref} @ {offer.site}: price moved "
                    f"{previous_offer.price_normalized} -> {offer.price_normalized} ({float(delta_pct):+.2%})"
                ),
            },
            ts=offer.observed_at,
        ))

    if offer.availability == "OutOfStock" and previous_offer.availability != "OutOfStock":
        candidates.append(Event(
            type="competitor_oos",
            severity="medium",
            source=source,
            dedup_key=_dedup_key("competitor_oos", offer),
            sku=offer.matched_product_id,
            payload={
                "site": offer.site,
                "competitor_sku_ref": offer.competitor_sku_ref,
                "matched_product_id": offer.matched_product_id,
                "message": f"{offer.matched_product_id or offer.competitor_sku_ref} @ {offer.site} went out of stock",
            },
            ts=offer.observed_at,
        ))

    if offer.promo_flag and not previous_offer.promo_flag:
        candidates.append(Event(
            type="promo_detected",
            severity="low",
            source=source,
            dedup_key=_dedup_key("promo_detected", offer),
            sku=offer.matched_product_id,
            payload={
                "site": offer.site,
                "competitor_sku_ref": offer.competitor_sku_ref,
                "matched_product_id": offer.matched_product_id,
                "price_normalized": str(offer.price_normalized),
                "list_price": str(offer.list_price) if offer.list_price is not None else None,
                "message": f"{offer.matched_product_id or offer.competitor_sku_ref} @ {offer.site}: promo detected",
            },
            ts=offer.observed_at,
        ))

    return _emit(candidates, ledger)


def new_competitor_listing_event(
    *,
    site: str,
    competitor_sku_ref: str,
    matched_product_id: str | None,
    now: datetime,
    source: str = SOURCE,
    ledger: EventLedger | None = None,
) -> Event | None:
    """A ``new_competitor_listing`` candidate for a ``(site,
    competitor_sku_ref)`` pair the CALLER has already determined has no
    prior ledger reading at all (this module has no ``PriceLedger``
    dependency -- that check is the caller's job; PR-15's only caller is
    ``webapp/app.py``'s ``POST /api/watch``: a changedetection.io watch
    reporting a competitor URL Kern has never observed before is the
    honest, narrow definition of "new" this event uses -- "new to our
    records", not a claim about when the listing actually went live on the
    competitor's site).

    Returns the recorded :class:`~scm_agent.events.Event`, or ``None`` when
    ``ledger`` is given and the dedup window already recorded this pair
    (same convention as :func:`detect_market_signal_events`).
    """
    ev = Event(
        type="new_competitor_listing",
        severity="low",
        source=source,
        dedup_key=f"new_competitor_listing:{site}:{competitor_sku_ref}",
        sku=matched_product_id,
        payload={
            "site": site,
            "competitor_sku_ref": competitor_sku_ref,
            "matched_product_id": matched_product_id,
            "message": f"new competitor listing observed: {competitor_sku_ref} @ {site}",
        },
        ts=now,
    )
    if ledger is None:
        return ev
    return ev if ledger.emit(ev) else None
