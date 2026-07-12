"""System state snapshot store (Linchpin 3.0 PR-1, capability F0 -- ``src/state``).

See ``system_state`` for the domain-validated, cycle-ordered, append-only
snapshot API (``snapshot`` / ``latest`` / ``history``) and ``store`` for the
on-disk SQLite index + parquet/CSV history layout underneath it.
"""

from __future__ import annotations

from .store import DEFAULT_BASE_PATH, DuplicateCycleError, StateStore, StoredRecord, default_store
from .system_state import (
    DOMAIN_COLUMNS,
    DOMAINS,
    ColumnRule,
    CycleOrderError,
    SchemaValidationError,
    StateSnapshot,
    UnknownDomainError,
    history,
    latest,
    snapshot,
)

__all__ = [
    "DEFAULT_BASE_PATH",
    "DOMAIN_COLUMNS",
    "DOMAINS",
    "ColumnRule",
    "CycleOrderError",
    "DuplicateCycleError",
    "SchemaValidationError",
    "StateSnapshot",
    "StateStore",
    "StoredRecord",
    "UnknownDomainError",
    "default_store",
    "history",
    "latest",
    "snapshot",
]
