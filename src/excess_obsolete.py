"""Excess & Obsolete (E&O) / dead-stock classification (plan §2.11, offline).

Pure, deterministic. Classifies on-hand stock into healthy / excess / dead from days-of-cover
and time-since-last-sale, and sizes the cash tied up in each. Dead stock has stopped moving
(no sale within a threshold, or zero demand); excess is still moving but holds far more than a
cover target; healthy is the rest. All quantities in consistent units (demand per day, cover
and last-sale in days).
"""
from __future__ import annotations

from dataclasses import dataclass

HEALTHY = "healthy"
EXCESS = "excess"
DEAD = "dead"

_ACTIONS = {
    DEAD: "liquidate / return to vendor / write off",
    EXCESS: "stop buying; redistribute or promote to draw down",
    HEALTHY: "monitor; no action",
}


@dataclass(frozen=True)
class SkuStock:
    product_id: str
    on_hand: float
    daily_demand: float
    unit_cost: float = 0.0
    days_since_last_sale: float = 0.0


@dataclass(frozen=True)
class SkuEO:
    product_id: str
    on_hand: float
    on_hand_value: float
    days_of_cover: float            # on_hand / daily_demand (inf if no demand)
    classification: str             # healthy | excess | dead
    excess_units: float             # units beyond the cover target (full on-hand if dead)
    excess_value: float             # excess_units * unit_cost (the cash at risk)
    recommended_action: str


def classify_sku(
    stock: SkuStock,
    *,
    target_cover_days: float = 90.0,
    dead_threshold_days: float = 180.0,
) -> SkuEO:
    """Classify one SKU and size its at-risk (excess/dead) cash."""
    on_hand_value = stock.on_hand * stock.unit_cost
    cover = stock.on_hand / stock.daily_demand if stock.daily_demand > 0 else float("inf")

    if stock.daily_demand <= 0 or stock.days_since_last_sale >= dead_threshold_days:
        classification, excess_units = DEAD, max(0.0, stock.on_hand)
    elif cover > target_cover_days:
        classification = EXCESS
        excess_units = max(0.0, stock.on_hand - target_cover_days * stock.daily_demand)
    else:
        classification, excess_units = HEALTHY, 0.0

    return SkuEO(
        product_id=stock.product_id, on_hand=stock.on_hand, on_hand_value=on_hand_value,
        days_of_cover=cover, classification=classification, excess_units=excess_units,
        excess_value=excess_units * stock.unit_cost, recommended_action=_ACTIONS[classification],
    )


def classify_excess_obsolete(
    stocks: list[SkuStock],
    *,
    target_cover_days: float = 90.0,
    dead_threshold_days: float = 180.0,
) -> list[SkuEO]:
    """Classify every SKU; rank by at-risk (excess) value descending."""
    out = [
        classify_sku(s, target_cover_days=target_cover_days, dead_threshold_days=dead_threshold_days)
        for s in stocks
    ]
    out.sort(key=lambda e: e.excess_value, reverse=True)
    return out
