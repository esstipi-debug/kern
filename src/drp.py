"""Distribution Requirements Planning (DRP) - time-phased, multi-echelon (Vollmann MPC).

Pure, deterministic. Runs the standard time-phased grid per branch (gross requirements ->
projected on-hand -> net requirements -> planned receipts -> planned order releases offset by
lead time), then rolls the branches' planned order releases up as the gross requirements at the
central DC and plans it the same way. Lot-for-lot by default; a lot size rounds receipts up.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Branch:
    name: str
    forecast: tuple[float, ...]     # gross demand per period
    on_hand: float
    lead_time: int
    safety_stock: float = 0.0
    lot_size: float = 1.0           # round planned receipts up to a multiple (<=1 = lot-for-lot)


@dataclass(frozen=True)
class DrpRow:
    period: int
    gross_requirements: float
    projected_on_hand: float
    net_requirements: float
    planned_receipt: float
    planned_order_release: float


def _round_lot(qty: float, lot_size: float) -> float:
    """Round a required quantity up to a whole lot (lot-for-lot when lot_size <= 1)."""
    if lot_size <= 1:
        return qty
    return math.ceil(qty / lot_size) * lot_size


def drp_plan(branch: Branch, n_periods: int | None = None) -> list[DrpRow]:
    """The time-phased DRP grid for one branch (planned order releases offset by lead time)."""
    n = n_periods or len(branch.forecast)
    forecast = list(branch.forecast) + [0.0] * (n - len(branch.forecast))

    planned_receipt = [0.0] * n
    projected = [0.0] * n
    net = [0.0] * n
    releases = [0.0] * n

    prev_oh = branch.on_hand
    for t in range(n):
        available = prev_oh - forecast[t]
        net[t] = max(0.0, branch.safety_stock - available)
        receipt = _round_lot(net[t], branch.lot_size) if net[t] > 0 else 0.0
        planned_receipt[t] = receipt
        projected[t] = available + receipt
        prev_oh = projected[t]
        if receipt > 0:
            release_t = max(0, t - branch.lead_time)   # past-due requirements land in period 0
            releases[release_t] += receipt

    return [
        DrpRow(t, forecast[t], projected[t], net[t], planned_receipt[t], releases[t])
        for t in range(n)
    ]


def rollup_gross_requirements(branch_plans: list[list[DrpRow]], n_periods: int) -> list[float]:
    """Sum the branches' planned order releases per period -> the DC's gross requirements."""
    total = [0.0] * n_periods
    for plan in branch_plans:
        for row in plan:
            if row.period < n_periods:
                total[row.period] += row.planned_order_release
    return total
