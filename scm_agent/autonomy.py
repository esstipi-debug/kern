"""Autonomy tiers -- A3 "execute" layer (Linchpin 3.0 PR-6, plan S5).

``config/event_routing.yaml`` already carries an ``autonomy_tier`` per route
(PR-4), and its own header comment says the quiet part out loud: "tiers are
enforced starting PR-6's autonomy.py; here the tier is carried as data only".
This module is that enforcement -- it turns the label into actual behavior
for the two shapes a routed tool result can take (plan S5, A3 row):

(a) **Analysis-only tools** (the common case -- most of ``scm_agent/tools.py``'s
    50 registered tools): a routed run produces a
    ``nosilo_core.types.JobResult`` with no system-of-record write attached.
    :func:`enforce_analysis_tier` gates it:

      - **T1** auto-execute: the tool already ran (through the real QA gate)
        and ``handle_event_tiered`` already let ``notify()`` fire -- this is
        informational autonomy, not a human gate. The tool's own protected
        outcome (``result.guided``) is returned as-is. What makes this
        "audited, not silent" is a durable :class:`AutonomyRecord` written to
        :class:`AutonomyLedger` with ``status=STATUS_AUTO_EXECUTED``.
      - **T2** one-click approval: the analysis still ran (and is still
        QA-gated), but the result is held as a ``STATUS_PENDING``
        :class:`AutonomyRecord` instead of being announced as done --
        ``handle_event_tiered`` suppresses the automatic notify for this
        tier. The returned outcome is a ``HANDOFF`` naming the pending
        record's id; :func:`acknowledge_pending` is the accept/approve
        function PR-7's ``POST /api/approvals/{id}`` calls to complete it.
      - **T3** full escalation: wrapped in a real ``ESCALATED`` outcome via
        ``nosilo_core/escalation.py`` (never a hand-rolled one) and recorded with
        ``status=STATUS_ESCALATED`` -- never treated as done, never
        auto-notified.

(b) **Writeback-capable tool results** (``odoo_replenishment``,
    ``excel_replenishment`` -- the few tools whose report carries a staged
    ``nosilo_core.writeback.Changeset``, not just a ``JobResult``):
    :func:`enforce_writeback_tier` gates the changeset directly:

      - **T1** auto-applies (``writeback.apply(..., auto_apply_reversible=True)``)
        ONLY when ``changeset.risk_tier == writeback.TIER_REVERSIBLE``. An
        irreversible changeset is downgraded to the same human-approval
        ``HANDOFF`` as T2 -- see that function's docstring for why this does
        not weaken ``writeback.requires_approval()``'s hard-coded invariant,
        only adds a second, independent layer in front of it.
      - **T2** stages only: returns a ``HANDOFF`` naming the exact
        ``approve()``/``apply()`` call a human must make. Never calls either.
      - **T3** escalates outright, before any apply is attempted.

Neither path needs its own ``jobs/qa.py``-style ``verify_*``/``*_passed``
gate: there is no new deliverable/report here to QA -- the routed tool's own
QA veto (plan rule 2) already ran upstream, and every ``GuidedOutcome`` this
module returns is itself checked by ``nosilo_core.guided.verify_guided`` at
construction time (``as_handoff``/``as_executed``/``escalate`` all raise or
enforce the contract already). This mirrors PR-1/PR-2's precedent of
skipping a QA function where the module has no ``JobReport`` to gate.
"""

from __future__ import annotations

import math
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from nosilo_core import escalation, writeback
from nosilo_core.escalation import escalate
from nosilo_core.guided import GuidedOutcome, HandoffPacket, as_executed, as_handoff
from nosilo_core.types import STATUS_OK

from src.data_paths import resolve_state_path

from .event_intent import (
    DEFAULT_ROUTING_PATH,
    VALID_AUTONOMY_TIERS,
    Route,
    RoutedResult,
    load_routing,
    resolve_route,
)
from .event_intent import handle_event as _handle_event
from .events import Event
from .orchestrator import Orchestrator

# Friendly aliases for VALID_AUTONOMY_TIERS's three values -- reused (not
# redefined) from event_intent.py so the two modules can never disagree about
# what a valid tier string is.
TIER_T1, TIER_T2, TIER_T3 = VALID_AUTONOMY_TIERS

# AutonomyRecord.status values.
STATUS_AUTO_EXECUTED = "auto_executed"   # T1: ran autonomously, audited
STATUS_PENDING = "pending"               # T2: awaiting one-click acknowledgment
STATUS_ACKNOWLEDGED = "acknowledged"     # T2: a human completed the acknowledgment
STATUS_ESCALATED = "escalated"           # T3: routed to a human, never auto-actioned
STATUS_EXPIRED = "expired"               # T2: the approval window lapsed, unacted on

# Env-override convention matching scm_agent/events.py's DEFAULT_PATH and
# src/state/store.py's LINCHPIN_STATE_PATH.
DEFAULT_PATH = resolve_state_path("autonomy")


def _default_ttl_seconds() -> float:
    """The T2 one-click-approval window, in seconds. Defaults to 900s -- the
    same TTL ``nosilo_core/writeback.py``'s ``Approval`` uses, which this module's own
    docstring says the autonomy layer *extends* (plan S12: "T2 con Approval
    TTL"). Overridable via ``LINCHPIN_AUTONOMY_TTL_SECONDS`` (env convention
    matching ``LINCHPIN_AUTONOMY_PATH`` above); a non-positive or unparseable
    value falls back to the 900s default rather than disabling expiry.
    """
    raw = os.environ.get("LINCHPIN_AUTONOMY_TTL_SECONDS", "").strip()
    if not raw:
        return 900.0
    try:
        value = float(raw)
    except ValueError:
        return 900.0
    # Reject non-positive AND non-finite ("inf"/"nan") values: float("inf")
    # parses and is > 0, but timedelta(seconds=inf) later raises OverflowError.
    if not math.isfinite(value) or value <= 0:
        return 900.0
    return value


# Resolved once at import (mirrors DEFAULT_PATH). Kept a module global so a
# test can monkeypatch it, and so record() reads it lazily at call time.
DEFAULT_TTL_SECONDS = _default_ttl_seconds()


def _ensure_utc(dt: datetime) -> datetime:
    """Treat a naive datetime as already-UTC; convert an aware one to UTC."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class AutonomyRecord:
    """One audited autonomy action -- a T1 auto-execution, a T2
    pending/acknowledged item, or a T3 escalation. The durable evidence that
    "the agent already ran and told someone" (T1) or "this is awaiting a
    human" (T2) is never just an in-memory GuidedOutcome nobody persisted.
    """

    id: str
    tier: str
    status: str
    event_type: str
    event_id: str
    summary: str
    created_at: datetime
    sku: str | None = None
    tool: str | None = None
    acknowledged_by: str | None = None
    acknowledged_at: datetime | None = None
    # Only T2 pending records carry a deadline. T1/T3 (and legacy rows written
    # before the TTL existed) leave this None and never expire -- see
    # is_expired().
    expires_at: datetime | None = None

    def is_expired(self, now: datetime | None = None) -> bool:
        """True once the T2 approval window has lapsed. A record with no
        deadline (``expires_at is None`` -- every T1/T3 row, and every legacy
        pending row predating this field) is never expired, so old data can
        always still be acknowledged rather than silently lapsing."""
        if self.expires_at is None:
            return False
        moment = _ensure_utc(now) if now is not None else datetime.now(timezone.utc)
        return moment >= self.expires_at


def _row_to_record(row: tuple) -> AutonomyRecord:
    (id_, tier, status, event_type, event_id, sku, tool, summary, created_at,
     acknowledged_by, acknowledged_at, expires_at) = row
    return AutonomyRecord(
        id=id_, tier=tier, status=status, event_type=event_type, event_id=event_id,
        summary=summary, created_at=datetime.fromisoformat(created_at), sku=sku, tool=tool,
        acknowledged_by=acknowledged_by,
        acknowledged_at=datetime.fromisoformat(acknowledged_at) if acknowledged_at else None,
        expires_at=datetime.fromisoformat(expires_at) if expires_at else None,
    )


class AutonomyLedger:
    """SQLite-backed audit trail for every T1/T2/T3 decision (A3's own
    ledger, alongside ``scm_agent.events.EventLedger`` and
    ``nosilo_core.writeback_store.SqliteAuditLedger``).

    ``path`` is injectable so tests never touch a real data directory: pass
    ``":memory:"`` for a pure in-process ledger, or a ``tmp_path``-based file
    to also exercise the on-disk path.
    """

    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        # timeout=30 matches scm_agent/events.py's EventLedger convention.
        self._conn = sqlite3.connect(self._path, timeout=30.0)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS autonomy_actions ("
            " id TEXT PRIMARY KEY,"
            " tier TEXT NOT NULL,"
            " status TEXT NOT NULL,"
            " event_type TEXT NOT NULL,"
            " event_id TEXT NOT NULL,"
            " sku TEXT,"
            " tool TEXT,"
            " summary TEXT NOT NULL,"
            " created_at TEXT NOT NULL,"
            " acknowledged_by TEXT,"
            " acknowledged_at TEXT,"
            " expires_at TEXT"
            ")"
        )
        self._migrate_add_expires_at()
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_autonomy_status ON autonomy_actions(status)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS idx_autonomy_event_id ON autonomy_actions(event_id)")
        self._conn.commit()

    def _migrate_add_expires_at(self) -> None:
        """Add the ``expires_at`` column to a ledger file created before this
        change (CREATE TABLE IF NOT EXISTS never adds a column to an existing
        table). Legacy rows keep NULL -> they never expire (see
        AutonomyRecord.is_expired), so no live pending approval is silently
        invalidated by the upgrade. Idempotent, and safe under concurrency: the
        webapp builds a fresh AutonomyLedger (and so runs this) on every
        request, so on the first deploy against a real legacy DB two requests
        can race between the PRAGMA check and the ALTER. SQLite has no
        ``ADD COLUMN IF NOT EXISTS``, so the losing racer's ALTER raises
        "duplicate column name" -- caught here as the benign already-migrated
        case rather than 500ing that request."""
        columns = {row[1] for row in self._conn.execute("PRAGMA table_info(autonomy_actions)")}
        if "expires_at" in columns:
            return
        try:
            self._conn.execute("ALTER TABLE autonomy_actions ADD COLUMN expires_at TEXT")
        except sqlite3.OperationalError as exc:
            if "duplicate column name" not in str(exc).lower():
                raise

    def record(
        self,
        *,
        tier: str,
        status: str,
        event_type: str,
        event_id: str,
        summary: str,
        sku: str | None = None,
        tool: str | None = None,
        now: datetime | None = None,
        ttl_seconds: float | None = None,
    ) -> AutonomyRecord:
        """Write one audit row and return it. Every T1/T2/T3 decision writes
        exactly one of these -- this is what makes "audited, not silent" true
        rather than aspirational.

        A ``STATUS_PENDING`` (T2) row is stamped with an ``expires_at`` deadline
        ``ttl_seconds`` (default :data:`DEFAULT_TTL_SECONDS`) after ``now`` --
        the one-click approval window (plan S12: "T2 con Approval TTL"). Every
        other status leaves ``expires_at`` NULL: a T1 auto-execution and a T3
        escalation are already terminal, not something a human is expected to
        act on before a clock runs out.
        """
        created_at = _ensure_utc(now) if now is not None else datetime.now(timezone.utc)
        expires_at: datetime | None = None
        if status == STATUS_PENDING:
            ttl = ttl_seconds if ttl_seconds is not None else DEFAULT_TTL_SECONDS
            expires_at = created_at + timedelta(seconds=ttl)
        record_id = uuid.uuid4().hex
        self._conn.execute(
            "INSERT INTO autonomy_actions"
            " (id, tier, status, event_type, event_id, sku, tool, summary, created_at, expires_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (record_id, tier, status, event_type, event_id, sku, tool, summary,
             created_at.isoformat(), expires_at.isoformat() if expires_at else None),
        )
        self._conn.commit()
        return AutonomyRecord(
            id=record_id, tier=tier, status=status, event_type=event_type, event_id=event_id,
            summary=summary, created_at=created_at, sku=sku, tool=tool, expires_at=expires_at,
        )

    _SELECT_COLUMNS = (
        "id, tier, status, event_type, event_id, sku, tool, summary, created_at,"
        " acknowledged_by, acknowledged_at, expires_at"
    )

    def get(self, record_id: str) -> AutonomyRecord | None:
        row = self._conn.execute(
            f"SELECT {self._SELECT_COLUMNS} FROM autonomy_actions WHERE id = ?",
            (record_id,),
        ).fetchone()
        return _row_to_record(row) if row else None

    def list_pending(self, now: datetime | None = None) -> list[AutonomyRecord]:
        """Every T2 record still awaiting acknowledgment, oldest first --
        what PR-7's Tower tab lists as the "approve with one click" queue.

        When ``now`` is given, records whose approval window has already lapsed
        (``is_expired(now)``) are excluded even if a sweep
        (:meth:`expire_stale`) has not yet flipped them to
        ``STATUS_EXPIRED`` -- so the actionable queue never offers a stale item
        a human can no longer complete. With ``now=None`` (the default), every
        ``STATUS_PENDING`` row is returned unchanged, preserving the original
        behavior for callers that do not care about the clock.
        """
        rows = self._conn.execute(
            f"SELECT {self._SELECT_COLUMNS} FROM autonomy_actions WHERE status = ? ORDER BY rowid ASC",
            (STATUS_PENDING,),
        ).fetchall()
        records = [_row_to_record(r) for r in rows]
        if now is None:
            return records
        return [r for r in records if not r.is_expired(now)]

    def list_all(self) -> list[AutonomyRecord]:
        """Every recorded decision, oldest first -- the full A3 audit trail."""
        rows = self._conn.execute(
            f"SELECT {self._SELECT_COLUMNS} FROM autonomy_actions ORDER BY rowid ASC"
        ).fetchall()
        return [_row_to_record(r) for r in rows]

    def expire_stale(self, now: datetime | None = None) -> list[AutonomyRecord]:
        """Flip every lapsed ``STATUS_PENDING`` row (deadline at or before
        ``now``) to ``STATUS_EXPIRED`` and return the transitioned records,
        oldest first. A pending row with no deadline (legacy NULL
        ``expires_at``) is never swept. Idempotent: an already-expired row is
        not touched a second time, so re-running the sweep returns an empty
        list once the backlog is drained.

        The safe direction on timeout, by construction: expiry makes a T2 item
        *lapse* (it must be re-triggered), never auto-approves it -- consistent
        with nosilo_core/guided.py's "three of four outcomes need a human" contract.
        """
        moment = _ensure_utc(now) if now is not None else datetime.now(timezone.utc)
        lapsed = [r for r in self.list_pending() if r.is_expired(moment)]
        if not lapsed:
            return []
        self._conn.executemany(
            "UPDATE autonomy_actions SET status = ? WHERE id = ?",
            [(STATUS_EXPIRED, r.id) for r in lapsed],
        )
        self._conn.commit()
        return [self.get(r.id) for r in lapsed]

    def acknowledge(self, record_id: str, approved_by: str, *, now: datetime | None = None) -> AutonomyRecord:
        """Flip a ``STATUS_PENDING`` record to ``STATUS_ACKNOWLEDGED``.

        Raises ``KeyError`` if ``record_id`` is unknown, or ``ValueError`` if
        it is not currently pending (already acknowledged, or was never a T2
        row) OR if its approval window has already lapsed -- an acknowledgment
        must be a loud, actionable failure when it cannot legitimately apply,
        never a silent no-op.

        The expiry check is the enforcement half of the T2 Approval TTL: a
        stale one-click approval can never be rubber-stamped after its window
        closed. Run :meth:`expire_stale` first if you want lapsed rows visibly
        moved to ``STATUS_EXPIRED``; either way, acknowledging one is refused.
        """
        acknowledged_at = _ensure_utc(now) if now is not None else datetime.now(timezone.utc)
        existing = self.get(record_id)
        if existing is None:
            raise KeyError(f"no autonomy record with id {record_id!r}")
        if existing.status != STATUS_PENDING:
            raise ValueError(
                f"autonomy record {record_id!r} is not pending (status={existing.status!r}) -- "
                "only a T2 record still awaiting acknowledgment can be acknowledged"
            )
        if existing.is_expired(acknowledged_at):
            raise ValueError(
                f"autonomy record {record_id!r} expired at {existing.expires_at.isoformat()} -- "
                "the one-click approval window has closed; re-trigger the action instead of "
                "acknowledging a stale request"
            )
        self._conn.execute(
            "UPDATE autonomy_actions SET status = ?, acknowledged_by = ?, acknowledged_at = ? WHERE id = ?",
            (STATUS_ACKNOWLEDGED, approved_by, acknowledged_at.isoformat(), record_id),
        )
        self._conn.commit()
        return self.get(record_id)

    def close(self) -> None:
        self._conn.close()


def acknowledge_pending(
    ledger: AutonomyLedger, record_id: str, approved_by: str, *, now: datetime | None = None
) -> GuidedOutcome:
    """The T2 "one-click approval" completion step.

    This is the accept/approve function PR-7's ``POST /api/approvals/{id}``
    wires to an HTTP endpoint -- everything below this call (auth, request
    parsing, the actual route) is that PR's job; this function is the whole
    of what it needs to call. Raises whatever ``AutonomyLedger.acknowledge``
    raises (``KeyError``/``ValueError``) for an unknown or already-resolved id.
    """
    record = ledger.acknowledge(record_id, approved_by, now=now)
    return as_executed(f"Acknowledged by {approved_by}: {record.summary}")


def enforce_analysis_tier(
    routed: RoutedResult,
    event: Event,
    *,
    ledger: AutonomyLedger | None = None,
) -> GuidedOutcome:
    """Gate an analysis-only ``RoutedResult`` by its route's autonomy tier.

    Reads the tier off ``routed.route.autonomy_tier`` (not a separate
    parameter -- the ``Route`` a caller already resolved is the single
    source of truth for it, matching ``config/event_routing.yaml``'s "ruteo
    como dato" convention). ``ledger`` defaults to a fresh
    ``AutonomyLedger()`` at ``DEFAULT_PATH`` when omitted -- unlike
    ``EventLedger``'s optional dedup ledger (``ledger=None`` there means "no
    persistence"), auditing here is not optional, so ``None`` still gets a
    real ledger, not a no-op.

    A non-``STATUS_OK`` result (``needs_data``/``qa_failed``/``error``/...)
    is returned as ``routed.result.guided`` unchanged and writes NO ledger
    record -- the plan's QA veto (rule 2) already means nothing was
    delivered, so there is nothing for a tier to gate.
    """
    result = routed.result
    route = routed.route
    tier = route.autonomy_tier
    if tier not in VALID_AUTONOMY_TIERS:
        raise ValueError(f"unknown autonomy tier {tier!r} (must be one of {VALID_AUTONOMY_TIERS})")

    if result.status != STATUS_OK:
        return result.guided

    ledger = ledger if ledger is not None else AutonomyLedger()
    sku_part = f" ({event.sku})" if event.sku else ""
    context = f"{event.type}{sku_part}: {route.tool} -- {result.summary}"

    if tier == TIER_T1:
        # Informational autonomy: handle_event_tiered() already let notify()
        # fire for T1 (and only T1) before this ever runs -- the tool's own
        # protected outcome IS the answer; what this adds is the durable
        # "this happened autonomously" record, not a second notification.
        ledger.record(
            tier=TIER_T1, status=STATUS_AUTO_EXECUTED, event_type=event.type, event_id=event.id,
            sku=event.sku, tool=route.tool, summary=context,
        )
        return result.guided

    if tier == TIER_T2:
        record = ledger.record(
            tier=TIER_T2, status=STATUS_PENDING, event_type=event.type, event_id=event.id,
            sku=event.sku, tool=route.tool, summary=context,
        )
        deliverable_list = ", ".join(result.deliverables.values()) or "(no files -- see result.summary)"
        expires_iso = record.expires_at.isoformat() if record.expires_at else None
        packet = HandoffPacket(
            title=f"Acknowledge: {route.tool} result for {event.sku or event.type}",
            steps=[
                f"Review the deliverable(s): {deliverable_list}",
                f"Call scm_agent.autonomy.acknowledge_pending(ledger, {record.id!r}, approved_by) "
                "to complete it (PR-7 wires this to POST /api/approvals/{id}).",
                f"Do so before the approval window closes at {expires_iso} -- after that it lapses "
                "and must be re-triggered.",
            ],
            data={
                "pending_id": record.id, "event_type": event.type, "sku": event.sku,
                "tool": route.tool, "expires_at": expires_iso,
            },
            risk_if_skipped=f"{context} stays unacted on until a human acknowledges it "
            f"(the one-click window closes at {expires_iso})",
        )
        return as_handoff(f"T2: {context} -- awaiting one-click acknowledgment.", [packet])

    # TIER_T3: full escalation, using the real nosilo_core/escalation.py machinery --
    # never a hand-rolled ESCALATED outcome, and never auto-notified as done
    # (handle_event_tiered() suppresses notify() for T3 same as T2).
    ledger.record(
        tier=TIER_T3, status=STATUS_ESCALATED, event_type=event.type, event_id=event.id,
        sku=event.sku, tool=route.tool, summary=context,
    )
    return escalate(
        f"T3: {context}",
        escalation.OPERATIONAL,
        f"{context} is routed T3 -- full escalation required, no autonomous action or notification.",
        recommendation=result.summary,
    )


def enforce_writeback_tier(
    store: writeback.InMemoryStore,
    changeset: writeback.Changeset,
    tier: str,
    *,
    approval: writeback.Approval | None = None,
    now: float | None = None,
) -> GuidedOutcome:
    """Gate an already-staged ``writeback.Changeset`` by autonomy tier.

    For the few tools whose report carries a real system-of-record write
    (``jobs/excel_replenishment_job.py``'s ``ExcelReplenishmentReport.changeset``,
    ``jobs/odoo_job.py``'s equivalent) instead of only a ``JobResult`` --
    ``enforce_analysis_tier`` above never sees this, because ``JobResult``
    itself carries no ``Changeset`` field.

    T3 escalates outright, before any apply is attempted. Staging itself
    (``writeback.stage()``) is a pure, side-effect-free dry-run computation
    with no write to the real system of record, so "escalate before even
    staging" (plan S5) and "escalate instead of applying an already-staged
    changeset" are behaviorally identical here: either way nothing is ever
    written until a human signs off.

    T2 stages only -- returns a HANDOFF naming the exact ``approve()`` +
    ``apply()`` call a human (or PR-7's endpoint) must make. This function
    never calls either for T2.

    T1 auto-applies (``writeback.apply(..., auto_apply_reversible=True)``)
    ONLY when ``changeset.risk_tier == writeback.TIER_REVERSIBLE``. Any other
    tier (irreversible, or the degenerate read tier) is downgraded to the
    SAME human-approval HANDOFF as T2. This is the safety invariant PR-6
    exists to enforce, and it is deliberately checked HERE, before ``apply()``
    is ever called, rather than relying solely on ``writeback.apply()`` to
    refuse: ``writeback.requires_approval()`` already hard-codes "irreversible
    always needs a human" independently of ``auto_apply_reversible`` (see
    ``nosilo_core/writeback.py``), so this check does not weaken or bypass that rule
    -- it is a second, independent layer in front of the same invariant. Even
    if this check were ever accidentally removed, ``apply()`` would still
    raise ``WritebackRefused`` instead of silently writing an irreversible
    change with ``approval=None``.
    """
    if tier not in VALID_AUTONOMY_TIERS:
        raise ValueError(f"unknown autonomy tier {tier!r} (must be one of {VALID_AUTONOMY_TIERS})")

    if tier == TIER_T3:
        return escalate(
            f"Writeback changeset for {changeset.target} routed T3.",
            escalation.OPERATIONAL,
            f"{changeset.summary()} requires full escalation before any write is attempted.",
        )

    handoff = as_handoff(
        f"Changeset staged for {changeset.target} -- awaiting explicit approval.",
        [HandoffPacket(
            title=f"Approve writeback: {changeset.target}",
            steps=[
                f"Review: {changeset.summary()}",
                "nosilo_core.writeback.approve(changeset, approved_by) then "
                "nosilo_core.writeback.apply(store, changeset, approval=approval) to commit.",
            ],
            data={"idempotency_key": changeset.idempotency_key, "risk_tier": changeset.risk_tier},
            risk_if_skipped=f"staged change to {changeset.target} never lands",
        )],
    )

    if tier == TIER_T2:
        return handoff

    # TIER_T1: irreversible (or non-reversible) changesets never auto-apply --
    # see the docstring above for why this is safe even in isolation.
    if changeset.risk_tier != writeback.TIER_REVERSIBLE:
        return handoff

    result = writeback.apply(store, changeset, approval=approval, now=now, auto_apply_reversible=True)
    verb = "Already applied (idempotent skip)" if result.idempotent_skip else "Auto-applied"
    return as_executed(f"{verb} writeback to {changeset.target} ({changeset.summary()}).")


@dataclass(frozen=True)
class TieredResult:
    """A full monitor -> route -> tier-enforced cycle's output (PR-6, A3).

    ``routed`` is exactly what ``event_intent.handle_event`` produced.
    ``outcome`` is the tier-enforced ``GuidedOutcome`` from
    ``enforce_analysis_tier``. ``ledger`` is the ``AutonomyLedger`` the cycle
    wrote to (or read a no-op default from), so a caller can look up the
    ``AutonomyRecord`` this cycle created via ``ledger.list_pending()`` /
    ``ledger.get(...)`` without this dataclass needing its own copy.
    """

    routed: RoutedResult
    outcome: GuidedOutcome
    ledger: AutonomyLedger


def handle_event_tiered(
    event: Event,
    *,
    routes: dict[str, Route] | None = None,
    routing_path: str | Path = DEFAULT_ROUTING_PATH,
    orchestrator: Orchestrator | None = None,
    out_dir: str | Path = "deliverables/agent",
    webhook_url: str | None = None,
    ledger: AutonomyLedger | None = None,
) -> TieredResult:
    """The full A1(sense, already emitted) -> A2(decide) -> A3(execute) cycle
    for one ``Event``: resolve its route, run the real orchestrator through
    ``event_intent.handle_event`` (suppressing its automatic ``notify()`` for
    every tier except T1 -- see ``notify_on_ok`` below), then gate the result
    through ``enforce_analysis_tier``.

    This is the wiring the plan's PR-6 acceptance criterion asks for: "T1 en
    banda se auto-ejecuta y audita; fuera de banda escala a T2 con Approval
    TTL" (plan table S12) -- a monitor-to-outcome cycle that actually
    respects T1/T2/T3, not just carries the label as inert data.

    Backward compatible by construction: this is a NEW function, not a
    change to ``event_intent.handle_event``'s existing behavior for any
    caller that does not care about tiers (that function's only change is
    one new keyword-only parameter, ``notify_on_ok``, defaulting to ``True``
    -- i.e. unchanged for every existing caller that does not pass it).
    """
    routes = routes if routes is not None else load_routing(routing_path)
    route = resolve_route(event, routes)
    ledger = ledger if ledger is not None else AutonomyLedger()

    routed = _handle_event(
        event, routes=routes, orchestrator=orchestrator, out_dir=out_dir, webhook_url=webhook_url,
        # T1 is the only tier where handle_event()'s own "ran ok -> notify()"
        # behavior should fire -- T2/T3 must never be announced as done
        # before a human has acknowledged/escalated it.
        notify_on_ok=(route.autonomy_tier == TIER_T1),
    )
    outcome = enforce_analysis_tier(routed, event, ledger=ledger)
    return TieredResult(routed=routed, outcome=outcome, ledger=ledger)
