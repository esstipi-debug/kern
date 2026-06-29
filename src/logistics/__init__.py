"""Logistics / transportation analytics (offline).

Pure, deterministic freight math - no carrier APIs (EasyPost et al. are a deferred,
credentialed layer). Two questions:

- ``modes``   : which transport mode (parcel / LTL / FTL / intermodal) is cheapest, and
                the LTL->FTL breakeven weight.
- ``freight`` : freight cost-to-serve by lane (cost per unit, freight as % of value).
"""
from src.logistics.freight import (
    FreightLine,
    LaneCost,
    lane_cost_to_serve,
)
from src.logistics.modes import (
    MODES,
    FreightRates,
    ModeQuote,
    ModeSelection,
    Shipment,
    ltl_ftl_breakeven_kg,
    quote_modes,
    select_mode,
)

__all__ = [
    "MODES",
    "FreightRates",
    "ModeQuote",
    "ModeSelection",
    "Shipment",
    "ltl_ftl_breakeven_kg",
    "quote_modes",
    "select_mode",
    "FreightLine",
    "LaneCost",
    "lane_cost_to_serve",
]
