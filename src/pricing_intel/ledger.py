"""Append-only competitor-price ledger (Linchpin 3.0 PR-10, ``src/pricing_intel``).

Layout under a configurable base path (default ``data/pricing_intel/ledger``,
gitignored -- see ``.gitignore``'s existing ``data/`` entry):

  <base>/index.sqlite3                                         batches + latest index
  <base>/<site>/date=YYYY-MM-DD/<batch_id>-<token>.parquet      one file per append() batch

History is written as parquet when a pandas parquet engine (pyarrow or
fastparquet -- the ``state`` extra) is importable; otherwise it degrades to an
equivalent CSV file, so a bare install never hard-fails just because the
optional engine is missing (the exact ``src/state/store.py`` pattern this PR
was told to reuse -- see "Why not literally wrap StateStore" below).

Two invariants (Linchpin 3.0 plan rule 8, "estado append-only"):

  - **append-only**: ``append()`` only ever writes new files and inserts new
    rows. A row already on disk is never edited or deleted -- a correction is
    a brand-new row with ``is_correction=True``, and the old row stays
    readable via ``history_for_sku``.
  - **latest-per-key index**: a second SQLite table (``latest``) tracks only
    the current-newest row per key, upserted (not appended) on every write --
    this is a derived pointer into the append-only history, not the history
    itself, the same way a git ref points at a commit without rewriting it.

Index design decision (plan S6.3 says "última observación por par
sku<->competidor" -- (site, competitor_sku_ref)): the ``latest`` table's
primary key is literally that pair, matching the plan text verbatim. A
*second*, non-unique index on ``(matched_product_id, site)`` sits on the same
table for PR-13's ``price_position_matrix``, whose access pattern is "for this
client SKU, what's each competitor's latest price" -- an exact-match scan on
``matched_product_id`` returning one row per site. Both access patterns are
served from one physical table (no duplicated storage, nothing to keep in
sync) rather than building two.

``is_correction`` is a caller-supplied flag on ``append()``, not something
inferred from "have we seen this key before". Every fetch cycle of a
continuously-monitored competitor naturally revisits the same
(site, competitor_sku_ref) key -- treating that as a "correction" every time
would make the flag meaningless. A correction is when a caller is explicitly
re-submitting a value that supersedes a previous one for (about) the same
point in time (e.g. a parser bug fixed after the fact), which only the caller
knows.

Why not literally wrap ``StateStore``: ``StateStore.load_payload()`` calls
bare ``pd.read_csv(path)`` with no dtype control, which is exactly the
fallback path this ledger most needs to protect -- pandas would silently
re-infer the canonical Decimal-as-string columns models.py produces as
``float64`` on read, destroying the "byte-identical Decimal precision"
guarantee on the one backend where there's no parquet engine to catch it.
This module keeps the identical file-layout/index-table shape and reuses
``src.state.store``'s live ``_HAS_PARQUET_ENGINE`` flag (so it degrades to CSV
in exactly the same circumstances ``src/state`` does, and the same test-time
monkeypatch flips both), but writes and reads its own parquet/CSV with
explicit ``dtype=str`` control on the CSV path.
"""

from __future__ import annotations

import os
import re
import sqlite3
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from src.state import store as state_store  # reuse the live _HAS_PARQUET_ENGINE flag

from .models import (
    OFFER_COLUMNS,
    CompetitorOffer,
    _to_bool,
    dataframe_to_offers,
    offers_to_dataframe,
    validate_offer_frame,
)

DEFAULT_BASE_PATH = os.environ.get("LINCHPIN_PRICING_LEDGER_PATH", "").strip() or "data/pricing_intel/ledger"

_LEDGER_COLUMN = "is_correction"
_SAFE_CHARS = re.compile(r"[^A-Za-z0-9._-]")


def _sanitize(token: str) -> str:
    """Make ``token`` safe for use as (part of) a filename on any OS."""
    cleaned = _SAFE_CHARS.sub("_", token).strip("_")
    return cleaned or "batch"


class DuplicateBatchError(ValueError):
    """A site already has a stored batch for this exact ``batch_id``."""

    def __init__(self, site: str, batch_id: str) -> None:
        self.site = site
        self.batch_id = batch_id
        super().__init__(f"site '{site}' already has a stored batch for batch_id '{batch_id}'")


@dataclass(frozen=True)
class BatchRecord:
    """One append-only row in the ``batches`` index -- metadata only, no payload."""

    seq: int
    site: str
    batch_id: str
    created_at: str  # ISO 8601 UTC
    file_path: str  # relative to the ledger's base_path, POSIX separators
    file_format: str  # "parquet" | "csv"
    row_count: int


@dataclass(frozen=True, eq=False)
class LedgerRecord:
    """A reconstructed offer plus the ledger-only bookkeeping around it."""

    offer: CompetitorOffer
    is_correction: bool
    batch_seq: int


@dataclass(frozen=True)
class AppendResult:
    """Outcome of one ``PriceLedger.append()`` call. A call spanning offers
    from multiple sites writes one batch file per site (the ledger's
    partition key), so ``batches`` may have more than one entry."""

    batches: tuple[BatchRecord, ...]
    rows_written: int
    rows_became_latest: int  # how many rows advanced their key's latest pointer


def _batch_to_record(row: tuple) -> BatchRecord:
    seq, site, batch_id, created_at, file_path, file_format, row_count = row
    return BatchRecord(seq, site, batch_id, created_at, file_path, file_format, row_count)


class PriceLedger:
    """SQLite-indexed, parquet/CSV-partitioned append-only ledger of
    ``CompetitorOffer`` rows, partitioned ``site/date`` (plan S6.3).

    ``base_path`` is created (with parents) on construction. Tests should pass
    an isolated ``tmp_path``-based directory rather than relying on
    ``DEFAULT_BASE_PATH`` -- see ``default_ledger()``.
    """

    def __init__(self, base_path: str | Path = DEFAULT_BASE_PATH) -> None:
        self._base = Path(base_path)
        self._base.mkdir(parents=True, exist_ok=True)
        self._db_path = self._base / "index.sqlite3"
        self._conn = sqlite3.connect(self._db_path, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS batches ("
            " seq INTEGER PRIMARY KEY AUTOINCREMENT,"
            " site TEXT NOT NULL,"
            " batch_id TEXT NOT NULL,"
            " created_at TEXT NOT NULL,"
            " file_path TEXT NOT NULL,"
            " file_format TEXT NOT NULL,"
            " row_count INTEGER NOT NULL,"
            " UNIQUE(site, batch_id)"
            ")"
        )
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS latest ("
            " site TEXT NOT NULL,"
            " competitor_sku_ref TEXT NOT NULL,"
            " matched_product_id TEXT,"
            " observed_at TEXT NOT NULL,"
            " match_confidence TEXT NOT NULL,"
            " price TEXT NOT NULL,"
            " currency TEXT NOT NULL,"
            " price_normalized TEXT NOT NULL,"
            " shipping TEXT,"
            " availability TEXT NOT NULL,"
            " promo_flag TEXT NOT NULL,"
            " list_price TEXT,"
            " acquisition_tier TEXT NOT NULL,"
            " extractor TEXT NOT NULL,"
            " extractor_version TEXT NOT NULL,"
            " extraction_confidence TEXT NOT NULL,"
            " is_correction TEXT NOT NULL,"
            " batch_seq INTEGER NOT NULL,"
            " PRIMARY KEY (site, competitor_sku_ref)"
            ")"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_latest_product_site ON latest(matched_product_id, site)"
        )
        self._conn.commit()

    @property
    def base_path(self) -> Path:
        return self._base

    # -- write path -----------------------------------------------------------

    def append(
        self,
        offers: Sequence[CompetitorOffer],
        *,
        is_correction: bool = False,
        batch_id: str | None = None,
        now: datetime | None = None,
    ) -> AppendResult:
        """Append ``offers`` to history and update the latest-per-key index.

        Never overwrites an existing file or row. Offers are grouped by
        ``site`` (the partition key) -- a call spanning several sites writes
        one batch file per site, each independently indexed. Raises
        ``DuplicateBatchError`` if a site already has a batch stored under
        ``batch_id`` (auto-generated per site when omitted, so callers only
        need to pass it explicitly when they want a stable, re-runnable id).
        """
        if not offers:
            raise ValueError("offers must be non-empty")
        if now is None:
            now = datetime.now(timezone.utc)

        groups: dict[str, list[CompetitorOffer]] = {}
        for offer in offers:
            groups.setdefault(offer.site, []).append(offer)

        batches: list[BatchRecord] = []
        became_latest = 0
        for site, site_offers in groups.items():
            frame = offers_to_dataframe(site_offers)
            frame[_LEDGER_COLUMN] = "True" if is_correction else "False"
            validate_offer_frame(frame)
            cycle = batch_id or uuid.uuid4().hex
            record = self._write_batch(site, cycle, frame, now)
            batches.append(record)
            for offer in site_offers:
                if self._upsert_latest(offer, is_correction, record.seq):
                    became_latest += 1

        return AppendResult(tuple(batches), rows_written=len(offers), rows_became_latest=became_latest)

    def _write_batch(self, site: str, batch_id: str, frame: pd.DataFrame, now: datetime) -> BatchRecord:
        created_at = now.isoformat()
        site_dir = self._base / _sanitize(site) / f"date={now.strftime('%Y-%m-%d')}"
        site_dir.mkdir(parents=True, exist_ok=True)

        file_format = "parquet" if state_store._HAS_PARQUET_ENGINE else "csv"
        filename = f"{_sanitize(batch_id)}-{uuid.uuid4().hex[:8]}.{file_format}"
        file_path = site_dir / filename
        if file_format == "parquet":
            frame.to_parquet(file_path, index=False)
        else:
            frame.to_csv(file_path, index=False)

        rel_path = file_path.relative_to(self._base).as_posix()
        try:
            cur = self._conn.execute(
                "INSERT INTO batches (site, batch_id, created_at, file_path, file_format, row_count)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (site, batch_id, created_at, rel_path, file_format, len(frame)),
            )
            self._conn.commit()
        except sqlite3.IntegrityError as exc:
            file_path.unlink(missing_ok=True)  # don't leave an unindexed file behind
            raise DuplicateBatchError(site, batch_id) from exc

        return BatchRecord(cur.lastrowid, site, batch_id, created_at, rel_path, file_format, len(frame))

    def _upsert_latest(self, offer: CompetitorOffer, is_correction: bool, batch_seq: int) -> bool:
        """Advance the (site, competitor_sku_ref) latest pointer to ``offer`` if
        it is newer -- or, at an exactly-tied ``observed_at``, if it is an
        explicit correction. Returns whether the pointer moved."""
        row = self._conn.execute(
            "SELECT observed_at FROM latest WHERE site = ? AND competitor_sku_ref = ?",
            (offer.site, offer.competitor_sku_ref),
        ).fetchone()
        if row is not None:
            existing_at = datetime.fromisoformat(row["observed_at"])
            if offer.observed_at < existing_at:
                return False
            if offer.observed_at == existing_at and not is_correction:
                return False

        one_row_frame = offers_to_dataframe([offer]).iloc[0]
        self._conn.execute(
            "INSERT INTO latest (site, competitor_sku_ref, matched_product_id, observed_at,"
            " match_confidence, price, currency, price_normalized, shipping, availability,"
            " promo_flag, list_price, acquisition_tier, extractor, extractor_version,"
            " extraction_confidence, is_correction, batch_seq)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(site, competitor_sku_ref) DO UPDATE SET"
            " matched_product_id=excluded.matched_product_id,"
            " observed_at=excluded.observed_at,"
            " match_confidence=excluded.match_confidence,"
            " price=excluded.price,"
            " currency=excluded.currency,"
            " price_normalized=excluded.price_normalized,"
            " shipping=excluded.shipping,"
            " availability=excluded.availability,"
            " promo_flag=excluded.promo_flag,"
            " list_price=excluded.list_price,"
            " acquisition_tier=excluded.acquisition_tier,"
            " extractor=excluded.extractor,"
            " extractor_version=excluded.extractor_version,"
            " extraction_confidence=excluded.extraction_confidence,"
            " is_correction=excluded.is_correction,"
            " batch_seq=excluded.batch_seq",
            (
                offer.site,
                offer.competitor_sku_ref,
                one_row_frame["matched_product_id"],
                one_row_frame["observed_at"],
                one_row_frame["match_confidence"],
                one_row_frame["price"],
                one_row_frame["currency"],
                one_row_frame["price_normalized"],
                one_row_frame["shipping"],
                one_row_frame["availability"],
                one_row_frame["promo_flag"],
                one_row_frame["list_price"],
                one_row_frame["acquisition_tier"],
                one_row_frame["extractor"],
                one_row_frame["extractor_version"],
                one_row_frame["extraction_confidence"],
                "True" if is_correction else "False",
                batch_seq,
            ),
        )
        self._conn.commit()
        return True

    # -- read path --------------------------------------------------------------

    def latest_by_sku(self, site: str, competitor_sku_ref: str) -> LedgerRecord | None:
        """The current-newest observation for one (site, competitor_sku_ref) pair."""
        row = self._conn.execute(
            "SELECT * FROM latest WHERE site = ? AND competitor_sku_ref = ?",
            (site, competitor_sku_ref),
        ).fetchone()
        return self._row_to_record(row) if row is not None else None

    def latest_for_product(self, matched_product_id: str) -> list[LedgerRecord]:
        """The current-newest observation per site for ``matched_product_id`` --
        the access pattern PR-13's price_position_matrix needs: "for this
        client SKU, what's each competitor's latest price"."""
        rows = self._conn.execute(
            "SELECT * FROM latest WHERE matched_product_id = ? ORDER BY site",
            (matched_product_id,),
        ).fetchall()
        return [self._row_to_record(r) for r in rows]

    def history_for_sku(self, site: str, competitor_sku_ref: str) -> list[LedgerRecord]:
        """Full append-only history for one (site, competitor_sku_ref) pair,
        oldest first -- proves corrections never erase what came before."""
        out: list[LedgerRecord] = []
        for record in self._list_batches(site):
            frame = self._load_batch(record)
            subset = frame[frame["competitor_sku_ref"] == competitor_sku_ref]
            if subset.empty:
                continue
            offers = dataframe_to_offers(subset)
            corrections = [_to_bool(v) for v in subset[_LEDGER_COLUMN].tolist()]
            out.extend(
                LedgerRecord(offer=offer, is_correction=is_corr, batch_seq=record.seq)
                for offer, is_corr in zip(offers, corrections)
            )
        out.sort(key=lambda r: r.offer.observed_at)
        return out

    def _row_to_record(self, row: sqlite3.Row) -> LedgerRecord:
        data = {c: row[c] for c in OFFER_COLUMNS}
        frame = pd.DataFrame([data], columns=list(OFFER_COLUMNS))
        offer = dataframe_to_offers(frame)[0]
        return LedgerRecord(offer=offer, is_correction=_to_bool(row["is_correction"]), batch_seq=row["batch_seq"])

    def _list_batches(self, site: str) -> list[BatchRecord]:
        rows = self._conn.execute(
            "SELECT seq, site, batch_id, created_at, file_path, file_format, row_count"
            " FROM batches WHERE site = ? ORDER BY seq ASC",
            (site,),
        ).fetchall()
        return [_batch_to_record(tuple(r)) for r in rows]

    def _load_batch(self, record: BatchRecord) -> pd.DataFrame:
        path = self._base / record.file_path
        if record.file_format == "parquet":
            return pd.read_parquet(path)
        # dtype=str + keep_default_na=False/na_values=[] is load-bearing: without
        # it pandas re-infers the canonical Decimal-as-string columns as
        # float64 (silently losing precision) and turns "" into NaN. See the
        # module docstring's "Why not literally wrap StateStore".
        return pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[])

    def close(self) -> None:
        self._conn.close()


_default_ledger: PriceLedger | None = None


def default_ledger() -> PriceLedger:
    """The process-wide ledger at ``DEFAULT_BASE_PATH`` (or
    ``LINCHPIN_PRICING_LEDGER_PATH``). Lazily constructed on first use and
    cached. Tests should construct their own ``PriceLedger(tmp_path / "ledger")``
    instead of touching this singleton."""
    global _default_ledger
    if _default_ledger is None:
        _default_ledger = PriceLedger(DEFAULT_BASE_PATH)
    return _default_ledger
