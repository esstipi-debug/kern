"""System state snapshot store (Linchpin 3.0 PR-1, capability F0 -- ``src/state``).

See ``system_state`` for the domain-validated, cycle-ordered, append-only
snapshot API (``snapshot`` / ``latest`` / ``history``) and ``store`` for the
on-disk SQLite index + parquet/CSV history layout underneath it.
"""

from __future__ import annotations

from .record import record_decision, record_forecast, record_outcome, record_stock
from .store import (
    DEFAULT_BASE_PATH,
    ClientIdError,
    DuplicateCycleError,
    StateStore,
    StoredRecord,
    default_store,
    store_for,
)
from .system_state import (
    DOMAIN_COLUMNS,
    DOMAINS,
    ColumnRule,
    CycleOrderError,
    MissingStoreError,
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
    "ClientIdError",
    "ColumnRule",
    "CycleOrderError",
    "DuplicateCycleError",
    "MissingStoreError",
    "SchemaValidationError",
    "StateSnapshot",
    "StateStore",
    "StoredRecord",
    "UnknownDomainError",
    "default_store",
    "history",
    "latest",
    "record_decision",
    "record_forecast",
    "record_outcome",
    "record_stock",
    "snapshot",
    "store_for",
]
