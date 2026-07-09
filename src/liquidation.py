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
                            price at all: recover a documented fraction of the cost
                            basis (return-to-vendor / jobber / write-down), no price.

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


@dataclass(frozen=True)
class LiquidationLine:
    """One excess/dead SKU's disposition: how to clear it, and what it recovers."""

    product_id: str
    classification: str          # excess | dead
    units_to_clear: float        # excess units (excess) or whole on-hand (dead)
    at_risk_value: float         # units_to_clear * unit_cost — the cash written off if nothing is done
    method: str                  # elasticity | default_discount | salvage_heuristic
    clearance_price: float | None  # unit price to sell at; None for a salvage recovery (no price)
    weeks_to_clear: float        # inf when it cannot be estimated (dead / no demand response)
    recovered_value: float       # expected cash recovered if the disposition is executed
    recovery_pct: float          # recovered_value / at_risk_value (0.0 when nothing is at risk)


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
    method = SALVAGE
    clearance_price: float | None = None
    weeks = math.inf
    recovered = salvage_recovery_pct * at_risk

    current = 0.0
    fit = None
    if history is not None:
        prices, quantities = history
        current = _current_price(prices)
        fit = estimate_elasticity(prices, quantities)

    # A non-negative elasticity (flat or upward-sloping demand) is not a usable
    # clearance curve - it would recommend cutting price to sell FEWER units, the
    # opposite of what a markdown should do. Fall through to default_discount/salvage.
    if fit is not None and fit.identified and fit.elasticity < 0 and current > 0 and units > 0:
        floor = floor_ratio * unit_cost if floor_ratio > 0 else None
        price = markdown_price(units, horizon_weeks, fit, current, floor=floor)
        rate = demand_at(fit, price)  # units per week at the clearance price
        weeks = units / rate if rate > 0 else math.inf
        method, clearance_price, recovered = ELASTICITY, price, price * units
    elif classification == EXCESS and current > 0:
        # Price known but flat: mark it down by the documented default. Excess stock
        # is still selling, so a markdown draws it down; dead stock is NOT eligible
        # here (it stopped moving — a nominal markdown will not clear it, so it
        # falls through to salvage instead).
        price = current * (1.0 - default_markdown_pct)
        weekly_demand = daily_demand * _DAYS_PER_WEEK
        weeks = units / weekly_demand if weekly_demand > 0 else math.inf
        method, clearance_price, recovered = DEFAULT_DISCOUNT, price, price * units

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
