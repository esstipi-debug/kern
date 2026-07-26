"""Inventory financial KPIs (capability M13 / methods §4.5).

Closed-form, standards-anchored metrics computed from the COGS / average-inventory /
margin signals Kern already produces. Each is exact and deterministic (finance
numbers must be auditable). No external library emits a defensible inventory-finance
pack, so these are built in-repo.

**A metric with no denominator returns ``None``, never a number.** These functions
used to encode absence as the most flattering value on the sheet: no inventory at
all returned *infinite* turns, and a stockout count over zero periods returned "0%
stockout" - exactly what a perfectly-run operation reports. The caller decides how
to render "not measurable"; it must not be handed a figure it cannot tell apart
from a measurement. Guards test ``math.isfinite`` explicitly, because ``nan > 0``
is False and would otherwise take the degenerate branch in silence.

References: SCOR Digital Standard (ASCM) — cash-to-cash; retail-math GMROI; GAAP DIO;
Chopra & Meindl, *Supply Chain Management* (7th ed.).
"""

from __future__ import annotations

import math

_DAYS_PER_YEAR = 365


def _measured(value: float | None) -> bool:
    """True when the value is a real number we can compute with."""
    return value is not None and math.isfinite(value)


def _divisible_by(value: float | None) -> bool:
    """True when the value is a real, positive denominator."""
    return _measured(value) and value > 0


def inventory_turns(cogs: float, average_inventory_value: float) -> float | None:
    """COGS / average inventory value. Higher = inventory cycles faster.

    ``None`` with no inventory to cycle: "infinite turns" is the best possible score.
    """
    if not _measured(cogs) or not _divisible_by(average_inventory_value):
        return None
    return cogs / average_inventory_value


def days_inventory_outstanding(
    average_inventory_value: float, cogs: float, period_days: int = _DAYS_PER_YEAR
) -> float | None:
    """Average days a unit sits in stock = period / turns = avg_inv / COGS * period."""
    if not _measured(average_inventory_value) or not _divisible_by(cogs):
        return None
    return average_inventory_value / cogs * period_days


def gmroi(gross_margin_value: float, average_inventory_cost: float) -> float | None:
    """Gross margin return on inventory investment. >1 = inventory earns its keep."""
    if not _measured(gross_margin_value) or not _divisible_by(average_inventory_cost):
        return None
    return gross_margin_value / average_inventory_cost


def sell_through(units_sold: float, units_on_hand: float) -> float | None:
    """Units sold / (units sold + units on hand) over the period."""
    if not _measured(units_sold) or not _measured(units_on_hand):
        return None
    denom = units_sold + units_on_hand
    if denom <= 0:
        return None     # no units moved and none on hand: nothing was observed
    return units_sold / denom


def weeks_of_supply(units_on_hand: float, avg_weekly_demand: float) -> float | None:
    """How many weeks current stock covers at the average demand rate."""
    if not _measured(units_on_hand) or not _divisible_by(avg_weekly_demand):
        return None
    return units_on_hand / avg_weekly_demand


def inventory_to_sales(average_inventory_value: float, net_sales: float) -> float | None:
    """Average inventory value / net sales."""
    if not _measured(average_inventory_value) or not _divisible_by(net_sales):
        return None
    return average_inventory_value / net_sales


def cash_to_cash(dio: float | None, dso: float, dpo: float) -> float | None:
    """Cash conversion cycle = DIO + DSO - DPO (days working capital is tied up).

    ``dio`` is itself only sometimes measurable, so this leg inherits that.
    """
    if not all(_measured(v) for v in (dio, dso, dpo)):
        return None
    return dio + dso - dpo


def stockout_rate(stockout_periods: float, total_periods: float) -> float | None:
    """Fraction of periods (or SKU-days) spent in stockout.

    ``None`` over zero periods. Returning 0.0 there printed "we never stocked out"
    from an absence of observation - the single most credibility-damaging number
    this module can emit.
    """
    if not _measured(stockout_periods) or not _divisible_by(total_periods):
        return None
    return stockout_periods / total_periods
