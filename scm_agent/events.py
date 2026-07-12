"""Event bus (Linchpin 3.0 plan S4.2, F0 -- ``scm_agent/events.py``).

The Control Tower's monitors (A1, PR-5) emit :class:`Event` objects instead of
calling a tool directly -- routing from event to tool is a config lookup
(``config/event_routing.yaml``, PR-4), not code. This module is only the
plumbing underneath that: a durable, idempotent record of "this happened".

Idempotency is the whole point: a monitor that re-evaluates the same SKU every
cycle would otherwise re-emit ``stock_below_rop`` every single run for as long
as the condition holds, spamming the Tower and any downstream notification.
:class:`EventLedger` collapses repeats of the same ``dedup_key`` that land
within a configurable time window into a single recorded row -- the second
(and third, and Nth) ``emit()`` call for that key inside the window is a
no-op that returns ``False``.

Real persistence, same pattern as ``src/writeback_store.SqliteAuditLedger``
and ``src/state/store.StateStore``: stdlib ``sqlite3``, an injectable path
(``":memory:"`` or a ``tmp_path`` file in tests, a real file in production),
``timeout=30`` for the same multi-worker-contention reason those two use it.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Env-override convention matching src/state/store.py's LINCHPIN_STATE_PATH /
# webapp/security.py's LINCHPIN_* variables (and scm_agent/autonomy.py's own
# LINCHPIN_AUTONOMY_PATH, added alongside it) -- this module's own DEFAULT_PATH
# previously named the convention in this comment without implementing it.
DEFAULT_PATH = os.environ.get("LINCHPIN_EVENTS_PATH", "").strip() or "data/events.sqlite3"

# A repeat of the same dedup_key inside this many seconds is treated as the
# same occurrence, not a new one -- e.g. an hourly monitor cycle re-detecting
# an unresolved stock_below_rop condition should not re-notify every hour.
# Configurable per-ledger (constructor) and callers may pass a much smaller
# window in tests so nothing has to sleep real time.
DEFAULT_DEDUP_WINDOW_SECONDS = 3600.0


def _ensure_utc(dt: datetime) -> datetime:
    """Treat a naive datetime as already-UTC; convert an aware one to UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class Event:
    """One emitted event.

    Field order here differs slightly from the plan's prose listing
    (id, type, severity, sku, source, payload, dedup_key, ts) because Python
    dataclasses require every field with a default to come after every field
    without one; construct with keyword arguments and the order is moot.

    ``id`` defaults to a fresh UUID per construction -- it identifies this
    particular emission attempt, not the "kind of thing that happened"; that
    is ``dedup_key``'s job. ``sku`` is optional because not every event is
    SKU-scoped (e.g. a titan health event like ``site_degraded``). ``ts``
    defaults to the current UTC time and is what the dedup window is measured
    against, not wall-clock "now" at ``emit()`` time -- so a caller in a test
    (or a backfill/replay) can pass an explicit ``ts`` and get deterministic
    dedup behavior.
    """

    type: str
    severity: str
    source: str
    dedup_key: str
    sku: str | None = None
    payload: dict = field(default_factory=dict)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    ts: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _row_to_event(row: tuple) -> Event:
    id_, type_, severity, sku, source, payload_json, dedup_key, ts = row
    return Event(
        type=type_,
        severity=severity,
        source=source,
        dedup_key=dedup_key,
        sku=sku,
        payload=json.loads(payload_json),
        id=id_,
        ts=datetime.fromisoformat(ts),
    )


class EventLedger:
    """Idempotent SQLite-backed event ledger.

    ``path`` is injectable so tests never touch a real data directory: pass
    ``":memory:"`` for a pure in-process ledger, or a ``tmp_path``-based file
    to also exercise the on-disk path. The default points at the same
    gitignored ``data/`` directory ``src/state`` uses.
    """

    def __init__(
        self,
        path: str | Path = DEFAULT_PATH,
        *,
        dedup_window_seconds: float = DEFAULT_DEDUP_WINDOW_SECONDS,
    ) -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._dedup_window_seconds = dedup_window_seconds
        # timeout=30 matches src/writeback_store.py's SqliteAuditLedger and
        # src/state/store.py's StateStore convention.
        self._conn = sqlite3.connect(self._path, timeout=30.0)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS events ("
            " seq INTEGER PRIMARY KEY AUTOINCREMENT,"
            " id TEXT NOT NULL,"
            " type TEXT NOT NULL,"
            " severity TEXT NOT NULL,"
            " sku TEXT,"
            " source TEXT NOT NULL,"
            " payload_json TEXT NOT NULL,"
            " dedup_key TEXT NOT NULL,"
            " ts TEXT NOT NULL"
            ")"
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_events_dedup_key ON events(dedup_key)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(type)")
        self._conn.commit()

    @property
    def dedup_window_seconds(self) -> float:
        return self._dedup_window_seconds

    def emit(self, event: Event) -> bool:
        """Record ``event`` and return True -- unless the same ``dedup_key`` was
        already recorded within the dedup window, in which case nothing is
        written and this returns False.

        The window is measured against ``event.ts`` (not wall-clock "now"),
        compared to the most recently recorded row for that ``dedup_key``.
        """
        event_ts = _ensure_utc(event.ts)
        last_ts = self._last_ts_for_dedup_key(event.dedup_key)
        if last_ts is not None and abs((event_ts - last_ts).total_seconds()) < self._dedup_window_seconds:
            return False

        self._conn.execute(
            "INSERT INTO events (id, type, severity, sku, source, payload_json, dedup_key, ts)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                event.id,
                event.type,
                event.severity,
                event.sku,
                event.source,
                json.dumps(event.payload),
                event.dedup_key,
                event_ts.isoformat(),
            ),
        )
        self._conn.commit()
        return True

    def _last_ts_for_dedup_key(self, dedup_key: str) -> datetime | None:
        row = self._conn.execute(
            "SELECT ts FROM events WHERE dedup_key = ? ORDER BY seq DESC LIMIT 1",
            (dedup_key,),
        ).fetchone()
        if row is None:
            return None
        return _ensure_utc(datetime.fromisoformat(row[0]))

    def list_by_type(self, event_type: str, *, limit: int | None = None) -> list[Event]:
        """All recorded events of ``event_type``, oldest first (insertion order)."""
        query = (
            "SELECT id, type, severity, sku, source, payload_json, dedup_key, ts"
            " FROM events WHERE type = ? ORDER BY seq ASC"
        )
        params: tuple = (event_type,)
        if limit is not None:
            query += " LIMIT ?"
            params = (event_type, limit)
        rows = self._conn.execute(query, params).fetchall()
        return [_row_to_event(r) for r in rows]

    def list_all(self) -> list[Event]:
        """Every recorded event, oldest first (insertion order)."""
        rows = self._conn.execute(
            "SELECT id, type, severity, sku, source, payload_json, dedup_key, ts FROM events ORDER BY seq ASC"
        ).fetchall()
        return [_row_to_event(r) for r in rows]

    def list_recent(self, *, event_type: str | None = None, limit: int = 200) -> list[Event]:
        """The most recent ``limit`` events (optionally filtered to one
        ``event_type``), oldest-first -- what ``GET /api/events`` (Linchpin
        3.0 PR-7) windows the Tower's "today's events" feed over so the
        response is never an unbounded dump of an ever-growing table.

        Deliberately NOT the same as ``list_by_type(..., limit=...)``: that
        method's ``LIMIT`` applies to an ``ORDER BY seq ASC`` query, so it
        returns the OLDEST rows of that type -- fine for its existing
        callers, wrong for a live "recent activity" feed, which must keep
        shrinking toward the newest rows as the table grows. This method
        queries ``ORDER BY seq DESC LIMIT ?`` (the newest rows) and reverses
        the page in Python before returning, so callers still see the usual
        oldest-first ordering within that most-recent window.
        """
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit}")
        query = "SELECT id, type, severity, sku, source, payload_json, dedup_key, ts FROM events"
        params: tuple = ()
        if event_type is not None:
            query += " WHERE type = ?"
            params = (event_type,)
        query += " ORDER BY seq DESC LIMIT ?"
        params = params + (limit,)
        rows = self._conn.execute(query, params).fetchall()
        return [_row_to_event(r) for r in reversed(rows)]

    def close(self) -> None:
        self._conn.close()
