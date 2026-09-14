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


#: The lead time was computed from the delivery records themselves (dates or a
#: per-delivery measurement). This is the only kind a planning parameter may be built on.
LEAD_TIME_MEASURED = "measured"
#: The lead time is what the supplier says it is, carried on the row. Reportable, but it
#: is a promise, not an observation.
LEAD_TIME_DECLARED = "declared"


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
    #: Spread of the OBSERVED lead time. ``None`` when fewer than two deliveries carried
    #: one - a single delivery has no variability, and reporting 0.0 there would hand
    #: ``safety_stock`` a measured-looking zero (see src/safety_stock.py).
    lead_time_std: float | None = None
    lead_time_p90: float | None = None
    #: What the supplier DECLARES, when the export also carried it, and how far the
    #: observed average sits from it (observed - declared, signed).
    declared_lead_time: float | None = None
    lead_time_gap: float | None = None
    #: Where ``avg_lead_time`` came from: LEAD_TIME_MEASURED | LEAD_TIME_DECLARED | None.
    lead_time_source: str | None = None
    #: The purchase orders this score was computed over. A grade nobody can trace back
    #: to the deliveries that produced it cannot be taken to a supplier review - the
    #: supplier's first question is "which orders?". Empty when the export carried no
    #: order identifier.
    scored_po_ids: tuple[str, ...] = ()
    #: The SKUs bought from this supplier whose safety stock and reorder point were sized
    #: on a lead time the deliveries contradict. Empty when the gap is immaterial, when the
    #: export carried no declared lead time to compare against, or when it carried no
    #: product identifier - an empty tuple means "nothing to recalculate here", and
    #: `lead_time_gap` is what says whether that is because the gap was small.
    skus_to_recalculate: tuple[str, ...] = ()
    #: Average % the supplier BILLED above (or below) the price its own PO agreed to.
    #: ``None`` when the export carried no invoiced price - a supplier nobody compared
    #: is not a supplier that billed correctly, and 0.0 would say it was.
    price_variance_pct: float | None = None


#: How far the observed lead time has to sit from the declared one before the SS/ROP built
#: on the declared number is worth redoing. Relative, because 2 days on a 7-day lead time
#: is a different problem than 2 days on a 60-day one.
LEAD_TIME_GAP_MATERIAL = 0.10


_NOT_MEASURED = Evaluable(on_time=0, in_full=0, otif=0, lead_time=0, ppm=0)

# The only words that mean exactly one thing. Anything else is refused rather than
# guessed: `bool("No")` and `bool("Cancelled")` are both True, so a Yes/No export
# used to score a 100% OTIF over sixty late deliveries.
_TRUE_WORDS = frozenset({"true", "t", "yes", "y"})
_FALSE_WORDS = frozenset({"false", "f", "no", "n"})


def _flag(delivery: dict, key: str) -> bool | None:
    """A recorded yes/no, or ``None`` when the row never carried one.

    ``False`` means "we looked and it was late"; ``None`` means "we never looked".
    Collapsing the two is the whole defect - and so is reading an uninterpretable
    value as a yes, which is what plain ``bool()`` does to every non-empty string
    and every non-zero number.
    """
    value = delivery.get(key)
    if value is None:
        return None

    # Numbers (and bools, and numpy scalars) come through here. Only 0/1 is a flag:
    # `bool(3)` on a days-late column marks a 3-day-late delivery as on time.
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = None
    if number is not None:
        # A blank cell arrives from pandas as NaN, and `bool(nan)` is True - a
        # missing record would otherwise score as the *best* possible outcome.
        if not math.isfinite(number):
            return None
        if number in (0.0, 1.0):
            return bool(number)
        raise ValueError(
            f"'{key}' holds {value!r}, not 0/1 - that is a measurement, not a yes/no "
            f"flag. Derive {key} from it explicitly instead of scoring it as one."
        )

    text = str(value).strip().lower()
    if not text:
        return None
    if text in _TRUE_WORDS:
        return True
    if text in _FALSE_WORDS:
        return False
    raise ValueError(
        f"'{key}' cannot be read as a yes/no flag: {value!r} is free text. Point "
        f"{key} at a real boolean column, or map the status values explicitly - "
        "guessing here scores every non-empty word as a successful delivery."
    )


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


def _std(values: list[float]) -> float | None:
    """Sample standard deviation, or ``None`` with fewer than two observations."""
    if len(values) < 2:
        return None
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


def _p90(values: list[float]) -> float | None:
    """Linear-interpolated 90th percentile, or ``None`` when nothing was observed."""
    if not values:
        return None
    ordenados = sorted(values)
    if len(ordenados) == 1:
        return ordenados[0]
    posicion = 0.9 * (len(ordenados) - 1)
    bajo = int(math.floor(posicion))
    alto = min(bajo + 1, len(ordenados) - 1)
    return ordenados[bajo] + (ordenados[alto] - ordenados[bajo]) * (posicion - bajo)


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
    declarados = [
        v for v in (_number(d, "declared_lead_time_days") for d in deliveries) if v is not None
    ]
    fuentes = {str(d["lead_time_source"]) for d in deliveries if d.get("lead_time_source")}
    po_ids = tuple(str(d["po_id"]) for d in deliveries if d.get("po_id"))
    skus = tuple(sorted({str(d["product_id"]) for d in deliveries if d.get("product_id")}))
    desvios = [
        v for v in (_number(d, "price_variance_pct") for d in deliveries) if v is not None
    ]
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

    avg_lead = (sum(leads) / len(leads)) if leads else None
    avg_declarado = (sum(declarados) / len(declarados)) if declarados else None
    # One source per supplier or nothing: a scorecard whose rows disagree about where the
    # lead time came from cannot be labelled, and mislabelling is worse than not labelling.
    fuente = fuentes.pop() if len(fuentes) == 1 else None

    gap = (
        avg_lead - avg_declarado
        if avg_lead is not None and avg_declarado is not None else None
    )
    # The SS/ROP of every SKU bought here was sized on `avg_declarado`. Once the deliveries
    # say otherwise by more than the materiality band, those parameters are stale - naming
    # the SKUs is the difference between a lead-time observation and an action.
    stale = (
        gap is not None
        and avg_declarado > 0
        and abs(gap) / avg_declarado >= LEAD_TIME_GAP_MATERIAL
    )

    return SupplierScore(
        supplier=supplier,
        deliveries=len(deliveries),
        on_time_rate=_rate(sum(1 for v in on_times if v), n_on_time),
        in_full_rate=_rate(sum(1 for v in in_fulls if v), n_in_full),
        otif=_rate(sum(1 for o, f in otif_pairs if o and f), len(otif_pairs)),
        avg_lead_time=avg_lead,
        ppm=(defects / units * 1_000_000) if units > 0 else None,
        evaluable=Evaluable(
            on_time=n_on_time,
            in_full=n_in_full,
            otif=len(otif_pairs),
            lead_time=len(leads),
            ppm=len(quality),
        ),
        lead_time_std=_std(leads),
        lead_time_p90=_p90(leads),
        declared_lead_time=avg_declarado,
        lead_time_gap=gap,
        lead_time_source=fuente,
        scored_po_ids=po_ids,
        skus_to_recalculate=skus if stale else (),
        price_variance_pct=(sum(desvios) / len(desvios)) if desvios else None,
    )


def rankable(
    scorecards: list[SupplierScore], criteria_fields: tuple[str, ...]
) -> tuple[list[SupplierScore], tuple[str, ...]]:
    """Split a panel into the suppliers that can be ranked, and the names of those that cannot.

    A supplier missing one of the criteria being ranked on has no defensible position in
    the ranking. Sorting or scoring it anyway needs a number, and every available number
    is a lie: ``0`` awards it the worst possible score on a criterion nobody measured,
    and the old fallbacks awarded it the best. Leaving it out - by name, so the caller can
    say who and why - is the only honest option.

    ``criteria_fields`` are :class:`SupplierScore` attribute names (``"otif"``,
    ``"avg_lead_time"``, ``"ppm"``, ...), and only the ones actually being ranked on:
    a missing PPM is irrelevant to a ranking that does not weigh quality.
    """
    for name in criteria_fields:
        if not hasattr(SupplierScore, "__dataclass_fields__") or name not in SupplierScore.__dataclass_fields__:
            raise AttributeError(
                f"{name!r} is not a SupplierScore field - a typo here would silently empty "
                f"the panel. Known fields: {', '.join(SupplierScore.__dataclass_fields__)}."
            )
    panel, dropped = [], []
    for card in scorecards:
        if all(getattr(card, name) is not None for name in criteria_fields):
            panel.append(card)
        else:
            dropped.append(card.supplier)
    return panel, tuple(dropped)


def lead_times_by_supplier(scorecards: list[SupplierScore]) -> dict[str, float]:
    """Observed average lead time per supplier - the real number to feed the risk period.

    Suppliers whose lead time was never recorded are omitted rather than reported as
    0: a zero-day lead time collapses the risk period, i.e. removes the safety stock.
    """
    return {s.supplier: s.avg_lead_time for s in scorecards if s.avg_lead_time is not None}


def lead_time_stds_by_supplier(scorecards: list[SupplierScore]) -> dict[str, float]:
    """Observed lead-time SPREAD per supplier - the producer sigma_L never had.

    ``jobs.inventory_optimization.run(lead_time_stds=...)`` has always accepted this
    number and nothing computed it, so every safety stock defaulted to sigma_L = 0 (the
    gap the battery's T04/T08 checks surface). Suppliers with fewer than two measured
    deliveries are omitted rather than reported as 0.0: a measured-looking zero removes
    the lead-time half of the buffer, which is the failure this whole module guards.
    """
    return {
        s.supplier: s.lead_time_std
        for s in scorecards
        if s.lead_time_std is not None and s.lead_time_source == LEAD_TIME_MEASURED
    }


def lead_time_stds_for_skus(
    scorecards: list[SupplierScore], sku_supplier: dict[str, str]
) -> dict[str, float]:
    """Join observed lead-time spreads onto SKUs via a SKU->supplier map.

    Same shape and same omission rule as :func:`lead_times_for_skus`; feeds
    ``jobs.inventory_optimization.run(lead_time_stds=...)``.
    """
    by_supplier = lead_time_stds_by_supplier(scorecards)
    return {sku: by_supplier[sup] for sku, sup in sku_supplier.items() if sup in by_supplier}


def declared_lead_times_for_skus(
    scorecards: list[SupplierScore], sku_supplier: dict[str, str]
) -> dict[str, float]:
    """Join the DECLARED lead time onto SKUs -- el contrafactual, no el parametro.

    Las otras dos funciones de este bloque entregan lo observado, que es con lo que se
    dimensiona. Esta entrega lo que el sistema del cliente cree, y existe para un solo
    uso: correr el mismo motor dos veces y poner la diferencia en plata. **Nunca para
    dimensionar** -- si este numero alimenta una politica, la politica quedo armada
    sobre la promesa del proveedor, que es el defecto entero que este modulo mide.

    Misma regla de omision que las otras dos: un proveedor cuyo export no traia plazo
    declarado se omite, no se reporta como 0.
    """
    by_supplier = {
        s.supplier: s.declared_lead_time
        for s in scorecards
        if s.declared_lead_time is not None
    }
    return {sku: by_supplier[sup] for sku, sup in sku_supplier.items() if sup in by_supplier}


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
