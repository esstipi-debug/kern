"""Per-client API key store for the read-only MCP server (Phase A go-to-market).

Kern's MCP server sells access to individual paying clients, each needing
their own revocable credential - unlike ``webapp/security.py``'s single shared
``LINCHPIN_API_KEY`` (fine for gating one operator's own dashboard, wrong for
multiple distinct customers). Phase A billing is manual (Stripe Payment Link,
then the operator issues a key by hand - see ``examples/issue_mcp_key.py``), so
this store only needs issue/validate/revoke, not usage-based metering.

Keys are high-entropy random tokens (``secrets.token_urlsafe``); only their
SHA-256 hash is ever persisted, mirroring how GitHub/Stripe-style tokens work -
the plaintext is returned once, at ``issue()`` time, and cannot be recovered
from the store afterward. SQLite (stdlib), same pattern as
``src.writeback_store.SqliteAuditLedger``.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
import time
from pathlib import Path

DEFAULT_PATH = "data/mcp_keys.sqlite3"
KEY_PREFIX = "lpk_"  # legacy "Linchpin key" prefix, kept stable so issued keys keep working - recognizable in logs/support tickets without being guessable


def _hash(key: str) -> str:
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class McpKeyStore:
    """Issue, validate, and revoke per-client API keys for the MCP server."""

    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        # check_same_thread=False + an explicit Lock: this store is a lazy
        # singleton (webapp/app.py::_get_mcp_key_store) read by an ASGI
        # middleware (webapp/mcp_auth.py) whose request-handling thread is not
        # guaranteed to be the one that constructed the store - Starlette's
        # BaseHTTPMiddleware in particular can dispatch through anyio's
        # from-thread portal. SQLite itself tolerates a connection being used
        # from multiple threads as long as access is serialized; the stdlib
        # sqlite3 module's default (check_same_thread=True) is a conservative
        # guard against exactly that lack of serialization, so disabling it is
        # only safe paired with the lock below.
        # timeout=30: same reasoning as SqliteAuditLedger - don't raise "database is
        # locked" under routine multi-worker contention on a shared keys file.
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self._path, timeout=30.0, check_same_thread=False)
        with self._lock:
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS keys ("
                " key_hash TEXT PRIMARY KEY,"
                " client_name TEXT NOT NULL,"
                " issued_at REAL NOT NULL,"
                " active INTEGER NOT NULL DEFAULT 1,"
                " last_used_at REAL"
                ")"
            )
            self._conn.commit()

    def issue(self, client_name: str, *, now: float | None = None) -> str:
        """Mint a new key for ``client_name`` and return it in PLAINTEXT.

        This is the only time the plaintext is ever available - the caller
        (a human operator, per Phase A's manual-issuance model) must hand it to
        the client immediately; it cannot be retrieved from the store again.
        """
        if now is None:
            now = time.time()
        key = KEY_PREFIX + secrets.token_urlsafe(32)
        with self._lock:
            self._conn.execute(
                "INSERT INTO keys (key_hash, client_name, issued_at, active, last_used_at) VALUES (?, ?, ?, 1, NULL)",
                (_hash(key), client_name, now),
            )
            self._conn.commit()
        return key

    def validate(self, presented_key: str, *, now: float | None = None) -> str | None:
        """Return the owning client_name if ``presented_key`` is a live, active key;
        None otherwise (unknown key, revoked key, or empty input). Updates
        ``last_used_at`` on success so an operator can see whether a client is
        actually using what they paid for."""
        if not presented_key:
            return None
        if now is None:
            now = time.time()
        key_hash = _hash(presented_key)
        with self._lock:
            row = self._conn.execute(
                "SELECT client_name, active FROM keys WHERE key_hash = ?", (key_hash,)
            ).fetchone()
            if row is None or not row[1]:
                return None
            self._conn.execute("UPDATE keys SET last_used_at = ? WHERE key_hash = ?", (now, key_hash))
            self._conn.commit()
            return row[0]

    def revoke(self, presented_key: str) -> bool:
        """Deactivate ``presented_key``. Returns whether an active key was found and
        revoked. Does not delete the row - list_keys() keeps showing it (inactive),
        preserving the audit trail of what was ever issued."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE keys SET active = 0 WHERE key_hash = ? AND active = 1", (_hash(presented_key),)
            )
            self._conn.commit()
            return cur.rowcount > 0

    def revoke_client(self, client_name: str) -> int:
        """Deactivate every active key belonging to ``client_name``. Returns how many."""
        with self._lock:
            cur = self._conn.execute(
                "UPDATE keys SET active = 0 WHERE client_name = ? AND active = 1", (client_name,)
            )
            self._conn.commit()
            return cur.rowcount

    def list_keys(self) -> list[dict]:
        """Operator-facing listing. Never includes the plaintext key or its hash."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT client_name, issued_at, active, last_used_at FROM keys ORDER BY issued_at"
            ).fetchall()
        return [
            {"client_name": r[0], "issued_at": r[1], "active": bool(r[2]), "last_used_at": r[3]} for r in rows
        ]

    def close(self) -> None:
        with self._lock:
            self._conn.close()
