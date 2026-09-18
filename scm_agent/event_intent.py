"""Event -> tool routing (Linchpin 3.0 PR-4, F0 -- ``scm_agent/event_intent.py``).

This is Track A's A2 "decide" layer wired ahead of its own PR (A2 is listed
against ``config/event_routing.yaml`` in plan S5, but the plan places the
config file itself in F0 alongside this module -- "reusa el registry
intacto", plan S4.2). A monitor (PR-5's ``monitors.py``, not built yet) will
call :func:`handle_event` with an :class:`~scm_agent.events.Event` it just
emitted; today, a test calling it with a synthetic event exercises exactly
the same path -- there is no monitor-shaped special case here.

Pipeline, matching the F0 acceptance criterion (plan S4, "Criterio de
aceptacion F0"):

    Event -> resolve_route() [config/event_routing.yaml]
          -> build_params()  [PARAM_BUILDERS[route.param_builder]]
          -> Orchestrator.run(..., job_type=route.tool)  [real prepare->run->qa->deliver]
          -> notify() on STATUS_OK only

The routing table is DATA (plan rule: "ruteo como dato") -- adding a new
event type means adding a YAML entry + (if the tool needs payload shaped
differently than an existing builder) one small function in
``PARAM_BUILDERS``, never editing ``nosilo_core/registry.py`` or
``scm_agent/tools.py``.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yaml

from jobs.notify import notify
from jobs.state_bridge import METRIC_ACTUAL_QTY
from nosilo_core.types import STATUS_OK, JobResult
from src.data_loader import lead_time_periods_from_days
from src.forecasting import MIN_PERIODS_FOR_POLICY
from src.state.store import DEFAULT_BASE_PATH as DEFAULT_STATE_BASE_PATH
from src.state.store import StateStore, store_for
from src.state.system_state import history as state_history

from .events import Event
from .orchestrator import Orchestrator

# Env-override convention matching scm_agent/events.py's DEFAULT_PATH and
# src/state/store.py's LINCHPIN_STATE_PATH.
DEFAULT_ROUTING_PATH = os.environ.get("LINCHPIN_EVENT_ROUTING_PATH", "").strip() or "config/event_routing.yaml"

VALID_AUTONOMY_TIERS = ("T1", "T2", "T3")

# Default label for events routed with no explicit payload["client"] -- these
# runs are system-initiated (a monitor, not a named client's own request), so
# "Tower" reads better on a deliverable than the generic "Client" default
# Orchestrator.run() otherwise falls back to.
DEFAULT_EVENT_CLIENT = "Tower"


class EventRoutingError(RuntimeError):
    """Raised for anything that makes an Event un-routable: a malformed
    ``event_routing.yaml``, an event type with no configured route, a route
    naming a ``param_builder`` this module does not know, a route naming a
    tool the registry does not have, or a param builder that cannot build
    valid params from the event's payload (e.g. a missing required key)."""


@dataclass(frozen=True)
class Route:
    """One resolved row of ``config/event_routing.yaml``."""

    event_type: str
    tool: str
    param_builder: str
    autonomy_tier: str


def load_routing(path: str | Path = DEFAULT_ROUTING_PATH) -> dict[str, Route]:
    """Parse ``config/event_routing.yaml`` into ``event_type -> Route``.

    Raises :class:`EventRoutingError` on anything malformed: the file is
    missing, ``routes`` is absent, or an entry is missing a required key or
    names an ``autonomy_tier`` outside :data:`VALID_AUTONOMY_TIERS`. A
    malformed routing table must fail loudly at load time, not silently
    misroute (or fail to route) an event later.
    """
    text = Path(path).read_text(encoding="utf-8")
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise EventRoutingError(f"{path}: invalid YAML: {exc}") from exc

    raw = raw or {}
    routes_raw = raw.get("routes")
    if not isinstance(routes_raw, dict):
        raise EventRoutingError(f"{path}: missing or malformed top-level 'routes' mapping")

    routes: dict[str, Route] = {}
    for event_type, spec in routes_raw.items():
        if not isinstance(spec, dict):
            raise EventRoutingError(f"{path}: route '{event_type}' is not a mapping")
        missing = [key for key in ("tool", "param_builder", "autonomy_tier") if key not in spec]
        if missing:
            raise EventRoutingError(f"{path}: route '{event_type}' is missing {missing}")
        tier = spec["autonomy_tier"]
        if tier not in VALID_AUTONOMY_TIERS:
            raise EventRoutingError(
                f"{path}: route '{event_type}' has invalid autonomy_tier {tier!r} "
                f"(must be one of {VALID_AUTONOMY_TIERS})"
            )
        routes[event_type] = Route(
            event_type=event_type, tool=spec["tool"], param_builder=spec["param_builder"], autonomy_tier=tier,
        )
    return routes


def resolve_route(event: Event, routes: dict[str, Route]) -> Route:
    """The configured :class:`Route` for ``event.type``.

    Raises :class:`EventRoutingError` if no route is configured for that
    event type -- an unrouted event must be a loud, actionable failure (the
    Tower surfacing "I don't know what to do with this"), not a silently
    dropped one.
    """
    route = routes.get(event.type)
    if route is None:
        raise EventRoutingError(f"no route configured for event type {event.type!r}")
    return route


# ---- param builders ----------------------------------------------------
#
# Each builder turns one Event's payload into the kwargs Orchestrator.run()
# needs (brief/data_path/overrides/client) for the route's tool. Keyed by the
# `param_builder` string in config/event_routing.yaml, so the YAML controls
# which builder runs -- adding a route to a tool this module already has a
# builder for is a YAML-only change.

# inventory_optimization's own run params (scm_agent/tools.py's
# _inventory_run) it makes sense for an event payload to override; anything
# else in the payload (on_hand, reorder_point -- the condition data, not a
# tool param) is left out of overrides rather than passed through blindly.
_INVENTORY_OVERRIDE_KEYS = (
    "service_level", "holding_rate", "order_cost", "budget", "lead_time_days", "periods_per_year",
)


def inventory_from_stock_event(event: Event) -> dict:
    """Build ``inventory_optimization`` params from a ``stock_below_rop`` event.

    Requires ``event.payload["data_path"]`` -- the demand data file the
    condition was detected against (until PR-5's monitors read directly from
    ``src/state``, a monitor emitting this event type is expected to point at
    an exported demand file the way a client brief would). Raises
    :class:`EventRoutingError` if it is missing.
    """
    data_path = event.payload.get("data_path")
    if not data_path:
        raise EventRoutingError(
            f"event {event.id} ({event.type}) payload is missing 'data_path' -- "
            "inventory_optimization needs a demand data file to run against"
        )
    sku_label = event.sku or "the flagged SKU(s)"
    overrides = {key: event.payload[key] for key in _INVENTORY_OVERRIDE_KEYS if key in event.payload}
    return {
        "brief": f"Reorder point breached for {sku_label} -- recompute the inventory policy.",
        "data_path": data_path,
        "overrides": overrides,
        "client": event.payload.get("client", DEFAULT_EVENT_CLIENT),
    }


# PR-5 (Control Tower A1, scm_agent/monitors.py): both builders below turn a
# monitor-emitted event's event.payload["rows"] (state DataFrame rows -- see
# monitors.py's rop_breach_monitor/stockout_projected_monitor/
# excess_growing_monitor) into a throwaway temp CSV + data_path override,
# the SAME "rows -> temp CSV -> data_path" idiom webapp/mcp_server.py's
# _run_analysis_tool_sync uses for inline MCP tool-call rows. Unlike that
# function, the CSV can't be cleaned up in the same call (build_params()
# returns before Orchestrator.run() ever reads the file) -- left on disk
# under the OS temp dir by design, the same tradeoff webapp/app.py's own
# tempfile.mkdtemp(dir=JOBS_OUTPUT_DIR) upload path makes (see its
# _prune_old_jobs() reaper); a future PR can add an equivalent sweep for
# this prefix if it becomes a real disk-usage concern in production.
_TEMP_DIR_PREFIX = "linchpin_monitor_"

# jobs/intake.py needs a date + quantity demand HISTORY; the 'stock' state
# domain only carries a single current snapshot (on_hand/reorder_point/
# avg_daily_demand -- src/state/system_state.py's DOMAIN_COLUMNS as of PR-1
# has no historical-demand domain yet). Bridges the gap the same honest way
# alerting.py's own inputs are shaped: each flagged row's avg_daily_demand is
# replayed as a flat, this-many-day daily series ending today -- an honestly
# SYNTHETIC bootstrap history (documented as such below), not a fabricated
# real one, close enough to drive a real forecast -> policy recompute.
_SYNTHETIC_HISTORY_DAYS = 28


def inventory_from_state_stock_event(event: Event) -> dict:
    """Build ``inventory_optimization`` params from a ``rop_breach`` /
    ``stockout_projected`` event's ``event.payload["rows"]`` -- the flagged
    'stock' state-domain row(s) (``scm_agent.monitors``), instead of
    ``inventory_from_stock_event``'s older ``data_path``-to-an-exported-CSV
    contract.

    Each row's ``avg_daily_demand`` is replayed as a flat
    ``_SYNTHETIC_HISTORY_DAYS``-day daily series (see module-level comment)
    written to a throwaway temp CSV, so ``jobs.intake``'s date/product_id/
    quantity column detection has something to actually detect. Raises
    :class:`EventRoutingError` if ``rows`` is missing or empty.
    """
    rows = event.payload.get("rows")
    if not rows:
        raise EventRoutingError(
            f"event {event.id} ({event.type}) payload is missing 'rows' -- "
            "inventory_from_state_stock_event needs the flagged 'stock' snapshot row(s)"
        )

    today = datetime.now(timezone.utc).date()
    records = [
        {"date": (today - timedelta(days=_SYNTHETIC_HISTORY_DAYS - 1 - offset)).isoformat(),
         "product_id": row["product_id"], "quantity": float(row.get("avg_daily_demand", 0.0))}
        for row in rows
        for offset in range(_SYNTHETIC_HISTORY_DAYS)
    ]
    tmp_dir = Path(tempfile.mkdtemp(prefix=_TEMP_DIR_PREFIX))
    data_path = tmp_dir / "state_stock_demand.csv"
    pd.DataFrame(records).to_csv(data_path, index=False)

    sku_label = event.sku or "the flagged SKU(s)"
    overrides = {key: event.payload[key] for key in _INVENTORY_OVERRIDE_KEYS if key in event.payload}
    return {
        "brief": f"Reorder point breached for {sku_label} -- recompute the inventory policy.",
        "data_path": str(data_path),
        "overrides": overrides,
        "client": event.payload.get("client", DEFAULT_EVENT_CLIENT),
    }


# excess_obsolete's own run params (jobs/excess_obsolete_job.py's
# prepare_records()) it makes sense for an event payload to override.
_EXCESS_OBSOLETE_OVERRIDE_KEYS = ("target_cover_days", "dead_threshold_days")


def excess_obsolete_from_state_stock_event(event: Event) -> dict:
    """Build ``excess_obsolete`` params from an ``excess_growing`` event's
    ``event.payload["rows"]`` -- the flagged 'stock' state-domain row(s)
    (``scm_agent.monitors.excess_growing_monitor``).

    Unlike :func:`inventory_from_state_stock_event`, no synthesis is needed:
    ``jobs.excess_obsolete_job.prepare_records`` reads a plain CURRENT stock
    snapshot (product_id/on_hand/daily_demand), which is exactly the state
    'stock' domain's shape -- ``avg_daily_demand`` is renamed to
    ``daily_demand`` (one of that job's own recognized column aliases) so its
    column-sniffer picks it up; ``product_id``/``on_hand`` pass through
    unchanged. Raises :class:`EventRoutingError` if ``rows`` is missing or
    empty.
    """
    rows = event.payload.get("rows")
    if not rows:
        raise EventRoutingError(
            f"event {event.id} ({event.type}) payload is missing 'rows' -- "
            "excess_obsolete_from_state_stock_event needs the flagged 'stock' snapshot row(s)"
        )

    records = [
        {**{k: v for k, v in row.items() if k != "avg_daily_demand"}, "daily_demand": row.get("avg_daily_demand", 0.0)}
        for row in rows
    ]
    tmp_dir = Path(tempfile.mkdtemp(prefix=_TEMP_DIR_PREFIX))
    data_path = tmp_dir / "state_stock_excess.csv"
    pd.DataFrame(records).to_csv(data_path, index=False)

    sku_label = event.sku or "the flagged SKU(s)"
    overrides = {key: event.payload[key] for key in _EXCESS_OBSOLETE_OVERRIDE_KEYS if key in event.payload}
    return {
        "brief": f"Days-of-cover is growing for {sku_label} -- re-run the excess & obsolete classification.",
        "data_path": str(data_path),
        "overrides": overrides,
        "client": event.payload.get("client", DEFAULT_EVENT_CLIENT),
    }


# price_intelligence's own refs-CSV shape (jobs/price_intelligence.py's
# prepare_records()) it makes sense for a price_move/competitor_oos event
# payload to carry straight through as extra ref columns.
_PRICE_INTEL_OPTIONAL_REF_KEYS = ("html_path", "our_price")


def price_intel_refresh_from_event(event: Event) -> dict:
    """Build ``price_intelligence`` params from a ``price_move`` /
    ``competitor_oos`` event (Linchpin 3.0 PR-15: ``jobs.price_monitor``'s
    scheduled L0 MercadoLibre cycle and ``webapp.app``'s L2 watcher receiver
    both emit these carrying ``event.payload["site"]``/
    ``["competitor_sku_ref"]`` -- see ``src.pricing_intel.events``) -- a
    ONE-ROW refs CSV so ``price_intelligence`` re-runs its own acquire ->
    sanity -> deliver pipeline for exactly the flagged pair. Same
    "rows -> temp CSV -> data_path" idiom PR-5's
    ``inventory_from_state_stock_event`` established for a state-domain row;
    here it is one ``price_intelligence`` ref row instead.

    A MELI (L0)-sourced pair has no fetchable HTML (its
    ``competitor_sku_ref`` is a MercadoLibre item id, not a URL) --
    ``price_intelligence``'s own one-shot acquire step is L1-only (PR-13
    scope), so that ref legitimately comes back "skipped:
    id_ref_requires_l0_api_not_yet_available" in the refreshed report
    (never silently dropped -- see that job's own Fuentes section). The
    ``price_move``/``competitor_oos`` Event's OWN payload (old/new price,
    delta) -- not this refresh -- is the actual market signal already
    delivered to the Tower; this route exists to produce a fuller,
    E5-cited deliverable for a human who clicks through the T2 approval,
    not to re-derive the signal itself. Wiring an L0-aware acquire step
    into the one-shot playbook is a natural follow-on PR, not this one.

    Raises :class:`EventRoutingError` if ``site``/``competitor_sku_ref``
    (and ``sku``/``matched_product_id``) are missing.
    """
    site = event.payload.get("site")
    competitor_sku_ref = event.payload.get("competitor_sku_ref")
    product_id = event.payload.get("matched_product_id") or event.sku
    if not site or not competitor_sku_ref or not product_id:
        raise EventRoutingError(
            f"event {event.id} ({event.type}) payload is missing 'site'/'competitor_sku_ref' "
            "(and sku/matched_product_id) -- price_intel_refresh_from_event needs all three"
        )

    row: dict = {"product_id": product_id, "competitor_url": competitor_sku_ref, "competitor_site": site}
    for key in _PRICE_INTEL_OPTIONAL_REF_KEYS:
        if event.payload.get(key) is not None:
            row[key] = event.payload[key]

    tmp_dir = Path(tempfile.mkdtemp(prefix=_TEMP_DIR_PREFIX))
    data_path = tmp_dir / "price_move_refs.csv"
    pd.DataFrame([row]).to_csv(data_path, index=False)

    label = event.type.replace("_", " ")
    return {
        "brief": f"{label.capitalize()} detected for {product_id} at {site} -- refresh the position report.",
        "data_path": str(data_path),
        "overrides": {},
        "client": event.payload.get("client", DEFAULT_EVENT_CLIENT),
    }


# ---- los dos monitores que estuvieron sin rutear, y con que ------------------
#
# `forecast_error_out_of_band` y `lead_time_drift` se emitian desde PR-5 y no los
# consumia nadie. La razon escrita en el encabezado de `config/event_routing.yaml`
# era correcta: ni el snapshot de 'forecast' ni el de 'outcomes' traian una serie
# historica real, y fabricar una con 1-2 numeros agregados para poder correr un
# tool viola la regla 7 (procedencia total) y la 14 (ningun cap silencioso).
#
# Lo que cambio no es el criterio: es que ahora HAY serie real. `jobs/state_bridge.py`
# escribe la demanda realizada de los conectores como `outcomes`/`actual_qty` con la
# FECHA de cada pedido, asi que esta funcion la lee en vez de inventarla -- y si no
# alcanza, se niega.

#: El minimo de puntos con el que `jobs/forecast_job.py` acepta pronosticar. Se
#: reusa, no se elige de nuevo: un umbral propio aca seria una segunda politica de
#: "cuanta historia alcanza" que nadie mantendria sincronizada.
MIN_REAL_SERIES_POINTS = MIN_PERIODS_FOR_POLICY

#: Cuantos snapshots de ``outcomes`` se leen hacia atras. No es un cap sobre la serie:
#: cada ciclo escribe la VENTANA ENTERA que el conector devolvio (no un delta), asi que
#: el snapshot mas nuevo ya suele traerla completa y los anteriores solo aportan puntos
#: que se corrigieron o que salieron de la ventana del backend. Doce acota el costo de
#: leer una particion con meses de ciclos sin poder recortar una serie real.
OUTCOMES_SNAPSHOTS_READ = 12
#: La frecuencia con la que el tool de inventario corre si nadie dice otra cosa
#: (``scm_agent/tools.py::_inventory_run``). Se usa para convertir el plazo medido de
#: dias a periodos; un evento puede traer la suya en el payload.
DEFAULT_PERIODS_PER_YEAR = 52.0


def _store_from_event(event: Event) -> StateStore:
    """La particion cuya serie real hay que leer, NOMBRADA por el evento.

    ``run_all_monitors(client_id=...)` la pone en el payload. Sin ella no se cae a
    ``store_for(None)`` -- esa es la particion compartida de todos los clientes, y
    resolverla por omision es exactamente el defecto que ``store_from_params``
    documenta. Un evento que no puede decir de quien es su serie no tiene serie.
    """
    client_id = event.payload.get("state_client_id")
    if not client_id:
        raise EventRoutingError(
            f"event {event.id} ({event.type}) carries no 'state_client_id': no hay serie "
            "real que leer, y la particion compartida no es un default -- emit the event "
            "with run_all_monitors(client_id=...)"
        )
    base_path = event.payload.get("state_path") or DEFAULT_STATE_BASE_PATH
    return store_for(str(client_id), base_path=base_path)


def _real_series_csv(store: StateStore, skus: Sequence[str], *, min_points: int) -> Path:
    """CSV ``(product_id, date, quantity)`` con la demanda REALIZADA del estado.

    Lee los ultimos :data:`OUTCOMES_SNAPSHOTS_READ` snapshots de ``outcomes`` (no solo
    el ultimo: un ciclo escribe la ventana que leyo, y dos ciclos consecutivos se
    solapan), se queda con ``metric == "actual_qty"`` y con los SKU pedidos, y deduplica
    por (SKU, fecha) quedandose con el valor del ciclo mas nuevo -- que es una
    correccion, no un duplicado.

    Levanta :class:`EventRoutingError` si algun SKU queda con menos de ``min_points``
    puntos. Es la regla 7 en una linea: una serie corta no se completa con nada. El
    constructor de eventos de stock SI sintetiza historia y lo dice
    (``_SYNTHETIC_HISTORY_DAYS``); aca no hace falta, porque la serie existe -- y si no
    existe, la respuesta correcta es negarse.
    """
    rows: dict[tuple[str, str], float] = {}
    wanted = {str(s) for s in skus}
    for snap in state_history("outcomes", window=OUTCOMES_SNAPSHOTS_READ, store=store):
        payload = snap.payload
        if payload.empty or "metric" not in payload.columns:
            continue
        actual = payload[payload["metric"] == METRIC_ACTUAL_QTY]
        for _, row in actual.iterrows():
            product_id = str(row["product_id"])
            if product_id not in wanted:
                continue
            period = str(row.get("period", "") or "").strip()
            if not period:
                continue
            rows[(product_id, period)] = float(row["value"])

    por_sku: dict[str, int] = {}
    for product_id, _period in rows:
        por_sku[product_id] = por_sku.get(product_id, 0) + 1
    faltantes = sorted(
        f"{sku} ({por_sku.get(sku, 0)})" for sku in wanted if por_sku.get(sku, 0) < min_points
    )
    if faltantes:
        raise EventRoutingError(
            f"la serie real de {', '.join(faltantes)} tiene menos de {min_points} puntos de "
            "'actual_qty' en el estado: no se completa con nada (regla 7, procedencia "
            "total). Corre primero un tool de conector con state_client_id para que la "
            "demanda realizada quede escrita."
        )

    records = [
        {"product_id": product_id, "date": period, "quantity": qty}
        for (product_id, period), qty in sorted(rows.items())
    ]
    tmp_dir = Path(tempfile.mkdtemp(prefix=_TEMP_DIR_PREFIX))
    data_path = tmp_dir / "state_actual_qty.csv"
    pd.DataFrame(records).to_csv(data_path, index=False)
    return data_path


def _event_skus(event: Event) -> list[str]:
    if event.sku:
        return [str(event.sku)]
    raise EventRoutingError(
        f"event {event.id} ({event.type}) carries no sku: no hay de quien leer la serie"
    )


def forecast_from_outcomes_event(event: Event) -> dict:
    """``forecast_error_out_of_band`` -> el tool ``forecast``, sobre la serie REAL.

    El monitor ya dijo que el pronostico se fue de banda; lo que falta es volver a
    pronosticar con lo que efectivamente paso, que es lo que este tool hace (elige
    metodo por segmento, hace backtest y mide el valor agregado contra el ingenuo). La
    serie sale de ``outcomes``/``actual_qty``: pedidos reales del conector, con sus
    fechas.
    """
    skus = _event_skus(event)
    data_path = _real_series_csv(_store_from_event(event), skus, min_points=MIN_REAL_SERIES_POINTS)
    error = event.payload.get("relative_error")
    detalle = f" ({error:+.0%} off)" if isinstance(error, (int, float)) else ""
    return {
        "brief": (f"Forecast error out of band for {', '.join(skus)}{detalle} -- re-fit the "
                  "forecast on the demand that actually happened."),
        "data_path": str(data_path),
        "overrides": {},
        "client": event.payload.get("client", DEFAULT_EVENT_CLIENT),
    }


def inventory_from_lead_time_drift_event(event: Event) -> dict:
    """``lead_time_drift`` -> ``inventory_optimization`` con el plazo MEDIDO encima.

    El destino es la politica, no el plan de ventas. Un plazo que se corre cambia el
    periodo de riesgo, y con el el punto de reorden y el stock de seguridad: eso es
    exactamente lo que ``lead_times`` -- un parametro que el tool YA acepta -- mueve.
    (La investigacion sugeria ``sop`` con un ``promo_lift`` derivado del drift; un lift
    de promocion es DEMANDA, no plazo, y usarlo de proxy seria el cap silencioso que la
    regla 14 prohibe.)

    El plazo reciente viene en el payload del propio evento -- el monitor lo midio --
    asi que no se vuelve al estado por el; al estado se vuelve solo por la serie de
    demanda, que es lo que el tool necesita para re-dimensionar.
    """
    skus = _event_skus(event)
    recent = event.payload.get("recent_lead_time_days")
    if recent is None:
        raise EventRoutingError(
            f"event {event.id} ({event.type}) carries no 'recent_lead_time_days': sin el "
            "plazo medido no hay nada que pasarle a la politica"
        )
    data_path = _real_series_csv(_store_from_event(event), skus, min_points=MIN_REAL_SERIES_POINTS)
    baseline = event.payload.get("baseline_lead_time_days")
    movimiento = (f"{baseline:.1f}d -> {float(recent):.1f}d"
                  if isinstance(baseline, (int, float)) else f"{float(recent):.1f}d")
    # EL CRUCE DE UNIDADES, que es donde esta el error facil. El monitor mide DIAS;
    # `lead_times` en `jobs/inventory_optimization.py::run` reemplaza
    # `lead_time_periods`. Pasar los dias tal cual multiplicaria el periodo de riesgo
    # por siete -- punto de reorden y stock de seguridad inflados sobre una lectura
    # correcta, sin que nada avise. La conversion es la del motor, no una propia.
    periods_per_year = float(event.payload.get("periods_per_year") or DEFAULT_PERIODS_PER_YEAR)
    en_periodos = lead_time_periods_from_days(float(recent), periods_per_year)
    return {
        "brief": (f"Lead time drifted for {', '.join(skus)} ({movimiento}) -- re-size the "
                  "policy against the measured lead time."),
        "data_path": str(data_path),
        "overrides": {"lead_times": {sku: en_periodos for sku in skus},
                      "periods_per_year": periods_per_year},
        "client": event.payload.get("client", DEFAULT_EVENT_CLIENT),
    }


PARAM_BUILDERS: dict[str, Callable[[Event], dict]] = {
    "inventory_from_stock_event": inventory_from_stock_event,
    "inventory_from_state_stock_event": inventory_from_state_stock_event,
    "excess_obsolete_from_state_stock_event": excess_obsolete_from_state_stock_event,
    "price_intel_refresh_from_event": price_intel_refresh_from_event,
    "forecast_from_outcomes_event": forecast_from_outcomes_event,
    "inventory_from_lead_time_drift_event": inventory_from_lead_time_drift_event,
}


def build_params(event: Event, route: Route) -> dict:
    """Run ``route``'s configured param builder against ``event``.

    Raises :class:`EventRoutingError` if the YAML names a ``param_builder``
    this module has no entry for.
    """
    builder = PARAM_BUILDERS.get(route.param_builder)
    if builder is None:
        raise EventRoutingError(
            f"route '{route.event_type}' names unknown param_builder {route.param_builder!r} "
            f"(known: {sorted(PARAM_BUILDERS)})"
        )
    return builder(event)


@dataclass(frozen=True)
class RoutedResult:
    """What :func:`handle_event` produced: the route it resolved, the
    orchestrator's real :class:`~nosilo_core.types.JobResult`, and whether
    ``notify()`` was invoked and succeeded (``False`` on non-``ok`` status,
    on a no-op notify -- e.g. no webhook configured -- or on a delivery
    failure; see ``jobs/notify.py``)."""

    route: Route
    result: JobResult
    notified: bool


def handle_event(
    event: Event,
    *,
    routes: dict[str, Route] | None = None,
    routing_path: str | Path = DEFAULT_ROUTING_PATH,
    orchestrator: Orchestrator | None = None,
    out_dir: str | Path = "deliverables/agent",
    webhook_url: str | None = None,
    notify_on_ok: bool = True,
) -> RoutedResult:
    """Route ``event`` to its tool and run it through the real orchestrator.

    ``routes`` lets a caller pass an already-loaded routing table (what the
    unit tests below do); when omitted, :func:`load_routing` reads
    ``routing_path`` (default ``config/event_routing.yaml``). ``orchestrator``
    defaults to a fresh ``Orchestrator(clients_root=None)`` -- events are
    system-initiated (a monitor, not an authenticated client identity), so
    this follows the same trust boundary the webapp/MCP surface uses (see
    ``scm_agent/orchestrator.py``'s ``clients_root`` docstring): a generic
    ``payload["client"]`` label must never resolve a real client's cost
    profile.

    On :data:`~nosilo_core.types.STATUS_OK`, calls ``jobs.notify.notify()``
    with a short summary; any other status (``needs_data``, ``qa_failed``,
    ``error``, ...) is returned without notifying -- the plan's QA veto
    (rule 2) applies here exactly as it does to a brief-driven run: no
    notification for a result nothing was actually delivered for.

    ``notify_on_ok`` (default ``True``, so every existing caller keeps its
    exact prior behavior) lets ``scm_agent.autonomy``'s tier enforcement
    (PR-6) suppress this automatic notify for T2/T3 routes -- those tiers
    must never be announced as done before a human has acknowledged or
    escalated them; only T1 (and any caller not using tiers at all) gets the
    old unconditional "ran ok -> notify" behavior.
    """
    routes = routes if routes is not None else load_routing(routing_path)
    route = resolve_route(event, routes)
    params = build_params(event, route)

    orch = orchestrator if orchestrator is not None else Orchestrator(clients_root=None)
    try:
        tool = orch.registry.get(route.tool)
    except KeyError as exc:
        raise EventRoutingError(
            f"route '{route.event_type}' names tool {route.tool!r}, which is not registered"
        ) from exc

    result = orch.run(
        params["brief"],
        data_path=params.get("data_path"),
        overrides=params.get("overrides"),
        job_type=route.tool,
        client=params.get("client", DEFAULT_EVENT_CLIENT),
        out_dir=out_dir,
    )

    notified = False
    if result.status == STATUS_OK and notify_on_ok:
        sku_part = f" ({event.sku})" if event.sku else ""
        summary = f"[{route.autonomy_tier}] {event.type}{sku_part}: {tool.title} -- {result.summary}"
        notified = notify(summary, webhook_url=webhook_url)

    return RoutedResult(route=route, result=result, notified=notified)
