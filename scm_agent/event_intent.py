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
``PARAM_BUILDERS``, never editing ``scm_agent/registry.py`` or
``scm_agent/tools.py``.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yaml

from jobs.notify import notify

from .events import Event
from .orchestrator import Orchestrator
from .types import STATUS_OK, JobResult

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


PARAM_BUILDERS: dict[str, Callable[[Event], dict]] = {
    "inventory_from_stock_event": inventory_from_stock_event,
    "inventory_from_state_stock_event": inventory_from_state_stock_event,
    "excess_obsolete_from_state_stock_event": excess_obsolete_from_state_stock_event,
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
    orchestrator's real :class:`~scm_agent.types.JobResult`, and whether
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

    On :data:`~scm_agent.types.STATUS_OK`, calls ``jobs.notify.notify()``
    with a short summary; any other status (``needs_data``, ``qa_failed``,
    ``error``, ...) is returned without notifying -- the plan's QA veto
    (rule 2) applies here exactly as it does to a brief-driven run: no
    notification for a result nothing was actually delivered for.
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
    if result.status == STATUS_OK:
        sku_part = f" ({event.sku})" if event.sku else ""
        summary = f"[{route.autonomy_tier}] {event.type}{sku_part}: {tool.title} -- {result.summary}"
        notified = notify(summary, webhook_url=webhook_url)

    return RoutedResult(route=route, result=result, notified=notified)
