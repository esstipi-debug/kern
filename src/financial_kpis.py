"""Inventory financial KPIs (capability M13 / methods §4.5).

Closed-form, standards-anchored metrics computed from the COGS / average-inventory /
margin signals Kern already produces. Each is exact and deterministic (finance
numbers must be auditable). No external library emits a defensible inventory-finance
pack, so these are built in-repo.

References: SCOR Digital Standard (ASCM) — cash-to-cash; retail-math GMROI; GAAP DIO;
Chopra & Meindl, *Supply Chain Management* (7th ed.).
"""

from __future__ import annotations

_DAYS_PER_YEAR = 365


def inventory_turns(cogs: float, average_inventory_value: float) -> float:
    """COGS / average inventory value. Higher = inventory cycles faster."""
    if average_inventory_value <= 0:
        return float("inf")
    return cogs / average_inventory_value


def days_inventory_outstanding(
    average_inventory_value: float, cogs: float, period_days: int = _DAYS_PER_YEAR
) -> float:
    """Average days a unit sits in stock = period / turns = avg_inv / COGS * period."""
    if cogs <= 0:
        return float("inf")
    return average_inventory_value / cogs * period_days


def gmroi(gross_margin_value: float, average_inventory_cost: float) -> float:
    """Gross margin return on inventory investment. >1 = inventory earns its keep."""
    if average_inventory_cost <= 0:
        return float("inf")
    return gross_margin_value / average_inventory_cost


def sell_through(units_sold: float, units_on_hand: float) -> float:
    """Units sold / (units sold + units on hand) over the period."""
    denom = units_sold + units_on_hand
    if denom <= 0:
        return 0.0
    return units_sold / denom


def weeks_of_supply(units_on_hand: float, avg_weekly_demand: float) -> float:
    """How many weeks current stock covers at the average demand rate."""
    if avg_weekly_demand <= 0:
        return float("inf")
    return units_on_hand / avg_weekly_demand


def inventory_to_sales(average_inventory_value: float, net_sales: float) -> float:
    """Average inventory value / net sales."""
    if net_sales <= 0:
        return float("inf")
    return average_inventory_value / net_sales


def cash_to_cash(dio: float, dso: float, dpo: float) -> float:
    """Cash conversion cycle = DIO + DSO - DPO (days working capital is tied up)."""
    return dio + dso - dpo


def stockout_rate(stockout_periods: float, total_periods: float) -> float:
    """Fraction of periods (or SKU-days) spent in stockout."""
    if total_periods <= 0:
        return 0.0
    return stockout_periods / total_periods
