"""System state snapshots (Linchpin 3.0 PR-1, capability F0 -- ``src/state``).

A versioned-by-cycle snapshot of system state: stock, own + competitor
prices, current forecast, decisions issued, outcomes. Each domain is a named
table (a pandas ``DataFrame``) validated against a schema contract before it
is allowed to land. Public interface (Linchpin 3.0 plan S4.1):

  snapshot(domain, payload, cycle_id)   -- validate + append; never overwrites
  latest(domain)                        -- most recent snapshot, or None
  history(domain, window)               -- chronological slice of snapshots

Two QA invariants make this the safe foundation the rest of the Tower (F0-A9)
builds on:

  - **append-only** (plan rule 8): a snapshot is a new row, never an edit --
    corrections are new cycles, not mutations of old ones.
  - **monotonic cycle_id per domain**: a snapshot whose ``cycle_id`` is not
    strictly after the domain's latest stored ``cycle_id`` is rejected before
    anything is written.

Schema validation uses ``pandera`` when the optional ``state`` extra is
installed; it falls back to an equivalent hand-rolled column/dtype/range
check when it is not (graceful degradation, matching ``src/sku_dedup.py``'s
rapidfuzz->difflib pattern) -- either way, an invalid payload is rejected
with a clear error and nothing is written.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pandas as pd

from .store import StateStore, default_store

try:  # optional strict schema validation
    import pandera.pandas as pa

    _HAS_PANDERA = True
except ImportError:
    try:
        import pandera as pa  # older pandera versions expose this at the top level
        _HAS_PANDERA = True
    except ImportError:
        pa = None
        _HAS_PANDERA = False


@dataclass(frozen=True)
class ColumnRule:
    """One column's contract: expected dtype and an optional value check."""

    name: str
    dtype: str               # "str" | "float"
    check: str | None = None  # "positive" (>0) | "nonnegative" (>=0) | None


# Domains covered by system state (plan S4.1): stock, own + competitor
# prices, current forecast, decisions issued, outcomes. Extra columns beyond
# these are always allowed (``strict=False`` below) -- this is the minimum
# contract a payload must satisfy, not an exhaustive one.
DOMAIN_COLUMNS: dict[str, tuple[ColumnRule, ...]] = {
    "stock": (
        ColumnRule("product_id", "str"),
        ColumnRule("on_hand", "float", "nonnegative"),
        ColumnRule("reorder_point", "float", "nonnegative"),
        ColumnRule("avg_daily_demand", "float", "nonnegative"),
    ),
    "prices_own": (
        ColumnRule("product_id", "str"),
        ColumnRule("price", "float", "positive"),
        ColumnRule("currency", "str"),
    ),
    "prices_competitor": (
        ColumnRule("product_id", "str"),
        ColumnRule("competitor", "str"),
        ColumnRule("price", "float", "positive"),
        ColumnRule("currency", "str"),
    ),
    "forecast": (
        ColumnRule("product_id", "str"),
        ColumnRule("period", "str"),
        ColumnRule("forecast_qty", "float", "nonnegative"),
    ),
    "decisions": (
        ColumnRule("product_id", "str"),
        ColumnRule("tool", "str"),
        ColumnRule("decision", "str"),
    ),
    "outcomes": (
        ColumnRule("product_id", "str"),
        ColumnRule("tool", "str"),
        ColumnRule("metric", "str"),
        ColumnRule("value", "float"),
    ),
}
DOMAINS: tuple[str, ...] = tuple(DOMAIN_COLUMNS)

_DTYPE_MAP = {"str": str, "float": float}


def _build_pandera_schema(columns: tuple[ColumnRule, ...]):
    fields = {}
    for c in columns:
        checks = []
        if c.check == "positive":
            checks.append(pa.Check.gt(0))
        elif c.check == "nonnegative":
            checks.append(pa.Check.ge(0))
        fields[c.name] = pa.Column(_DTYPE_MAP[c.dtype], checks=checks, nullable=False, coerce=True)
    # strict=False: extra columns beyond the contract are allowed.
    return pa.DataFrameSchema(fields, strict=False)


_PANDERA_SCHEMAS = {domain: _build_pandera_schema(cols) for domain, cols in DOMAIN_COLUMNS.items()} if _HAS_PANDERA else {}


class UnknownDomainError(ValueError):
    """``domain`` is not one of the known state domains (see ``DOMAINS``)."""

    def __init__(self, domain: str) -> None:
        self.domain = domain
        super().__init__(f"unknown state domain '{domain}' (known: {', '.join(DOMAINS)})")


class SchemaValidationError(ValueError):
    """A snapshot payload failed its domain's schema contract. Nothing was written."""

    def __init__(self, domain: str, issues: list[str]) -> None:
        self.domain = domain
        self.issues = list(issues)
        message = f"snapshot rejected for domain '{domain}': " + "; ".join(self.issues)
        super().__init__(message)


class CycleOrderError(ValueError):
    """``cycle_id`` is not strictly after the domain's latest stored cycle.

    State history is append-only and ``cycle_id`` must increase monotonically
    per domain (plan S4.1 QA invariant) -- this is raised before anything is
    written.
    """

    def __init__(self, domain: str, cycle_id: str, latest_cycle_id: str) -> None:
        self.domain = domain
        self.cycle_id = cycle_id
        self.latest_cycle_id = latest_cycle_id
        super().__init__(
            f"cycle_id '{cycle_id}' is not after the latest stored cycle_id '{latest_cycle_id}'"
            f" for domain '{domain}' (state history is append-only and cycle_id must increase"
            " monotonically)"
        )


def _cycle_sort_key(cycle_id: str) -> tuple:
    """Order ``cycle_id`` values sensibly regardless of scheme: plain integers
    ("1", "2", ..., "10") sort numerically, ISO-8601 timestamps sort
    chronologically, anything else sorts lexicographically as a last resort.
    Callers should keep one scheme per domain; comparing across schemes still
    produces a total (if not very meaningful) order rather than raising.
    """
    try:
        return (0, int(cycle_id))
    except (TypeError, ValueError):
        pass
    try:
        return (1, datetime.fromisoformat(cycle_id.replace("Z", "+00:00")))
    except (TypeError, ValueError):
        pass
    return (2, cycle_id)


def _validate_fallback(columns: tuple[ColumnRule, ...], payload: pd.DataFrame) -> list[str]:
    issues: list[str] = []
    for c in columns:
        if c.name not in payload.columns:
            issues.append(f"{c.name}: missing required column")
            continue
        series = payload[c.name]
        if series.isna().any():
            issues.append(f"{c.name}: contains null values")
            continue
        if c.dtype == "float":
            numeric = pd.to_numeric(series, errors="coerce")
            if numeric.isna().any():
                issues.append(f"{c.name}: expected numeric values, found non-numeric entries")
                continue
            if c.check == "positive" and (numeric <= 0).any():
                issues.append(f"{c.name}: expected strictly positive values")
            elif c.check == "nonnegative" and (numeric < 0).any():
                issues.append(f"{c.name}: expected non-negative values")
        # str columns: any scalar is representable as a string, so beyond the
        # null-check above there is nothing further to coerce/validate.
    return issues


def _validate_schema(domain: str, payload: pd.DataFrame) -> None:
    """Raise ``SchemaValidationError`` if ``payload`` does not satisfy ``domain``'s
    contract. Returns None (no side effects) when it does."""
    columns = DOMAIN_COLUMNS[domain]
    if _HAS_PANDERA:
        try:
            _PANDERA_SCHEMAS[domain].validate(payload, lazy=True)
        except pa.errors.SchemaErrors as exc:
            records = exc.failure_cases[["column", "check", "failure_case"]].to_dict("records")
            issues = [f"{r['column']}: {r['check']} failed on {r['failure_case']!r}" for r in records]
            raise SchemaValidationError(domain, issues or [str(exc)]) from exc
        except pa.errors.SchemaError as exc:
            raise SchemaValidationError(domain, [str(exc)]) from exc
    else:
        issues = _validate_fallback(columns, payload)
        if issues:
            raise SchemaValidationError(domain, issues)


@dataclass(frozen=True, eq=False)
class StateSnapshot:
    """One stored (or about-to-be-stored) snapshot: a domain's payload as of one cycle.

    ``eq``/``hash`` are left as identity (default ``object`` behavior) rather
    than dataclass field-wise equality, because comparing two ``DataFrame``s
    with ``==`` does not produce a usable bool -- compare ``.payload``
    directly (e.g. with ``DataFrame.equals``) when a test needs that.
    """

    domain: str
    cycle_id: str
    payload: pd.DataFrame
    created_at: str


def snapshot(
    domain: str,
    payload: pd.DataFrame,
    cycle_id: str,
    *,
    store: StateStore | None = None,
    now: datetime | None = None,
) -> StateSnapshot:
    """Validate and append a new snapshot for ``domain``. Never overwrites history.

    Raises ``UnknownDomainError`` for a domain outside ``DOMAINS``,
    ``SchemaValidationError`` if ``payload`` fails the domain's schema
    contract, or ``CycleOrderError`` if ``cycle_id`` is not strictly after the
    domain's latest stored cycle. Nothing is written to disk unless all three
    checks pass.
    """
    if domain not in DOMAIN_COLUMNS:
        raise UnknownDomainError(domain)
    if not isinstance(payload, pd.DataFrame):
        raise TypeError(f"payload must be a pandas DataFrame, got {type(payload).__name__}")
    if not isinstance(cycle_id, str) or not cycle_id.strip():
        raise ValueError("cycle_id must be a non-empty string")

    _validate_schema(domain, payload)  # raises SchemaValidationError; nothing written on failure

    store = store or default_store()
    latest_id = store.latest_cycle_id(domain)
    if latest_id is not None and _cycle_sort_key(cycle_id) <= _cycle_sort_key(latest_id):
        raise CycleOrderError(domain, cycle_id, latest_id)

    record = store.append_snapshot(domain, cycle_id, payload, now=now)
    return StateSnapshot(domain, cycle_id, payload.copy(deep=True), record.created_at)


def latest(domain: str, *, store: StateStore | None = None) -> StateSnapshot | None:
    """The most recently stored snapshot for ``domain``, or None if it has none."""
    if domain not in DOMAIN_COLUMNS:
        raise UnknownDomainError(domain)
    store = store or default_store()
    record = store.latest_record(domain)
    if record is None:
        return None
    return StateSnapshot(domain, record.cycle_id, store.load_payload(record), record.created_at)


def history(
    domain: str, window: int | None = None, *, store: StateStore | None = None
) -> list[StateSnapshot]:
    """All snapshots for ``domain``, oldest first. ``window`` (if given) keeps only
    the most recent ``window`` snapshots, still oldest-first."""
    if domain not in DOMAIN_COLUMNS:
        raise UnknownDomainError(domain)
    if window is not None and window < 0:
        raise ValueError("window must be >= 0")

    store = store or default_store()
    records = store.list_records(domain)
    if window is not None:
        records = records[-window:] if window > 0 else []
    return [StateSnapshot(domain, r.cycle_id, store.load_payload(r), r.created_at) for r in records]
