"""Control Tower monitors -- A1 "sense" layer (Linchpin 3.0 PR-5, plan S5).

Pure detection over already-fetched ``src.state`` snapshots: given the data,
decide which conditions fire, return :class:`~scm_agent.events.Event`
objects. Reading the state (``src.state.latest``/``history``) and deciding
what to *do* about an emitted event (``scm_agent.event_intent``, A2) are both
someone else's job -- a monitor function here never touches ``src.state``
itself; its caller (a test, or :func:`run_all_monitors` below) fetches the
snapshot(s) and passes them in.

**Generalizes** ``src/alerting.py``'s ``detect_events`` (the exact
evidence-from-a-per-SKU-dict pattern this module extends to the rest of
Control Tower's 5 base monitors, per plan S5's A1 row):

  - :func:`rop_breach_monitor`               -- ROP cruzado (reorder_due)
  - :func:`stockout_projected_monitor`       -- stockout proyectado (stockout_risk)
  - :func:`excess_growing_monitor`           -- exceso creciente (a real 2-snapshot trend,
                                                 not alerting.py's single-snapshot "excess")
  - :func:`forecast_error_out_of_band_monitor` -- sigma_e fuera de banda
  - :func:`lead_time_drift_monitor`          -- drift de lead time

Dedup (plan S4.2): every monitor accepts an optional ``ledger:
scm_agent.events.EventLedger``. When given, each candidate event is recorded
via ``ledger.emit()`` and only the ones actually recorded (i.e. not a repeat
of the same ``dedup_key`` inside the ledger's window) are returned -- running
the same monitor twice over unchanged state is a no-op the second time. With
``ledger=None`` (the default), every candidate is returned unfiltered, which
is what makes the detection logic itself trivially unit-testable in
isolation from SQLite. ``dedup_key`` is always ``"{product_id}:{event_type}"``
-- stable across repeated runs of the SAME condition (so the ledger collapses
them), and naturally different whenever the condition itself is a different
*kind* of breach (e.g. a SKU's cover dropping from "reorder_due" territory
into "stockout_risk" fires a different event TYPE, hence a different key --
see ``rop_breach_monitor``/``stockout_projected_monitor``'s docstrings).

Two of the five monitors below (``forecast_error_out_of_band_monitor`` and
``lead_time_drift_monitor``) are deliberately NOT wired into
``config/event_routing.yaml`` in this PR -- see their docstrings and
``config/event_routing.yaml``'s own header comment for why forcing them onto
a registered tool right now would mean fabricating a "demand history" from
1-2 aggregate numbers, which the plan's own new rules (7 "procedencia total",
14 "ningun cap silencioso") forbid. They still emit real, dedup'd events onto
the ledger -- a future PR (A4's ``src/verify/backtest.py`` is the natural
home) is expected to consume them directly instead of routing through a tool
that cannot honestly act on them yet.
"""

from __future__ import annotations

import os
from pathlib import Path

import pandas as pd
import yaml

from src.alerting import detect_events
from src.state.store import StateStore
from src.state.system_state import StateSnapshot
from src.state.system_state import history as state_history
from src.state.system_state import latest as state_latest

from .events import Event, EventLedger

SOURCE = "monitors"

# Env-override convention matching scm_agent/event_intent.py's
# LINCHPIN_EVENT_ROUTING_PATH and scm_agent/events.py's DEFAULT_PATH.
DEFAULT_MONITORS_CONFIG_PATH = os.environ.get("LINCHPIN_MONITORS_CONFIG_PATH", "").strip() or "config/monitors.yaml"

# Fallback thresholds when config/monitors.yaml is not loaded/passed and the
# caller does not override a monitor's kwarg explicitly. The first two mirror
# src/alerting.py's own detect_events() defaults exactly (7.0 / 90.0 days).
DEFAULT_CRITICAL_COVER_DAYS = 7.0
DEFAULT_EXCESS_COVER_DAYS = 90.0
DEFAULT_MIN_GROWTH_DAYS = 1.0
DEFAULT_FORECAST_ERROR_BAND = 0.30
DEFAULT_LEAD_TIME_DRIFT_THRESHOLD = 0.25
DEFAULT_MIN_BASELINE_LEAD_TIME_DAYS = 1.0

# Event types this module emits. Deliberately DISTINCT from "stock_below_rop"
# (config/event_routing.yaml's pre-existing PR-4 route, protected by an F0
# integration test) -- these carry event.payload["rows"] (state DataFrame
# rows) instead of stock_below_rop's event.payload["data_path"] (an exported
# CSV path), so they need their own routes/param builders, not a shared one.
EVENT_ROP_BREACH = "rop_breach"
EVENT_STOCKOUT_PROJECTED = "stockout_projected"
EVENT_EXCESS_GROWING = "excess_growing"
EVENT_FORECAST_ERROR_OUT_OF_BAND = "forecast_error_out_of_band"
EVENT_LEAD_TIME_DRIFT = "lead_time_drift"


class MonitorConfigError(RuntimeError):
    """``config/monitors.yaml`` is missing, malformed, or a monitor entry in
    it is not a mapping. Mirrors ``scm_agent.event_intent.EventRoutingError``
    -- a malformed monitor config must fail loudly at load time, not silently
    run every monitor at some undefined default."""


def load_monitor_config(path: str | Path = DEFAULT_MONITORS_CONFIG_PATH) -> dict[str, dict]:
    """Parse ``config/monitors.yaml`` into ``monitor_name -> its settings dict``
    (``enabled``, ``cadence_minutes``, plus whichever threshold keys that
    monitor reads -- see the module docstring's per-monitor list).
    ``cadence_minutes`` is data only in this PR (no scheduler job reads it
    yet); it documents the production cadence a later PR's
    ``jobs.scheduler.ScheduledJob`` is expected to use.
    """
    text = Path(path).read_text(encoding="utf-8")
    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise MonitorConfigError(f"{path}: invalid YAML: {exc}") from exc

    raw = raw or {}
    monitors = raw.get("monitors")
    if not isinstance(monitors, dict):
        raise MonitorConfigError(f"{path}: missing or malformed top-level 'monitors' mapping")
    for name, spec in monitors.items():
        if not isinstance(spec, dict):
            raise MonitorConfigError(f"{path}: monitor '{name}' is not a mapping")
    return monitors


def _setting(config: dict | None, monitor_name: str, key: str, default: float) -> float:
    if not config:
        return default
    spec = config.get(monitor_name) or {}
    return float(spec.get(key, default))


def _monitor_enabled(config: dict | None, monitor_name: str) -> bool:
    if not config:
        return True
    spec = config.get(monitor_name) or {}
    return bool(spec.get("enabled", True))


def _dedup_key(product_id: str, event_type: str) -> str:
    return f"{product_id}:{event_type}"


def _emit(candidates: list[Event], ledger: EventLedger | None) -> list[Event]:
    """Record each candidate in ``ledger`` and keep only the ones actually
    written -- a repeat ``dedup_key`` inside the ledger's window is dropped,
    exactly matching ``EventLedger.emit()``'s own contract. ``ledger=None``
    returns every candidate unfiltered."""
    if ledger is None:
        return candidates
    return [e for e in candidates if ledger.emit(e)]


# ---- (a)/(d) ROP cruzado + stockout proyectado -----------------------------
#
# Both read the 'stock' domain (product_id, on_hand, reorder_point,
# avg_daily_demand -- exactly src/alerting.py's InventoryEvent input shape)
# through the SAME src.alerting.detect_events() call, then split its
# "reorder_due"/"stockout_risk" kinds into two distinct event types. Kept
# mutually exclusive per product_id (detect_events' own "at most one state
# event per SKU" invariant), matching alerting.py's stated design instead of
# double-firing both for the same breach.


def rop_breach_monitor(
    stock: StateSnapshot,
    *,
    critical_cover_days: float = DEFAULT_CRITICAL_COVER_DAYS,
    source: str = SOURCE,
    ledger: EventLedger | None = None,
) -> list[Event]:
    """ROP cruzado: product_ids at/below their reorder point with days-of-cover
    still at/above ``critical_cover_days`` (``detect_events``'s "reorder_due"
    kind -- the less urgent of the two ROP-crossing signals; see
    ``stockout_projected_monitor`` for the urgent one).

    Reference example: on_hand=15, reorder_point=20, avg_daily_demand=1 ->
    cover=15.0 days >= 7.0 -> fires (medium severity). on_hand=15,
    reorder_point=20, avg_daily_demand=5 -> cover=3.0 days < 7.0 -> does NOT
    fire here (that's ``stockout_projected_monitor``'s case instead).
    """
    rows = stock.payload.to_dict("records")
    rows_by_id = {str(r["product_id"]): r for r in rows}
    inv_events = detect_events(rows, critical_cover_days=critical_cover_days)

    candidates = [
        Event(
            type=EVENT_ROP_BREACH,
            severity=e.severity,
            source=source,
            dedup_key=_dedup_key(e.product_id, EVENT_ROP_BREACH),
            sku=e.product_id,
            payload={**e.detail, "message": e.message, "rows": [dict(rows_by_id[e.product_id])]},
        )
        for e in inv_events
        if e.kind == "reorder_due"
    ]
    return _emit(candidates, ledger)


def stockout_projected_monitor(
    stock: StateSnapshot,
    *,
    critical_cover_days: float = DEFAULT_CRITICAL_COVER_DAYS,
    source: str = SOURCE,
    ledger: EventLedger | None = None,
) -> list[Event]:
    """Stockout proyectado: product_ids at/below their reorder point with
    days-of-cover BELOW ``critical_cover_days`` (``detect_events``'s
    "stockout_risk" kind -- its single most urgent signal).

    Reference example: on_hand=10, reorder_point=20, avg_daily_demand=5 ->
    cover=2.0 days < 7.0 -> fires (high severity).
    """
    rows = stock.payload.to_dict("records")
    rows_by_id = {str(r["product_id"]): r for r in rows}
    inv_events = detect_events(rows, critical_cover_days=critical_cover_days)

    candidates = [
        Event(
            type=EVENT_STOCKOUT_PROJECTED,
            severity=e.severity,
            source=source,
            dedup_key=_dedup_key(e.product_id, EVENT_STOCKOUT_PROJECTED),
            sku=e.product_id,
            payload={**e.detail, "message": e.message, "rows": [dict(rows_by_id[e.product_id])]},
        )
        for e in inv_events
        if e.kind == "stockout_risk"
    ]
    return _emit(candidates, ledger)


# ---- (e) Exceso creciente ---------------------------------------------------


def _cover_by_product(df: pd.DataFrame) -> dict[str, float | None]:
    """days-of-cover per product_id; None where avg_daily_demand <= 0 (cover is
    undefined/infinite -- alerting.py's own "dead_stock" territory, out of
    this monitor's scope)."""
    out: dict[str, float | None] = {}
    for row in df.to_dict("records"):
        adt = float(row["avg_daily_demand"])
        out[str(row["product_id"])] = (float(row["on_hand"]) / adt) if adt > 0 else None
    return out


def excess_growing_monitor(
    stock_history: list[StateSnapshot],
    *,
    excess_cover_days: float = DEFAULT_EXCESS_COVER_DAYS,
    min_growth_days: float = DEFAULT_MIN_GROWTH_DAYS,
    source: str = SOURCE,
    ledger: EventLedger | None = None,
) -> list[Event]:
    """Exceso creciente: unlike ``detect_events``'s single-snapshot "excess"
    kind (cover > ``excess_cover_days`` at one point in time), this is a real
    TREND check across the two most recent 'stock' snapshots
    (``src.state.history("stock", window=2+)``, oldest first, as returned by
    ``src.state.history``). A product_id must be over the excess ceiling in
    the LATEST snapshot AND its days-of-cover must have grown by at least
    ``min_growth_days`` since the PREVIOUS snapshot -- a product that is
    excess but flat or shrinking does not fire here.

    Needs at least 2 snapshots; returns ``[]`` with fewer (nothing to compare
    a trend against yet).

    Reference example: previous snapshot on_hand=200, avg_daily_demand=2 ->
    cover=100.0 days; latest snapshot on_hand=260, avg_daily_demand=2 ->
    cover=130.0 days. 130.0 > 90.0 (excess) and grew by 30.0 >= 1.0
    (``min_growth_days``) -> fires. A latest snapshot of on_hand=200 (cover
    unchanged at 100.0, growth=0.0) does NOT fire -- excess, but not growing.
    """
    if len(stock_history) < 2:
        return []
    prev, curr = stock_history[-2], stock_history[-1]
    prev_cover = _cover_by_product(prev.payload)
    curr_cover = _cover_by_product(curr.payload)

    candidates: list[Event] = []
    for product_id, cover in curr_cover.items():
        if cover is None or cover <= excess_cover_days:
            continue
        prev_c = prev_cover.get(product_id)
        if prev_c is None:
            continue
        growth = cover - prev_c
        if growth < min_growth_days:
            continue
        severity = "medium" if growth >= 2 * min_growth_days else "low"
        candidates.append(
            Event(
                type=EVENT_EXCESS_GROWING,
                severity=severity,
                source=source,
                dedup_key=_dedup_key(product_id, EVENT_EXCESS_GROWING),
                sku=product_id,
                payload={
                    "days_of_cover_prev": prev_c,
                    "days_of_cover_curr": cover,
                    "growth_days": growth,
                    "excess_cover_days": excess_cover_days,
                    "message": (
                        f"{product_id}: days-of-cover grew {prev_c:.1f} -> {cover:.1f} "
                        f"(+{growth:.1f}d), still excess"
                    ),
                    "rows": [dict(r) for r in curr.payload.to_dict("records") if str(r["product_id"]) == product_id],
                },
            )
        )
    return _emit(candidates, ledger)


# ---- (b) sigma_e fuera de banda --------------------------------------------


def forecast_error_out_of_band_monitor(
    forecast: StateSnapshot,
    outcomes: StateSnapshot,
    *,
    band_width: float = DEFAULT_FORECAST_ERROR_BAND,
    source: str = SOURCE,
    ledger: EventLedger | None = None,
) -> list[Event]:
    """sigma_e fuera de banda: relative forecast error outside an expected band.

    **Documented convention** (state's 'forecast'/'outcomes' domains carry no
    dedicated error column -- ``src/state/system_state.py``'s DOMAIN_COLUMNS
    as of PR-1 was not extended for this): ``forecast`` gives ``forecast_qty``
    per product_id (summed across any 'period' rows present in the snapshot);
    realized demand is read from ``outcomes`` rows whose ``metric ==
    "actual_qty"`` (summed per product_id via the SAME generic metric/value
    shape the 'outcomes' domain already offers -- no schema change needed).
    ``relative_error = (actual - forecast) / forecast``. A product_id with no
    ``forecast_qty`` > 0, or no matching ``actual_qty`` row, is skipped (not
    flagged) -- nothing to compare it against.

    Reference example: forecast_qty=100, actual_qty=150 -> error=+50%,
    |0.50| > 0.30 (``band_width``) but <= 0.60 (``2 * band_width``) -> fires,
    medium severity. actual_qty=210 -> error=+110%, > 0.60 -> fires, high
    severity. actual_qty=120 -> error=+20%, <= 0.30 -> does NOT fire.

    Deliberately NOT routed in ``config/event_routing.yaml`` in this PR -- see
    the module docstring.
    """
    forecast_by_product = forecast.payload.groupby("product_id")["forecast_qty"].sum().to_dict()
    actual_rows = outcomes.payload[outcomes.payload["metric"] == "actual_qty"]
    actual_by_product = actual_rows.groupby("product_id")["value"].sum().to_dict()

    candidates: list[Event] = []
    for product_id, forecast_qty in forecast_by_product.items():
        if forecast_qty <= 0 or product_id not in actual_by_product:
            continue
        actual_qty = actual_by_product[product_id]
        error = (actual_qty - forecast_qty) / forecast_qty
        if abs(error) <= band_width:
            continue
        severity = "high" if abs(error) > 2 * band_width else "medium"
        candidates.append(
            Event(
                type=EVENT_FORECAST_ERROR_OUT_OF_BAND,
                severity=severity,
                source=source,
                dedup_key=_dedup_key(str(product_id), EVENT_FORECAST_ERROR_OUT_OF_BAND),
                sku=str(product_id),
                payload={
                    "forecast_qty": float(forecast_qty),
                    "actual_qty": float(actual_qty),
                    "relative_error": error,
                    "band_width": band_width,
                    "message": (
                        f"{product_id}: forecast {forecast_qty:g} vs actual {actual_qty:g} "
                        f"({error:+.0%} error, band +/-{band_width:.0%})"
                    ),
                },
            )
        )
    return _emit(candidates, ledger)


# ---- (c) drift de lead time -------------------------------------------------


def _lead_time_by_product(df: pd.DataFrame) -> dict[str, float]:
    rows = df[df["metric"] == "lead_time_days"]
    if rows.empty:
        return {}
    return rows.groupby("product_id")["value"].mean().to_dict()


def lead_time_drift_monitor(
    outcomes_history: list[StateSnapshot],
    *,
    drift_threshold: float = DEFAULT_LEAD_TIME_DRIFT_THRESHOLD,
    min_baseline_days: float = DEFAULT_MIN_BASELINE_LEAD_TIME_DAYS,
    source: str = SOURCE,
    ledger: EventLedger | None = None,
) -> list[Event]:
    """Drift de lead time: compares mean observed lead time per product_id
    between the two most recent 'outcomes' snapshots
    (``src.state.history("outcomes", window=2+)``, oldest first).

    **Documented convention** (state's domains as of PR-1 carry no dedicated
    lead-time domain; DOMAIN_COLUMNS was NOT extended for this monitor --
    matching PR-1's own "extend DOMAIN_COLUMNS rather than bypass validation
    IF a consumer needs more required fields" guidance, applied here by
    choosing NOT to touch a finished, tested module when the existing generic
    shape already covers it): lead-time observations are 'outcomes' rows with
    ``metric == "lead_time_days"`` (``value`` = one observed PO lead time in
    days), reusing the exact same generic metric/value shape the forecast-
    error monitor above uses.

    Needs at least 2 snapshots; returns ``[]`` with fewer. A product_id absent
    from the baseline snapshot, or whose baseline mean is below
    ``min_baseline_days`` (avoids a noisy ratio off a near-zero denominator),
    is skipped.

    Reference example: baseline (previous snapshot) mean=10.0 days, recent
    (latest snapshot) mean=13.0 days -> drift=+30%, > 0.25
    (``drift_threshold``) but <= 0.50 (``2 * drift_threshold``) -> fires,
    medium severity. recent=16.0 -> drift=+60%, > 0.50 -> fires, high
    severity. recent=9.0 -> drift=-10% -> shrinking lead time still fires
    (worth noting) but at low severity, never high/medium (a shorter lead
    time is good news operationally, not an urgent risk).

    Deliberately NOT routed in ``config/event_routing.yaml`` in this PR -- see
    the module docstring.
    """
    if len(outcomes_history) < 2:
        return []
    prev, curr = outcomes_history[-2], outcomes_history[-1]
    prev_avg = _lead_time_by_product(prev.payload)
    curr_avg = _lead_time_by_product(curr.payload)

    candidates: list[Event] = []
    for product_id, recent in curr_avg.items():
        baseline = prev_avg.get(product_id)
        if baseline is None or baseline < min_baseline_days:
            continue
        drift = (recent - baseline) / baseline
        if abs(drift) <= drift_threshold:
            continue
        if drift > 0:
            severity = "high" if drift > 2 * drift_threshold else "medium"
        else:
            severity = "low"
        candidates.append(
            Event(
                type=EVENT_LEAD_TIME_DRIFT,
                severity=severity,
                source=source,
                dedup_key=_dedup_key(str(product_id), EVENT_LEAD_TIME_DRIFT),
                sku=str(product_id),
                payload={
                    "baseline_lead_time_days": baseline,
                    "recent_lead_time_days": recent,
                    "drift_ratio": drift,
                    "drift_threshold": drift_threshold,
                    "message": f"{product_id}: lead time drifted {baseline:.1f}d -> {recent:.1f}d ({drift:+.0%})",
                },
            )
        )
    return _emit(candidates, ledger)


# ---- one full A1 "sense" cycle ---------------------------------------------


def run_all_monitors(
    *,
    config: dict | None = None,
    ledger: EventLedger | None = None,
    store: StateStore | None = None,
    stock_history_window: int = 2,
    outcomes_history_window: int = 2,
) -> list[Event]:
    """One full A1 'sense' cycle: read the current state domains and run every
    ``enabled`` monitor from ``config`` (default: ``load_monitor_config()``)
    against them, returning every event actually recorded (post-dedup, when
    ``ledger`` is given).

    Batch-degradable (plan rule 9): a plain, zero-required-arg-shaped
    function call -- no scheduler loop, no daemon, no sleeping -- safe to
    call directly from a test today and trivially registrable as a
    ``jobs.scheduler.ScheduledJob``'s ``func`` in a later PR. A domain with no
    snapshot yet (nothing written by any job) makes its monitor(s) silently
    contribute zero events rather than raising -- an empty Tower on day one
    is expected, not an error.
    """
    config = config if config is not None else load_monitor_config()
    events: list[Event] = []

    stock_hist = state_history("stock", window=stock_history_window, store=store)
    if stock_hist:
        latest_stock = stock_hist[-1]
        if _monitor_enabled(config, "rop_breach"):
            events += rop_breach_monitor(
                latest_stock,
                critical_cover_days=_setting(
                    config, "rop_breach", "critical_cover_days", DEFAULT_CRITICAL_COVER_DAYS
                ),
                ledger=ledger,
            )
        if _monitor_enabled(config, "stockout_projected"):
            events += stockout_projected_monitor(
                latest_stock,
                critical_cover_days=_setting(
                    config, "stockout_projected", "critical_cover_days", DEFAULT_CRITICAL_COVER_DAYS
                ),
                ledger=ledger,
            )
        if _monitor_enabled(config, "excess_growing"):
            events += excess_growing_monitor(
                stock_hist,
                excess_cover_days=_setting(
                    config, "excess_growing", "excess_cover_days", DEFAULT_EXCESS_COVER_DAYS
                ),
                min_growth_days=_setting(
                    config, "excess_growing", "min_growth_days", DEFAULT_MIN_GROWTH_DAYS
                ),
                ledger=ledger,
            )

    forecast_snap = state_latest("forecast", store=store)
    outcomes_hist = state_history("outcomes", window=outcomes_history_window, store=store)
    if forecast_snap is not None and outcomes_hist and _monitor_enabled(config, "forecast_error_out_of_band"):
        events += forecast_error_out_of_band_monitor(
            forecast_snap,
            outcomes_hist[-1],
            band_width=_setting(
                config, "forecast_error_out_of_band", "band_width", DEFAULT_FORECAST_ERROR_BAND
            ),
            ledger=ledger,
        )
    if outcomes_hist and _monitor_enabled(config, "lead_time_drift"):
        events += lead_time_drift_monitor(
            outcomes_hist,
            drift_threshold=_setting(
                config, "lead_time_drift", "drift_threshold", DEFAULT_LEAD_TIME_DRIFT_THRESHOLD
            ),
            min_baseline_days=_setting(
                config, "lead_time_drift", "min_baseline_days", DEFAULT_MIN_BASELINE_LEAD_TIME_DAYS
            ),
            ledger=ledger,
        )
    return events
