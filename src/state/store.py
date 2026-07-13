"""On-disk state store: SQLite index + parquet/CSV history (F0, ``src/state``).

Layout under a configurable base path (default ``data/state``, gitignored --
see ``.gitignore``):

  <base>/index.sqlite3                                  domain -> ordered snapshot index
  <base>/<domain>/date=YYYY-MM-DD/<cycle>-<token>.parquet   one file per snapshot

History is written as parquet when a pandas parquet engine (pyarrow or
fastparquet -- the optional ``state`` extra) is importable; otherwise it
degrades to an equivalent CSV file next to where the parquet would have gone,
so a bare install never hard-fails just because the optional engine is
missing (graceful degradation, matching ``src/sku_dedup.py``'s
rapidfuzz->difflib pattern). The index records which format was actually
written for each row, so a read is never ambiguous about how to load it back.

This module has NO opinion about what a "domain" is or what schema it must
satisfy -- that is ``system_state.py``'s job. It only appends immutable rows
and lists them back out, in insertion order, per domain. Rows are never
updated or deleted: append-only (Linchpin 3.0 plan rule 8).
"""

from __future__ import annotations

import os
import re
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

try:  # optional fast/columnar path
    import pyarrow  # noqa: F401

    _HAS_PARQUET_ENGINE = True
except ImportError:
    try:
        import fastparquet  # noqa: F401

        _HAS_PARQUET_ENGINE = True
    except ImportError:
        _HAS_PARQUET_ENGINE = False

# Configurable base path (Linchpin 3.0 plan S4.1: "Persistencia en Fly ... un
# volumen"). Empty env var falls back to the repo-relative default, matching
# the "LINCHPIN_*" env-override convention already used by webapp/security.py
# and src/writeback.py.
DEFAULT_BASE_PATH = os.environ.get("LINCHPIN_STATE_PATH", "").strip() or "data/state"

_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def _sanitize(token: str) -> str:
    """Make ``token`` safe for use as (part of) a filename on any OS."""
    cleaned = _SAFE_CHARS.sub("_", token).strip("_")
    return cleaned or "cycle"


class DuplicateCycleError(ValueError):
    """A domain already has a stored snapshot for this exact ``cycle_id``.

    Defense-in-depth against a race between two concurrent writers (the
    ``UNIQUE(domain, cycle_id)`` index constraint is what actually catches
    it); the common single-writer case is caught earlier and more clearly by
    ``system_state.CycleOrderError``.
    """

    def __init__(self, domain: str, cycle_id: str) -> None:
        self.domain = domain
        self.cycle_id = cycle_id
        super().__init__(f"domain '{domain}' already has a stored snapshot for cycle_id '{cycle_id}'")


@dataclass(frozen=True)
class StoredRecord:
    """One append-only row in the state index -- metadata only, no payload."""

    seq: int
    domain: str
    cycle_id: str
    created_at: str      # ISO 8601 UTC
    file_path: str        # relative to the store's base_path, POSIX separators
    file_format: str       # "parquet" | "csv"
    row_count: int


def _row_to_record(row: tuple) -> StoredRecord:
    seq, domain, cycle_id, created_at, file_path, file_format, row_count = row
    return StoredRecord(seq, domain, cycle_id, created_at, file_path, file_format, row_count)


class StateStore:
    """SQLite index of append-only snapshot files, partitioned by domain/date.

    ``base_path`` is created (with parents) on construction. Tests should
    pass an isolated ``tmp_path``-based directory rather than relying on
    ``DEFAULT_BASE_PATH`` -- see ``default_store()``.
    """

    def __init__(self, base_path: str | Path = DEFAULT_BASE_PATH) -> None:
        self._base = Path(base_path)
        self._base.mkdir(parents=True, exist_ok=True)
        self._db_path = self._base / "index.sqlite3"
        # timeout=30 matches src/writeback_store.py's SqliteAuditLedger convention.
        self._conn = sqlite3.connect(self._db_path, timeout=30.0)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS snapshots ("
            " seq INTEGER PRIMARY KEY AUTOINCREMENT,"
            " domain TEXT NOT NULL,"
            " cycle_id TEXT NOT NULL,"
            " created_at TEXT NOT NULL,"
            " file_path TEXT NOT NULL,"
            " file_format TEXT NOT NULL,"
            " row_count INTEGER NOT NULL,"
            " UNIQUE(domain, cycle_id)"
            ")"
        )
        self._conn.commit()

    @property
    def base_path(self) -> Path:
        return self._base

    def append_snapshot(
        self, domain: str, cycle_id: str, payload: pd.DataFrame, *, now: datetime | None = None
    ) -> StoredRecord:
        """Write ``payload`` to a brand-new file and index it. Never overwrites an
        existing file or row -- append-only. Raises ``DuplicateCycleError`` if
        ``domain``+``cycle_id`` was already recorded (see class docstring)."""
        if now is None:
            now = datetime.now(timezone.utc)
        created_at = now.isoformat()
        domain_dir = self._base / domain / f"date={now.strftime('%Y-%m-%d')}"
        domain_dir.mkdir(parents=True, exist_ok=True)

        file_format = "parquet" if _HAS_PARQUET_ENGINE else "csv"
        filename = f"{_sanitize(cycle_id)}-{uuid.uuid4().hex[:8]}.{file_format}"
        file_path = domain_dir / filename
        if file_format == "parquet":
            payload.to_parquet(file_path, index=False)
        else:
            payload.to_csv(file_path, index=False)

        rel_path = file_path.relative_to(self._base).as_posix()
        try:
            cur = self._conn.execute(
                "INSERT INTO snapshots (domain, cycle_id, created_at, file_path, file_format, row_count)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (domain, cycle_id, created_at, rel_path, file_format, len(payload)),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            file_path.unlink(missing_ok=True)  # don't leave an unindexed file behind
            raise DuplicateCycleError(domain, cycle_id) from exc

        return StoredRecord(cur.lastrowid, domain, cycle_id, created_at, rel_path, file_format, len(payload))

    def latest_record(self, domain: str) -> StoredRecord | None:
        row = self._conn.execute(
            "SELECT seq, domain, cycle_id, created_at, file_path, file_format, row_count"
            " FROM snapshots WHERE domain = ? ORDER BY seq DESC LIMIT 1",
            (domain,),
        ).fetchone()
        return _row_to_record(row) if row else None

    def latest_cycle_id(self, domain: str) -> str | None:
        record = self.latest_record(domain)
        return record.cycle_id if record else None

    def list_records(self, domain: str) -> list[StoredRecord]:
        """All records for ``domain``, oldest first (insertion order)."""
        rows = self._conn.execute(
            "SELECT seq, domain, cycle_id, created_at, file_path, file_format, row_count"
            " FROM snapshots WHERE domain = ? ORDER BY seq ASC",
            (domain,),
        ).fetchall()
        return [_row_to_record(r) for r in rows]

    def load_payload(self, record: StoredRecord) -> pd.DataFrame:
        path = self._base / record.file_path
        if record.file_format == "parquet":
            return pd.read_parquet(path)
        return pd.read_csv(path)

    def close(self) -> None:
        self._conn.close()


_default_store: StateStore | None = None


def default_store() -> StateStore:
    """The process-wide store at ``DEFAULT_BASE_PATH`` (or ``LINCHPIN_STATE_PATH``).

    Lazily constructed on first use and cached. Tests should construct their
    own ``StateStore(tmp_path / "state")`` and pass it explicitly to
    ``system_state.snapshot``/``latest``/``history`` instead of touching this
    singleton, so they never read or write the real ``data/state`` directory.
    """
    global _default_store
    if _default_store is None:
        _default_store = StateStore(DEFAULT_BASE_PATH)
    return _default_store
