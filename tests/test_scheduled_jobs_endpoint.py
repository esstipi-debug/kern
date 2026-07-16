"""Tests for POST /api/jobs/run-scheduled -- the cron-triggered HTTP entry
point that wires jobs.scheduler.JobRegistry.run_once() into production
(webapp/app.py), feeding both price jobs' emitted market-signal events into
scm_agent.monitors.run_all_monitors so competitor_price_move_monitor
actually receives real data.

Every test in this module replaces production_registry() with a fake
JobRegistry whose jobs are plain Python callables returning canned reports --
the REAL jobs.price_monitor.run_price_monitor_cycle /
jobs.price_watch.run_price_watch_cycle are NEVER invoked here, so no test in
this module ever makes a real network call (matches the isolation convention
tests/test_webapp_watch.py and tests/test_webapp_tower.py already use for
their own ledgers).

Guarantees under test:
- gated behind LINCHPIN_API_KEY, same convention as every other mutating
  endpoint (401 without/with a wrong key; the job registry is never even
  reached in that case -- require_api_key runs before the handler body);
- a real run (both jobs mocked) returns a genuinely useful summary: per-job
  status + pairs_checked/by_status/summary, plus how many
  competitor_price_move events were promoted -- never a bare {"ok": true};
- events emitted by the fake cycles are threaded into run_all_monitors and
  the promoted competitor_price_move event lands on the SAME production
  EventLedger GET /api/events already reads;
- one job raising an exception is reported as that job's own "error" entry
  without hiding the other job's successful result, and the top-level
  status reflects the partial failure (golden rule 14 -- no silent cap);
- a run with no price-signal events at all is a clean, valid "ok" cycle
  (zero promoted, no error) -- matches run_all_monitors' own "empty Tower on
  day one is expected" contract.
"""
from __future__ import annotations

import threading
import time
from datetime import datetime, timezone

import pytest

pytest.importorskip("fastapi")
try:
    import python_multipart  # noqa: F401  (canonical name, python-multipart >= 0.0.26)
except ImportError:
    pytest.importorskip("multipart")  # legacy name; skips the module if also absent
from fastapi.testclient import TestClient  # noqa: E402

import webapp.app as appmod  # noqa: E402
from jobs.price_monitor import PairOutcome, PriceMonitorCycleReport  # noqa: E402
from jobs.price_watch import PriceWatchCycleReport  # noqa: E402
from jobs.scheduler import JobRegistry, ScheduledJob  # noqa: E402
from scm_agent.events import Event  # noqa: E402
from webapp import security  # noqa: E402
from webapp.app import app  # noqa: E402

client = TestClient(app)

NOW = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)


def _price_signal_event(*, event_type: str = "price_move", sku: str = "SKU-X", severity: str = "high") -> Event:
    """Hand-built stand-in for what src.pricing_intel.events.detect_market_signal_events
    actually emits -- same shape as tests/test_monitors_price_move.py's own
    fixture, kept independent so this endpoint test never depends on the
    real acquisition pipeline."""
    return Event(
        type=event_type,
        severity=severity,
        source="pricing_intel.events",
        dedup_key=f"{event_type}:competitor.test:ABC-123",
        sku=sku,
        payload={"site": "competitor.test", "competitor_sku_ref": "ABC-123", "matched_product_id": sku},
        ts=NOW,
    )


def _fake_monitor_report(events: tuple[Event, ...] = ()) -> PriceMonitorCycleReport:
    accepted = tuple(
        PairOutcome(
            site="mercadolibre.com.ar", competitor_sku_ref=f"MLA-{i}", matched_product_id=f"SKU-{i}",
            status="accepted", reason="ok", events=(ev,),
        )
        for i, ev in enumerate(events)
    )
    skipped = (
        PairOutcome(
            site="mercadolibre.com.ar", competitor_sku_ref="MLA-skip", matched_product_id="SKU-skip",
            status="skipped", reason="circuit_open",
        ),
    )
    outcomes = accepted + skipped
    return PriceMonitorCycleReport(now=NOW, pairs_checked=len(outcomes), outcomes=outcomes)


def _fake_watch_report(events: tuple[Event, ...] = ()) -> PriceWatchCycleReport:
    outcomes = tuple(
        PairOutcome(
            site="competitor.test", competitor_sku_ref=f"CMP-{i}", matched_product_id=f"SKU-W{i}",
            status="accepted", reason="ok", events=(ev,),
        )
        for i, ev in enumerate(events)
    )
    return PriceWatchCycleReport(now=NOW, pairs_checked=len(outcomes), outcomes=outcomes)


def _fake_registry(monitor_func, watch_func) -> JobRegistry:
    registry = JobRegistry()
    registry.register(ScheduledJob(id="price_monitor_cycle", func=monitor_func))
    registry.register(ScheduledJob(id="price_watch_cycle", func=watch_func))
    return registry


@pytest.fixture()
def isolated_scheduled_jobs(tmp_path, monkeypatch):
    """Point the endpoint's EventLedger/StateStore at throwaway paths (same
    fixture idiom as test_webapp_watch.py's isolated_watch_stores) and
    disable rate limiting/API-key auth by default -- individual tests opt
    back into the API-key gate where they test it. Does NOT touch
    production_registry() -- each test below monkeypatches that itself, so
    the exact fake job behavior is visible right at the call site.

    Fix round 1 also added module-level throttle/lock state
    (``_SCHEDULED_JOBS_LAST_STARTED_AT`` / ``_SCHEDULED_JOBS_LOCK``) that
    persists across tests in this same process -- reset both to a
    cold-start state on every test so one test's run can never throttle or
    lock out the next (a stale lock left over from a crashed/timed-out
    prior test would otherwise wedge every later test with a 409)."""
    events_path = tmp_path / "events.sqlite3"
    state_path = tmp_path / "state"
    monkeypatch.setattr(appmod, "EVENTS_LEDGER_PATH", str(events_path))
    monkeypatch.setattr(appmod, "STATE_STORE_PATH", str(state_path))
    security.reset_rate_limit()
    monkeypatch.setattr(security, "RATE_LIMIT", 0)
    monkeypatch.setattr(security, "API_KEY", "")
    monkeypatch.setattr(appmod, "_SCHEDULED_JOBS_LAST_STARTED_AT", None)
    monkeypatch.setattr(appmod, "_SCHEDULED_JOBS_LOCK", threading.Lock())
    return events_path, state_path


# -- auth gate -----------------------------------------------------------------


def test_gated_behind_api_key_when_configured(isolated_scheduled_jobs, monkeypatch):
    monkeypatch.setattr(security, "API_KEY", "s3cret")
    monkeypatch.setattr(
        appmod, "production_registry",
        lambda: _fake_registry(lambda: _fake_monitor_report(), lambda: _fake_watch_report()),
    )

    assert client.post("/api/jobs/run-scheduled", headers={"X-API-Key": "nope"}).status_code == 401
    ok = client.post("/api/jobs/run-scheduled", headers={"X-API-Key": "s3cret"})
    assert ok.status_code == 200


def test_missing_api_key_never_runs_any_job(isolated_scheduled_jobs, monkeypatch):
    """The auth dependency must run BEFORE the handler body -- an
    unauthenticated caller who guesses/brute-forces this path triggers no
    network activity or cost."""
    monkeypatch.setattr(security, "API_KEY", "s3cret")
    called = {"count": 0}

    def _boom():
        called["count"] += 1
        raise AssertionError("a scheduled job must never run without a valid API key")

    monkeypatch.setattr(appmod, "production_registry", lambda: _fake_registry(_boom, _boom))

    resp = client.post("/api/jobs/run-scheduled")

    assert resp.status_code == 401
    assert called["count"] == 0


# -- real run, mocked jobs ------------------------------------------------------


def test_real_run_with_mocked_jobs_returns_expected_summary_shape(isolated_scheduled_jobs, monkeypatch):
    signal = _price_signal_event()
    monkeypatch.setattr(
        appmod, "production_registry",
        lambda: _fake_registry(lambda: _fake_monitor_report((signal,)), lambda: _fake_watch_report()),
    )

    resp = client.post("/api/jobs/run-scheduled")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["jobs"]["price_monitor_cycle"]["status"] == "ok"
    assert body["jobs"]["price_monitor_cycle"]["pairs_checked"] == 2
    assert body["jobs"]["price_monitor_cycle"]["by_status"] == {"accepted": 1, "skipped": 1}
    assert "summary" in body["jobs"]["price_monitor_cycle"]
    assert body["jobs"]["price_watch_cycle"]["status"] == "ok"
    assert body["jobs"]["price_watch_cycle"]["pairs_checked"] == 0
    assert body["competitor_price_move_events_promoted"] == 1
    assert body["tower_events_promoted"] >= 1


def test_empty_run_is_a_clean_ok_cycle_with_nothing_promoted(isolated_scheduled_jobs, monkeypatch):
    monkeypatch.setattr(
        appmod, "production_registry",
        lambda: _fake_registry(lambda: _fake_monitor_report(), lambda: _fake_watch_report()),
    )

    resp = client.post("/api/jobs/run-scheduled")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["competitor_price_move_events_promoted"] == 0
    assert body["tower_events_promoted"] == 0


def test_promoted_event_lands_in_the_same_ledger_get_events_reads(isolated_scheduled_jobs, monkeypatch):
    signal = _price_signal_event(sku="SKU-Z")
    monkeypatch.setattr(
        appmod, "production_registry",
        lambda: _fake_registry(lambda: _fake_monitor_report((signal,)), lambda: _fake_watch_report()),
    )

    resp = client.post("/api/jobs/run-scheduled")
    assert resp.status_code == 200

    events_resp = client.get("/api/events", params={"event_type": "competitor_price_move"})
    assert events_resp.status_code == 200
    events_body = events_resp.json()
    assert events_body["count"] == 1
    assert events_body["events"][0]["sku"] == "SKU-Z"
    assert events_body["events"][0]["payload"]["signal_type"] == "price_move"


def test_both_price_watch_and_price_monitor_signals_are_promoted(isolated_scheduled_jobs, monkeypatch):
    monitor_signal = _price_signal_event(sku="SKU-M", event_type="price_move")
    watch_signal = _price_signal_event(sku="SKU-W", event_type="competitor_oos")
    monkeypatch.setattr(
        appmod, "production_registry",
        lambda: _fake_registry(
            lambda: _fake_monitor_report((monitor_signal,)), lambda: _fake_watch_report((watch_signal,)),
        ),
    )

    resp = client.post("/api/jobs/run-scheduled")

    assert resp.status_code == 200
    assert resp.json()["competitor_price_move_events_promoted"] == 2


# -- per-job error isolation -----------------------------------------------------


def test_one_job_failure_is_reported_without_hiding_the_other_jobs_result(isolated_scheduled_jobs, monkeypatch):
    def _boom():
        raise RuntimeError("simulated network failure")

    monkeypatch.setattr(
        appmod, "production_registry",
        lambda: _fake_registry(_boom, lambda: _fake_watch_report()),
    )

    resp = client.post("/api/jobs/run-scheduled")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "error"
    assert body["jobs"]["price_monitor_cycle"]["status"] == "error"
    assert "RuntimeError" in body["jobs"]["price_monitor_cycle"]["error"]
    assert body["jobs"]["price_watch_cycle"]["status"] == "ok"


# -- fix round 1: throttle / concurrency guard / dedicated executor / error text --
#
# Before fix round 1, none of the five behaviors below existed: every request
# below would have re-run both real jobs (throttle test), run two overlapping
# cycles concurrently (lock test), reflected the raw exception string verbatim
# (error-text test), potentially executed on a different shared-pool thread
# each time (dedicated-thread test), or hung the caller indefinitely on a
# stuck job (timeout test). Each test asserts the specific behavior that
# closes its corresponding review finding.


def test_second_call_within_min_interval_is_throttled_without_rerunning_jobs(isolated_scheduled_jobs, monkeypatch):
    """Critical finding: no endpoint-level throttle/minimum-interval existed,
    so a looping caller could re-trigger real outbound crawling on every
    request. A 429 must be returned BEFORE any job runs a second time."""
    monkeypatch.setattr(appmod, "SCHEDULED_JOBS_MIN_INTERVAL_SECONDS", 300)
    call_count = {"monitor": 0, "watch": 0}

    def _monitor():
        call_count["monitor"] += 1
        return _fake_monitor_report()

    def _watch():
        call_count["watch"] += 1
        return _fake_watch_report()

    monkeypatch.setattr(appmod, "production_registry", lambda: _fake_registry(_monitor, _watch))

    first = client.post("/api/jobs/run-scheduled")
    assert first.status_code == 200
    assert call_count == {"monitor": 1, "watch": 1}

    second = client.post("/api/jobs/run-scheduled")
    assert second.status_code == 429
    assert "Retry-After" in second.headers
    # the throttle must reject BEFORE any outbound work happens again
    assert call_count == {"monitor": 1, "watch": 1}


def test_call_arriving_after_the_interval_elapses_is_allowed_again(isolated_scheduled_jobs, monkeypatch):
    """The throttle is a floor, not a permanent lockout -- once
    SCHEDULED_JOBS_MIN_INTERVAL_SECONDS has genuinely elapsed, a new call
    must succeed and run the jobs again."""
    monkeypatch.setattr(appmod, "SCHEDULED_JOBS_MIN_INTERVAL_SECONDS", 1)
    monkeypatch.setattr(
        appmod, "production_registry",
        lambda: _fake_registry(lambda: _fake_monitor_report(), lambda: _fake_watch_report()),
    )

    assert client.post("/api/jobs/run-scheduled").status_code == 200
    assert client.post("/api/jobs/run-scheduled").status_code == 429

    time.sleep(1.05)

    assert client.post("/api/jobs/run-scheduled").status_code == 200


def test_concurrent_call_while_a_run_is_in_flight_is_rejected_without_double_running(
    isolated_scheduled_jobs, monkeypatch,
):
    """Important finding #1/#2: no in-process lock guarded against overlapping
    calls, so two overlapping requests (an overlapping manual retry plus a
    cron tick, a double-firing probe) could run two real cycles at once --
    double outbound fetches, and (in production) a cross-thread sqlite race
    on the default_ledger()/default_sku_map() singletons. A second call that
    arrives while the first is still running must get 409 and never invoke
    the job a second time."""
    monkeypatch.setattr(appmod, "SCHEDULED_JOBS_MIN_INTERVAL_SECONDS", 0)
    started = threading.Event()
    release = threading.Event()
    call_count = {"monitor": 0}

    def _slow_monitor():
        call_count["monitor"] += 1
        started.set()
        assert release.wait(timeout=5), "test setup error: release was never signaled"
        return _fake_monitor_report()

    monkeypatch.setattr(
        appmod, "production_registry",
        lambda: _fake_registry(_slow_monitor, lambda: _fake_watch_report()),
    )

    results: dict[str, object] = {}

    def _first_call() -> None:
        results["first"] = client.post("/api/jobs/run-scheduled")

    first_thread = threading.Thread(target=_first_call)
    first_thread.start()
    assert started.wait(timeout=5), "first call never entered the job"

    second = client.post("/api/jobs/run-scheduled")
    assert second.status_code == 409

    release.set()
    first_thread.join(timeout=5)
    assert not first_thread.is_alive()
    assert results["first"].status_code == 200
    assert call_count["monitor"] == 1


def test_job_error_never_reflects_raw_exception_text(isolated_scheduled_jobs, monkeypatch):
    """Important finding #3: the endpoint used to return
    f"{type(exc).__name__}: {exc}" straight into the 200 body -- a raw
    exception message can carry internal filesystem paths, library
    internals, or URLs. Only the exception's CLASS NAME may appear in the
    response; the full message must never be reflected."""
    secret_detail = "unable to open database file /home/operator/data/price_ledger/index.sqlite3 (leaked-detail)"

    def _boom():
        raise RuntimeError(secret_detail)

    monkeypatch.setattr(
        appmod, "production_registry",
        lambda: _fake_registry(_boom, lambda: _fake_watch_report()),
    )

    resp = client.post("/api/jobs/run-scheduled")

    assert resp.status_code == 200
    body = resp.json()
    assert body["jobs"]["price_monitor_cycle"]["status"] == "error"
    assert body["jobs"]["price_monitor_cycle"]["error"] == "RuntimeError"
    assert secret_detail not in resp.text
    assert "/home/operator" not in resp.text
    assert "leaked-detail" not in resp.text


def test_all_runs_execute_on_the_same_dedicated_worker_thread(isolated_scheduled_jobs, monkeypatch):
    """Important finding #2: the endpoint used to offload onto
    asyncio.to_thread's SHARED default executor pool (also used by
    /api/jobs and /api/demo-scan), so successive calls could land on
    different worker threads -- fatal for the default_ledger()/
    default_sku_map() singletons' non-``check_same_thread=False`` sqlite3
    connections. Two successive real runs must now execute on the exact
    same OS thread, and that thread must not be the event-loop/main thread."""
    monkeypatch.setattr(appmod, "SCHEDULED_JOBS_MIN_INTERVAL_SECONDS", 0)
    thread_ids: list[int] = []

    def _monitor():
        thread_ids.append(threading.get_ident())
        return _fake_monitor_report()

    monkeypatch.setattr(
        appmod, "production_registry",
        lambda: _fake_registry(_monitor, lambda: _fake_watch_report()),
    )

    assert client.post("/api/jobs/run-scheduled").status_code == 200
    assert client.post("/api/jobs/run-scheduled").status_code == 200

    assert len(thread_ids) == 2
    assert thread_ids[0] == thread_ids[1]
    assert thread_ids[0] != threading.get_ident()


def test_run_exceeding_the_timeout_returns_a_bounded_error_instead_of_hanging(isolated_scheduled_jobs, monkeypatch):
    """Important finding #4: no overall timeout bounded the synchronous run,
    so a large sku_map or a slow/stuck job could hang the caller (and the
    external cron's HTTP client) indefinitely. The caller must get a clear
    error well within the configured timeout, even though the underlying
    thread is still finishing in the background."""
    monkeypatch.setattr(appmod, "SCHEDULED_JOBS_MIN_INTERVAL_SECONDS", 0)
    monkeypatch.setattr(appmod, "SCHEDULED_JOBS_TIMEOUT_SECONDS", 0.05)

    def _slow_monitor():
        time.sleep(0.3)
        return _fake_monitor_report()

    monkeypatch.setattr(
        appmod, "production_registry",
        lambda: _fake_registry(_slow_monitor, lambda: _fake_watch_report()),
    )

    started_at = time.monotonic()
    resp = client.post("/api/jobs/run-scheduled")
    elapsed = time.monotonic() - started_at

    assert resp.status_code == 504
    assert elapsed < 0.25, "the caller must not wait for the full 0.3s job -- only the 0.05s timeout"

    # Let the background job actually finish and release its lock before the
    # next test runs (the fixture also hands out a fresh lock, but the
    # background thread itself is real and outlives this test otherwise).
    time.sleep(0.35)
