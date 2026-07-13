"""Daily digest job (Linchpin 3.0 PR-3, F0 -- an example scheduled job).

Composes a plain-text summary of recent :class:`~scm_agent.events.Event`
rows (the PR-2 event bus ledger) and sends it through :func:`jobs.notify.notify`.
Every number in the digest is read from the ledger -- plan rule 14 ("ningun
cap silencioso"): a digest job that hardcoded a placeholder count instead of
querying real events would be exactly the kind of fabricated-data deliverable
the rest of this repo's QA gates exist to prevent. See ``jobs.qa.verify_digest``
for the invariant that catches a message/count mismatch before it ships.

Independently callable (``run_daily_digest()``, no required args -- what
tests and CI use) and scheduler-runnable (``DAILY_DIGEST_JOB``, registered
with a ``jobs.scheduler.JobRegistry`` and given a cron trigger in
production) -- the golden-rule-9 shape every F0 job follows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from scm_agent.events import Event, EventLedger

from .notify import notify
from .scheduler import ScheduledJob

DEFAULT_WINDOW_HOURS = 24.0

# Production trigger: once a day at 08:00 (server local time, matching
# APScheduler's cron trigger default timezone behavior).
DAILY_DIGEST_HOUR = 8
DAILY_DIGEST_MINUTE = 0


@dataclass(frozen=True)
class DigestResult:
    """What one digest run produced. Returned (not just sent) so a caller or
    test can assert on the real counts without re-parsing ``message``."""

    message: str
    event_count: int
    counts_by_type: dict
    notified: bool


def _recent_events(ledger: EventLedger, *, window_hours: float, now: datetime) -> list[Event]:
    cutoff = now - timedelta(hours=window_hours)
    return [e for e in ledger.list_all() if e.ts >= cutoff]


def _counts_by_type(events: list[Event]) -> dict:
    counts: dict = {}
    for e in events:
        counts[e.type] = counts.get(e.type, 0) + 1
    return counts


def build_digest_message(events: list[Event], *, window_hours: float) -> str:
    """Plain-text digest body. ASCII-only (Windows cp1252 console convention --
    no em dashes or curly quotes), so it is always safe to print directly."""
    if not events:
        return f"Kern daily digest: no events in the last {window_hours:g}h."

    counts = _counts_by_type(events)
    lines = [f"Kern daily digest: {len(events)} event(s) in the last {window_hours:g}h."]
    for event_type in sorted(counts):
        lines.append(f"  - {event_type}: {counts[event_type]}")
    return "\n".join(lines)


def run_daily_digest(
    *,
    ledger: EventLedger | None = None,
    window_hours: float = DEFAULT_WINDOW_HOURS,
    now: datetime | None = None,
    webhook_url: str | None = None,
) -> DigestResult:
    """Compose and send the daily digest. Plain, idempotent, no required args.

    ``ledger`` defaults to the process-wide event ledger at its default path;
    tests should pass an isolated ``EventLedger(tmp_path / "events.sqlite3")``
    (or ``EventLedger(":memory:")``) instead, matching
    ``src.state.store.default_store()``'s "tests build their own, production
    uses the default" convention. The ledger this function opened itself is
    closed before returning; a ledger passed in by the caller is left open
    (the caller owns its lifecycle).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    owns_ledger = ledger is None
    ledger = ledger if ledger is not None else EventLedger()
    try:
        events = _recent_events(ledger, window_hours=window_hours, now=now)
        message = build_digest_message(events, window_hours=window_hours)
        notified = notify(message, webhook_url=webhook_url)
        return DigestResult(
            message=message,
            event_count=len(events),
            counts_by_type=_counts_by_type(events),
            notified=notified,
        )
    finally:
        if owns_ledger:
            ledger.close()


# Registrable with jobs.scheduler.JobRegistry: same function, either called
# directly or run under this trigger by a real BackgroundScheduler.
DAILY_DIGEST_JOB = ScheduledJob(
    id="daily_digest",
    func=run_daily_digest,
    trigger="cron",
    trigger_args={"hour": DAILY_DIGEST_HOUR, "minute": DAILY_DIGEST_MINUTE},
)
