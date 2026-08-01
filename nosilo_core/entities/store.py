"""Append-only bitemporal entity store (SQLite, stdlib).

The persistence half of the canonical model: it turns a stream of "system X
reported these attributes at time T" observations into entities with stable
identity, per-field provenance and a history you can query along both time axes.

**Genuinely append-only.** There is no ``UPDATE`` on ``entity_version`` - not
even to stamp a closing timestamp, which is where most bitemporal schemas quietly
start mutating history. Because every version is a *full* snapshot and versions
are ordered, "the version in effect at (valid_at, known_at)" is just the highest
version whose ``valid_from`` and ``recorded_at`` are both at or before those
instants. No closing stamps to write, no closing stamps to get wrong.
``valid_to``/``superseded_at`` still appear on the objects handed back, derived
from the neighbouring version at read time.

**An observation that changes nothing writes nothing.** A monitor re-reading an
unchanged catalogue every hour would otherwise append a version per cycle until
the history is unreadable. Same reasoning as ``scm_agent.events.EventLedger``'s
dedup window, enforced here by :attr:`~.resolution.Resolution.changed`.

**One source record maps to exactly one entity**, enforced by the primary key on
``entity_xref``. That invariant is what makes ``resolve_source`` a lookup instead
of a search, and it means a mis-link is a visible constraint violation rather
than a silently duplicated entity.

Follows the storage conventions already set by
``nosilo_core.writeback_store.SqliteAuditLedger`` and ``src.state.store``:
stdlib ``sqlite3``, an injectable path (``":memory:"`` in tests, a real file in
production), ``timeout=30`` for multi-worker contention, and an env override.
"""

from __future__ import annotations

import json
import os
import sqlite3
import uuid
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping

from .model import (
    LINK_EXACT_KEY,
    LINK_MINTED,
    EntityType,
    EntityTypeRegistry,
    EntityVersion,
    FieldProvenance,
    FieldValue,
    SourceRef,
    Xref,
    new_entity_id,
    utc_now,
)
from .resolution import FieldConflict, resolve

DEFAULT_PATH = os.environ.get("LINCHPIN_ENTITIES_PATH", "").strip() or "data/entities.sqlite3"

#: JSON cannot carry Decimal, and going through float would defeat the entire
#: reason money is Decimal in the first place. Tagged round-trip instead.
_DECIMAL_TAG = "__decimal__"


class EntityStoreError(RuntimeError):
    """Raised when an observation cannot be stored as asked."""


def _encode(value: Any) -> Any:
    if isinstance(value, Decimal):
        return {_DECIMAL_TAG: str(value)}
    if isinstance(value, (list, tuple)):
        return [_encode(v) for v in value]
    if isinstance(value, dict):
        return {k: _encode(v) for k, v in value.items()}
    return value


def _decode(value: Any) -> Any:
    if isinstance(value, dict):
        if _DECIMAL_TAG in value and len(value) == 1:
            return Decimal(value[_DECIMAL_TAG])
        return {k: _decode(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_decode(v) for v in value]
    return value


def _fields_to_json(fields: Mapping[str, FieldValue]) -> str:
    return json.dumps(
        {
            name: {
                "value": _encode(fv.value),
                "source": [fv.provenance.source.system, fv.provenance.source.entity, fv.provenance.source.id],
                "recorded_at": fv.provenance.recorded_at,
                "trust": fv.provenance.trust,
            }
            for name, fv in sorted(fields.items())  # sorted: byte-stable rows
        },
        sort_keys=True,
    )


def _fields_from_json(blob: str) -> dict[str, FieldValue]:
    return {
        name: FieldValue(
            value=_decode(payload["value"]),
            provenance=FieldProvenance(
                source=SourceRef(*payload["source"]),
                recorded_at=payload["recorded_at"],
                trust=payload["trust"],
            ),
        )
        for name, payload in json.loads(blob).items()
    }


@dataclass(frozen=True)
class ObserveResult:
    """What one :meth:`SqliteEntityStore.observe` call did.

    ``version is None`` means the observation was a no-op: the entity was
    resolved (or created) but nothing it claimed changed anything, so no version
    was appended. Callers that log "updated N entities" should count on
    ``version is not None``, not on the call succeeding.
    """

    nosilo_id: str
    entity_type: str
    version: EntityVersion | None
    conflicts: tuple[FieldConflict, ...]
    created: bool
    link_method: str

    @property
    def changed(self) -> bool:
        return self.version is not None


class SqliteEntityStore:
    """Canonical entities, their versions, and their links to source systems."""

    def __init__(self, registry: EntityTypeRegistry, path: str | Path = DEFAULT_PATH) -> None:
        self._registry = registry
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        # timeout=30 matches nosilo_core/writeback_store.py's SqliteAuditLedger.
        self._conn = sqlite3.connect(self._path, timeout=30.0)
        self._conn.row_factory = sqlite3.Row
        self._ensure_schema()

    # ---- schema -------------------------------------------------------------

    def _ensure_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS entity (
                nosilo_id   TEXT PRIMARY KEY,
                entity_type TEXT NOT NULL,
                created_at  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS entity_by_type ON entity(entity_type);

            CREATE TABLE IF NOT EXISTS entity_version (
                version_id    TEXT PRIMARY KEY,
                nosilo_id     TEXT NOT NULL,
                entity_type   TEXT NOT NULL,
                version       INTEGER NOT NULL,
                fields_json   TEXT NOT NULL,
                valid_from    TEXT NOT NULL,
                recorded_at   TEXT NOT NULL,
                change_reason TEXT NOT NULL DEFAULT '',
                UNIQUE (nosilo_id, version)
            );
            CREATE INDEX IF NOT EXISTS version_by_entity ON entity_version(nosilo_id, version);

            -- The PK is the invariant: one source record resolves to one entity.
            CREATE TABLE IF NOT EXISTS entity_xref (
                source_system TEXT NOT NULL,
                source_entity TEXT NOT NULL,
                source_id     TEXT NOT NULL,
                nosilo_id     TEXT NOT NULL,
                confidence    REAL NOT NULL,
                method        TEXT NOT NULL,
                recorded_at   TEXT NOT NULL,
                PRIMARY KEY (source_system, source_entity, source_id)
            );
            CREATE INDEX IF NOT EXISTS xref_by_entity ON entity_xref(nosilo_id);

            -- A derived projection of the CURRENT natural-key values, not history:
            -- it is rewritten when a key value changes. entity_version remains the
            -- only record of what was true when.
            CREATE TABLE IF NOT EXISTS entity_natural_key (
                entity_type TEXT NOT NULL,
                key_name    TEXT NOT NULL,
                key_value   TEXT NOT NULL,
                nosilo_id   TEXT NOT NULL,
                PRIMARY KEY (entity_type, key_name, key_value)
            );
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # ---- identity -----------------------------------------------------------

    def resolve_source(self, source: SourceRef) -> str | None:
        """The entity a source record is linked to, or ``None`` if never seen."""
        row = self._conn.execute(
            "SELECT nosilo_id FROM entity_xref WHERE source_system=? AND source_entity=? AND source_id=?",
            (source.system, source.entity, source.id),
        ).fetchone()
        return row["nosilo_id"] if row else None

    def find_by_natural_key(self, entity_type: str, key_name: str, key_value: object) -> str | None:
        row = self._conn.execute(
            "SELECT nosilo_id FROM entity_natural_key WHERE entity_type=? AND key_name=? AND key_value=?",
            (entity_type, key_name, str(key_value)),
        ).fetchone()
        return row["nosilo_id"] if row else None

    def link(
        self,
        nosilo_id: str,
        source: SourceRef,
        *,
        confidence: float,
        method: str,
        recorded_at: str | None = None,
    ) -> Xref:
        """Link a source record to an entity.

        Re-linking the same source record to a *different* entity is refused: it
        is the signature of a bad match, and silently repointing it would orphan
        every fact already attributed to the first entity.
        """
        stamped = recorded_at or utc_now()
        existing = self.resolve_source(source)
        if existing is not None and existing != nosilo_id:
            raise EntityStoreError(
                f"{source} is already linked to {existing}; refusing to repoint it to {nosilo_id}. "
                "Unlink deliberately if this is a correction - silently repointing would strand "
                "every observation already attributed to the first entity."
            )
        self._conn.execute(
            "INSERT OR REPLACE INTO entity_xref"
            " (source_system, source_entity, source_id, nosilo_id, confidence, method, recorded_at)"
            " VALUES (?,?,?,?,?,?,?)",
            (source.system, source.entity, source.id, nosilo_id, confidence, method, stamped),
        )
        self._conn.commit()
        return Xref(nosilo_id, source, confidence, method, stamped)

    def xrefs(self, nosilo_id: str) -> tuple[Xref, ...]:
        rows = self._conn.execute(
            "SELECT * FROM entity_xref WHERE nosilo_id=? ORDER BY source_system, source_entity, source_id",
            (nosilo_id,),
        ).fetchall()
        return tuple(
            Xref(
                nosilo_id=r["nosilo_id"],
                source=SourceRef(r["source_system"], r["source_entity"], r["source_id"]),
                confidence=r["confidence"],
                method=r["method"],
                recorded_at=r["recorded_at"],
            )
            for r in rows
        )

    # ---- observation --------------------------------------------------------

    def observe(
        self,
        entity_type: str,
        source: SourceRef,
        attributes: Mapping[str, Any],
        *,
        valid_from: str | None = None,
        recorded_at: str | None = None,
        trust: float = 1.0,
        change_reason: str = "",
    ) -> ObserveResult:
        """Record what one source system currently says about one record.

        This is the only write path. It resolves the source record to an entity
        (by xref, then by exact natural key, then by minting a new one), merges
        the claimed attributes against what is already held, and appends a
        version only if something actually changed.

        ``valid_from`` is *business* time and defaults to ``recorded_at``. It must
        not move backwards for an entity: out-of-order corrections need an
        explicit correction path rather than a silently reordered history, and
        this layer does not have one yet.
        """
        declared: EntityType = self._registry.get(entity_type)
        stamped = recorded_at or utc_now()
        effective = valid_from or stamped

        nosilo_id = self.resolve_source(source)
        created = False
        link_method = ""

        if nosilo_id is None:
            nosilo_id, link_method = self._resolve_or_mint(declared, source, attributes, stamped)
            created = link_method == LINK_MINTED

        held = self.current(nosilo_id)
        resolution = resolve(
            declared,
            held.fields if held else {},
            attributes,
            FieldProvenance(source=source, recorded_at=stamped, trust=trust),
        )

        if not resolution.changed:
            return ObserveResult(nosilo_id, entity_type, None, resolution.conflicts, created, link_method)

        version = self._append_version(
            nosilo_id=nosilo_id,
            entity_type=entity_type,
            previous=held,
            fields=resolution.fields,
            valid_from=effective,
            recorded_at=stamped,
            change_reason=change_reason,
        )
        self._reindex_natural_keys(declared, nosilo_id, resolution.fields)
        self._conn.commit()
        return ObserveResult(nosilo_id, entity_type, version, resolution.conflicts, created, link_method)

    def _resolve_or_mint(
        self,
        declared: EntityType,
        source: SourceRef,
        attributes: Mapping[str, Any],
        recorded_at: str,
    ) -> tuple[str, str]:
        """Find the entity this record belongs to, or create it.

        Natural-key matching here is *exact only*. Probabilistic matching belongs
        behind a human review queue, not inside the write path where a wrong
        guess silently welds two real-world entities together.
        """
        for key_name in declared.natural_keys:
            claimed = attributes.get(key_name)
            if claimed is None:
                continue
            found = self.find_by_natural_key(declared.name, key_name, claimed)
            if found is not None:
                self.link(found, source, confidence=1.0, method=LINK_EXACT_KEY, recorded_at=recorded_at)
                return found, LINK_EXACT_KEY

        nosilo_id = new_entity_id()
        self._conn.execute(
            "INSERT INTO entity (nosilo_id, entity_type, created_at) VALUES (?,?,?)",
            (nosilo_id, declared.name, recorded_at),
        )
        self.link(nosilo_id, source, confidence=1.0, method=LINK_MINTED, recorded_at=recorded_at)
        return nosilo_id, LINK_MINTED

    def _append_version(
        self,
        *,
        nosilo_id: str,
        entity_type: str,
        previous: EntityVersion | None,
        fields: Mapping[str, FieldValue],
        valid_from: str,
        recorded_at: str,
        change_reason: str,
    ) -> EntityVersion:
        if previous is not None and valid_from < previous.valid_from:
            raise EntityStoreError(
                f"{nosilo_id}: valid_from {valid_from!r} precedes the current version's "
                f"{previous.valid_from!r}. Backdated corrections would reorder history and this "
                "layer has no correction path yet - record it forward, or add one deliberately."
            )
        number = (previous.version + 1) if previous else 1
        self._conn.execute(
            "INSERT INTO entity_version"
            " (version_id, nosilo_id, entity_type, version, fields_json, valid_from, recorded_at, change_reason)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), nosilo_id, entity_type, number, _fields_to_json(fields),
             valid_from, recorded_at, change_reason),
        )
        return EntityVersion(
            nosilo_id=nosilo_id,
            entity_type=entity_type,
            version=number,
            fields=dict(fields),
            valid_from=valid_from,
            recorded_at=recorded_at,
            change_reason=change_reason,
        )

    def _reindex_natural_keys(
        self, declared: EntityType, nosilo_id: str, fields: Mapping[str, FieldValue]
    ) -> None:
        for key_name in declared.natural_keys:
            held = fields.get(key_name)
            if held is None or held.value is None:
                continue
            self._conn.execute(
                "DELETE FROM entity_natural_key WHERE entity_type=? AND key_name=? AND nosilo_id=?",
                (declared.name, key_name, nosilo_id),
            )
            self._conn.execute(
                "INSERT OR REPLACE INTO entity_natural_key"
                " (entity_type, key_name, key_value, nosilo_id) VALUES (?,?,?,?)",
                (declared.name, key_name, str(held.value), nosilo_id),
            )

    # ---- reading ------------------------------------------------------------

    def current(self, nosilo_id: str) -> EntityVersion | None:
        """The newest version, regardless of either time axis."""
        row = self._conn.execute(
            "SELECT * FROM entity_version WHERE nosilo_id=? ORDER BY version DESC LIMIT 1",
            (nosilo_id,),
        ).fetchone()
        return self._row_to_version(row) if row else None

    def as_of(
        self, nosilo_id: str, *, valid_at: str | None = None, known_at: str | None = None
    ) -> EntityVersion | None:
        """The version in effect at a business instant, as known at a system instant.

        ``valid_at`` answers "what was true then". ``known_at`` answers "what did
        we know then" - the one that matters when judging a decision, because it
        reconstructs the information the decider actually had rather than the
        information that exists now.
        """
        clauses = ["nosilo_id=?"]
        params: list[Any] = [nosilo_id]
        if valid_at is not None:
            clauses.append("valid_from<=?")
            params.append(valid_at)
        if known_at is not None:
            clauses.append("recorded_at<=?")
            params.append(known_at)
        row = self._conn.execute(
            f"SELECT * FROM entity_version WHERE {' AND '.join(clauses)} ORDER BY version DESC LIMIT 1",
            params,
        ).fetchone()
        return self._row_to_version(row) if row else None

    def history(self, nosilo_id: str) -> tuple[EntityVersion, ...]:
        """Every version, oldest first, with closing bounds derived from neighbours."""
        rows = self._conn.execute(
            "SELECT * FROM entity_version WHERE nosilo_id=? ORDER BY version ASC", (nosilo_id,)
        ).fetchall()
        versions = [self._row_to_version(r) for r in rows]
        closed: list[EntityVersion] = []
        for index, version in enumerate(versions):
            successor = versions[index + 1] if index + 1 < len(versions) else None
            closed.append(
                version
                if successor is None
                else EntityVersion(
                    nosilo_id=version.nosilo_id,
                    entity_type=version.entity_type,
                    version=version.version,
                    fields=version.fields,
                    valid_from=version.valid_from,
                    recorded_at=version.recorded_at,
                    valid_to=successor.valid_from,
                    superseded_at=successor.recorded_at,
                    change_reason=version.change_reason,
                )
            )
        return tuple(closed)

    def entities_of_type(self, entity_type: str) -> tuple[str, ...]:
        rows = self._conn.execute(
            "SELECT nosilo_id FROM entity WHERE entity_type=? ORDER BY nosilo_id", (entity_type,)
        ).fetchall()
        return tuple(r["nosilo_id"] for r in rows)

    @staticmethod
    def _row_to_version(row: sqlite3.Row) -> EntityVersion:
        return EntityVersion(
            nosilo_id=row["nosilo_id"],
            entity_type=row["entity_type"],
            version=row["version"],
            fields=_fields_from_json(row["fields_json"]),
            valid_from=row["valid_from"],
            recorded_at=row["recorded_at"],
            change_reason=row["change_reason"],
        )
