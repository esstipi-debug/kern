"""Kern's own GMV-band commercial pricing (Stage 2 of the GMV-band GTM plan,
capability A2 in docs/superpowers/plans/2026-07-18-kern-gmv-band-gtm.md).

This module computes what Kern CHARGES a customer for a package (Starter/
Growth/Scale/Retainer). Do not confuse it with ``src/pricing.py``,
``jobs/repricing.py``, or ``src/pricing_intel/`` - those price the CLIENT's
OWN product catalog and are unrelated.

**Primary-axis resolution (the one design nuance a prior adversarial review
flagged as ambiguous - resolved in the plan's Part 2, mirrored here):**
package and revenue band are 1:1 (Starter=$1-3M/yr, Growth=$3-8M/yr,
Scale=$8M+/yr). ``base_price`` and the SKU fairness rule are looked up by
``package_key`` - **package_key is the source of truth for price, never
``annual_revenue``.** ``annual_revenue`` is used only to (a) suggest a
better-fitting package and (b) flag a mismatch when the requested package
doesn't match the buyer's declared revenue - it never silently overrides the
requested package's price. Below the lowest band (sub-$1,000,000/yr),
``needs_clarification`` is set instead of a silent clamp (route to the
LatAm reduced-scope Starter tier, or ask the buyer to confirm).

SKU count is a SECONDARY fairness adjuster, reusing the already-shipped
floor/block/ceiling arithmetic verbatim (see ``scm_agent/package_specs.py``'s
STARTER/GROWTH ``price`` strings - this module's constants are cross-checked
against that prose). Scale and Retainer are flat: ``fairness_adjustment`` is
always 0 for those two.

No I/O, pure functions/dataclasses only - mirrors ``src/contingent_fee.py``'s
shape (frozen-dataclass result, validated bounds, a floor/ceiling, a
``render_*`` prose function).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

# Sentinel ``revenue_band_key`` for packages with no revenue band at all
# (Retainer is upgrade-only, never picked cold off a revenue figure).
UNBANDED = "unbanded"

_RETAINER_KEY = "retainer"
_RETAINER_BASE_PRICE = 4_500.0
_STARTER_MASS_KEY = "starter_mass"


@dataclass(frozen=True)
class RevenueBand:
    """One package's revenue band - the PRIMARY pricing axis."""

    key: str                          # e.g. "starter_band"
    label: str                        # "USD 1,000,000-3,000,000/yr" (display)
    min_annual_revenue: float
    max_annual_revenue: float | None  # None = open-ended top band
    base_price: float                 # GMV-band list price (primary axis)


@dataclass(frozen=True)
class SkuFairnessRule:
    """The SECONDARY fairness adjuster within a package's own band."""

    included_skus: int                # covered by base_price before any add-on
    block_size: int                   # SKUs per increment (Starter 250, Growth 500)
    block_increment: float            # USD per block (Starter 40, Growth 60)
    ceiling: float                    # hard cap = next tier's floor (Starter 1500, Growth 3200)


@dataclass(frozen=True)
class PriceQuote:
    """One quote, with enough detail to explain it to a client and to flag a
    package/revenue mismatch without ever silently overriding the price."""

    package_key: str
    revenue_band_key: str
    base_price: float
    sku_count: int | None
    fairness_adjustment: float
    monthly_price: float
    ceiling_hit: bool
    explanation: str                  # feeds render_price_string

    # --- revenue-vs-package reconciliation (annual_revenue never drives price) ---
    annual_revenue: float
    revenue_band_match: bool          # False when annual_revenue doesn't fit package_key's own band
    suggested_package_key: str | None  # a better-fitting package, if one exists
    needs_clarification: bool         # annual_revenue is below every existing band (sub-$1M/yr)


# ---- the DECIDED constants (do not invent or adjust - see the plan's Part 2/5) --

_REVENUE_BANDS: dict[str, RevenueBand] = {
    "starter": RevenueBand(
        key="starter_band", label="USD 1,000,000-3,000,000/yr",
        min_annual_revenue=1_000_000.0, max_annual_revenue=3_000_000.0,
        base_price=900.0,
    ),
    _STARTER_MASS_KEY: RevenueBand(
        key="starter_band", label="USD 1,000,000-3,000,000/yr",
        min_annual_revenue=1_000_000.0, max_annual_revenue=3_000_000.0,
        base_price=79.0,
    ),
    "growth": RevenueBand(
        key="growth_band", label="USD 3,000,000-8,000,000/yr",
        min_annual_revenue=3_000_000.0, max_annual_revenue=8_000_000.0,
        base_price=1_500.0,
    ),
    "scale": RevenueBand(
        key="scale_band", label="USD 8,000,000+/yr",
        min_annual_revenue=8_000_000.0, max_annual_revenue=None,
        base_price=3_200.0,
    ),
}

# Bands are contiguous half-open intervals [min, max) except the top band,
# which is open-ended - ordered low to high so a linear scan finds the first
# (and only) match. Boundary revenue belongs to the HIGHER band (e.g. exactly
# 3,000,000 is Growth, not Starter).
_BANDED_PACKAGE_ORDER = ("starter", "growth", "scale")

# base_price for banded packages is sourced from _REVENUE_BANDS (single
# source of truth) - only Retainer needs a standalone entry, since it has no
# RevenueBand of its own.
_BASE_PRICE: dict[str, float] = {
    **{key: band.base_price for key, band in _REVENUE_BANDS.items()},
    _RETAINER_KEY: _RETAINER_BASE_PRICE,
}

_FAIRNESS_RULES: dict[str, SkuFairnessRule] = {
    "starter": SkuFairnessRule(included_skus=500, block_size=250, block_increment=40.0, ceiling=1_500.0),
    _STARTER_MASS_KEY: SkuFairnessRule(included_skus=500, block_size=250, block_increment=25.0, ceiling=290.0),
    "growth": SkuFairnessRule(included_skus=2_000, block_size=500, block_increment=60.0, ceiling=3_200.0),
    # scale and retainer intentionally absent -> flat, no fairness rule.
}

VALID_PACKAGE_KEYS = ("starter", _STARTER_MASS_KEY, "growth", "scale", _RETAINER_KEY)


def _find_band_for_revenue(annual_revenue: float) -> str | None:
    """Which banded package (starter/growth/scale) this revenue belongs to,
    or None when it's below the lowest band (sub-$1,000,000/yr - Scale's top
    is open-ended, so there is no "above every band" case)."""
    for package_key in _BANDED_PACKAGE_ORDER:
        band = _REVENUE_BANDS[package_key]
        if annual_revenue < band.min_annual_revenue:
            continue
        if band.max_annual_revenue is None or annual_revenue < band.max_annual_revenue:
            return package_key
    return None


def _apply_fairness(
    base_price: float, rule: SkuFairnessRule | None, sku_count: int | None,
) -> tuple[float, float, bool]:
    """fairness = ceil(max(0, sku_count - included) / block) * increment;
    monthly = min(base_price + fairness, ceiling) - the plan's Part 2 formula,
    verbatim. Flat packages (rule is None) and an unknown sku_count both mean
    "no fairness add", not an error - we never charge for data we don't have."""
    if rule is None or sku_count is None or sku_count <= rule.included_skus:
        return 0.0, base_price, False
    blocks = math.ceil((sku_count - rule.included_skus) / rule.block_size)
    fairness_adjustment = blocks * rule.block_increment
    uncapped = base_price + fairness_adjustment
    monthly_price = min(uncapped, rule.ceiling)
    return fairness_adjustment, monthly_price, monthly_price < uncapped


def _explain(
    package_key: str, base_price: float, rule: SkuFairnessRule | None,
    sku_count: int | None, fairness_adjustment: float, ceiling_hit: bool,
) -> str:
    label = package_key.capitalize()
    if rule is None:
        return f"{label} es un paquete flat (sin variable de SKUs): USD {base_price:,.0f}/mes fijo."
    if sku_count is None:
        return (
            f"{label}: sin conteo de SKUs informado, se cobra el piso de "
            f"USD {base_price:,.0f}/mes (hasta {rule.included_skus:,} SKUs incluidos)."
        )
    if sku_count <= rule.included_skus:
        return (
            f"{label}: {sku_count:,} SKUs esta dentro del piso incluido "
            f"({rule.included_skus:,}); no aplica ajuste de equidad. USD {base_price:,.0f}/mes."
        )
    if ceiling_hit:
        return (
            f"{label}: {sku_count:,} SKUs excede largamente el piso incluido "
            f"({rule.included_skus:,}); el ajuste de equidad se limita al techo de "
            f"USD {rule.ceiling:,.0f}/mes (tope duro - subir de techo siempre se acuerda antes)."
        )
    blocks = round(fairness_adjustment / rule.block_increment)
    return (
        f"{label}: {sku_count:,} SKUs excede el piso incluido ({rule.included_skus:,}) "
        f"en {blocks} bloque(s) de {rule.block_size:,} SKUs -> "
        f"+USD {fairness_adjustment:,.0f}/mes sobre el piso de USD {base_price:,.0f}/mes."
    )


def quote_price(package_key: str, annual_revenue: float, sku_count: int | None = None) -> PriceQuote:
    """Quote Kern's monthly price for ``package_key``.

    ``package_key`` is the source of truth for ``base_price`` and the
    fairness rule - ``annual_revenue`` never overrides it, it only informs
    ``revenue_band_match`` / ``suggested_package_key`` / ``needs_clarification``.
    """
    if package_key not in VALID_PACKAGE_KEYS:
        raise ValueError(f"package_key must be one of {VALID_PACKAGE_KEYS!r}, got {package_key!r}")
    if not math.isfinite(annual_revenue) or annual_revenue < 0:
        raise ValueError(f"annual_revenue must be finite and >= 0, got {annual_revenue!r}")
    if sku_count is not None and sku_count < 0:
        raise ValueError(f"sku_count must be >= 0 or None, got {sku_count!r}")

    base_price = _BASE_PRICE[package_key]
    rule = _FAIRNESS_RULES.get(package_key)
    fairness_adjustment, monthly_price, ceiling_hit = _apply_fairness(base_price, rule, sku_count)

    actual_band_key = _find_band_for_revenue(annual_revenue)
    if package_key == _RETAINER_KEY:
        # Retainer has no revenue band of its own (upgrade-only, never sold
        # cold off a GMV figure) - there is nothing for annual_revenue to
        # mismatch against, and nothing to suggest instead.
        revenue_band_key = UNBANDED
        revenue_band_match = True
        suggested_package_key = None
    elif package_key == _STARTER_MASS_KEY:
        revenue_band_key = _REVENUE_BANDS[package_key].key
        revenue_band_match = actual_band_key == "starter"
        suggested_package_key = (
            actual_band_key if actual_band_key is not None and actual_band_key != "starter" else None
        )
    else:
        revenue_band_key = _REVENUE_BANDS[package_key].key
        revenue_band_match = actual_band_key == package_key
        suggested_package_key = (
            actual_band_key if actual_band_key is not None and actual_band_key != package_key else None
        )
    # needs_clarification reflects annual_revenue itself (sub-$1,000,000/yr
    # fits no band at all), independent of which package_key was requested -
    # even a Retainer quote on a too-low revenue figure is worth flagging.
    needs_clarification = actual_band_key is None

    explanation = _explain(package_key, base_price, rule, sku_count, fairness_adjustment, ceiling_hit)

    return PriceQuote(
        package_key=package_key,
        revenue_band_key=revenue_band_key,
        base_price=base_price,
        sku_count=sku_count,
        fairness_adjustment=fairness_adjustment,
        monthly_price=monthly_price,
        ceiling_hit=ceiling_hit,
        explanation=explanation,
        annual_revenue=annual_revenue,
        revenue_band_match=revenue_band_match,
        suggested_package_key=suggested_package_key,
        needs_clarification=needs_clarification,
    )


def render_price_string(quote: PriceQuote) -> str:
    """Client-facing price string - same prose convention already shipped on
    ``PackageSpec.price`` / ``Offer.price`` in ``scm_agent/package_specs.py``."""
    parts = [f"USD {quote.monthly_price:,.0f} / mes ({quote.explanation})"]
    if quote.needs_clarification:
        parts.append(
            "Nota: el revenue anual declarado esta por debajo de la banda mas baja "
            "(USD 1,000,000/yr) - no hay banda GMV self-serve para este caso; "
            "confirmar alcance manualmente (candidato a Starter LatAm reducido)."
        )
    elif quote.suggested_package_key is not None:
        parts.append(
            f"Nota: el revenue anual declarado corresponde a la banda de "
            f"'{quote.suggested_package_key}', no a '{quote.package_key}' - "
            "confirmar el paquete antes de facturar."
        )
    return " ".join(parts)
