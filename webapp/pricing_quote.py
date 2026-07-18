"""GET /api/pricing-quote - read-only HTTP adapter over ``src/commercial_pricing.py``,
Kern's own GMV-band commercial-pricing engine (Task 2, capability A2 of the
GMV-band GTM plan, ``docs/superpowers/plans/2026-07-18-kern-gmv-band-gtm.md``).

Stateless per request - no client_profile.py persistence (YAGNI per the plan; a
buyer's declared revenue/SKU count live only for the duration of one request).

**Boot-safety (critical, repo-specific):** ``webapp/app.py`` imports every route
module unconditionally at boot, so a module-level import of an optional-extra
dependency anywhere on this module's import chain crashes production even
though the test suite stays green (see that module's own docstring / SECURITY.md
for the "prod-boot" CI job this protects). This module imports ONLY
``src.commercial_pricing`` (verified zero-optional-extra: that module imports
only ``math`` and ``dataclasses``) plus FastAPI/stdlib - never
``src/pricing_intel/`` or anything else that pulls in bs4/rapidfuzz/etc.
"""

from __future__ import annotations

from dataclasses import asdict

from fastapi import APIRouter, HTTPException, Query

from src.commercial_pricing import quote_price, render_price_string

router = APIRouter()

# Public-facing package keys (scm_agent/package_specs.py's PackageSpec.key
# values, also what a future UI widget - Task 4 - would send) -> the package_key
# src/commercial_pricing.py's quote_price() actually accepts (its own
# VALID_PACKAGE_KEYS). Starter/Growth/Scale are the identical string in both
# modules; only Retainer differs - package_specs.py's PackageSpec.key is
# "retainer_ejecutivo" (not sold cold, upgrade-only) while commercial_pricing.py
# names the same package "retainer" internally (see that module's
# ``_RETAINER_KEY``). "retainer" itself is accepted too so a caller that already
# knows commercial_pricing's own vocabulary doesn't get an unnecessary 400.
#
# Deliberately NOT aliased: package_specs.py keys with no GMV-band pricing in
# commercial_pricing.py at all - e.g. "starter_latam" (flat USD 250-300/mes
# reduced-scope pricing, a different product, not a discount on Starter) and the
# one-off project/diagnostic offers in webapp/offers.py. Those fall through to
# the unknown-package 400 below rather than silently mapping onto a GMV-band
# price that was never decided for them.
_PACKAGE_KEY_ALIASES: dict[str, str] = {
    "starter": "starter",
    "growth": "growth",
    "scale": "scale",
    "retainer_ejecutivo": "retainer",
    "retainer": "retainer",
}


@router.get("/api/pricing-quote")
def api_pricing_quote(
    package: str = Query(..., description="Package key, e.g. starter/growth/scale/retainer_ejecutivo"),
    revenue: float = Query(..., ge=0, description="Buyer's declared annual revenue, USD"),
    skus: int | None = Query(None, ge=0, description="Buyer's SKU count (optional secondary fairness adjuster)"),
) -> dict:
    """Quote Kern's monthly price for a package given a buyer's declared annual
    revenue (+ optional SKU count).

    Never a 500 for caller-supplied bad input: an unmapped/unknown ``package``
    or a value ``quote_price`` itself rejects (e.g. non-finite revenue, which
    ``ge=0`` alone does not catch) both become a 400. ``needs_clarification``
    and a package/revenue-band mismatch are NOT errors - ``quote_price`` never
    silently overrides the requested package's price, so both are surfaced
    as-is in the response body for the caller (a future UI widget, Task 4) to
    render, not hidden behind a 200 that looks unconditionally clean.
    """
    package_key = _PACKAGE_KEY_ALIASES.get(package)
    if package_key is None:
        raise HTTPException(
            status_code=400,
            detail=f"unknown package {package!r}; expected one of {sorted(_PACKAGE_KEY_ALIASES)}",
        )

    try:
        quote = quote_price(package_key, revenue, skus)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return {
        "quote": asdict(quote),
        "price_string": render_price_string(quote),
    }
