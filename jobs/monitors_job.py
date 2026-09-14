"""Control Tower monitors as a registrable cron job -- the "sense" loop
finally wired to a schedule.

``scm_agent/monitors.py`` shipped the five (+1) monitors and
``run_all_monitors()`` deliberately as a plain, one-shot, all-default-kwargs
function, with ``config/monitors.yaml``'s ``cadence_minutes`` explicitly "data
only in this PR (no scheduler job reads it yet)" -- its own docstring names
``jobs.scheduler.ScheduledJob`` as the expected future home. This module is
that home: it wraps ``run_all_monitors()`` in the exact ``ScheduledJob.func``
shape ``jobs/price_monitor.py`` and ``jobs/price_watch.py`` already use, ready
for a DEDICATED monitors cron on the monitors' own cadence.

**Not wired into the price endpoint.** ``POST /api/jobs/run-scheduled``
(``webapp/app.py``) already runs ``run_all_monitors`` INLINE on every tick
(threading in the price-signal events the two price jobs emit), so the
inventory monitors already sense in production on the price-cron cadence.
``MONITORS_JOB`` is therefore deliberately absent from
``jobs.scheduled_jobs.production_registry()`` -- it is the sense cycle
packaged for a SEPARATE driver (a dedicated GitHub Actions cron on the
monitors' own hourly cadence, or a real ``BackgroundScheduler`` should the
``tower`` extra ever be installed), never a second copy double-running inside
the price endpoint. See ``jobs/scheduled_jobs.py``'s own note.

This module also hosts :func:`run_concierge_alerts` -- the Kern Alerts **Fase
1 concierge** primitive (MONETIZATION_BRIEF §7): run the monitors over a
merchant's uploaded stock snapshot and hand the operator a ready-to-send
:class:`~scm_agent.merchant_alerts.MerchantAlert`. It is manual and
operator-triggered, NOT a scheduled job -- the concierge phase is exactly the
"no app until there are payers" gate the brief draws.

Golden rule 9 ("todo componente continuo degrada a batch") holds: no
background thread, no sleeping, no in-process scheduler. ``run_once()`` (tests,
CI) and an external cron are the only callers.

**Deliberately notification-silent by default.** ``run_all_monitors`` emits its
events onto the shared :class:`~scm_agent.events.EventLedger` (dedup'd), which
is what the Tower / console feed reads -- that alone is the useful product of
scheduling it. Pushing an OPERATOR Slack ping every tick is opt-in
(``notify_operator=True`` / ``LINCHPIN_MONITORS_NOTIFY=1``): registering this
job in production must not start spamming a webhook the moment it lands, and a
MERCHANT-facing send is a different surface entirely
(``scm_agent/merchant_alerts.py``, reviewed-then-sent by a concierge operator,
never auto-pushed from here). This job is T1 sense-only: it reads state and
records events. It never stages a PO, never touches an ERP, never imports
``nosilo_core/writeback.py``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from scm_agent.events import EventLedger
from scm_agent.merchant_alerts import MerchantAlert, render_merchant_alert
from scm_agent.monitors import MonitorConfigError, load_monitor_config, run_all_monitors

from .notify import notify
from .scheduler import ScheduledJob

#: Cadencia por defecto cuando el config no se puede leer. NO es "la cadencia":
#: es el piso al que caer si `config/monitors.yaml` falta o esta roto, para que
#: importar este modulo nunca explote por un archivo ausente.
_CADENCE_FALLBACK_MINUTES = 60


def monitors_cadence_minutes(path: str | Path | None = None) -> int:
    """Un tick combinado de "sentido" a la cadencia MAS AJUSTADA que pida el config.

    Antes esto era `MONITORS_CADENCE_MINUTES = 60`, una constante escrita a mano
    cuyo comentario decia "matches config/monitors.yaml's tightest per-monitor
    cadence". Eran DOS COPIAS del mismo numero sincronizadas por memoria: editar
    `cadence_minutes` en el YAML no movia nada y ningun test fallaba. La
    configuracion parecia viva y no lo estaba -- el docstring de
    `load_monitor_config` lo admitia en voz alta ("data only in this PR, no
    scheduler job reads it yet").

    Ahora el YAML manda. Se toma el minimo sobre los monitores HABILITADOS: los
    lentos (1440 min) son idempotentes bajo la ventana de dedup de 1h del
    EventLedger, asi que correrlos a la cadencia del mas rapido no re-registra
    nada hasta que su condicion cambie de verdad.

    Un config ilegible NO tumba el import: cae a `_CADENCE_FALLBACK_MINUTES`. Un
    config legible con cadencias invalidas tampoco inventa un numero -- las ignora
    y cae al mismo piso.
    """
    try:
        cfg = load_monitor_config(path) if path is not None else load_monitor_config()
    except (MonitorConfigError, OSError):
        return _CADENCE_FALLBACK_MINUTES
    cadencias = [
        int(s["cadence_minutes"])
        for s in cfg.values()
        if s.get("enabled", True) and isinstance(s.get("cadence_minutes"), (int, float))
        and int(s["cadence_minutes"]) > 0
    ]
    return min(cadencias) if cadencias else _CADENCE_FALLBACK_MINUTES


MONITORS_CADENCE_MINUTES = monitors_cadence_minutes()

# Opt-in operator Slack ping (off by default -- see module docstring).
NOTIFY_ENV = "LINCHPIN_MONITORS_NOTIFY"


@dataclass(frozen=True)
class MonitorsCycleReport:
    """Serializable summary of one ``run_all_monitors`` pass -- counts only,
    no raw ``Event`` objects, so it is safe to return straight out of the
    ``POST /api/jobs/run-scheduled`` endpoint (matching
    ``PriceMonitorCycleReport`` / ``PriceWatchCycleReport``'s own JSON-friendly
    shape)."""

    event_count: int = 0
    by_severity: dict[str, int] = field(default_factory=dict)
    by_type: dict[str, int] = field(default_factory=dict)
    notified_operator: bool = False


def _tally(events: list, key) -> dict[str, int]:
    out: dict[str, int] = {}
    for event in events:
        k = key(event)
        out[k] = out.get(k, 0) + 1
    return out


def run_monitors_cycle(
    *,
    ledger: EventLedger | None = None,
    store: object | None = None,
    notify_operator: bool | None = None,
) -> MonitorsCycleReport:
    """One full Control Tower "sense" cycle.

    ``ledger`` defaults to a fresh :class:`EventLedger` (opened at
    ``events.py``'s ``DEFAULT_PATH``) and, when this function opened it itself,
    is closed again in ``finally`` -- matching ``jobs/price_monitor.py``'s own
    owns-it-then-closes-it discipline. A caller-supplied ledger is left open
    (the caller's lifecycle).

    ``store`` is passed straight through to ``run_all_monitors`` (``None`` =
    the process-wide ``src.state.default_store()``, which is what production
    uses); tests inject an isolated ``StateStore`` here so a cycle reads only
    the state they wrote.

    ``notify_operator`` resolves to the ``LINCHPIN_MONITORS_NOTIFY`` env var
    (any non-empty, non-"0" value = on) when left ``None``. When on, a single
    summary line is posted through ``jobs.notify.notify`` -- itself a safe
    no-op when no Slack webhook is configured, so "on" never raises either.
    """
    if notify_operator is None:
        notify_operator = os.environ.get(NOTIFY_ENV, "").strip() not in ("", "0")

    owns_ledger = ledger is None
    ledger = ledger if ledger is not None else EventLedger()
    try:
        events = run_all_monitors(ledger=ledger, store=store)
        by_severity = _tally(events, lambda e: e.severity)
        by_type = _tally(events, lambda e: e.type)

        notified = False
        if notify_operator and events:
            summary = (
                f"[Kern Control Tower] {len(events)} evento(s) nuevo(s): "
                + ", ".join(f"{k}={v}" for k, v in sorted(by_type.items()))
            )
            notified = notify(summary)

        return MonitorsCycleReport(
            event_count=len(events),
            by_severity=by_severity,
            by_type=by_type,
            notified_operator=notified,
        )
    finally:
        if owns_ledger:
            ledger.close()


def run_concierge_alerts(
    *,
    merchant_name: str,
    store: object | None = None,
) -> MerchantAlert:
    """Kern Alerts **Fase 1 concierge** run: sense a merchant's current stock
    and hand back a ready-to-send :class:`~scm_agent.merchant_alerts.MerchantAlert`.

    The operator loads the merchant's uploaded CSV into ``src.state`` (a
    ``stock`` snapshot) and calls this; the returned alert's ``subject`` +
    ``body`` are what the operator reviews and emails (MONETIZATION_BRIEF §7's
    "email semiautomatico"). No app, no scheduler, no transport.

    Deliberately **ledger-free**: ``run_all_monitors`` is called WITHOUT an
    ``EventLedger`` (``ledger=None`` returns every candidate unfiltered), so a
    one-shot concierge run over a freshly uploaded snapshot shows the FULL
    current picture, never a view deduped against some prior run's ledger.
    Only the three Kern Alerts v1 event types survive the render (see
    ``scm_agent/merchant_alerts.py``). Reads state, renders text -- stages
    nothing, writes nothing, sends nothing (autonomy tier T1).
    """
    events = run_all_monitors(store=store)
    return render_merchant_alert(events, merchant_name=merchant_name)


# Registrable with jobs.scheduler.JobRegistry -- same shape as
# jobs.price_monitor.PRICE_MONITOR_JOB / jobs.price_watch.PRICE_WATCH_JOB, a
# third independent ScheduledJob entry. Wired into production via
# jobs/scheduled_jobs.py's production_registry().
MONITORS_JOB = ScheduledJob(
    id="control_tower_monitors",
    func=run_monitors_cycle,
    trigger="interval",
    trigger_args={"minutes": MONITORS_CADENCE_MINUTES},
)
