"""In-process run metrics per capability: how often each tool runs, how it ends,
how long it takes.

The gap this fills: ``leads.jsonl`` records who asked for a demo, and the access
log records that ``POST /api/jobs`` returned 200 -- neither says *which
capability ran* or *whether QA let it ship*. "QA fails => no deliverable" is the
product's central claim and nothing counted how often it fired, which means a
gate that silently stopped biting would look exactly like a gate that never
needed to.

Deliberately small, and deliberately not a metrics library:

* **In-process and volatile.** Counters reset when the process restarts, and a
  multi-worker deploy reports per worker (Fly runs ``WEB_CONCURRENCY``). That is
  honest for "what has this worker been doing lately", and wrong for billing or
  SLOs -- do not grow it into either. A real time series belongs in Prometheus or
  Sentry, not here.
* **Bounded.** Only the last :data:`_WINDOW` durations per tool are kept, so
  memory is O(tools x window) no matter how long the process lives.
* **Never raises.** Observability must not be able to break the thing it
  observes -- :func:`record_run` swallows everything.
"""

from __future__ import annotations

import math
import threading
from collections import deque

# Durations kept per tool for the percentile view. 256 samples is enough for a
# stable p95 and small enough that 50 tools cost well under a megabyte.
_WINDOW = 256

# Bucket for a request that never reached a tool (ambiguous brief, hard error).
UNROUTED = "_unrouted"

_LOCK = threading.Lock()
_RUNS: dict[str, dict[str, int]] = {}  # tool -> status -> count
_DURATIONS: dict[str, deque[float]] = {}  # tool -> recent durations (ms)


def record_run(tool: str | None, status: str, duration_ms: float) -> None:
    """Record one orchestrator run. Never raises."""
    try:
        key = tool or UNROUTED
        with _LOCK:
            by_status = _RUNS.setdefault(key, {})
            by_status[status] = by_status.get(status, 0) + 1
            _DURATIONS.setdefault(key, deque(maxlen=_WINDOW)).append(float(duration_ms))
    except Exception:  # pragma: no cover - defensive; a counter must never break a run
        pass


def _percentile(sorted_values: list[float], fraction: float) -> float:
    """Nearest-rank percentile: the smallest value at or above `fraction` of the
    sample. Requires a non-empty, ascending list.

    ``ceil``, not ``round``: with 100 samples of 1..100ms, round-half-even puts
    p95 at 96 -- off by one, and wrong in the direction that flatters latency.
    """
    rank = max(1, min(len(sorted_values), math.ceil(fraction * len(sorted_values))))
    return sorted_values[rank - 1]


def snapshot() -> dict:
    """Point-in-time view: per-tool counts by status, plus latency percentiles.

    ``qa_failed`` is surfaced as its own top-level total because it is the one
    number that says whether the QA gate is still doing anything.
    """
    with _LOCK:
        runs = {tool: dict(by_status) for tool, by_status in _RUNS.items()}
        durations = {tool: sorted(values) for tool, values in _DURATIONS.items()}

    tools: dict[str, dict] = {}
    for tool, by_status in sorted(runs.items()):
        samples = durations.get(tool, [])
        tools[tool] = {
            "runs": sum(by_status.values()),
            "by_status": dict(sorted(by_status.items())),
            "p50_ms": round(_percentile(samples, 0.50), 1) if samples else None,
            "p95_ms": round(_percentile(samples, 0.95), 1) if samples else None,
            "samples": len(samples),
        }

    totals: dict[str, int] = {}
    for by_status in runs.values():
        for status, count in by_status.items():
            totals[status] = totals.get(status, 0) + count

    total_runs = sum(totals.values())
    return {
        "total_runs": total_runs,
        "by_status": dict(sorted(totals.items())),
        "qa_failed": totals.get("qa_failed", 0),
        "tools": tools,
        # Explicit, so a reader of the JSON cannot mistake this for a durable
        # time series aggregated across workers.
        "scope": "in-process since boot; not aggregated across workers",
    }


def reset() -> None:
    """Drop every counter. For tests, and for an operator wanting a clean window."""
    with _LOCK:
        _RUNS.clear()
        _DURATIONS.clear()
