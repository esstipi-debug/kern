"""Safe-staging writeback control plane (capability M15).

The agent never mutates a client's system of record directly. It:
  1. STAGES a dry-run ``Changeset`` (field-level before/after) without writing;
  2. classifies it by RISK TIER (read / reversible / irreversible);
  3. requires a valid, matching, unexpired ``Approval`` for anything that is not
     auto-applicable under policy;
  4. APPLIES idempotently (the same idempotency_key never lands twice);
  5. records an AUDIT entry so any applied changeset can be ROLLED BACK.

This module is pure and ships an ``InMemoryStore`` reference implementation that
stands in for a real connector (ERP / Excel / DB). Real connectors implement the
same read/commit/rollback surface; the safety logic here is connector-agnostic.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

# Risk tiers, by reversibility/impact.
TIER_READ = "read"
TIER_REVERSIBLE = "reversible"        # a write that can be cleanly undone (e.g. set a field)
TIER_IRREVERSIBLE = "irreversible"    # a write that cannot be safely undone (e.g. send a PO)


class WritebackRefused(Exception):
    """Raised when an apply is blocked by the safety policy (missing/invalid approval)."""


def requires_approval(tier: str, *, auto_apply_reversible: bool = False) -> bool:
    """Whether a tier needs explicit human approval before it can be applied."""
    if tier == TIER_READ:
        return False
    if tier == TIER_REVERSIBLE:
        return not auto_apply_reversible
    return True  # irreversible always needs a human in the loop


@dataclass(frozen=True)
class Change:
    """A single field-level edit, as a dry-run before/after pair."""

    entity_id: str
    field: str
    before: object
    after: object

    @property
    def is_noop(self) -> bool:
        return self.before == self.after


def _content_hash(target: str, risk_tier: str, changes: tuple[Change, ...]) -> str:
    """Stable hash of what a changeset actually does (not its idempotency_key).

    Binding an ``Approval`` to this - not just to ``idempotency_key`` - closes the
    "approve X, apply Y" gap: a caller can no longer approve one set of edits and
    then apply a different changeset that happens to reuse the same key.
    """
    payload = repr((target, risk_tier, tuple((c.entity_id, c.field, c.before, c.after) for c in changes)))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Changeset:
    """A staged, not-yet-applied set of changes against one target system."""

    target: str
    changes: tuple[Change, ...]
    risk_tier: str
    idempotency_key: str
    reason: str = ""

    @property
    def is_noop(self) -> bool:
        return all(c.is_noop for c in self.changes)

    @property
    def content_hash(self) -> str:
        return _content_hash(self.target, self.risk_tier, self.changes)

    def summary(self) -> str:
        n = sum(1 for c in self.changes if not c.is_noop)
        return f"{n} change(s) to {self.target} [{self.risk_tier}] key={self.idempotency_key}"


@dataclass(frozen=True)
class Approval:
    """A time-boxed authorization bound to one changeset's key AND its exact content."""

    changeset_key: str
    content_hash: str
    approved_by: str
    expires_at: float

    def is_valid_at(self, now: float) -> bool:
        return now < self.expires_at

    def matches(self, changeset: Changeset) -> bool:
        """Whether this approval was granted for exactly this changeset's content."""
        return self.changeset_key == changeset.idempotency_key and self.content_hash == changeset.content_hash


@dataclass(frozen=True)
class AuditEntry:
    """What was applied, by whom, and how to undo it."""

    idempotency_key: str
    target: str
    approved_by: str
    restore: tuple[tuple[str, str, object], ...]  # (entity_id, field, original_value)


@dataclass(frozen=True)
class ApplyResult:
    applied: bool
    idempotent_skip: bool = False
    audit_id: str | None = None


def approve(
    changeset: Changeset, approved_by: str, *, now: float | None = None, ttl_seconds: float = 900.0
) -> Approval:
    """Mint an approval valid for ``ttl_seconds`` from ``now``.

    ``now`` defaults to the real wall clock (``time.time()``); pass an explicit value
    only for deterministic tests. Bound to both the changeset's key and its content
    hash, so it cannot later validate a different changeset that reuses the same key.
    """
    if now is None:
        now = time.time()
    return Approval(changeset.idempotency_key, changeset.content_hash, approved_by, now + ttl_seconds)


class AuditBookkeeping:
    """Shared applied/audit bookkeeping for a writeback store.

    Backed by an in-memory dict, or - when ``ledger`` is given (e.g. a
    ``src.writeback_store.SqliteAuditLedger``) - a persistent ledger that survives a
    process restart. Any store implementing read/commit/rollback (``InMemoryStore``
    below, or a real connector's system-of-record wrapper) composes this instead of
    re-deriving the same ledger-or-dict branching.
    """

    def __init__(self, ledger: object | None = None) -> None:
        self._ledger = ledger
        self._applied: dict[str, AuditEntry] = {}

    def applied_keys(self) -> set[str]:
        return self._ledger.applied_keys() if self._ledger is not None else set(self._applied)

    def record(self, entry: AuditEntry) -> None:
        if self._ledger is not None:
            self._ledger.record(entry)
        else:
            self._applied[entry.idempotency_key] = entry

    def get(self, idempotency_key: str) -> AuditEntry | None:
        if self._ledger is not None:
            return self._ledger.get(idempotency_key)
        return self._applied.get(idempotency_key)

    def forget(self, idempotency_key: str) -> None:
        if self._ledger is not None:
            self._ledger.forget(idempotency_key)
        else:
            del self._applied[idempotency_key]


class InMemoryStore:
    """Reference system-of-record. Real connectors mirror read/_commit/rollback.

    ``ledger``, when given, persists the applied/audit bookkeeping (e.g. a
    ``src.writeback_store.SqliteAuditLedger``) so idempotency and rollback survive
    a process restart. Without one, bookkeeping lives in process memory exactly as
    before - fully backward compatible.
    """

    def __init__(self, records: dict | None = None, *, ledger: object | None = None) -> None:
        self._records: dict[str, dict] = {k: dict(v) for k, v in (records or {}).items()}
        self._audit = AuditBookkeeping(ledger)

    def read(self, entity_id: str) -> dict:
        return dict(self._records.get(entity_id, {}))

    def applied_keys(self) -> set[str]:
        return self._audit.applied_keys()

    def commit(self, changeset: Changeset, approved_by: str) -> AuditEntry:
        # Capture originals BEFORE writing, so rollback is exact.
        restore = tuple(
            (c.entity_id, c.field, self._records.get(c.entity_id, {}).get(c.field, ABSENT))
            for c in changeset.changes
        )
        for c in changeset.changes:
            self._records.setdefault(c.entity_id, {})[c.field] = c.after
        entry = AuditEntry(changeset.idempotency_key, changeset.target, approved_by, restore)
        self._audit.record(entry)
        return entry

    def rollback(self, idempotency_key: str) -> None:
        entry = self._audit.get(idempotency_key)
        if entry is None:
            raise KeyError(idempotency_key)
        for entity_id, fld, original in entry.restore:
            if original is ABSENT:
                self._records.get(entity_id, {}).pop(fld, None)
            else:
                self._records.setdefault(entity_id, {})[fld] = original
        self._audit.forget(idempotency_key)


# Shared "this field did not exist before the change" sentinel. Public (not
# module-private) so any real connector's system-of-record wrapper - and any
# persistent ledger serializing a `restore` tuple - can recognize it by
# identity instead of every store inventing its own equivalent sentinel.
ABSENT = object()


def stage(
    store: InMemoryStore,
    target: str,
    edits: dict[str, dict],
    *,
    risk_tier: str,
    idempotency_key: str,
    reason: str = "",
) -> Changeset:
    """Compute a dry-run Changeset from current store values. Does NOT write."""
    changes: list[Change] = []
    for entity_id, fields in edits.items():
        current = store.read(entity_id)
        for fld, after in fields.items():
            changes.append(Change(entity_id, fld, current.get(fld), after))
    return Changeset(target, tuple(changes), risk_tier, idempotency_key, reason)


def apply(
    store: InMemoryStore,
    changeset: Changeset,
    *,
    approval: Approval | None = None,
    now: float | None = None,
    auto_apply_reversible: bool = False,
) -> ApplyResult:
    """Apply a staged changeset under the safety policy.

    ``now`` defaults to the real wall clock (``time.time()``) - pass an explicit
    value only for deterministic tests. Without this default, every production
    caller that omitted ``now`` was implicitly evaluating approvals at ``t=0``,
    so a 900s TTL never actually expired.

    Refuses (``WritebackRefused``) when approval is required but missing, does not
    match this exact changeset (key AND content), or is expired. Idempotent on
    ``idempotency_key``.
    """
    if now is None:
        now = time.time()
    if requires_approval(changeset.risk_tier, auto_apply_reversible=auto_apply_reversible):
        if approval is None or not approval.matches(changeset) or not approval.is_valid_at(now):
            raise WritebackRefused(
                f"approval required for tier '{changeset.risk_tier}' and is missing/mismatched/expired"
            )

    if changeset.idempotency_key in store.applied_keys():
        return ApplyResult(applied=False, idempotent_skip=True, audit_id=changeset.idempotency_key)

    approved_by = approval.approved_by if approval is not None else "auto"
    entry = store.commit(changeset, approved_by)
    return ApplyResult(applied=True, idempotent_skip=False, audit_id=entry.idempotency_key)
