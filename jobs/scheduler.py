"""Job scheduling (Linchpin 3.0 PR-3, F0 -- ``jobs/scheduler.py``).

Golden rule 9 ("todo componente continuo degrada a batch"): every registered
job is a plain, idempotent, no-required-args Python function. It must be
callable two ways:

  1. directly, or via :meth:`JobRegistry.run_once` -- synchronous, one-shot,
     no background thread, no sleeping. This is what tests and CI use, and
     what makes the whole module demo-able without a deploy.
  2. through a real :class:`~apscheduler.schedulers.background.BackgroundScheduler`
     (:meth:`JobRegistry.build_scheduler`) with cron/interval triggers,
     persisted to a SQLite-backed ``SQLAlchemyJobStore`` -- for production,
     running in-process inside the FastAPI app's lifespan (plan S4.3: "cero
     maquinas extra").

APScheduler (the ``tower`` optional-dependency extra) is imported lazily and
guarded, matching ``src/state/store.py``'s pyarrow/fastparquet fallback and
``src/state/system_state.py``'s pandera fallback: a bare install (no
``tower`` extra) can still import this module and use ``run_once`` --
``build_scheduler`` is the only entry point that requires the extra, and it
raises a clear error instead of an ``ImportError`` deep in a call stack.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from apscheduler.schedulers.background import BackgroundScheduler

try:  # optional: the 'tower' extra
    from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
    from apscheduler.schedulers.background import BackgroundScheduler as _BackgroundScheduler

    _HAS_APSCHEDULER = True
except ImportError:
    SQLAlchemyJobStore = None
    _BackgroundScheduler = None
    _HAS_APSCHEDULER = False

# Same env-override + "data/" base-dir convention as src/state/store.py's
# LINCHPIN_STATE_PATH and scm_agent/events.py's DEFAULT_PATH.
DEFAULT_JOBSTORE_PATH = os.environ.get("LINCHPIN_SCHEDULER_PATH", "").strip() or "data/scheduler.sqlite3"


class SchedulerUnavailableError(RuntimeError):
    """Raised by :meth:`JobRegistry.build_scheduler` when APScheduler (the
    ``tower`` extra) is not installed. ``run_once`` never raises this -- the
    synchronous batch path has no APScheduler dependency at all."""

    def __init__(self) -> None:
        super().__init__(
            "APScheduler is not installed - install the 'tower' extra "
            "(pip install -e '.[tower]') to build a real background scheduler; "
            "JobRegistry.run_once() works without it"
        )


@dataclass(frozen=True)
class ScheduledJob:
    """One registrable job: a plain, idempotent, no-required-args callable
    plus the trigger it should run under in production.

    ``func`` takes no required positional/keyword args -- it must be directly
    callable as ``func()``, whether invoked by ``run_once`` or by APScheduler.
    ``trigger`` is an APScheduler trigger alias (``"interval"`` or
    ``"cron"``); ``trigger_args`` are its keyword args (e.g.
    ``{"minutes": 30}`` or ``{"hour": 8, "minute": 0}``) -- see APScheduler's
    ``add_job(..., trigger=trigger, **trigger_args)``.
    """

    id: str
    func: Callable[[], object]
    trigger: str = "interval"
    trigger_args: dict = field(default_factory=dict)


class JobRegistry:
    """A named collection of :class:`ScheduledJob` definitions.

    Instance-based (matching ``scm_agent.registry.ToolRegistry`` and
    ``src.state.store.StateStore``), so tests construct their own isolated
    registry instead of mutating shared module state.
    """

    def __init__(self) -> None:
        self._jobs: dict[str, ScheduledJob] = {}

    def register(self, job: ScheduledJob, *, replace: bool = False) -> None:
        """Register ``job`` by id. Raises ``ValueError`` on a duplicate id
        unless ``replace=True`` (re-registering the same id is otherwise a
        mistake, not an idempotent no-op -- matching
        ``ToolRegistry.register``'s "tool already registered" guard)."""
        if not replace and job.id in self._jobs:
            raise ValueError(f"job already registered: {job.id}")
        self._jobs[job.id] = job

    def get(self, job_id: str) -> ScheduledJob:
        return self._jobs[job_id]

    def list(self) -> list[ScheduledJob]:
        return list(self._jobs.values())

    def run_once(self, job_id: str | None = None) -> dict[str, object]:
        """Run one job (``job_id``) or every registered job, synchronously,
        right now -- no scheduler loop, no background thread, no sleeping.

        This is the golden-rule-9 batch-degradation entry point: CI and tests
        call this instead of starting a real ``BackgroundScheduler`` and
        polling for it to fire. Returns ``{job_id: return_value}`` for
        whichever job(s) ran; a job that raises propagates the exception
        (the caller decides whether to catch it per-job).
        """
        jobs = [self.get(job_id)] if job_id is not None else self.list()
        return {job.id: job.func() for job in jobs}

    def build_scheduler(self, *, jobstore_path: str | Path = DEFAULT_JOBSTORE_PATH) -> "BackgroundScheduler":
        """Construct (but do not start) a ``BackgroundScheduler`` with every
        currently-registered job added, backed by a SQLite ``SQLAlchemyJobStore``
        at ``jobstore_path`` (parent directories created if needed).

        Raises ``SchedulerUnavailableError`` if APScheduler is not installed.
        The caller is responsible for ``.start()``/``.shutdown()`` -- this
        function never starts the loop, so importing/calling it in a test
        without ``.start()`` cannot spawn a background thread.
        """
        if not _HAS_APSCHEDULER:
            raise SchedulerUnavailableError()
        path = Path(jobstore_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        jobstores = {"default": SQLAlchemyJobStore(url=f"sqlite:///{path}")}
        scheduler = _BackgroundScheduler(jobstores=jobstores)
        for job in self._jobs.values():
            scheduler.add_job(job.func, trigger=job.trigger, id=job.id, replace_existing=True, **job.trigger_args)
        return scheduler


_default_registry: JobRegistry | None = None


def default_registry() -> JobRegistry:
    """The process-wide registry, lazily constructed and cached (matching
    ``src.state.store.default_store()``). Tests should construct their own
    ``JobRegistry()`` instead of touching this singleton, so registrations in
    one test never leak into another."""
    global _default_registry
    if _default_registry is None:
        _default_registry = JobRegistry()
    return _default_registry
