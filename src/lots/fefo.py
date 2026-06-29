"""First-Expired-First-Out issuance + expiry-risk (offline).

Pure, deterministic. Works in *days to expiry* per lot (no clock reads). FEFO issues the
earliest-expiring stock first; the at-risk quantity is the portion a lot holds that demand
cannot consume before it expires (the classic perishables waste calc).
"""
from __future__ import annotations

from dataclasses import dataclass
from itertools import groupby


@dataclass(frozen=True)
class Lot:
    lot_id: str
    product_id: str
    quantity: float
    days_to_expiry: float
    unit_cost: float = 0.0
    unit_price: float = 0.0


@dataclass(frozen=True)
class Pick:
    lot_id: str
    product_id: str
    quantity: float
    days_to_expiry: float


@dataclass(frozen=True)
class AtRiskLot:
    lot_id: str
    product_id: str
    at_risk_quantity: float
    days_to_expiry: float
    at_risk_value: float        # at_risk_quantity * unit_cost (the cost exposure)
    potential_revenue: float    # at_risk_quantity * unit_price (markdown upside)


def fefo_order(lots: list[Lot]) -> list[Lot]:
    """Lots sorted First-Expired-First-Out (soonest expiry first, lot id as tiebreak)."""
    return sorted(lots, key=lambda lot: (lot.days_to_expiry, lot.lot_id))


def _by_product(lots: list[Lot]) -> list[tuple[str, list[Lot]]]:
    ordered = sorted(lots, key=lambda lot: lot.product_id)
    return [(pid, list(group)) for pid, group in groupby(ordered, key=lambda lot: lot.product_id)]


def fefo_allocate(lots: list[Lot], demand_by_product: dict[str, float]) -> list[Pick]:
    """Allocate each product's demand to its lots First-Expired-First-Out."""
    picks: list[Pick] = []
    for product, plots in _by_product(lots):
        remaining = demand_by_product.get(product, 0.0)
        for lot in fefo_order(plots):
            if remaining <= 0:
                break
            take = min(lot.quantity, remaining)
            if take > 0:
                picks.append(Pick(lot.lot_id, product, take, lot.days_to_expiry))
                remaining -= take
    return picks


def at_risk_quantities(lots: list[Lot], demand_rate_by_product: dict[str, float]) -> list[AtRiskLot]:
    """The quantity per lot that demand cannot consume before it expires.

    With a positive daily demand rate, consume lots FEFO: by the time a lot expires (day d)
    total demand is ``rate * d``; whatever earlier lots already claimed is gone, the rest can
    serve this lot, and any remainder is at risk. With no rate for a product, only its already
    expired lots (days_to_expiry <= 0) are flagged (forward risk can't be assessed)."""
    out: list[AtRiskLot] = []
    for product, plots in _by_product(lots):
        rate = demand_rate_by_product.get(product, 0.0)
        if rate <= 0:
            for lot in fefo_order(plots):
                if lot.days_to_expiry <= 0 and lot.quantity > 0:
                    out.append(AtRiskLot(lot.lot_id, product, lot.quantity, lot.days_to_expiry,
                                         lot.quantity * lot.unit_cost, lot.quantity * lot.unit_price))
            continue
        consumed = 0.0
        for lot in fefo_order(plots):
            capacity = max(0.0, rate * lot.days_to_expiry)
            consume = min(lot.quantity, max(0.0, capacity - consumed))
            consumed += consume
            at_risk = lot.quantity - consume
            if at_risk > 1e-9:
                out.append(AtRiskLot(lot.lot_id, product, at_risk, lot.days_to_expiry,
                                     at_risk * lot.unit_cost, at_risk * lot.unit_price))
    return out
