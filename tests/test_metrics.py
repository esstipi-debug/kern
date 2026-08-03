"""Tests for nosilo_core/metrics.py and its wiring into the orchestrator.

The behavior worth guarding is not "a counter increments" -- it is that a run
which produced NO deliverable is still counted, and counted under the status
that says why. A metrics layer that only sees successes is how a QA gate goes
quiet without anyone noticing.
"""

from __future__ import annotations

import sys
import threading
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from nosilo_core import metrics  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_registry():
    metrics.reset()
    yield
    metrics.reset()


def test_snapshot_of_an_untouched_registry_is_empty_not_missing() -> None:
    snap = metrics.snapshot()
    assert snap["total_runs"] == 0
    assert snap["qa_failed"] == 0
    assert snap["tools"] == {}
    assert snap["by_status"] == {}


def test_counts_are_grouped_by_tool_and_status() -> None:
    metrics.record_run("pricing", "ok", 10.0)
    metrics.record_run("pricing", "ok", 20.0)
    metrics.record_run("pricing", "qa_failed", 5.0)
    metrics.record_run("forecast", "needs_data", 1.0)

    snap = metrics.snapshot()
    assert snap["total_runs"] == 4
    assert snap["by_status"] == {"needs_data": 1, "ok": 2, "qa_failed": 1}
    assert snap["tools"]["pricing"]["runs"] == 3
    assert snap["tools"]["pricing"]["by_status"] == {"ok": 2, "qa_failed": 1}
    assert snap["tools"]["forecast"]["runs"] == 1


def test_qa_failed_is_surfaced_as_its_own_total() -> None:
    """The one number that says the gate is still biting."""
    metrics.record_run("inventory_optimization", "qa_failed", 3.0)
    metrics.record_run("pricing", "qa_failed", 4.0)
    metrics.record_run("pricing", "ok", 4.0)
    assert metrics.snapshot()["qa_failed"] == 2


def test_a_run_that_never_reached_a_tool_is_still_counted() -> None:
    metrics.record_run(None, "error", 2.0)
    snap = metrics.snapshot()
    assert snap["total_runs"] == 1
    assert metrics.UNROUTED in snap["tools"]


def test_percentiles_use_nearest_rank_and_report_sample_count() -> None:
    for value in range(1, 101):  # 1..100 ms
        metrics.record_run("forecast", "ok", float(value))
    tool = metrics.snapshot()["tools"]["forecast"]
    assert tool["samples"] == 100
    assert tool["p50_ms"] == 50.0
    assert tool["p95_ms"] == 95.0


def test_duration_window_is_bounded_but_counts_are_not() -> None:
    """Memory stays O(window) while the run count keeps growing -- otherwise a
    long-lived worker would accumulate one float per request forever."""
    over = metrics._WINDOW + 50
    for _ in range(over):
        metrics.record_run("sop", "ok", 1.0)
    tool = metrics.snapshot()["tools"]["sop"]
    assert tool["runs"] == over
    assert tool["samples"] == metrics._WINDOW


def test_record_run_never_raises_on_a_bad_duration() -> None:
    metrics.record_run("pricing", "ok", "not-a-number")  # type: ignore[arg-type]
    # Swallowed: no exception, and nothing half-written that breaks the snapshot.
    metrics.snapshot()


def test_concurrent_records_do_not_lose_counts() -> None:
    def hammer() -> None:
        for _ in range(200):
            metrics.record_run("pricing", "ok", 1.0)

    threads = [threading.Thread(target=hammer) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert metrics.snapshot()["tools"]["pricing"]["runs"] == 8 * 200


def test_snapshot_labels_its_own_scope_so_it_is_not_read_as_a_time_series() -> None:
    metrics.record_run("pricing", "ok", 1.0)
    assert "not aggregated across workers" in metrics.snapshot()["scope"]


# -- Orchestrator wiring -------------------------------------------------------


def test_orchestrator_records_a_refused_run_under_its_status(tmp_path: Path) -> None:
    """The refusal path must be measured. An orchestrator that only counted
    successes would report a perfect record while shipping nothing."""
    from scm_agent import Orchestrator

    bad_csv = tmp_path / "unusable.csv"
    bad_csv.write_text("sku_reference,units_moved\nA-100,42\n", encoding="utf-8")

    result = Orchestrator().run(
        "build a replenishment plan from this file",
        data_path=str(bad_csv),
        job_type="inventory_optimization",
        out_dir=str(tmp_path / "out"),
    )
    assert not result.deliverables

    tool_metrics = metrics.snapshot()["tools"]["inventory_optimization"]
    assert tool_metrics["runs"] == 1
    assert tool_metrics["by_status"] == {result.status: 1}
    assert tool_metrics["p50_ms"] is not None


def test_orchestrator_records_a_successful_run_with_a_duration(tmp_path: Path) -> None:
    from scm_agent import Orchestrator

    result = Orchestrator().run(
        "classify my portfolio by value and demand variability",
        data_path=str(_REPO_ROOT / "data" / "sample_demand_portfolio.csv"),
        job_type="abc_xyz",
        out_dir=str(tmp_path / "out"),
    )
    assert result.status == "ok"

    tool_metrics = metrics.snapshot()["tools"]["abc_xyz"]
    assert tool_metrics["by_status"] == {"ok": 1}
    assert tool_metrics["p95_ms"] >= 0.0


# -- HTTP surface --------------------------------------------------------------


def test_api_metrics_exposes_capability_runs_alongside_lead_telemetry() -> None:
    """GET /api/metrics keeps its existing shape and gains a `capabilities`
    section. Same auth gate as before -- this is operator tooling, and per-tool
    latency plus failure counts is exactly the shape of an operational leak."""
    from fastapi.testclient import TestClient

    from webapp.app import app

    metrics.record_run("pricing", "qa_failed", 12.0)

    payload = TestClient(app).get("/api/metrics").json()
    assert "leads" in payload and "demo_scan" in payload  # unchanged
    capabilities = payload["capabilities"]
    assert capabilities["qa_failed"] == 1
    assert capabilities["tools"]["pricing"]["by_status"] == {"qa_failed": 1}
