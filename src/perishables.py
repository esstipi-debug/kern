"""Perishable inventory bounding and shelf-life constrained lot sizing.

Grounds perishable replenishment on physical absorption capacity:
    s + Q <= mean_daily_demand * effective_shelf_life

A batch of size Q entering when reorder trigger is s takes (s + Q) / demand_rate
days to be completely cleared under FEFO/FIFO. If that dwell time exceeds the
effective shelf life, the tail of the batch will spoil with mathematical certainty.

This module provides:
  1. effective_shelf_life: discounts channel B2B gating (e.g. 70% remaining life
     required by supermarkets) and transit lead time.
  2. bound_order_quantity_by_expiry: caps Q so maximum system inventory does not
     exceed absorption capacity, and quantifies forced spoilage when supplier MOQ
     exceeds absorption.
  3. evaluate_spoilage: computes expected expired units and USD exposure.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PerishableLimit:
    """The perishable boundary calculation for a replenishment decision."""

    unconstrained_q: float
    reorder_point: float
    effective_shelf_life_days: float
    max_absorbable_units: float
    constrained_q: float
    spoilage_risk_units: float
    spoilage_risk_usd: float
    verdict: str  # "ok" | "shelf_life_constrained" | "unviable_turnover_too_slow" | "moq_forces_spoilage"
    forced_moq_penalty_usd: float = 0.0

    @property
    def is_constrained(self) -> bool:
        """True when shelf life bounded the order quantity below unconstrained Q."""
        return self.verdict in ("shelf_life_constrained", "moq_forces_spoilage")

    @property
    def generates_spoilage(self) -> bool:
        """True when the order will structurally spoil units before consumption."""
        return self.spoilage_risk_units > 1e-6


def effective_shelf_life(
    total_shelf_life_days: float,
    *,
    channel_gating_pct: float = 0.0,
    transit_lead_time_days: float = 0.0,
) -> float:
    """Calculate the usable commercial shelf life horizon.

    Parameters:
      total_shelf_life_days: Total shelf life of the item from manufacture/receipt.
      channel_gating_pct: Minimum fraction of shelf life required by channel/retailer
        at reception (e.g., 0.70 means retailer accepts only with >=70% life remaining).
      transit_lead_time_days: Delivery time from dispatch to customer/channel reception.

    Returns:
      Usable days available to store and sell the product before exceeding channel limits.
    """
    if total_shelf_life_days <= 0:
        return 0.0
    if not (0.0 <= channel_gating_pct < 1.0):
        raise ValueError(f"channel_gating_pct must be in [0, 1), got {channel_gating_pct}")
    if transit_lead_time_days < 0:
        raise ValueError(f"transit_lead_time_days must be >= 0, got {transit_lead_time_days}")

    # The channel reserves channel_gating_pct * total for itself, leaving (1 - gating)
    # for the distributor, minus transit days to reach the customer.
    usable = total_shelf_life_days * (1.0 - channel_gating_pct) - transit_lead_time_days
    return max(0.0, float(usable))


def bound_order_quantity_by_expiry(
    unconstrained_q: float,
    reorder_point: float,
    *,
    daily_demand: float,
    shelf_life_days: float,
    unit_cost: float = 0.0,
    moq: float | None = None,
    order_multiple: float | None = None,
) -> PerishableLimit:
    """Cap replenishment lot Q to prevent structural expiry before consumption.

    Parameters:
      unconstrained_q: The economic order quantity proposed without shelf life.
      reorder_point: Reorder point s (or safety stock + lead time demand).
      daily_demand: Average daily consumption or sales rate.
      shelf_life_days: Effective shelf life in days.
      unit_cost: Cost per unit for financial exposure calculation.
      moq: Supplier Minimum Order Quantity (if any).
      order_multiple: Supplier case/pallet multiple (if any).

    Returns:
      PerishableLimit containing bounded Q, expected spoilage and verdict.
    """
    if unconstrained_q < 0:
        raise ValueError(f"unconstrained_q must be >= 0, got {unconstrained_q}")
    if reorder_point < 0:
        raise ValueError(f"reorder_point must be >= 0, got {reorder_point}")
    if daily_demand < 0:
        raise ValueError(f"daily_demand must be >= 0, got {daily_demand}")
    if shelf_life_days <= 0:
        return PerishableLimit(
            unconstrained_q=unconstrained_q,
            reorder_point=reorder_point,
            effective_shelf_life_days=0.0,
            max_absorbable_units=0.0,
            constrained_q=0.0,
            spoilage_risk_units=unconstrained_q,
            spoilage_risk_usd=unconstrained_q * unit_cost,
            verdict="unviable_turnover_too_slow",
        )

    # Total demand that the market can absorb during the lifespan of a batch
    absorption_capacity = daily_demand * shelf_life_days
    
    # Remaining capacity available for new batch after satisfying reorder point
    max_absorbable_units = max(0.0, absorption_capacity - reorder_point)

    if max_absorbable_units <= 0.0:
        # Reorder point alone already matches or exceeds absorption capacity:
        # turnover is inherently too slow for this shelf life.
        return PerishableLimit(
            unconstrained_q=unconstrained_q,
            reorder_point=reorder_point,
            effective_shelf_life_days=shelf_life_days,
            max_absorbable_units=0.0,
            constrained_q=0.0,
            spoilage_risk_units=unconstrained_q,
            spoilage_risk_usd=unconstrained_q * unit_cost,
            verdict="unviable_turnover_too_slow",
        )

    # 1. If unconstrained Q fits inside absorption capacity:
    if unconstrained_q <= max_absorbable_units:
        # Check if supplier MOQ forces a larger quantity:
        if moq is not None and moq > max_absorbable_units:
            forced_spoilage = moq - max_absorbable_units
            return PerishableLimit(
                unconstrained_q=unconstrained_q,
                reorder_point=reorder_point,
                effective_shelf_life_days=shelf_life_days,
                max_absorbable_units=max_absorbable_units,
                constrained_q=moq,
                spoilage_risk_units=forced_spoilage,
                spoilage_risk_usd=forced_spoilage * unit_cost,
                verdict="moq_forces_spoilage",
                forced_moq_penalty_usd=forced_spoilage * unit_cost,
            )
        return PerishableLimit(
            unconstrained_q=unconstrained_q,
            reorder_point=reorder_point,
            effective_shelf_life_days=shelf_life_days,
            max_absorbable_units=max_absorbable_units,
            constrained_q=unconstrained_q,
            spoilage_risk_units=0.0,
            spoilage_risk_usd=0.0,
            verdict="ok",
        )

    # 2. Unconstrained Q exceeds absorption capacity: cap to max_absorbable_units
    target_q = max_absorbable_units

    # If an order multiple is required by supplier, snap down to nearest multiple
    # that does not exceed absorption capacity:
    if order_multiple and order_multiple > 0:
        steps = int(target_q // order_multiple)
        snapped_q = steps * order_multiple
        if snapped_q > 0:
            target_q = snapped_q

    # Check if MOQ forces higher:
    if moq is not None and moq > max_absorbable_units:
        forced_spoilage = moq - max_absorbable_units
        return PerishableLimit(
            unconstrained_q=unconstrained_q,
            reorder_point=reorder_point,
            effective_shelf_life_days=shelf_life_days,
            max_absorbable_units=max_absorbable_units,
            constrained_q=moq,
            spoilage_risk_units=forced_spoilage,
            spoilage_risk_usd=forced_spoilage * unit_cost,
            verdict="moq_forces_spoilage",
            forced_moq_penalty_usd=forced_spoilage * unit_cost,
        )

    return PerishableLimit(
        unconstrained_q=unconstrained_q,
        reorder_point=reorder_point,
        effective_shelf_life_days=shelf_life_days,
        max_absorbable_units=max_absorbable_units,
        constrained_q=target_q,
        spoilage_risk_units=0.0,
        spoilage_risk_usd=0.0,
        verdict="shelf_life_constrained",
    )
