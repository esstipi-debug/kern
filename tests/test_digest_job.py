"""Tests for the daily digest job (Linchpin 3.0 PR-3, F0 -- an example
scheduled job over jobs/scheduler.py + jobs/notify.py + scm_agent/events.py).

Guarantees under test:
- build_digest_message() produces a hand-verifiable count breakdown from a
  known list of Events -- no placeholder/fabricated numbers (plan rule 14);
- run_daily_digest() only counts events inside the requested window, reading
  from a real (isolated, tmp_path-backed) EventLedger -- not a mock of the
  ledger's query logic;
- run_daily_digest() is a plain, idempotent, no-required-args callable, both
  directly and through JobRegistry.run_once() (golden rule 9);
- DAILY_DIGEST_JOB is registrable and its func is exactly run_daily_digest;
- jobs.qa.verify_digest()/digest_passed() catch a fabricated (self-
  inconsistent) DigestResult and pass a genuine one.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from jobs import digest_job as digest_job_module
from jobs.digest_job import (
    DAILY_DIGEST_JOB,
    DigestResult,
    build_digest_message,
    run_daily_digest,
)
from jobs.qa import digest_passed, verify_digest
from jobs.scheduler import JobRegistry
from scm_agent.events import Event, EventLedger

_T0 = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)


def _event(event_type: str, *, ts: datetime, dedup_key: str) -> Event:
    return Event(type=event_type, severity="medium", source="monitors", dedup_key=dedup_key, ts=ts)


# -- build_digest_message: hand-verified reference counts -------------------


def test_build_digest_message_with_no_events():
    assert build_digest_message([], window_hours=24.0) == "Kern daily digest: no events in the last 24h."


def test_build_digest_message_reports_the_exact_total_and_per_type_breakdown():
    # 3 stock_below_rop + 2 excess + 1 dead_stock = 6 total, hand-counted.
    events = [
        _event("stock_below_rop", ts=_T0, dedup_key="k1"),
        _event("stock_below_rop", ts=_T0, dedup_key="k2"),
        _event("stock_below_rop", ts=_T0, dedup_key="k3"),
        _event("excess", ts=_T0, dedup_key="k4"),
        _event("excess", ts=_T0, dedup_key="k5"),
        _event("dead_stock", ts=_T0, dedup_key="k6"),
    ]

    message = build_digest_message(events, window_hours=24.0)

    assert message == (
        "Kern daily digest: 6 event(s) in the last 24h.\n"
        "  - dead_stock: 1\n"
        "  - excess: 2\n"
        "  - stock_below_rop: 3"
    )


# -- run_daily_digest: reads real counts from an isolated ledger ------------


def test_run_daily_digest_counts_only_events_inside_the_window(tmp_path, monkeypatch):
    monkeypatch.delenv("LINCHPIN_SLACK_WEBHOOK_URL", raising=False)
    ledger = EventLedger(tmp_path / "events.sqlite3", dedup_window_seconds=0.0)
    ledger.emit(_event("stock_below_rop", ts=_T0, dedup_key="in-window-1"))
    ledger.emit(_event("excess", ts=_T0 - timedelta(hours=1), dedup_key="in-window-2"))
    ledger.emit(_event("dead_stock", ts=_T0 - timedelta(hours=48), dedup_key="outside-window"))

    result = run_daily_digest(ledger=ledger, window_hours=24.0, now=_T0)

    assert result.event_count == 2  # the 48h-old event is excluded
    assert result.counts_by_type == {"stock_below_rop": 1, "excess": 1}
    assert "2 event(s)" in result.message
    ledger.close()


def test_run_daily_digest_with_an_empty_ledger_reports_zero_and_no_events_message(tmp_path, monkeypatch):
    monkeypatch.delenv("LINCHPIN_SLACK_WEBHOOK_URL", raising=False)
    ledger = EventLedger(tmp_path / "events.sqlite3")

    result = run_daily_digest(ledger=ledger, now=_T0)

    assert result.event_count == 0
    assert result.counts_by_type == {}
    assert "no events" in result.message
    ledger.close()


def test_run_daily_digest_no_ops_the_notification_without_a_webhook_configured(tmp_path, monkeypatch):
    """Zero-config safety (plan: same 'unset env var disables the feature'
    convention as LINCHPIN_API_KEY): the digest job must not raise or hang
    when nothing is configured -- exactly the case in CI."""
    monkeypatch.delenv("LINCHPIN_SLACK_WEBHOOK_URL", raising=False)
    ledger = EventLedger(tmp_path / "events.sqlite3")
    ledger.emit(_event("excess", ts=_T0, dedup_key="k1"))

    result = run_daily_digest(ledger=ledger, now=_T0)

    assert result.notified is False
    ledger.close()


def test_run_daily_digest_wires_the_composed_message_into_notify(tmp_path, monkeypatch):
    """Verifies run_daily_digest() actually calls notify() with the message it
    built -- without touching the network (notify itself is tested in full in
    test_notify.py)."""
    captured = {}

    def fake_notify(message, **kwargs):
        captured["message"] = message
        return True

    monkeypatch.setattr(digest_job_module, "notify", fake_notify)
    ledger = EventLedger(tmp_path / "events.sqlite3")
    ledger.emit(_event("stock_below_rop", ts=_T0, dedup_key="k1"))

    result = run_daily_digest(ledger=ledger, now=_T0)

    assert result.notified is True
    assert captured["message"] == result.message
    assert "1 event(s)" in captured["message"]
    ledger.close()


def test_run_daily_digest_closes_a_ledger_it_opened_itself(tmp_path, monkeypatch):
    import sqlite3

    monkeypatch.delenv("LINCHPIN_SLACK_WEBHOOK_URL", raising=False)
    created: list[EventLedger] = []

    def fake_ledger_ctor() -> EventLedger:
        led = EventLedger(tmp_path / "events.sqlite3")
        created.append(led)
        return led

    monkeypatch.setattr(digest_job_module, "EventLedger", fake_ledger_ctor)

    run_daily_digest(now=_T0)  # ledger=None -> opens (and must close) its own

    with pytest.raises(sqlite3.ProgrammingError):
        created[0].list_all()  # querying a closed sqlite3 connection raises


def test_run_daily_digest_does_not_close_a_caller_supplied_ledger(tmp_path, monkeypatch):
    monkeypatch.delenv("LINCHPIN_SLACK_WEBHOOK_URL", raising=False)
    own_ledger = EventLedger(tmp_path / "own.sqlite3")

    run_daily_digest(ledger=own_ledger, now=_T0)

    assert own_ledger.list_all() == []  # still queryable -- connection wasn't closed
    own_ledger.close()


# -- golden rule 9: plain callable, directly and via JobRegistry.run_once ---


def test_run_daily_digest_is_callable_with_no_required_args(tmp_path, monkeypatch):
    monkeypatch.delenv("LINCHPIN_SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(digest_job_module, "EventLedger", lambda: EventLedger(tmp_path / "events.sqlite3"))

    result = run_daily_digest()  # no args at all -- matches ScheduledJob.func's Callable[[], object] contract

    assert isinstance(result, DigestResult)


def test_daily_digest_job_runs_via_job_registry_run_once_with_no_scheduler_or_sleep(tmp_path, monkeypatch):
    monkeypatch.delenv("LINCHPIN_SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(digest_job_module, "EventLedger", lambda: EventLedger(tmp_path / "events.sqlite3"))
    registry = JobRegistry()
    registry.register(DAILY_DIGEST_JOB)

    result = registry.run_once("daily_digest")

    assert isinstance(result["daily_digest"], DigestResult)


def test_daily_digest_job_func_is_exactly_run_daily_digest():
    assert DAILY_DIGEST_JOB.func is run_daily_digest
    assert DAILY_DIGEST_JOB.trigger == "cron"


# -- jobs.qa.verify_digest / digest_passed: no-fabrication QA gate ---------


def test_a_genuine_digest_result_passes_qa():
    result = DigestResult(
        message="Kern daily digest: 2 event(s) in the last 24h.\n  - excess: 2",
        event_count=2,
        counts_by_type={"excess": 2},
        notified=False,
    )
    assert verify_digest(result) == []
    assert digest_passed(result) is True


def test_a_digest_whose_counts_do_not_sum_to_the_total_fails_qa():
    fabricated = DigestResult(
        message="Kern daily digest: 5 event(s) in the last 24h.\n  - excess: 2",
        event_count=5,  # claims 5, but counts_by_type only accounts for 2
        counts_by_type={"excess": 2},
        notified=False,
    )
    issues = verify_digest(fabricated)
    assert issues  # not empty
    assert digest_passed(fabricated) is False


def test_a_digest_with_a_negative_count_fails_qa():
    bad = DigestResult(message="x", event_count=-1, counts_by_type={}, notified=False)
    assert verify_digest(bad)


def test_a_digest_claiming_zero_events_but_saying_something_else_fails_qa():
    bad = DigestResult(message="everything is fine", event_count=0, counts_by_type={}, notified=False)
    assert verify_digest(bad)


def test_an_empty_message_fails_qa():
    bad = DigestResult(message="   ", event_count=0, counts_by_type={}, notified=False)
    assert verify_digest(bad)
