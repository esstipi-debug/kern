"""Batch, portfolio-level price elasticity with empirical-Bayes shrinkage
(Linchpin 3.0 PR-16, P2 `price_optimization`).

Extends ``src/pricing.py``'s existing per-SKU machinery
(``estimate_elasticity`` / ``ElasticityFit``, already a log-log OLS with an
``identified`` "enough price variation" flag) -- this module does not
reimplement that fit. It adds three things ``pricing.py`` deliberately does
not: a confidence interval on the per-SKU coefficient, a category-level
(pooled) elasticity, and precision-weighted shrinkage of each SKU's own
estimate toward its category.

1. **Confidence interval.** ``statsmodels`` OLS (optional ``[elasticity]``
   extra) gives a coefficient + CI out of the box. When the extra is not
   installed, :func:`fit_sku_elasticity_ci` falls back to the closed-form
   OLS standard error (``se = sqrt(MSE / Sxx)``) and a Student-t interval via
   ``scipy.stats`` (already a core dependency -- no extra needed for the
   fallback). Both paths solve the same normal equations as
   ``src.pricing.estimate_elasticity``'s ``np.polyfit`` call, so the point
   estimate always matches ``ElasticityFit.elasticity`` exactly and the two
   CI code paths agree with each other to floating-point precision.

2. **Category-level elasticity.** SKUs are grouped by a caller-supplied
   ``category_col``; when omitted, every SKU falls into one category named
   ``"_default"`` (documented convention -- there is no principled default
   grouping without domain input, so "no grouping" is itself the sensible
   default rather than an invented taxonomy). The category value is the
   **precision-weighted (inverse-variance) mean** of its "contributing" SKUs
   -- those that are ``identified`` AND have a strictly positive, finite
   standard error (``n_points >= MIN_POINTS_FOR_SE``, i.e. enough points for
   a residual-based SE to exist at all). A SKU with too few points to carry
   its own SE (a "thin" SKU) does not distort the category prior; it only
   *receives* shrinkage from it.

3. **Shrinkage.** A simplified DerSimonian-Laird (method-of-moments)
   estimator gives the between-SKU variance ``tau^2`` per category from its
   contributing SKUs. Each SKU's shrinkage weight is the classic
   precision-weighted (James-Stein-style) empirical-Bayes formula
   ``w = tau^2 / (tau^2 + se^2)``:
     - ``se`` undefined (fewer than ``MIN_POINTS_FOR_SE`` points) -> ``w=0``,
       fully deferred to the category (a thin SKU "inherits" its category).
     - ``se == 0`` (a perfect, zero-residual fit) -> ``w=1``, the SKU's own
       exact value is kept untouched.
     - otherwise the formula above, which naturally goes to ``0`` when
       ``tau2 == 0`` (no evidence categories differ -> pool fully) and to
       ``1`` when ``se`` is tiny relative to ``tau2`` (an unusually precise
       SKU is trusted over the category).
   ``shrunk = w * own_elasticity + (1 - w) * category_elasticity``.

**Design note on CI gating (read before wiring this into a pricing job):**
``SkuElasticityFit.ci_excludes_zero`` is computed from the SKU's **own**
confidence interval only, never a shrinkage-adjusted one. A thin SKU
therefore always reports ``ci_excludes_zero=False`` (there is no CI to
exclude anything with) even though ``shrunk_elasticity`` is populated and
economically informative -- this is deliberate: ``src/price_optimizer.py``
gates an actual price *move* on ``ci_excludes_zero``, and moving a specific
SKU's price off a category-borrowed number the SKU itself cannot statistically
support is exactly the "fabricated number" the plan's QA invariants forbid.
The shrunk value is still useful for reporting / a category-level view; a
category-CI-informed gate for thin SKUs is a documented future extension,
intentionally left out of this PR's ~50-line-numpy scope.
"""

from __future__ import annotations

import importlib.util
from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import stats as scipy_stats

from src.pricing import ElasticityFit, estimate_elasticity

# Fewer than this many (price, quantity) points leaves zero residual degrees
# of freedom (df = n_points - 2 < 1) -- no standard error can be computed at
# all, let alone a confidence interval.
MIN_POINTS_FOR_SE = 3

# Category assigned when the caller passes no `category_col` (documented
# convention -- see module docstring point 2).
DEFAULT_CATEGORY = "_default"

CONFIDENCE_LEVEL = 0.95


def statsmodels_available() -> bool:
    """True when the optional ``statsmodels`` package (the ``[elasticity]``
    extra) is importable."""
    return importlib.util.find_spec("statsmodels") is not None


@dataclass(frozen=True)
class ElasticityCI:
    """A per-SKU elasticity fit plus its confidence interval."""

    fit: ElasticityFit
    se: float | None  # None when unidentified or n_points < MIN_POINTS_FOR_SE
    ci_low: float | None
    ci_high: float | None
    ci_excludes_zero: bool


def _ci_excludes_zero(ci_low: float | None, ci_high: float | None) -> bool:
    if ci_low is None or ci_high is None:
        return False
    return ci_low > 0.0 or ci_high < 0.0


def fit_sku_elasticity_ci(prices: object, quantities: object) -> ElasticityCI:
    """Fit one SKU's elasticity (via ``src.pricing.estimate_elasticity``) and
    attach a 95% confidence interval on the coefficient.

    Uses ``statsmodels`` OLS when installed; otherwise the identical
    closed-form computation via numpy + ``scipy.stats.t`` (see module
    docstring point 1 -- same numbers either way).
    """
    fit = estimate_elasticity(prices, quantities)
    if not fit.identified or fit.n_points < MIN_POINTS_FOR_SE:
        return ElasticityCI(fit, None, None, None, False)

    p = np.asarray(list(prices), dtype=float)
    q = np.asarray(list(quantities), dtype=float)
    mask = (p > 0) & (q > 0)  # identical filter to estimate_elasticity's own
    lp, lq = np.log(p[mask]), np.log(q[mask])

    if statsmodels_available():
        import statsmodels.api as sm

        model = sm.OLS(lq, sm.add_constant(lp)).fit()
        se = float(model.bse[1])
        ci = model.conf_int(alpha=1 - CONFIDENCE_LEVEL)
        ci_low, ci_high = float(ci[1][0]), float(ci[1][1])
    else:
        n = lp.size
        df = n - 2
        pred = np.log(fit.scale) + fit.elasticity * lp
        sse = float(np.sum((lq - pred) ** 2))
        sxx = float(np.sum((lp - lp.mean()) ** 2))
        mse = sse / df
        se = float(np.sqrt(mse / sxx)) if sxx > 0 else 0.0
        t_crit = float(scipy_stats.t.ppf(1 - (1 - CONFIDENCE_LEVEL) / 2, df))
        margin = t_crit * se
        ci_low, ci_high = fit.elasticity - margin, fit.elasticity + margin

    return ElasticityCI(fit, se, ci_low, ci_high, _ci_excludes_zero(ci_low, ci_high))


def shrinkage_weight(se: float | None, tau2: float) -> float:
    """Precision-weighted (empirical-Bayes) weight on a SKU's own estimate.

    ``1.0`` keeps the SKU's own value entirely; ``0.0`` defers entirely to
    the category. See module docstring point 3 for the three cases.
    """
    if se is None:
        return 0.0
    if se == 0.0:
        return 1.0
    return tau2 / (tau2 + se**2)


def shrink_toward_category(own_elasticity: float, weight: float, category_elasticity: float) -> float:
    """Blend a SKU's own elasticity with its category's, per ``weight``."""
    return weight * own_elasticity + (1.0 - weight) * category_elasticity


def _category_summary(entries: list[tuple[str, float, float]]) -> tuple[float, float, int]:
    """Precision-weighted category mean + between-SKU variance (a simplified
    DerSimonian-Laird method-of-moments ``tau^2``) from "contributing" SKUs
    -- ``(product_id, elasticity, se)`` triples with ``se > 0`` (see module
    docstring point 2). Returns ``(mean, tau2, n_contributors)``.
    """
    elasticities = np.array([e for _, e, _ in entries], dtype=float)
    ses = np.array([s for _, _, s in entries], dtype=float)
    n = len(entries)
    weights = 1.0 / (ses**2)
    mean = float(np.sum(weights * elasticities) / np.sum(weights))
    if n < 2:
        return mean, 0.0, n
    q_stat = float(np.sum(weights * (elasticities - mean) ** 2))
    sum_w = float(np.sum(weights))
    sum_w2 = float(np.sum(weights**2))
    c = sum_w - sum_w2 / sum_w
    tau2 = max(0.0, (q_stat - (n - 1)) / c) if c > 0 else 0.0
    return mean, tau2, n


@dataclass(frozen=True)
class SkuElasticityFit:
    """One SKU's elasticity, its confidence interval, its category's pooled
    elasticity, and the empirical-Bayes-shrunk estimate that blends the two.
    """

    product_id: str
    category: str
    elasticity: float  # SKU's own OLS estimate (== ElasticityFit.elasticity)
    se: float | None
    ci_low: float | None
    ci_high: float | None
    r_squared: float
    n_points: int
    identified: bool  # propagated verbatim from ElasticityFit.identified
    ci_excludes_zero: bool  # own CI only -- see module docstring
    category_elasticity: float | None
    category_n_contributors: int
    shrinkage_weight: float | None  # None only when identified is False
    shrunk_elasticity: float | None  # None only when identified is False


def estimate_portfolio_elasticities(
    history: pd.DataFrame,
    *,
    product_col: str = "product_id",
    price_col: str = "price",
    quantity_col: str = "quantity",
    category_col: str | None = None,
) -> dict[str, SkuElasticityFit]:
    """Fit + shrink elasticity for every SKU in a long-format price/quantity
    history table. Returns a dict keyed by ``product_id`` (as ``str``,
    matching ``src.forecasting_auto.forecast_portfolio``'s convention) --
    some SKUs may carry a usable ``shrunk_elasticity`` while others are
    ``identified=False`` (no signal at all), in the same call.
    """
    required = {product_col, price_col, quantity_col}
    if category_col is not None:
        required.add(category_col)
    missing = required - set(history.columns)
    if missing:
        raise ValueError(f"history missing columns: {sorted(missing)}")

    # Pass 1: per-SKU OLS fit + CI, and resolve each SKU's category.
    raw: dict[str, dict] = {}
    for product_id, group in history.groupby(product_col, sort=True):
        pid = str(product_id)
        prices = group[price_col].to_numpy(dtype=float)
        quantities = group[quantity_col].to_numpy(dtype=float)
        ci = fit_sku_elasticity_ci(prices, quantities)

        if category_col is not None:
            categories = group[category_col].astype(str).unique()
            if len(categories) > 1:
                raise ValueError(f"product_id {pid!r} has inconsistent {category_col!r} values: {sorted(categories)}")
            category = categories[0]
            if not category.strip():
                raise ValueError(f"product_id {pid!r} has an empty {category_col!r} value")
        else:
            category = DEFAULT_CATEGORY

        raw[pid] = {"ci": ci, "category": category}

    # Pass 2: category-level precision-weighted mean + tau^2, from
    # contributing SKUs only (identified, se strictly positive and finite).
    by_category: dict[str, list[tuple[str, float, float]]] = {}
    for pid, r in raw.items():
        ci: ElasticityCI = r["ci"]
        if ci.fit.identified and ci.se is not None and ci.se > 0.0:
            by_category.setdefault(r["category"], []).append((pid, ci.fit.elasticity, ci.se))

    category_summary: dict[str, tuple[float, float, int]] = {
        category: _category_summary(entries) for category, entries in by_category.items()
    }

    # Pass 3: shrink each identified SKU toward its category (or leave it at
    # its own value, weight=1, when the category has no signal to lend).
    out: dict[str, SkuElasticityFit] = {}
    for pid, r in raw.items():
        ci: ElasticityCI = r["ci"]
        category = r["category"]
        summary = category_summary.get(category)
        category_elasticity, category_n = (summary[0], summary[2]) if summary else (None, 0)

        if not ci.fit.identified:
            weight, shrunk = None, None
        elif category_elasticity is None:
            weight, shrunk = 1.0, ci.fit.elasticity  # nothing to shrink toward
        else:
            tau2 = summary[1]
            weight = shrinkage_weight(ci.se, tau2)
            shrunk = shrink_toward_category(ci.fit.elasticity, weight, category_elasticity)

        out[pid] = SkuElasticityFit(
            product_id=pid,
            category=category,
            elasticity=ci.fit.elasticity,
            se=ci.se,
            ci_low=ci.ci_low,
            ci_high=ci.ci_high,
            r_squared=ci.fit.r_squared,
            n_points=ci.fit.n_points,
            identified=ci.fit.identified,
            ci_excludes_zero=ci.ci_excludes_zero,
            category_elasticity=category_elasticity,
            category_n_contributors=category_n,
            shrinkage_weight=weight,
            shrunk_elasticity=shrunk,
        )
    return out
