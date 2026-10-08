"""Connector-backed replenishment — the offline execution loop (Gap #5).

Reads any ``InventorySource`` (a SimulatedStore, a store over HTTP, or a live adapter
later), forecasts each SKU from its order history, computes the restock needed to reach a
target cover, and returns a guided **never-unprotected** outcome:

- when the source is a writable store, the restock is **staged as a dry-run** through the
  safe-staging writeback plane (nothing mutates until a human applies it);
- otherwise a ready-to-execute **restock packet** (the quantities + steps) is handed off.

Either way no task ends in a dead end. Pure orchestration over the existing engines +
``nosilo_core.writeback`` plane; deterministic given the source.
"""

from __future__ import annotations

import math
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from nosilo_core.guided import GuidedOutcome, HandoffPacket, Residual, as_executed, as_handoff
from nosilo_core.writeback import Changeset
from src.connectors import InventorySource, Order
from src.constraints import purchasable_quantity
from src.ddmrp import net_flow_position
from src.forecasting import MIN_PERIODS_FOR_POLICY, forecast_demand


@dataclass(frozen=True)
class ReplenishmentLine:
    sku: str
    on_hand: float
    forecast_per_period: float
    target: float
    restock_qty: float
    #: "ok" when the line carries a real, evidence-backed quantity;
    #: "insufficient_history" when the SKU's order history was too short to
    #: forecast from and the engine declined to size it (restock_qty stays 0).
    status: str = "ok"
    #: Order-days observed for this SKU. See demand_by_sku()'s docstring on why
    #: this is order-DAYS and not calendar periods.
    n_observations: int = 0
    #: Units on open purchase orders netted out of ``restock_qty``, or ``None``
    #: when the caller supplied no ``on_order_by_sku`` map (or this SKU was
    #: absent from it). ``None`` and ``0.0`` mean different things: the first is
    #: "nobody told us", the second is "measured, and nothing is inbound".
    on_order: float | None = None
    #: The position the quantity was sized against - ``on_hand + on_order``.
    #: ``None`` on refused lines, which were never sized at all.
    position: float | None = None
    #: Units per CALENDAR day over the observed window (:func:`daily_demand_by_sku`),
    #: which is NOT ``forecast_per_period``: that one's period is an order-DAY. The
    #: distinction only shows up on an intermittent SKU, and there it is large. ``None``
    #: when no usable order date was found - never ``0.0``, which would read as "measured,
    #: and it does not sell". Emitted for consumers that reason in days: days of cover,
    #: the Tower's ``stock`` state domain.
    avg_daily_demand: float | None = None
    #: Lo que pidio el plan ANTES de llevarlo a la unidad del producto (a un decimal,
    #: como siempre). ``restock_qty`` es lo que se escribe; esta es la cifra que el
    #: entregable muestra al lado para que el redondeo se vea. ``None`` en una linea
    #: rechazada, que nunca se dimensiono. Un solo campo para todo camino del plan: la
    #: politica min/max lo reusa, no declara otro.
    restock_unrounded: float | None = None
    #: El paso de la unidad de stock con que se redondeo (1.0 = piezas enteras).
    #: ``None`` cuando la fuente no declaro unidad para este SKU: la cantidad sale SIN
    #: redondear, y eso no es lo mismo que "paso cero".
    unit_step: float | None = None


@dataclass(frozen=True)
class ReplenishmentPlan:
    lines: tuple[ReplenishmentLine, ...]
    restock: dict[str, float]          # only the SKUs that need topping up
    outcome: GuidedOutcome             # handoff packet (or executed when nothing's needed)
    changeset: Changeset | None        # staged dry-run, when a writable store was provided
    #: The realized demand this plan forecast from, WITH its dates
    #: (:func:`demand_points_by_sku`), carried out so a caller does not have to read the
    #: backend's orders a SECOND time to get it. It is what ``jobs/state_bridge.py``
    #: writes as the Tower's ``actual_qty`` outcomes: without a producer of realized
    #: demand, ``forecast_error_out_of_band`` can never fire in production, because
    #: ``forecast_cycle.freeze`` only ever writes the FORECAST. The dates travel because
    #: whoever reads those outcomes back has to forecast on them, and a point with an
    #: invented date is a point that cannot be audited against the ERP.
    demand_points: dict[str, tuple[tuple[str, float], ...]] = field(default_factory=dict)


def demand_points_by_sku(orders: list[Order]) -> dict[str, list[tuple[str, float]]]:
    """``(fecha, cantidad)`` por SKU, sumado dentro de cada fecha y ordenado por fecha.

    Es la fuente de :func:`demand_by_sku`, separada porque la fecha NO es descartable
    para todo consumidor: quien escribe la demanda realizada en la memoria del Tower
    (``jobs/state_bridge.py``) la necesita, y numerar los puntos ``p0001, p0002, ...``
    obligaria despues a inventarles un dia para poder pronosticar sobre ellos. La serie
    sin fechas alcanza para pronosticar y no para nada que se archive.
    """
    by_date: dict[str, dict[str, float]] = {}
    for order in orders:
        for line in order.lines:
            bucket = by_date.setdefault(line.sku, {})
            bucket[order.created_at] = bucket.get(order.created_at, 0.0) + line.quantity
    return {sku: [(d, dates[d]) for d in sorted(dates)] for sku, dates in by_date.items()}


def demand_by_sku(orders: list[Order]) -> dict[str, list[float]]:
    """Per-SKU demand as a per-period series (summed within a date, ordered by date).

    Publica, no privada: la consume tambien `jobs/channel_replenishment_job.py`.
    Un guion bajo importado desde otro modulo no protege nada -- solo avisa mal.
    El alias `_demand_by_sku` se conserva por si algo interno lo nombraba asi.
    """
    return {sku: [qty for _date, qty in points]
            for sku, points in demand_points_by_sku(orders).items()}


def _order_date(raw: object) -> date | None:
    """La fecha de un pedido, tolerando los dos formatos que emiten los conectores.

    Odoo escribe ``"2026-01-07"`` y Shopify ``"2026-01-07T10:00:00Z"``. Una fecha que
    no parsea devuelve ``None`` y su pedido se descarta del calculo de la tasa diaria
    -- descartar una observacion es honesto, inventarle una fecha no.
    """
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        return datetime.fromisoformat(raw.strip().replace("Z", "+00:00")).date()
    except ValueError:
        return None


def daily_demand_by_sku(orders: list[Order]) -> dict[str, float]:
    """Unidades por dia CALENDARIO, por SKU, sobre la ventana observada.

    No es :func:`demand_by_sku` dividido por su largo, y la diferencia importa. Esa
    serie agrega por FECHA CON PEDIDOS: su periodo es un dia-con-pedidos, no un dia.
    Para un SKU que vende casi todos los dias las dos cosas coinciden; para uno
    intermitente -- tres pedidos en dos meses -- el promedio por punto sobre-estima la
    tasa diaria varias veces. Y esa tasa es justo con la que el Tower calcula dias de
    cobertura (``scm_agent/monitors.py``): sobre-estimarla dispara falsas alarmas de
    quiebre sobre stock que alcanza de sobra.

    Ventana: del primer al ultimo dia con pedidos, inclusive (un solo dia da 1, no 0).
    Un SKU sin ninguna fecha usable NO entra al mapa -- ausente, nunca ``0.0``, que
    seria "medido, y no vende".
    """
    totals: dict[str, float] = {}
    spans: dict[str, tuple[date, date]] = {}
    for order in orders:
        when = _order_date(order.created_at)
        if when is None:
            continue
        for line in order.lines:
            totals[line.sku] = totals.get(line.sku, 0.0) + float(line.quantity)
            first, last = spans.get(line.sku, (when, when))
            spans[line.sku] = (min(first, when), max(last, when))
    return {
        sku: totals[sku] / ((last - first).days + 1)
        for sku, (first, last) in spans.items()
    }


#: Days per demand bucket in the min/max policy. A week, not a day: a distributor
#: that ships three orders a week has most DAYS at zero, and that is its order
#: calendar, not variability that accumulates over a lead time.
WEEK_DAYS = 7
#: Declared defaults of the min/max policy. Each one that is USED is written into
#: the line's ``defaults_declared`` and the plan's ``assumptions``.
DEFAULT_SERVICE_LEVEL = 0.95
DEFAULT_REVIEW_DAYS = 7.0
#: An intermittent SKU needs this many weeks WITH sales before it is sized: six
#: calendar weeks with a single sale pass the week gate and still say nothing.
MIN_NONZERO_WEEKS = 3
#: Weekly variance above this multiple of the mean means sales come in lots, not
#: one unit at a time, and the Poisson quantile is no longer a safe minimum.
LUMPY_VARIANCE_RATIO = 1.5
#: 49.999999997 is 50 units, not 51: float noise must not buy a unit.
_ROUND_TOL = 1e-9


@dataclass(frozen=True)
class WeeklyWindow:
    """Demand in 7-day buckets, counted BACKWARD from one shared end date.

    ``end`` is ``as_of`` when given, else the last order date of the WHOLE
    catalogue: deterministic, and the same for every SKU, so a SKU that stopped
    selling gets its trailing zero weeks instead of a window that ends at its own
    last sale. ``start`` drops the leading partial week.

    Where each SKU's series begins: at ``start`` when the SKU already sold BEFORE
    ``start`` (it existed for the whole window, so its leading zeros are real);
    otherwise at the bucket of its first sale inside the window. No zeros are
    invented for weeks before a product is known to exist (same doctrine as
    ``src/sources.py::_series_from_frame``); the price is a slight upward bias on
    an intermittent SKU whose first sale falls late, declared in the plan's
    assumptions. A SKU with no sale inside the window is absent from ``series`` --
    never a series of zeros.
    """

    start: date | None
    end: date | None
    n_weeks: int
    series: dict[str, tuple[float, ...]]


def weekly_demand_by_sku(
    orders: list[Order], *, as_of: date | None = None, bucket_days: int = WEEK_DAYS,
) -> WeeklyWindow:
    """Per-SKU demand per calendar week, zeros included (see :class:`WeeklyWindow`)."""
    dated = [(when, line.sku, float(line.quantity))
             for order in orders
             if (when := _order_date(order.created_at)) is not None
             for line in order.lines]
    if as_of is not None:
        dated = [row for row in dated if row[0] <= as_of]
    if not dated:
        return WeeklyWindow(None, None, 0, {})
    end = as_of if as_of is not None else max(row[0] for row in dated)
    n_weeks = ((end - min(row[0] for row in dated)).days + 1) // bucket_days
    if n_weeks == 0:
        return WeeklyWindow(None, end, 0, {})
    start = end - timedelta(days=n_weeks * bucket_days - 1)
    sold_before = {sku for when, sku, qty in dated if when < start and qty > 0}
    grid: dict[str, list[float]] = {}
    for when, sku, qty in dated:
        if when < start:
            continue
        idx = n_weeks - 1 - (end - when).days // bucket_days
        grid.setdefault(sku, [0.0] * n_weeks)[idx] += qty
    series = {}
    for sku, weeks in grid.items():
        if not any(q > 0 for q in weeks):
            continue
        first = 0 if sku in sold_before else next(i for i, q in enumerate(weeks) if q > 0)
        series[sku] = tuple(weeks[first:])
    return WeeklyWindow(start, end, n_weeks, series)


@dataclass(frozen=True)
class _Sizing:
    mu_week: float
    method: str
    sigma_source: str
    n_errors: int
    safety_stock: float
    distribution: str
    level: float          # mu*(L+R) + SS, unrounded


def _smooth_sizing(weeks: tuple[float, ...], tau_weeks: float, csl: float) -> _Sizing:
    """SES (fixed, so lab and prod agree) + SS from the engine over tau = L + R.

    Sigma is the LARGER of the out-of-sample and in-sample one-step error: twelve
    origins leave ~21% relative error on the OOS estimate, and on half a year of
    weeks it can come out below the in-sample residual. The smaller one sizes the
    optimistic buffer. Local imports keep scipy off the import chain of every
    module that merely imports ``replenish``.
    """
    from scipy.stats import skew

    from src.demand_variability import safety_stock_risk_period
    from src.forecasting import forecast_with_oos_sigma
    from src.risk_period import demand_over_risk_period

    fc = forecast_with_oos_sigma(list(weeks), method="ses")
    usable = [(v, name) for v, name in ((fc.error_std_oos, "forecast_error_oos"),
                                        (fc.error_std, "forecast_error"))
              if v is not None and math.isfinite(v) and v > 0]
    sigma, source = max(usable) if usable else (fc.demand_std, "demand_std_fallback")
    risk = demand_over_risk_period(fc.forecast, sigma, tau_weeks, lead_time_std=0.0)
    asym = float(skew(weeks, bias=False)) if fc.demand_std > 0 else None
    ss = safety_stock_risk_period(risk.mean_demand, risk.demand_std, csl, tau_weeks,
                                  distribution="auto", observed_skewness=asym)
    return _Sizing(fc.forecast, "ses", source, fc.n_oos_errors, ss.safety_stock,
                   ss.distribution, risk.mean_demand + ss.safety_stock)


def _intermittent_sizing(weeks: tuple[float, ...], tau_weeks: float, csl: float) -> _Sizing:
    """Poisson quantile of the mean weekly rate over tau = L + R.

    With ~2 units expected over the risk period a normal or gamma band returns a
    fractional minimum; Poisson answers in whole units with the right tail. It
    assumes sales of one unit (variance = mean). When the weekly variance runs
    above ``LUMPY_VARIANCE_RATIO`` times the mean the sales come in lots, Poisson
    under-protects, and the gamma band of the engine is taken if it is higher.
    """
    from scipy.stats import poisson

    from src.demand_variability import safety_stock_risk_period

    rate = statistics.fmean(weeks)
    mean_tau = rate * tau_weeks
    level = float(poisson.ppf(csl, mean_tau))
    distribution = "poisson"
    variance = statistics.variance(weeks)
    if variance > LUMPY_VARIANCE_RATIO * rate:
        gamma = safety_stock_risk_period(mean_tau, math.sqrt(variance * tau_weeks), csl,
                                         tau_weeks, distribution="gamma")
        if mean_tau + gamma.safety_stock > level:
            level, distribution = mean_tau + gamma.safety_stock, "gamma_lumpy"
    return _Sizing(rate, "tasa_media", "poisson" if distribution == "poisson" else "demand_std",
                   0, level - mean_tau, distribution, level)


def _size_min_max(weeks: tuple[float, ...], lead_days: float, review_days: float,
                  csl: float) -> _Sizing:
    """(R,S) de libro sobre semanas calendario: tau = (L + R) / 7 semanas."""
    from src.forecasting import is_intermittent

    tau_weeks = (lead_days + review_days) / WEEK_DAYS
    if is_intermittent(weeks):
        return _intermittent_sizing(weeks, tau_weeks, csl)
    return _smooth_sizing(weeks, tau_weeks, csl)


def _to_step(quantity: float, step: float | None) -> float:
    """``quantity`` rounded UP to the unit step with F4's rule
    (``src.constraints.purchasable_quantity``). ``step`` None = unit not read: no rounding."""
    from src.constraints import purchasable_quantity

    if quantity <= _ROUND_TOL:
        return 0.0
    return purchasable_quantity(quantity, unit_step=step or 0.0).quantity


def _buyable(quantity: float, *, step: float | None, moq: float, multiple: float) -> float:
    """What the supplier can be sent, with the SAME rule the draft PO applies: the supplier's
    case pack (stock units, assumed a multiple of the unit step) or else the unit step, then MOQ."""
    from src.constraints import purchasable_quantity

    if quantity <= _ROUND_TOL:
        return 0.0
    paso = multiple if multiple > 0 else (step or 0.0)
    return purchasable_quantity(quantity, unit_step=paso, minimum_order_quantity=moq).quantity


def _refusal_residual(refused: tuple[str, ...]) -> Residual:
    return Residual(
        description=f"{len(refused)} SKU(s) had too little order history to forecast from "
                    f"and were NOT sized: {', '.join(sorted(refused))}.",
        risk_if_skipped="these SKUs are unplanned, not well-stocked - a slow mover with a "
                        "genuine gap and a brand-new SKU look identical here; review each "
                        "by hand before assuming no action is needed.",
    )


def _packet(
    restock: dict[str, float],
    changeset: Changeset | None,
    refused: tuple[str, ...] = (),
) -> GuidedOutcome:
    """Wrap the restock as a protected handoff (or executed when there's nothing to do)."""
    if not restock:
        # A portfolio where every SKU was REFUSED is not a portfolio that is well
        # stocked, and must never report as one. Route the refusals to a human
        # instead: a refusal that goes nowhere is exactly the dead end the
        # never-unprotected contract (nosilo_core.guided.verify_guided) exists to stop.
        if refused:
            packet = HandoffPacket(
                title=f"Unplanned SKUs: {len(refused)}",
                steps=["Review each SKU's order history by hand.",
                       "Set an opening buy manually, or wait for more sales history."],
                artifact="\n".join(f"  {sku}" for sku in sorted(refused)),
                data={"insufficient_history": list(sorted(refused))},
                risk_if_skipped="An unplanned SKU stocks out with nobody watching it.",
            )
            return as_handoff(
                f"No SKU could be planned: {len(refused)} had too little order history.",
                [packet],
                residuals=[_refusal_residual(refused)],
            )
        return as_executed("All SKUs are above target cover - no restock needed.")

    artifact = "\n".join(f"  {sku}: +{qty:g} units" for sku, qty in sorted(restock.items()))
    steps = (
        ["Review the staged restock (dry-run).", "Approve and apply it to update inventory."]
        if changeset is not None
        else ["Issue restock POs for the quantities below.", "Receive and update inventory."]
    )
    packet = HandoffPacket(
        title=f"Restock packet: {len(restock)} SKU(s)",
        steps=steps,
        artifact=artifact,
        data=dict(restock),
        risk_if_skipped="The thin SKUs stock out before the next replenishment cycle.",
    )
    residuals = [Residual(
        description="Approve and apply the restock (or issue the POs).",
        risk_if_skipped="Stockouts and lost sales on the under-covered SKUs.",
    )]
    if refused:
        residuals.append(_refusal_residual(refused))
    return as_handoff(f"Restock {len(restock)} SKU(s) to target cover.", [packet], residuals=residuals)


def plan_replenishment(
    source: InventorySource,
    *,
    cover_periods: float = 8.0,
    method: str = "auto",
    store: object | None = None,
    idempotency_key: str = "replenish-1",
    min_observations: int = MIN_PERIODS_FOR_POLICY,
    on_order_by_sku: dict[str, float] | None = None,
    unit_step_by_sku: dict[str, float] | None = None,
) -> ReplenishmentPlan:
    """Forecast each SKU and plan the restock to ``cover_periods`` of demand.

    Pass ``store`` (a writable SimulatedStore, usually the same object as ``source``) to
    stage the restock as a dry-run; omit it to get a ready-to-execute packet only.

    ``on_order_by_sku`` is units already on an open purchase order and not yet
    received. When supplied, each quantity is sized against the inventory
    POSITION (``src.ddmrp.net_flow_position``: on hand plus on order) instead of
    on-hand alone, so stock that is already paid for and in transit is not bought
    twice. ``OdooConnector.open_purchase_qty_by_sku()`` produces exactly this map.

    Omitting it is NOT the same as passing zeros: a SKU missing from the map gets
    ``on_order=None`` on its line and the plan reports that it could not net,
    rather than claiming nothing is inbound. A default of ``{}`` here would read
    as "measured, and it is zero", which is the failure this parameter exists to
    prevent.

    ``unit_step_by_sku`` es el paso minimo de cantidad de cada SKU en su unidad de
    stock (``OdooConnector.unit_step_by_sku().by_sku`` lo produce). Con el, cada
    cantidad sube al paso con ``src.constraints.purchasable_quantity`` -- 187.6 rollos
    son 188 -- y la linea conserva la cifra sin redondear en ``restock_unrounded``. Se
    redondea ACA y no solo al escribir para que el CSV, la orden borrador y el punto de
    reorden digan el mismo numero. Un SKU fuera del mapa (o el mapa omitido) sale sin
    redondear con ``unit_step=None``: sin unidad conocida no se inventa un entero.
    """
    on_hand = {lvl.sku: lvl.available for lvl in source.inventory_levels()}
    orders = source.orders()
    points = demand_points_by_sku(orders)
    series = {sku: [qty for _date, qty in pts] for sku, pts in points.items()}
    # La misma lectura de pedidos, en la otra unidad. El plan razona en periodos
    # (un dia-con-pedidos); quien cuenta dias de cobertura necesita dias calendario,
    # y derivar uno del otro dividiendo por la cantidad de puntos seria el error que
    # `daily_demand_by_sku` documenta.
    daily = daily_demand_by_sku(orders)

    lines: list[ReplenishmentLine] = []
    restock: dict[str, float] = {}
    refused: list[str] = []
    for product in source.list_products():
        sku = product.sku
        history = series.get(sku, [])
        current = on_hand.get(sku, 0.0)

        # Too few observations to forecast from. This path is thinner than the CSV
        # path's (src/forecasting.py's MIN_PERIODS_FOR_POLICY, shared) and needs the
        # gate MORE, not less: there is no safety stock here at all - the target is
        # a bare `forecast * cover_periods` - so a forecast built on two order-days
        # goes straight into a purchase quantity with nothing damping it.
        if len(history) < min_observations:
            lines.append(ReplenishmentLine(
                sku, current, 0.0, 0.0, 0.0,
                status="insufficient_history", n_observations=len(history),
                # La tasa diaria SI se mide sobre lo poco que hay: no alcanza para
                # pronosticar (que es lo que esta linea rechaza) y alcanza de sobra
                # para decir a que ritmo se consume el estante.
                avg_daily_demand=daily.get(sku),
            ))
            refused.append(sku)
            continue

        forecast = float(forecast_demand(history, method=method).forecast)
        target = forecast * cover_periods
        # Size against the position, not the shelf. `qualified_demand=0.0` is
        # honest here: an InventorySource exposes no committed-demand concept, so
        # there is nothing to subtract - not a shortcut, a declared absence.
        on_order = None if on_order_by_sku is None else on_order_by_sku.get(sku)
        position = net_flow_position(current, on_order or 0.0, 0.0)
        unrounded = max(0.0, round(target - position, 1))
        step = None if unit_step_by_sku is None else unit_step_by_sku.get(sku)
        qty = unrounded if step is None else purchasable_quantity(unrounded, unit_step=step).quantity
        lines.append(ReplenishmentLine(
            sku, current, forecast, target, qty, n_observations=len(history),
            on_order=on_order, position=position, avg_daily_demand=daily.get(sku),
            restock_unrounded=unrounded, unit_step=step,
        ))
        if qty > 0:
            restock[sku] = qty

    changeset = None
    if store is not None and restock and hasattr(store, "stage_restock"):
        changeset = store.stage_restock(restock, idempotency_key=idempotency_key,
                                        reason="replenish to target cover")

    return ReplenishmentPlan(
        lines=tuple(lines),
        restock=restock,
        outcome=_packet(restock, changeset, tuple(refused)),
        changeset=changeset,
        demand_points={sku: tuple(pts) for sku, pts in points.items()},
    )


#: Alias historico: este helper fue privado entre 2026-08 y 2026-09-15.
_demand_by_sku = demand_by_sku
