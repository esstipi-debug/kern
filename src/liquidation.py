"""Markdown-liquidation planner — cross E&O classification with clearance pricing.

The gap this closes: ``excess_obsolete.classify_excess_obsolete`` tells you WHICH
stock is excess/dead and how much cash is stuck in it, and ``pricing.markdown_price``
can solve a clearance price from an elasticity curve — but nothing joined them into
a per-SKU disposition plan (price + weeks-to-clear + cash recovered vs. writing it
off). This module is that join.

For every excess/dead SKU it picks one of three methods, most-to-least informed:

  * ``elasticity``        — the SKU has real price-response history (an identified
                            constant-elasticity fit): solve the clearance price that
                            drains the at-risk units over the horizon (Gallego &
                            van Ryzin's fluid/deterministic limit), then read the
                            weeks-to-clear back off the fitted demand.
  * ``default_discount``  — an *excess* SKU whose price is known but flat (no
                            variation to fit elasticity from): apply a documented
                            default markdown off the current price. Weeks-to-clear
                            is a CONSERVATIVE upper bound taken at the current
                            (pre-markdown) demand rate — a markdown only lifts demand.
  * ``salvage_heuristic`` — dead stock without a usable fit, or any SKU with no
                            price at all: there is no demand curve to solve a price
                            against, so the price is anchored to the COST side
                            instead of the demand side -- ``salvage_recovery_pct *
                            unit_cost`` per unit (return-to-vendor / jobber /
                            write-down). It is a proposed per-unit RECOVERY, not an
                            advertised markdown -- see :func:`announces_discount`.

Every line proposes a per-unit price whenever there is something to anchor one to,
and names the method that produced it: "no elasticity" is a reason to price by
another method and say which, never a reason to hand back a plan with no number in
the price column. The only line without a price is a salvage line with no cost to
anchor to (``unit_cost <= 0``) -- and since the cash at risk is
``units_to_clear * unit_cost``, that line has nothing at risk either. Any line with
money at stake carries a price, zero included.

Honest limitations (documented, not hidden): this is the single-pass deterministic
limit of dynamic clearance pricing, NOT a multi-stage markdown optimiser and NOT
Smith & Achabal's inventory-depletion effect; the default-discount and salvage
rates are practitioner heuristics, not optima. See documentation/FINANCE_MARKETING_BRIDGE.md.

Pure/deterministic. Stdlib + the two engines only.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from statistics import median

from src.excess_obsolete import DEAD, EXCESS, SkuStock, classify_sku
from src.pricing import demand_at, estimate_elasticity, markdown_price

# Disposition methods, most-to-least informed.
ELASTICITY = "elasticity"
DEFAULT_DISCOUNT = "default_discount"
SALVAGE = "salvage_heuristic"

_DEFAULT_HORIZON_WEEKS = 13.0
_DEFAULT_MARKDOWN_PCT = 0.40
_DEFAULT_SALVAGE_RECOVERY_PCT = 0.30
_DAYS_PER_WEEK = 7.0

PriceHistory = dict[str, tuple[Sequence[float], Sequence[float]]]


def announces_discount(method: str) -> bool:
    """Whether a disposition method ANNOUNCES a price reduction to the public.

    ``elasticity`` and ``default_discount`` are advertised markdowns: a shopper is
    shown "was X, now Y", so EU/UK Omnibus (Directive (EU) 2019/2161 art. 6a, as
    implemented in ``src.pricing_guardrails``) applies and a prior-price reference
    is mandatory. ``salvage_heuristic`` is a DISPOSAL -- return-to-vendor, a jobber
    lot, or a write-down -- so there is no announcement, no shopper, and nothing to
    reference it against.

    The distinction lives in the METHOD, not in whether a price happens to be
    populated. A salvage line does carry a proposed per-unit recovery (anchored to
    cost, see :func:`_salvage_unit_price`), so reading "no price" as "no
    announcement" would misclassify it the moment that number is published. The
    downstream gates (``src.liquidation_calendar``'s Omnibus and competitive-floor
    checks, ``src.sop_engine.demand_plan``'s demand lift) read THIS function.
    """
    return method != SALVAGE


def _salvage_unit_price(unit_cost: float, salvage_recovery_pct: float) -> float | None:
    """Per-unit cash a salvage disposition recovers: ``salvage_recovery_pct * unit_cost``.

    Not a new heuristic and not an invented elasticity: it is the SAME recovery the
    line already books, expressed per unit instead of as a lump sum. ``at_risk_value``
    is ``units * unit_cost`` (``src.excess_obsolete.classify_sku``), so
    ``salvage_recovery_pct * at_risk_value / units`` is exactly this number. Writing
    it down makes ``recovered_value == clearance_price * units_to_clear`` hold for all
    three methods instead of only the two priced ones.

    The ONLY ``None`` case is a missing cost anchor (``unit_cost <= 0``): there is
    nothing to multiply, so the price is genuinely unknown. A zero
    ``salvage_recovery_pct`` -- legal, the validation is ``0.0 <= pct <= 1.0`` -- is
    NOT that case: the anchor is present, the multiplication happened, and the answer
    is 0.00 per unit (a write-off recovers nothing). Returning ``None`` there
    overloaded one value with two incompatible meanings, "no anchor to compute from"
    and "computed, came out zero", and every reader downstream resolved the ambiguity
    the wrong way -- ``jobs.markdown_liquidation_job._price_phrase`` printed "no
    observed price and no unit cost to anchor one to" next to a SKU that had a unit
    cost and real cash at risk. A known zero is reported as zero; only an absent
    anchor is reported as absent.
    """
    return salvage_recovery_pct * unit_cost if unit_cost > 0 else None


@dataclass(frozen=True)
class LiquidationLine:
    """One excess/dead SKU's disposition: how to clear it, and what it recovers."""

    product_id: str
    classification: str          # excess | dead
    units_to_clear: float        # excess units (excess) or whole on-hand (dead)
    at_risk_value: float         # units_to_clear * unit_cost — the cash written off if nothing is done
    method: str                  # elasticity | default_discount | salvage_heuristic
    #: Proposed per-unit price for the disposition, populated whenever there is an
    #: anchor for one: solved off the fitted demand curve (elasticity), cut off the
    #: observed price (default_discount), or anchored to cost (salvage_heuristic,
    #: ``salvage_recovery_pct * unit_cost``). ``method`` is what says WHICH of the
    #: three produced it -- and whether it is an advertised markdown at all
    #: (:func:`announces_discount`). ``None`` only when the method that ran found no
    #: anchor: in practice a salvage line with ``unit_cost <= 0``, since the two
    #: priced branches are only reachable with an observed price. The observed price
    #: does NOT rescue a salvage line -- a disposal is anchored to cost, never to the
    #: shelf -- so a dead SKU can carry a ``current_price`` and still have no
    #: ``clearance_price``. What DOES hold, and is the invariant worth relying on:
    #: ``at_risk_value > 0`` implies a price, because the cash at risk is
    #: ``units_to_clear * unit_cost`` and any risk therefore proves the cost anchor
    #: exists. A blank price column never sits next to money at stake. A zero is a
    #: price, not a blank: see :func:`_salvage_unit_price`.
    clearance_price: float | None
    weeks_to_clear: float        # inf when it cannot be estimated (dead / no demand response)
    recovered_value: float       # expected cash recovered if the disposition is executed
    recovery_pct: float          # recovered_value / at_risk_value (0.0 when nothing is at risk)
    #: Reference price the markdown is measured from, and the cut as a share of it.
    #: They do NOT go blank together, and two earlier versions of this comment said
    #: they did ("both None on a salvage recovery") while the code never behaved that
    #: way. What the code actually sustains, across all three methods:
    #:
    #: ``discount_pct`` is ``None`` on EVERY salvage line and populated on every
    #: announced one -- the METHOD decides it, not whether a price happened to be
    #: observed. It is the claim that this price is a reduction off something, and a
    #: disposal makes no such claim (:func:`announces_discount`). On the two
    #: announced branches an observed price is a precondition of even reaching them
    #: (``current > 0``), so an announced markdown never lacks its percentage.
    #:
    #: ``current_price`` is an OBSERVATION, not a claim: the median of this SKU's
    #: strictly-positive observed prices. It is ``None`` only when no positive price
    #: was observed -- which does force salvage, since both announced branches need
    #: one -- but the converse is false. A dead SKU WITH a price history is still
    #: salvaged (a disposal is anchored to cost, never to the shelf, see
    #: ``clearance_price``) and still reports the shelf price it is walking away
    #: from, next to a ``discount_pct`` of ``None``. Blanking it there would delete a
    #: fact the client supplied and that the line needs to be legible -- it is what
    #: makes a ``gross_margin_value`` of -700 readable beside "recovers 30%".
    #: Withholding the percentage withholds an inference nobody made; withholding the
    #: reference would withhold an input somebody gave.
    current_price: float | None = None
    discount_pct: float | None = None
    #: Gross margin in money if the disposition is executed: recovered cash minus the
    #: cost of the units it clears. Negative is the normal case on dead stock and is
    #: reported as-is - "recovered 30%" reads like a win until you see the sign.
    gross_margin_value: float = 0.0
    #: Days of cover the clearance frees up (units_to_clear / daily demand). ``None``
    #: when the SKU has no demand: dead stock frees no cover, it frees cash.
    cover_days_released: float | None = None


@dataclass(frozen=True)
class LiquidationReport:
    lines: tuple[LiquidationLine, ...]  # excess + dead SKUs, ranked by cash at risk desc
    n_assessed: int
    n_excess: int
    n_dead: int
    n_elasticity: int
    n_default_discount: int
    n_salvage: int
    total_at_risk: float
    total_recovered: float
    recovery_pct: float                 # total_recovered / total_at_risk (0.0 when nothing at risk)
    horizon_weeks: float
    default_markdown_pct: float
    salvage_recovery_pct: float
    summary: str


def _current_price(prices: Sequence[float]) -> float:
    """Median of the strictly-positive observed prices, mirroring recommend_price."""
    positive = [float(p) for p in prices if float(p) > 0]
    return median(positive) if positive else 0.0


def _choose_disposition(
    classification: str,
    units: float,
    at_risk: float,
    daily_demand: float,
    unit_cost: float,
    current: float,
    fit,
    *,
    horizon_weeks: float,
    default_markdown_pct: float,
    salvage_recovery_pct: float,
    floor_ratio: float,
) -> tuple[str, float | None, float, float]:
    """Pick the disposition and return ``(method, clearance_price, weeks, recovered)``.

    The three branches are ordered most-to-least informed (see module docstring).
    ``salvage_heuristic`` is the DEFAULT, not the failure case: it already proposes a
    cost-anchored per-unit price, so a SKU neither priced branch can take still leaves
    with a number and the label of the method that produced it.

    Both ANNOUNCED branches require ``EXCESS``: dead stock stopped moving, so a markdown
    does not clear it. It binds hardest on the elasticity branch, which also promises a
    DATE -- ``weeks = units / demand_at(fit, price)`` reads a rate off a curve fitted on
    demand the SKU no longer has, so a dead SKU left with a finite horizon.
    """
    # A non-negative elasticity (flat or upward-sloping demand) is not a usable
    # clearance curve - it would recommend cutting price to sell FEWER units, the
    # opposite of what a markdown should do. Fall through to default_discount/salvage.
    if (classification == EXCESS and fit is not None and fit.identified
            and fit.elasticity < 0 and current > 0 and units > 0):
        floor = floor_ratio * unit_cost if floor_ratio > 0 else None
        price = markdown_price(units, horizon_weeks, fit, current, floor=floor)
        rate = demand_at(fit, price)  # units per week at the clearance price
        weeks = units / rate if rate > 0 else math.inf
        return ELASTICITY, price, weeks, price * units

    if classification == EXCESS and current > 0:
        # Price known but flat: mark it down by the documented default. Excess stock
        # is still selling, so a markdown draws it down; dead stock is NOT eligible
        # here (it stopped moving - a nominal markdown will not clear it, so it
        # falls through to salvage instead).
        price = current * (1.0 - default_markdown_pct)
        weekly_demand = daily_demand * _DAYS_PER_WEEK
        weeks = units / weekly_demand if weekly_demand > 0 else math.inf
        return DEFAULT_DISCOUNT, price, weeks, price * units

    salvage_price = _salvage_unit_price(unit_cost, salvage_recovery_pct)
    return SALVAGE, salvage_price, math.inf, salvage_recovery_pct * at_risk


def _plan_sku(
    product_id: str,
    classification: str,
    units: float,
    at_risk: float,
    daily_demand: float,
    unit_cost: float,
    history: tuple[Sequence[float], Sequence[float]] | None,
    *,
    horizon_weeks: float,
    default_markdown_pct: float,
    salvage_recovery_pct: float,
    floor_ratio: float,
) -> LiquidationLine:
    current = 0.0
    fit = None
    if history is not None:
        prices, quantities = history
        current = _current_price(prices)
        fit = estimate_elasticity(prices, quantities)

    method, clearance_price, weeks, recovered = _choose_disposition(
        classification, units, at_risk, daily_demand, unit_cost, current, fit,
        horizon_weeks=horizon_weeks,
        default_markdown_pct=default_markdown_pct,
        salvage_recovery_pct=salvage_recovery_pct,
        floor_ratio=floor_ratio,
    )

    return LiquidationLine(
        product_id=product_id,
        classification=classification,
        units_to_clear=units,
        at_risk_value=at_risk,
        method=method,
        clearance_price=clearance_price,
        weeks_to_clear=weeks,
        recovered_value=recovered,
        recovery_pct=(recovered / at_risk) if at_risk > 0 else 0.0,
        current_price=current if current > 0 else None,
        discount_pct=(
            1.0 - clearance_price / current
            if announces_discount(method) and clearance_price is not None and current > 0
            else None
        ),
        gross_margin_value=recovered - units * unit_cost,
        cover_days_released=(units / daily_demand) if daily_demand > 0 else None,
    )


def plan_liquidation(
    stocks: list[SkuStock],
    price_history: PriceHistory | None = None,
    *,
    target_cover_days: float = 90.0,
    dead_threshold_days: float = 180.0,
    horizon_weeks: float = _DEFAULT_HORIZON_WEEKS,
    default_markdown_pct: float = _DEFAULT_MARKDOWN_PCT,
    salvage_recovery_pct: float = _DEFAULT_SALVAGE_RECOVERY_PCT,
    floor_ratio: float = 0.0,
) -> LiquidationReport:
    """Build a per-SKU liquidation plan for the excess/dead stock.

    ``price_history`` maps ``product_id -> (prices, quantities)`` in the SAME period
    as ``horizon_weeks`` (weekly buckets); SKUs absent from it (or with too little
    price variation) fall back to the default-markdown / salvage heuristics. Healthy
    stock is excluded. Lines are ranked by cash at risk, descending.

    Duplicate ``product_id``s in ``stocks`` are NOT aggregated (matching
    ``excess_obsolete_job.prepare_records``, which builds one row per CSV line with no
    groupby): each row is classified and planned independently, using only ITS OWN
    ``unit_cost``/``daily_demand`` - never another row's, even if they share an id.
    """
    if not 0.0 < default_markdown_pct < 1.0:
        raise ValueError("default_markdown_pct must be in (0, 1)")
    if not 0.0 <= salvage_recovery_pct <= 1.0:
        raise ValueError("salvage_recovery_pct must be in [0, 1]")
    if horizon_weeks <= 0.0:
        raise ValueError("horizon_weeks must be > 0")
    if floor_ratio < 0.0:
        raise ValueError("floor_ratio must be >= 0")

    history = price_history or {}
    # classify_sku (not the batch classify_excess_obsolete, which re-sorts and would
    # force an id-keyed re-lookup) preserves a 1:1 zip with `stocks`, so each line
    # always reads its OWN row's cost/demand - immune to duplicate product_ids.
    classifications = [
        classify_sku(s, target_cover_days=target_cover_days, dead_threshold_days=dead_threshold_days)
        for s in stocks
    ]
    lines = [
        _plan_sku(
            s.product_id,
            e.classification,
            e.excess_units,
            e.excess_value,
            s.daily_demand,
            s.unit_cost,
            history.get(s.product_id),
            horizon_weeks=horizon_weeks,
            default_markdown_pct=default_markdown_pct,
            salvage_recovery_pct=salvage_recovery_pct,
            floor_ratio=floor_ratio,
        )
        for s, e in zip(stocks, classifications)
        if e.classification in (EXCESS, DEAD)
    ]
    lines.sort(key=lambda line: line.at_risk_value, reverse=True)

    n_excess = sum(1 for line in lines if line.classification == EXCESS)
    n_dead = sum(1 for line in lines if line.classification == DEAD)
    n_elasticity = sum(1 for line in lines if line.method == ELASTICITY)
    n_default = sum(1 for line in lines if line.method == DEFAULT_DISCOUNT)
    n_salvage = sum(1 for line in lines if line.method == SALVAGE)
    total_at_risk = sum(line.at_risk_value for line in lines)
    total_recovered = sum(line.recovered_value for line in lines)
    recovery_pct = (total_recovered / total_at_risk) if total_at_risk > 0 else 0.0

    summary = (
        f"Liquidation plan for {len(lines)} at-risk SKU(s) ({n_excess} excess + {n_dead} dead): "
        f"recover ~{total_recovered:,.0f} of {total_at_risk:,.0f} at risk "
        f"({recovery_pct * 100:.0f}%) via {n_elasticity} elasticity-priced, "
        f"{n_default} default-markdown, {n_salvage} salvage."
    )

    return LiquidationReport(
        lines=tuple(lines),
        n_assessed=len(lines),
        n_excess=n_excess,
        n_dead=n_dead,
        n_elasticity=n_elasticity,
        n_default_discount=n_default,
        n_salvage=n_salvage,
        total_at_risk=total_at_risk,
        total_recovered=total_recovered,
        recovery_pct=recovery_pct,
        horizon_weeks=horizon_weeks,
        default_markdown_pct=default_markdown_pct,
        salvage_recovery_pct=salvage_recovery_pct,
        summary=summary,
    )
