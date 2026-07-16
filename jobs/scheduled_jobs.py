"""Production job registry -- wires PRICE_MONITOR_JOB and PRICE_WATCH_JOB
into a real, populated ``jobs.scheduler.JobRegistry`` for the cron-triggered
``POST /api/jobs/run-scheduled`` endpoint (``webapp/app.py``).

Why this module exists, not a change to ``jobs/scheduler.py`` itself:
``jobs.scheduler.default_registry()`` ships intentionally EMPTY (F0, PR-3) --
``scheduler.py`` is the generic scheduling primitive and must not import any
specific job module. Both ``jobs/price_monitor.py`` and ``jobs/price_watch.py``
already import FROM ``jobs/scheduler.py`` (``from .scheduler import
ScheduledJob``); having ``scheduler.py`` import back from either of them to
populate ``default_registry()`` would be circular. This module sits above
both, on the other side of that dependency, and is the ONE place that
actually registers the two production jobs -- both ``webapp/app.py``'s
endpoint and any future caller (e.g. a real ``BackgroundScheduler``, should
the ``tower`` extra ever be installed in production) should import
:func:`production_registry` from here rather than re-registering the jobs a
second time somewhere else.

Golden rule 9 ("todo componente continuo degrada a batch") still applies in
full: as of this module, ``JobRegistry.run_once()`` (driven by an EXTERNAL
cron -- a GitHub Actions scheduled workflow, added separately) is the ONLY
thing that ever calls these jobs in this codebase. No ``BackgroundScheduler``
is started anywhere by this module or its caller -- seeing one wired in here
would defeat the entire point (a real in-process background thread doing
periodic network scraping was explicitly rejected for this deploy: 512MB VM,
``WEB_CONCURRENCY=1``, already OOM-killed once at 2 workers). ``JobRegistry.
build_scheduler()`` -- the only entry point in ``jobs/scheduler.py`` that
touches APScheduler at all -- is never called from here.
"""

from __future__ import annotations

import threading

from jobs.price_monitor import PRICE_MONITOR_JOB
from jobs.price_watch import PRICE_WATCH_JOB

from .scheduler import JobRegistry

# Single source of truth for which job ids POST /api/jobs/run-scheduled
# (webapp/app.py) drives -- never a second, hand-copied list of id string
# literals that could silently drift from the ScheduledJob objects above.
PRODUCTION_JOB_IDS: tuple[str, ...] = (PRICE_MONITOR_JOB.id, PRICE_WATCH_JOB.id)

_production_registry: JobRegistry | None = None
# Fix round 1: guards the lazy-singleton init below against a race under
# concurrent first-calls (e.g. two threads both observing `None` and each
# building their own JobRegistry). Benign in practice today -- webapp/app.py's
# own concurrency lock (_SCHEDULED_JOBS_LOCK) already serializes every real
# call into this module to one at a time -- but this module has no way to
# guarantee every future caller does the same, so it defends itself too.
_production_registry_lock = threading.Lock()


def production_registry() -> JobRegistry:
    """The process-wide registry of jobs that actually run in production --
    today: ``price_monitor_cycle`` (L0 MercadoLibre poll,
    ``jobs.price_monitor.run_price_monitor_cycle``) and ``price_watch_cycle``
    (L1 discovery-assisted watch, ``jobs.price_watch.run_price_watch_cycle``).

    Lazily constructed and cached, matching
    ``jobs.scheduler.default_registry()``'s own singleton convention --
    double-checked locking (``_production_registry_lock``) makes the lazy
    init itself safe under concurrent first-calls. Tests should construct
    their own ``JobRegistry()`` (or monkeypatch this function on whichever
    module imported it, e.g. ``webapp.app``) instead of touching this
    singleton, so registrations in one test never leak into another -- same
    discipline ``default_registry()`` itself documents.
    """
    global _production_registry
    if _production_registry is None:
        with _production_registry_lock:
            if _production_registry is None:  # re-check: another thread may have won the race
                registry = JobRegistry()
                registry.register(PRICE_MONITOR_JOB)
                registry.register(PRICE_WATCH_JOB)
                _production_registry = registry
    return _production_registry
