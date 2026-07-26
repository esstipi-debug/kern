"""Supplier scorecards — OTIF / DIFOT and quality (capability M8).

OTIF (on-time in-full) requires both conditions on a delivery; on-time and in-full
rates are reported separately, with average lead time and defect PPM. Pure arithmetic.
Reference: ASCM/CIPS supplier-performance practice.

Every criterion is scored **over the rows that could actually be scored**, not over
the row count. A delivery record that never carried an on-time flag is an absence,
not a late delivery: dividing by ``len(deliveries)`` turns "we never measured this"
into a confident "0%", which is how a real 777-PO export produced an all-zero panel
and an award anyway. A rate with nothing to divide by is ``None``, and
:class:`Evaluable` says how much evidence each number rests on.
"""

from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Evaluable:
    """How many delivery rows carried the data each criterion needs.

    ``0`` means the criterion was never observed - the matching rate is ``None``
    and must be rendered as "not measured", never as a number.
    """

    on_time: int
    in_full: int
    otif: int
    lead_time: int
    ppm: int


@dataclass(frozen=True)
class SupplierScore:
    supplier: str
    deliveries: int                 # rows seen, whether or not they could be scored
    on_time_rate: float | None
    in_full_rate: float | None
    otif: float | None
    avg_lead_time: float | None
    ppm: float | None
    evaluable: Evaluable


_NOT_MEASURED = Evaluable(on_time=0, in_full=0, otif=0, lead_time=0, ppm=0)


def _flag(delivery: dict, key: str) -> bool | None:
    """A recorded yes/no, or ``None`` when the row never carried one.

    ``False`` means "we looked and it was late"; ``None`` means "we never looked".
    Collapsing the two is the whole defect.
    """
    value = delivery.get(key)
    if value is None:
        return None
    # A blank cell arrives from pandas as NaN, and `bool(nan)` is True - a missing
    # record would otherwise score as the *best* possible outcome.
    if isinstance(value, float) and math.isnan(value):
        return None
    return bool(value)


def _number(delivery: dict, key: str) -> float | None:
    """A recorded quantity, or ``None`` when absent, blank or not a real number."""
    value = delivery.get(key)
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _rate(hits: int, evaluable: int) -> float | None:
    """A share of the observations that existed - ``None`` when none did."""
    return (hits / evaluable) if evaluable > 0 else None


def score_supplier(supplier: str, deliveries: list[dict]) -> SupplierScore:
    """Aggregate a supplier's delivery records into a scorecard."""
    if not deliveries:
        return SupplierScore(supplier, 0, None, None, None, None, None, _NOT_MEASURED)

    on_times = [_flag(d, "on_time") for d in deliveries]
    in_fulls = [_flag(d, "in_full") for d in deliveries]
    leads = [v for v in (_number(d, "lead_time_days") for d in deliveries) if v is not None]
    quality = [
        (units, defects)
        for units, defects in ((_number(d, "units"), _number(d, "defects")) for d in deliveries)
        if units is not None and defects is not None
    ]

    n_on_time = sum(1 for v in on_times if v is not None)
    n_in_full = sum(1 for v in in_fulls if v is not None)
    # OTIF is only knowable where *both* halves were recorded on the same row.
    otif_pairs = [(o, f) for o, f in zip(on_times, in_fulls) if o is not None and f is not None]

    units = sum(u for u, _ in quality)
    defects = sum(d for _, d in quality)

    return SupplierScore(
        supplier=supplier,
        deliveries=len(deliveries),
        on_time_rate=_rate(sum(1 for v in on_times if v), n_on_time),
        in_full_rate=_rate(sum(1 for v in in_fulls if v), n_in_full),
        otif=_rate(sum(1 for o, f in otif_pairs if o and f), len(otif_pairs)),
        avg_lead_time=(sum(leads) / len(leads)) if leads else None,
        ppm=(defects / units * 1_000_000) if units > 0 else None,
        evaluable=Evaluable(
            on_time=n_on_time,
            in_full=n_in_full,
            otif=len(otif_pairs),
            lead_time=len(leads),
            ppm=len(quality),
        ),
    )


def lead_times_by_supplier(scorecards: list[SupplierScore]) -> dict[str, float]:
    """Observed average lead time per supplier - the real number to feed the risk period.

    Suppliers whose lead time was never recorded are omitted rather than reported as
    0: a zero-day lead time collapses the risk period, i.e. removes the safety stock.
    """
    return {s.supplier: s.avg_lead_time for s in scorecards if s.avg_lead_time is not None}


def lead_times_for_skus(
    scorecards: list[SupplierScore], sku_supplier: dict[str, str]
) -> dict[str, float]:
    """Join observed supplier lead times onto SKUs via a SKU->supplier map.

    Feeds ``jobs.inventory_optimization.run(lead_times=...)`` so the policy uses the lead
    time actually observed in deliveries, not a static assumption. SKUs whose supplier has
    no scorecard - or whose scorecard never measured a lead time - are dropped (the caller
    keeps the default for those). Units follow the scorecard (days); convert to the
    inventory model's period if they differ.
    """
    by_supplier = lead_times_by_supplier(scorecards)
    return {sku: by_supplier[sup] for sku, sup in sku_supplier.items() if sup in by_supplier}
