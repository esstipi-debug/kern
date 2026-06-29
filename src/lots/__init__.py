"""Lot / batch expiry analytics (offline).

Pure, deterministic perishables math - works in *days to expiry* (never reads the clock),
so output is fully testable. Two questions:

- ``fefo``   : First-Expired-First-Out issue order / allocation, and the at-risk quantity
               that demand cannot consume before each lot expires.
- ``expiry`` : the shelf-life aging report (expired / expiring / aging / fresh) and the
               markdown-vs-scrap disposition for the at-risk units.
"""
from src.lots.expiry import (
    DEFAULT_THRESHOLDS,
    DispositionPlan,
    ExpiryBucket,
    aging_report,
    markdown_vs_scrap,
)
from src.lots.fefo import (
    AtRiskLot,
    Lot,
    Pick,
    at_risk_quantities,
    fefo_allocate,
    fefo_order,
)

__all__ = [
    "Lot",
    "Pick",
    "AtRiskLot",
    "fefo_order",
    "fefo_allocate",
    "at_risk_quantities",
    "ExpiryBucket",
    "DEFAULT_THRESHOLDS",
    "aging_report",
    "DispositionPlan",
    "markdown_vs_scrap",
]
