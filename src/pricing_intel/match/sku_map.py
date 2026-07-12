"""Versioned, append-only product-match store (Linchpin 3.0 PR-14, plan S6.5
point 5 -- "Revision humana T2: sku_map versionada con estados confirmed/
suspect/rejected + quien/que confirmo").

Golden rule 8 (state append-only) applies to match decisions exactly as it
does to price history: a re-review that changes a verdict does NOT overwrite
the old row -- :meth:`SkuMap.record` always INSERTs a new row with a higher
``version`` for the same ``(our_product_id, competitor_sku_ref, site)`` key.
:meth:`SkuMap.latest` returns the highest-version row; :meth:`SkuMap.history`
returns every version, oldest first, so a disputed match's full audit trail
(who/what said what, and when) is never lost.

``confirmed_by`` is a free-form string, not an enum -- the task brief names
three legitimate shapes it takes: a human's identifier/email (a manual T2
review), :data:`AUTO_CONFIRMED_BY` ("auto" -- the algorithmic paths:
``gtin.py``'s exact match, or ``probabilistic.py`` clearing
``CONFIRM_THRESHOLD``), or :data:`LLM_CONFIRMED_BY` ("llm" -- an operator
explicitly accepting an :mod:`adjudicate` proposal as sufficient basis to
confirm, a deliberate act THEY perform by calling ``record()`` with that
value, never something ``adjudicate.py`` writes on its own -- see that
module's docstring for why "propone, nunca confirma solo" is enforced at
the type level, not just here). :meth:`record` only enforces that a
``status="confirmed"`` candidate carries SOME ``confirmed_by`` -- never that
it is exactly one of these three -- an operator's own identifier system
(email, SSO subject, ticket id) is legitimate too.

Deliberately a lightweight SQLite-only sibling to ``ledger.py`` rather than
a literal reuse of its parquet-partitioned machinery: sku_map rows are one
per (product, competitor-sku) pair -- thousands, not the price ledger's
millions-of-timestamped-readings scale -- so a single indexed SQLite table
is both simpler and sufficient. See ``ledger.py``'s own module docstring for
why THAT module needed parquet partitioning (history-at-scale) in the first
place, a need sku_map does not share.
"""

from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..models import MatchCandidate

DEFAULT_BASE_PATH = os.environ.get("LINCHPIN_SKU_MAP_PATH", "").strip() or "data/pricing_intel/sku_map"

AUTO_CONFIRMED_BY = "auto"
LLM_CONFIRMED_BY = "llm"


@dataclass(frozen=True)
class SkuMapEntry:
    """One immutable, versioned row -- a :class:`~src.pricing_intel.models.MatchCandidate`
    plus sku_map's own bookkeeping (``version``, ``recorded_at``)."""

    version: int
    our_product_id: str
    competitor_sku_ref: str
    site: str
    method: str
    score: float
    status: str
    reason: str
    confirmed_by: str | None
    confirmed_at: datetime | None
    recorded_at: datetime

    def to_match_candidate(self) -> MatchCandidate:
        """Reconstruct the :class:`MatchCandidate` this entry recorded."""
        return MatchCandidate(
            our_product_id=self.our_product_id,
            competitor_sku_ref=self.competitor_sku_ref,
            site=self.site,
            method=self.method,
            score=self.score,
            status=self.status,
            reason=self.reason,
            confirmed_by=self.confirmed_by,
            confirmed_at=self.confirmed_at,
        )


def _iso_or_none(value: datetime | None) -> str | None:
    return value.astimezone(timezone.utc).isoformat() if value is not None else None


def _parse_iso_or_none(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class SkuMap:
    """SQLite-backed, versioned, append-only store of match decisions.

    ``base_path`` is created (with parents) on construction. Tests should
    pass an isolated ``tmp_path``-based directory rather than relying on
    ``DEFAULT_BASE_PATH`` -- see :func:`default_sku_map`.
    """

    def __init__(self, base_path: str | Path = DEFAULT_BASE_PATH) -> None:
        self._base = Path(base_path)
        self._base.mkdir(parents=True, exist_ok=True)
        self._db_path = self._base / "sku_map.sqlite3"
        self._conn = sqlite3.connect(self._db_path, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS sku_map ("
            " seq INTEGER PRIMARY KEY AUTOINCREMENT,"
            " our_product_id TEXT NOT NULL,"
            " competitor_sku_ref TEXT NOT NULL,"
            " site TEXT NOT NULL,"
            " version INTEGER NOT NULL,"
            " method TEXT NOT NULL,"
            " score REAL NOT NULL,"
            " status TEXT NOT NULL,"
            " reason TEXT NOT NULL,"
            " confirmed_by TEXT,"
            " confirmed_at TEXT,"
            " recorded_at TEXT NOT NULL,"
            " UNIQUE(our_product_id, competitor_sku_ref, site, version)"
            ")"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_sku_map_key ON sku_map(our_product_id, competitor_sku_ref, site)"
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_sku_map_product ON sku_map(our_product_id)")
        self._conn.commit()

    @property
    def base_path(self) -> Path:
        return self._base

    # -- write path -----------------------------------------------------------

    def record(self, candidate: MatchCandidate, *, now: datetime | None = None) -> SkuMapEntry:
        """Append a new, immutable version for
        ``(candidate.our_product_id, candidate.competitor_sku_ref, candidate.site)``.

        NEVER updates an existing row -- a re-review producing a different
        verdict is a brand-new row with ``version = previous_version + 1``
        (golden rule 8); the first record for a key gets ``version=1``.
        Raises ``ValueError`` if ``candidate.status == "confirmed"`` but
        ``candidate.confirmed_by`` is empty -- a confirmed entry with no
        record of who/what confirmed it is exactly the silent gap this
        store exists to prevent (see module docstring).
        """
        if candidate.status == "confirmed" and not candidate.confirmed_by:
            raise ValueError(
                "a 'confirmed' MatchCandidate must set confirmed_by (who/what confirmed it) -- "
                "got confirmed_by=None/''"
            )
        if now is None:
            now = datetime.now(timezone.utc)

        row = self._conn.execute(
            "SELECT MAX(version) AS v FROM sku_map WHERE our_product_id = ? AND competitor_sku_ref = ? AND site = ?",
            (candidate.our_product_id, candidate.competitor_sku_ref, candidate.site),
        ).fetchone()
        next_version = (row["v"] or 0) + 1

        self._conn.execute(
            "INSERT INTO sku_map (our_product_id, competitor_sku_ref, site, version, method, score, status,"
            " reason, confirmed_by, confirmed_at, recorded_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (
                candidate.our_product_id,
                candidate.competitor_sku_ref,
                candidate.site,
                next_version,
                candidate.method,
                candidate.score,
                candidate.status,
                candidate.reason,
                candidate.confirmed_by,
                _iso_or_none(candidate.confirmed_at),
                now.isoformat(),
            ),
        )
        self._conn.commit()
        return SkuMapEntry(
            version=next_version,
            our_product_id=candidate.our_product_id,
            competitor_sku_ref=candidate.competitor_sku_ref,
            site=candidate.site,
            method=candidate.method,
            score=candidate.score,
            status=candidate.status,
            reason=candidate.reason,
            confirmed_by=candidate.confirmed_by,
            confirmed_at=candidate.confirmed_at,
            recorded_at=now,
        )

    # -- read path --------------------------------------------------------------

    def _row_to_entry(self, row: sqlite3.Row) -> SkuMapEntry:
        return SkuMapEntry(
            version=row["version"],
            our_product_id=row["our_product_id"],
            competitor_sku_ref=row["competitor_sku_ref"],
            site=row["site"],
            method=row["method"],
            score=row["score"],
            status=row["status"],
            reason=row["reason"],
            confirmed_by=row["confirmed_by"],
            confirmed_at=_parse_iso_or_none(row["confirmed_at"]),
            recorded_at=datetime.fromisoformat(row["recorded_at"]),
        )

    def latest(self, our_product_id: str, competitor_sku_ref: str, site: str) -> SkuMapEntry | None:
        """The highest-version entry for one (product, competitor-sku,
        site) key, or ``None`` if it has never been recorded."""
        row = self._conn.execute(
            "SELECT * FROM sku_map WHERE our_product_id = ? AND competitor_sku_ref = ? AND site = ?"
            " ORDER BY version DESC LIMIT 1",
            (our_product_id, competitor_sku_ref, site),
        ).fetchone()
        return self._row_to_entry(row) if row is not None else None

    def history(self, our_product_id: str, competitor_sku_ref: str, site: str) -> list[SkuMapEntry]:
        """Every version for one key, oldest first -- the full audit trail
        (golden rule 8: nothing here is ever overwritten or deleted)."""
        rows = self._conn.execute(
            "SELECT * FROM sku_map WHERE our_product_id = ? AND competitor_sku_ref = ? AND site = ?"
            " ORDER BY version ASC",
            (our_product_id, competitor_sku_ref, site),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def latest_confirmed_for_product(self, our_product_id: str) -> list[SkuMapEntry]:
        """The current-latest, ``status == "confirmed"`` entry per
        (competitor_sku_ref, site) key for ``our_product_id`` -- the QA
        invariant plan S6.5 states verbatim: "solo confirmed (o >=0.9)
        alimenta P2/A5". One SQL pass: group by key, keep the max-version
        row per key, then filter to confirmed -- equivalent to calling
        :meth:`latest` per key but without the N+1 round trips.
        """
        rows = self._conn.execute(
            "SELECT s.* FROM sku_map s"
            " INNER JOIN ("
            "   SELECT competitor_sku_ref, site, MAX(version) AS max_version"
            "   FROM sku_map WHERE our_product_id = ? GROUP BY competitor_sku_ref, site"
            " ) latest_keys"
            " ON s.competitor_sku_ref = latest_keys.competitor_sku_ref"
            " AND s.site = latest_keys.site AND s.version = latest_keys.max_version"
            " WHERE s.our_product_id = ? AND s.status = 'confirmed'"
            " ORDER BY s.site",
            (our_product_id, our_product_id),
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def list_all_confirmed(self) -> list[SkuMapEntry]:
        """The current-latest, ``status == "confirmed"`` entry per
        ``(our_product_id, competitor_sku_ref, site)`` key across the WHOLE
        store (Linchpin 3.0 PR-15) -- the read path continuous monitoring
        (``jobs/price_monitor.py``) needs to enumerate every confirmed pair
        to re-acquire, generalizing :meth:`latest_confirmed_for_product`
        (which scopes to one ``our_product_id``) to every product at once.
        Same one-SQL-pass shape (group by key, keep the max-version row per
        key, then filter to confirmed) as that method -- see its own
        docstring for why this is one pass instead of N+1 round trips.
        """
        rows = self._conn.execute(
            "SELECT s.* FROM sku_map s"
            " INNER JOIN ("
            "   SELECT our_product_id, competitor_sku_ref, site, MAX(version) AS max_version"
            "   FROM sku_map GROUP BY our_product_id, competitor_sku_ref, site"
            " ) latest_keys"
            " ON s.our_product_id = latest_keys.our_product_id"
            " AND s.competitor_sku_ref = latest_keys.competitor_sku_ref"
            " AND s.site = latest_keys.site AND s.version = latest_keys.max_version"
            " WHERE s.status = 'confirmed'"
            " ORDER BY s.our_product_id, s.site",
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def latest_confirmed_for_competitor_ref(self, competitor_sku_ref: str, site: str) -> SkuMapEntry | None:
        """The current-latest, ``status == "confirmed"`` entry for one
        competitor reference, regardless of which ``our_product_id`` it is
        matched to (Linchpin 3.0 PR-15) -- the REVERSE lookup
        ``jobs/price_monitor.py``'s L2 webhook receiver needs ("which of OUR
        skus does this incoming competitor URL belong to?", the mirror image
        of :meth:`latest_confirmed_for_product`'s "which competitor refs
        match THIS our_product_id?"). ``None`` when no CONFIRMED match
        exists for this ref at all -- an honestly-unmatched observation, the
        caller's own concern (e.g. a ``new_competitor_listing`` candidate),
        not an error here.
        """
        row = self._conn.execute(
            "SELECT s.* FROM sku_map s"
            " INNER JOIN ("
            "   SELECT our_product_id, MAX(version) AS max_version FROM sku_map"
            "   WHERE competitor_sku_ref = ? AND site = ? GROUP BY our_product_id"
            " ) latest_keys"
            " ON s.our_product_id = latest_keys.our_product_id AND s.version = latest_keys.max_version"
            " WHERE s.competitor_sku_ref = ? AND s.site = ? AND s.status = 'confirmed'"
            " ORDER BY s.recorded_at DESC LIMIT 1",
            (competitor_sku_ref, site, competitor_sku_ref, site),
        ).fetchone()
        return self._row_to_entry(row) if row is not None else None

    def close(self) -> None:
        self._conn.close()


_default_sku_map: SkuMap | None = None


def default_sku_map() -> SkuMap:
    """The process-wide store at ``DEFAULT_BASE_PATH`` (or
    ``LINCHPIN_SKU_MAP_PATH``). Lazily constructed on first use and cached.
    Tests should construct their own ``SkuMap(tmp_path / "sku_map")``
    instead of touching this singleton."""
    global _default_sku_map
    if _default_sku_map is None:
        _default_sku_map = SkuMap(DEFAULT_BASE_PATH)
    return _default_sku_map
