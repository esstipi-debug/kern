"""Tests for the job scheduler registry (Linchpin 3.0 PR-3, F0 -- jobs/scheduler.py).

Guarantees under test (plan rule 9, "todo componente continuo degrada a
batch"):
- a registered job is callable synchronously via JobRegistry.run_once(), by
  id or all-at-once, with no background thread and no sleeping -- this is
  the entry point tests/CI use, never a real scheduler loop;
- registering a duplicate job id is rejected (unless replace=True), matching
  ToolRegistry.register's "already registered" guard;
- build_scheduler() raises SchedulerUnavailableError when APScheduler (the
  'tower' extra) is not installed -- the 'tower' extra IS installed in this
  test environment (requirements-dev.txt), so absence is simulated via
  monkeypatching _HAS_APSCHEDULER rather than relying on ambient state;
- build_scheduler() wires every registered job onto the underlying
  BackgroundScheduler with the right trigger/trigger_args, verified against
  fake APScheduler classes (so the wiring logic is tested without requiring
  the real optional dependency);
- default_registry() is a lazily constructed, cached singleton.
"""

from __future__ import annotations

import pytest

from jobs import scheduler as scheduler_module
from jobs.scheduler import (
    DEFAULT_JOBSTORE_PATH,
    JobRegistry,
    ScheduledJob,
    SchedulerUnavailableError,
    default_registry,
)

# -- registration --------------------------------------------------------


def test_register_and_get_a_job():
    registry = JobRegistry()
    job = ScheduledJob(id="ping", func=lambda: "pong")

    registry.register(job)

    assert registry.get("ping") is job


def test_registering_a_duplicate_id_raises_value_error():
    registry = JobRegistry()
    registry.register(ScheduledJob(id="ping", func=lambda: "pong"))

    with pytest.raises(ValueError):
        registry.register(ScheduledJob(id="ping", func=lambda: "pong 2"))


def test_registering_a_duplicate_id_with_replace_true_overwrites():
    registry = JobRegistry()
    registry.register(ScheduledJob(id="ping", func=lambda: "v1"))
    registry.register(ScheduledJob(id="ping", func=lambda: "v2"), replace=True)

    assert registry.run_once("ping") == {"ping": "v2"}


def test_list_returns_all_registered_jobs():
    registry = JobRegistry()
    registry.register(ScheduledJob(id="a", func=lambda: 1))
    registry.register(ScheduledJob(id="b", func=lambda: 2))

    ids = {job.id for job in registry.list()}
    assert ids == {"a", "b"}


def test_get_unknown_job_id_raises_key_error():
    registry = JobRegistry()
    with pytest.raises(KeyError):
        registry.get("does-not-exist")


# -- run_once: the golden-rule-9 synchronous batch path -------------------


def test_run_once_runs_a_single_job_by_id_and_returns_its_result():
    calls = []
    registry = JobRegistry()
    registry.register(ScheduledJob(id="counter", func=lambda: calls.append(1) or len(calls)))

    result = registry.run_once("counter")

    assert result == {"counter": 1}
    assert len(calls) == 1  # ran exactly once, synchronously


def test_run_once_runs_all_registered_jobs_when_no_id_given():
    registry = JobRegistry()
    registry.register(ScheduledJob(id="a", func=lambda: "ran-a"))
    registry.register(ScheduledJob(id="b", func=lambda: "ran-b"))

    result = registry.run_once()

    assert result == {"a": "ran-a", "b": "ran-b"}


def test_run_once_unknown_job_id_raises_key_error():
    registry = JobRegistry()
    with pytest.raises(KeyError):
        registry.run_once("does-not-exist")


def test_run_once_propagates_a_job_exception_instead_of_swallowing_it():
    def boom():
        raise RuntimeError("job failed")

    registry = JobRegistry()
    registry.register(ScheduledJob(id="boom", func=boom))

    with pytest.raises(RuntimeError, match="job failed"):
        registry.run_once("boom")


def test_run_once_with_no_jobs_registered_returns_an_empty_dict():
    registry = JobRegistry()
    assert registry.run_once() == {}


# -- build_scheduler: APScheduler absent (simulated -- see note below) -----


def test_build_scheduler_raises_when_apscheduler_not_installed(monkeypatch):
    """The 'tower' extra IS installed in this repo's test environment (it's
    in requirements-dev.txt, needed by the wiring tests below), so this
    simulates absence via the same _HAS_APSCHEDULER flag those wiring tests
    flip the other way (_install_fake_apscheduler) rather than relying on
    the real environment to lack the package -- that assumption broke once
    CI's requirements-dev.txt was brought in sync with pyproject.toml's
    extras (2026-07-13), and asserting on ambient install state is fragile
    regardless. The guard logic itself (build_scheduler reading the flag)
    is unchanged and still genuinely exercised."""
    monkeypatch.setattr(scheduler_module, "_HAS_APSCHEDULER", False)
    registry = JobRegistry()

    with pytest.raises(SchedulerUnavailableError):
        registry.build_scheduler()


# -- build_scheduler wiring, against fake APScheduler classes --------------


class _FakeJobStore:
    def __init__(self, url: str) -> None:
        self.url = url


class _FakeBackgroundScheduler:
    def __init__(self, jobstores: dict) -> None:
        self.jobstores = jobstores
        self.added_jobs: list[tuple] = []

    def add_job(self, func, trigger, id, replace_existing, **trigger_args):
        self.added_jobs.append((func, trigger, id, replace_existing, trigger_args))


def _install_fake_apscheduler(monkeypatch):
    monkeypatch.setattr(scheduler_module, "_HAS_APSCHEDULER", True)
    monkeypatch.setattr(scheduler_module, "SQLAlchemyJobStore", _FakeJobStore)
    monkeypatch.setattr(scheduler_module, "_BackgroundScheduler", _FakeBackgroundScheduler)


def test_build_scheduler_wires_every_registered_job(monkeypatch, tmp_path):
    _install_fake_apscheduler(monkeypatch)
    registry = JobRegistry()
    registry.register(ScheduledJob(id="daily", func=lambda: None, trigger="cron", trigger_args={"hour": 8}))
    registry.register(ScheduledJob(id="hourly", func=lambda: None, trigger="interval", trigger_args={"hours": 1}))

    built = registry.build_scheduler(jobstore_path=tmp_path / "sched.sqlite3")

    assert isinstance(built, _FakeBackgroundScheduler)
    added_by_id = {call[2]: call for call in built.added_jobs}
    assert set(added_by_id) == {"daily", "hourly"}
    _, trigger, _, replace_existing, trigger_args = added_by_id["daily"]
    assert trigger == "cron"
    assert replace_existing is True
    assert trigger_args == {"hour": 8}


def test_build_scheduler_uses_a_sqlite_jobstore_at_the_given_path(monkeypatch, tmp_path):
    _install_fake_apscheduler(monkeypatch)
    registry = JobRegistry()
    path = tmp_path / "nested" / "sched.sqlite3"

    built = registry.build_scheduler(jobstore_path=path)

    assert built.jobstores["default"].url == f"sqlite:///{path}"
    assert path.parent.is_dir()  # parent directories created, matching StateStore's convention


def test_default_jobstore_path_falls_back_to_a_repo_relative_data_path():
    # Same convention as src/state/store.py's LINCHPIN_STATE_PATH: an unset
    # env var falls back to a repo-relative default under data/.
    assert DEFAULT_JOBSTORE_PATH.replace("\\", "/") == "data/scheduler.sqlite3"


# -- default_registry(): lazily constructed singleton ----------------------


def test_default_registry_is_a_lazily_constructed_singleton(monkeypatch):
    monkeypatch.setattr(scheduler_module, "_default_registry", None)

    got = default_registry()
    again = default_registry()

    assert got is again
