"""Persistent audit/idempotency ledger for the writeback plane (SQLite, stdlib).

Real connectors (Odoo, etc.) already persist *records* in their own system of
record. What they do NOT persist on their own is the writeback plane's own
audit/idempotency bookkeeping - today a plain process-memory dict
(``applied_keys()`` / ``commit()`` / ``rollback()``) that is lost on restart,
letting the same ``idempotency_key`` re-apply after a crash or redeploy, and
destroying the data needed to roll a change back.

``SqliteAuditLedger`` is a drop-in replacement for that in-memory dict, shared
by ``InMemoryStore`` and any real connector's system-of-record wrapper (see
``src.connectors.odoo._ReorderRuleStore`` / ``_DraftPoStore``). It is optional
and additive: nothing that already worked without a ledger changes behavior.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path

from .writeback import ABSENT, AuditEntry

DEFAULT_PATH = "data/writeback_ledger.sqlite3"

# How long a claim() may sit unrecorded before a fresh claim() is allowed to steal
# it. Bounds how long a hard crash (SIGKILL/OOM-kill/power loss - anything that
# skips apply()'s except/release()) can strand an idempotency_key: without this, an
# orphaned claims row would block every future retry of that key forever. Set well
# above any realistic single commit() duration (OdooClient's own socket timeout is
# 30s per RPC call, and a multi-supplier draft-PO commit() makes several) so a claim
# is never stolen out from under a caller that is still genuinely working.
CLAIM_STALE_SECONDS = 300.0

_ABSENT_MARKER = {"__writeback_absent__": True}


def _to_json_safe(value: object) -> object:
    return _ABSENT_MARKER if value is ABSENT else value


def _from_json_safe(value: object) -> object:
    return ABSENT if isinstance(value, dict) and value.get("__writeback_absent__") else value


class SqliteAuditLedger:
    """Persists applied ``AuditEntry`` rows keyed by ``idempotency_key``.

    Pass ``:memory:`` for a ledger that behaves like the old in-memory dict but
    still exercises the same code path (useful in tests). Pass a real file path
    (the default) for a ledger that survives a process restart.
    """

    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        # timeout=30 (Python's sqlite3 default is 5s): matches OdooClient's own
        # per-RPC socket timeout, so a writer queued behind another in-flight
        # claim()/record()/release() waits at least as long as a single real Odoo
        # call could legitimately take before giving up, instead of raising
        # "database is locked" under routine multi-worker contention.
        self._conn = sqlite3.connect(self._path, timeout=30.0)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS applied ("
            " idempotency_key TEXT PRIMARY KEY,"
            " target TEXT NOT NULL,"
            " approved_by TEXT NOT NULL,"
            " restore_json TEXT NOT NULL,"
            " applied_at REAL NOT NULL"
            ")"
        )
        # In-flight (claimed but not yet recorded) keys. Kept in its own table -
        # never queried by applied_keys()/get() - so a claim in progress is invisible
        # to every existing reader, exactly as an unfinished commit() was before
        # claim() existed (e.g. rollback() on a key that hasn't finished applying
        # still cleanly raises KeyError instead of seeing a half-written row).
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS claims (idempotency_key TEXT PRIMARY KEY, claimed_at REAL NOT NULL)"
        )
        self._conn.commit()

    def applied_keys(self) -> set[str]:
        rows = self._conn.execute("SELECT idempotency_key FROM applied").fetchall()
        return {r[0] for r in rows}

    def claim(self, idempotency_key: str, *, now: float | None = None) -> bool:
        """Atomically reserve ``idempotency_key`` using the PRIMARY KEY constraints on
        ``claims``/``applied`` as the concurrency primitive: two connections racing to
        insert the same key can only have one INSERT succeed - SQLite serializes
        writers at the file level, so this is safe across separate worker PROCESSES
        sharing this file (the scenario a multi-worker webapp deployment actually
        has - one connection per process/request). It is NOT safe to share a single
        ``SqliteAuditLedger``/connection across multiple THREADS: Python's ``sqlite3``
        defaults to ``check_same_thread=True``, so a non-owning thread already raises
        ``ProgrammingError`` on any call, independent of this method. (Cross-thread
        safety for a single in-process store is provided separately, by
        ``AuditBookkeeping``'s own lock, for the non-ledger path.)

        The first statement atomically checks both "already fully applied" and
        "already claimed by another in-flight apply()", so there is no separate
        check-then-insert gap of its own. If a claim already exists, it is only
        stolen when it is older than ``CLAIM_STALE_SECONDS`` - i.e. almost certainly
        orphaned by a process that claimed and then crashed before ``record()`` or
        ``release()`` ever ran - via a second, equally atomic conditional UPDATE
        (only one racing stealer's ``WHERE claimed_at < ?`` can still match after the
        first stealer's UPDATE refreshes ``claimed_at``).

        ``now`` defaults to the real wall clock; pass an explicit value only for
        deterministic tests. Returns True if the caller now owns the key (must
        follow up with ``record()`` or ``release()``); False if it is already
        (freshly) claimed or already applied.
        """
        if now is None:
            now = time.time()
        try:
            cur = self._conn.execute(
                "INSERT INTO claims (idempotency_key, claimed_at)"
                " SELECT ?, ? WHERE NOT EXISTS (SELECT 1 FROM applied WHERE idempotency_key = ?)",
                (idempotency_key, now, idempotency_key),
            )
            self._conn.commit()
            return cur.rowcount == 1  # 0 rows inserted -> idempotency_key was already applied
        except sqlite3.IntegrityError:
            pass  # a claims row already exists for this key - fall through to a staleness check

        stale_before = now - CLAIM_STALE_SECONDS
        cur = self._conn.execute(
            "UPDATE claims SET claimed_at = ? WHERE idempotency_key = ? AND claimed_at < ?",
            (now, idempotency_key, stale_before),
        )
        self._conn.commit()
        return cur.rowcount == 1

    def release(self, idempotency_key: str) -> None:
        """Release a claim that will NOT be followed by ``record()`` (the
        side-effecting write raised or was aborted) - lets a legitimate retry proceed
        instead of leaving the key permanently claimed. Only call this once the write
        it guarded has definitively failed; calling it speculatively while that write
        may still be genuinely in flight would let a second claimant through early."""
        self._conn.execute("DELETE FROM claims WHERE idempotency_key = ?", (idempotency_key,))
        self._conn.commit()

    def record(self, entry: AuditEntry, *, applied_at: float | None = None) -> None:
        """Persist ``entry``. ``applied_at`` defaults to the real clock."""
        if applied_at is None:
            applied_at = time.time()
        safe_restore = [[eid, fld, _to_json_safe(val)] for eid, fld, val in entry.restore]
        self._conn.execute(
            "INSERT OR REPLACE INTO applied"
            " (idempotency_key, target, approved_by, restore_json, applied_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (entry.idempotency_key, entry.target, entry.approved_by, json.dumps(safe_restore), applied_at),
        )
        self._conn.execute("DELETE FROM claims WHERE idempotency_key = ?", (entry.idempotency_key,))
        self._conn.commit()

    def get(self, idempotency_key: str) -> AuditEntry | None:
        row = self._conn.execute(
            "SELECT idempotency_key, target, approved_by, restore_json FROM applied WHERE idempotency_key = ?",
            (idempotency_key,),
        ).fetchone()
        if row is None:
            return None
        key, target, approved_by, restore_json = row
        restore = tuple((eid, fld, _from_json_safe(val)) for eid, fld, val in json.loads(restore_json))
        return AuditEntry(key, target, approved_by, restore)

    def forget(self, idempotency_key: str) -> None:
        self._conn.execute("DELETE FROM applied WHERE idempotency_key = ?", (idempotency_key,))
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
