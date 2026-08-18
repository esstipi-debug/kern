"""Billing helpers for the masivo tiers. Reuses webapp.offers OFFERS + CTA
resolution (no new Stripe SDK dependency). The checkout URL is whatever
``STRIPE_LINK_<SLUG>`` the operator configured (or mailto degrade), so this
module is safe to import even before Stripe is wired.

Pure surface only: it does NOT call the Stripe API. Creating real Checkout
Sessions is a future step once per-customer sub-metering is needed.
"""

from __future__ import annotations

from webapp.offers import get_offer, resolve_pagar_cta


def create_checkout_session(offer_slug: str, sku_count: int | None = None) -> dict:
    """Return a ready-to-redirect checkout descriptor for ``offer_slug``.

    Mirrors ``src/commercial_pricing.quote_price``'s fairness band without
    importing nosilo-core (keeps the webapp import path light): the ceiling is
    enforced client-side via the offer's ``stripe_env_var`` link, which the
    operator points at a capped Stripe price.

    Returns:
        {
            "offer": Offer,
            "stripe_env_var": str,
            "sku_count": int|None,
            "checkout_url": str,         # stripe or mailto
            "checkout_kind": "stripe"|"mailto",
            "ceiling_hit": bool,         # True if sku_count exceeds starter_mass ceiling
        }
    """
    offer = get_offer(offer_slug)
    if offer is None:
        raise ValueError(f"unknown offer slug: {offer_slug!r}")

    cta = resolve_pagar_cta(offer)

    ceiling_hit = False
    if offer_slug == "starter-mass" and sku_count is not None:
        ceiling_hit = sku_count > 500

    return {
        "offer": offer,
        "stripe_env_var": offer.stripe_env_var,
        "sku_count": sku_count,
        "checkout_url": cta.href,
        "checkout_kind": cta.kind,
        "ceiling_hit": ceiling_hit,
    }


__all__ = ["create_checkout_session"]
