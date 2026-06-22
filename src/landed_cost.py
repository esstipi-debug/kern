"""Landed-cost / total-cost engine (capability M8).

True delivered cost of a SKU from a supplier: goods + freight + insurance + handling
+ duty + broker fees, with an Incoterm-aware duty base (FOB/EXW/FCA dutiable on goods;
CIF/CIP/DAP etc. dutiable on goods + freight + insurance). Pure arithmetic + rules,
auditable for the QA gate. Reference: Ellram, *Total Cost of Ownership*; ICC Incoterms 2020.
"""

from __future__ import annotations

from dataclasses import dataclass

# Incoterms whose customs (duty) base is the goods value only.
_GOODS_ONLY_DUTY_BASE = {"EXW", "FCA", "FOB", "FAS", "FdA"}


@dataclass(frozen=True)
class LandedCost:
    incoterm: str
    qty: float
    goods_value: float
    freight: float
    insurance: float
    handling: float
    duty_base: float
    duty: float
    broker_fee: float
    total: float
    per_unit: float


def landed_cost(
    unit_cost: float,
    qty: float,
    *,
    freight: float = 0.0,
    insurance: float = 0.0,
    duty_rate: float = 0.0,
    handling: float = 0.0,
    broker_fee: float = 0.0,
    incoterm: str = "FOB",
) -> LandedCost:
    """Compute the fully-landed cost and per-unit landed cost."""
    goods_value = unit_cost * qty
    term = incoterm.upper()
    if term in _GOODS_ONLY_DUTY_BASE:
        duty_base = goods_value
    else:  # CIF/CIP/DAP/DDP... — freight & insurance are in the dutiable value
        duty_base = goods_value + freight + insurance
    duty = duty_base * duty_rate
    total = goods_value + freight + insurance + handling + duty + broker_fee
    per_unit = (total / qty) if qty > 0 else 0.0
    return LandedCost(
        incoterm=term, qty=qty, goods_value=goods_value, freight=freight,
        insurance=insurance, handling=handling, duty_base=duty_base, duty=duty,
        broker_fee=broker_fee, total=total, per_unit=per_unit,
    )
