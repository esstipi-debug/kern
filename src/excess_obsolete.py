"""Excess & Obsolete (E&O) / dead-stock classification (plan §2.11, offline).

Pure, deterministic. Classifies on-hand stock into healthy / excess / dead from days-of-cover
and time-since-last-sale, and sizes the cash tied up in each. Dead stock has stopped moving
(no sale within a threshold, or zero demand); excess is still moving but holds far more than a
cover target; healthy is the rest. All quantities in consistent units (demand per day, cover
and last-sale in days).
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from src.i18n import DEFAULT_LANG, Lang

HEALTHY = "healthy"
EXCESS = "excess"
DEAD = "dead"

# ``recommended_action`` es el unico campo de este modulo que llega tal cual a un
# entregable cliente-facing, asi que es bilingue y sigue el idioma por defecto del
# repo (``src.i18n.DEFAULT_LANG`` = "es"). El resto de la prosa del tool sigue
# siendo nativa en ingles, como documenta ``src/i18n.py``.
_ACTIONS: dict[str, dict[Lang, str]] = {
    DEAD: {
        "es": "liquidar / devolver al proveedor / dar de baja",
        "en": "liquidate / return to vendor / write off",
    },
    EXCESS: {
        "es": "dejar de comprar; redistribuir o promocionar para bajar stock",
        "en": "stop buying; redistribute or promote to draw down",
    },
    HEALTHY: {
        "es": "monitorear; sin accion",
        "en": "monitor; no action",
    },
}


def action_for(classification: str, lang: Lang = DEFAULT_LANG) -> str:
    """Accion recomendada para una clasificacion, en ``lang`` (fallback: es)."""
    by_lang = _ACTIONS[classification]
    return by_lang.get(lang, by_lang[DEFAULT_LANG])


@dataclass(frozen=True)
class SkuStock:
    product_id: str
    on_hand: float
    daily_demand: float
    unit_cost: float = 0.0
    # None = never recorded. It used to default to 0.0, i.e. "sold today" - the reading
    # that most flatters the SKU, and the one that keeps a genuinely dead one out of the
    # count. An unknown idle time simply does not trigger the idleness rule.
    days_since_last_sale: float | None = None


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
    lang: Lang = DEFAULT_LANG,
) -> SkuEO:
    """Classify one SKU and size its at-risk (excess/dead) cash."""
    on_hand_value = stock.on_hand * stock.unit_cost
    cover = (
        stock.on_hand / stock.daily_demand
        if math.isfinite(stock.daily_demand) and stock.daily_demand > 0
        else float("inf")
    )

    idle = stock.days_since_last_sale
    long_idle = idle is not None and idle >= dead_threshold_days
    if stock.daily_demand <= 0 or long_idle:
        classification, excess_units = DEAD, max(0.0, stock.on_hand)
    elif cover > target_cover_days:
        classification = EXCESS
        excess_units = max(0.0, stock.on_hand - target_cover_days * stock.daily_demand)
    else:
        classification, excess_units = HEALTHY, 0.0

    return SkuEO(
        product_id=stock.product_id, on_hand=stock.on_hand, on_hand_value=on_hand_value,
        days_of_cover=cover, classification=classification, excess_units=excess_units,
        excess_value=excess_units * stock.unit_cost,
        recommended_action=action_for(classification, lang),
    )


def classify_excess_obsolete(
    stocks: list[SkuStock],
    *,
    target_cover_days: float = 90.0,
    dead_threshold_days: float = 180.0,
    lang: Lang = DEFAULT_LANG,
) -> list[SkuEO]:
    """Classify every SKU; rank by at-risk (excess) value descending."""
    out = [
        classify_sku(
            s,
            target_cover_days=target_cover_days,
            dead_threshold_days=dead_threshold_days,
            lang=lang,
        )
        for s in stocks
    ]
    out.sort(key=lambda e: e.excess_value, reverse=True)
    return out
