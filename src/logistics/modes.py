"""Transport-mode selection - parcel / LTL / FTL / intermodal cost models (offline).

Pure, deterministic freight math: a per-shipment cost for each mode from a configurable
rate card, the cheapest *feasible* mode (with optional transit-time limit), and the
LTL->FTL breakeven weight. No carrier APIs - EasyPost and friends are a deferred,
credentialed layer; this is the analytics that pick the mode and size the trade-off.

Cost models (all linear / closed-form, Vandeput & Christopher transport economics):
- parcel:     rate_per_kg * weight; feasible only up to a per-parcel weight cap.
- LTL:        max(min_charge, rate_per_kg_per_km * weight * distance).
- FTL:        cost_per_km * distance * trucks_needed (weight-insensitive within a truck).
- intermodal: fixed_drayage + rate_per_kg_per_km * weight * distance; feasible past a
              minimum line-haul distance.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, replace

MODES = ("parcel", "ltl", "ftl", "intermodal")


@dataclass(frozen=True)
class FreightRates:
    """A configurable rate card. Defaults are illustrative - override per lane / carrier."""

    parcel_rate_per_kg: float = 2.5
    parcel_max_kg: float = 30.0
    parcel_transit_days: float = 2.0
    ltl_rate_per_kg_per_km: float = 0.0009
    ltl_min_charge: float = 60.0
    ltl_transit_days: float = 4.0
    ftl_cost_per_km: float = 1.8
    truck_capacity_kg: float = 20_000.0
    ftl_transit_days: float = 3.0
    intermodal_fixed: float = 350.0
    intermodal_rate_per_kg_per_km: float = 0.0004
    intermodal_min_distance_km: float = 700.0
    intermodal_transit_days: float = 7.0


@dataclass(frozen=True)
class Shipment:
    shipment_id: str
    weight_kg: float
    distance_km: float
    units: float = 0.0
    order_value: float = 0.0


@dataclass(frozen=True)
class ModeQuote:
    mode: str
    cost: float
    transit_days: float
    feasible: bool
    reason: str = ""


@dataclass(frozen=True)
class ModeSelection:
    shipment_id: str
    recommended_mode: str
    recommended_cost: float
    transit_days: float
    savings_vs_next: float          # vs the next-cheapest feasible mode
    quotes: tuple[ModeQuote, ...]   # all modes, feasible-first then cost asc


def _parcel(s: Shipment, r: FreightRates) -> ModeQuote:
    feasible = 0 < s.weight_kg <= r.parcel_max_kg
    reason = "" if feasible else f"weight outside parcel range (max {r.parcel_max_kg:g} kg)"
    return ModeQuote("parcel", r.parcel_rate_per_kg * s.weight_kg, r.parcel_transit_days, feasible, reason)


def _ltl(s: Shipment, r: FreightRates) -> ModeQuote:
    cost = max(r.ltl_min_charge, r.ltl_rate_per_kg_per_km * s.weight_kg * s.distance_km)
    return ModeQuote("ltl", cost, r.ltl_transit_days, True)


def _ftl(s: Shipment, r: FreightRates) -> ModeQuote:
    trucks = math.ceil(s.weight_kg / r.truck_capacity_kg) if s.weight_kg > 0 else 1
    return ModeQuote("ftl", r.ftl_cost_per_km * s.distance_km * max(1, trucks), r.ftl_transit_days, True)


def _intermodal(s: Shipment, r: FreightRates) -> ModeQuote:
    feasible = s.distance_km >= r.intermodal_min_distance_km
    reason = "" if feasible else f"distance below intermodal minimum ({r.intermodal_min_distance_km:g} km)"
    cost = r.intermodal_fixed + r.intermodal_rate_per_kg_per_km * s.weight_kg * s.distance_km
    return ModeQuote("intermodal", cost, r.intermodal_transit_days, feasible, reason)


def quote_modes(
    shipment: Shipment,
    rates: FreightRates = FreightRates(),
    *,
    max_transit_days: float | None = None,
) -> list[ModeQuote]:
    """Quote every mode; mark infeasible ones (weight/distance limits, transit cap). Cost-sorted."""
    quotes = [_parcel(shipment, rates), _ltl(shipment, rates),
              _ftl(shipment, rates), _intermodal(shipment, rates)]
    if max_transit_days is not None:
        quotes = [
            q if (q.feasible and q.transit_days <= max_transit_days)
            else replace(q, feasible=False,
                         reason=q.reason or f"transit {q.transit_days:g} d exceeds limit {max_transit_days:g} d")
            for q in quotes
        ]
    return sorted(quotes, key=lambda q: (not q.feasible, q.cost))


def select_mode(
    shipment: Shipment,
    rates: FreightRates = FreightRates(),
    *,
    max_transit_days: float | None = None,
) -> ModeSelection:
    """Pick the cheapest feasible mode for a shipment and the saving vs the next-best."""
    quotes = quote_modes(shipment, rates, max_transit_days=max_transit_days)
    feasible = [q for q in quotes if q.feasible] or list(quotes)
    best = feasible[0]
    savings = (feasible[1].cost - best.cost) if len(feasible) > 1 else 0.0
    return ModeSelection(
        shipment_id=shipment.shipment_id, recommended_mode=best.mode,
        recommended_cost=best.cost, transit_days=best.transit_days,
        savings_vs_next=savings, quotes=tuple(quotes),
    )


def ltl_ftl_breakeven_kg(rates: FreightRates = FreightRates()) -> float:
    """Weight where one full truck beats LTL (distance cancels above the LTL minimum)."""
    if rates.ltl_rate_per_kg_per_km <= 0:
        return float("inf")
    return rates.ftl_cost_per_km / rates.ltl_rate_per_kg_per_km
