"""T2->T1 autonomy promotion, evidence-gated by A4 -- and its mirror-image,
IMMEDIATE T1->T2 degradation (Linchpin 3.0 PR-9, plan section 5, Golden Rule
11).

Golden Rule 11: "la autonomia se gana con evidencia (N ciclos consecutivos
con precision >= umbral), nunca por edicion manual de config. La
degradacion T1->T2 SI es inmediata ante cualquier fallo." This module is
that rule as code, split along the same asymmetry the plan states:

  - :func:`evaluate_promotion` is PURE evidence evaluation: does a route's
    last N ``src.verify.reliability.ToolReliabilityReport`` cycles clear
    ``config/autonomy.yaml``'s bar? If so it returns a
    :class:`PromotionProposal` -- nothing is written anywhere yet.
    :func:`propose_promotion` persists that as a durable, PENDING
    :class:`PromotionRecord` a human can review (PR-7's Tower tab surfaces
    it). Only :func:`approve_promotion` -- an explicit human action --
    actually mutates ``config/event_routing.yaml``, and it does so as a
    real, signed :class:`src.writeback.Changeset`/``Approval``, never a
    bare file edit: "auditable como cualquier changeset" (plan section 5).

  - :func:`evaluate_degradation` looks at ONLY the single latest report (no
    N-cycle confirmation) for a currently-T1 route. When it fails,
    :func:`apply_degradation` mutates ``config/event_routing.yaml``
    IMMEDIATELY -- no human approval gate, because reducing autonomy is
    always safe to automate (Golden Rule 11 only gates GAINING it). It
    still never edits the file directly: it reuses the exact
    ``writeback.apply(..., auto_apply_reversible=True)`` path
    ``scm_agent.autonomy.enforce_writeback_tier`` already uses for a T1
    tool's own reversible auto-apply, so this is not a second, parallel
    "auto-write config" code path, and every application is still a
    genuine, audited ``Changeset``/``AuditEntry`` plus a durable
    :class:`PromotionRecord` -- "audited, not silent" applies here exactly
    as it does to a T1 analysis run (see ``scm_agent/autonomy.py``).

:class:`RoutingConfigStore` is the connector that makes both of the above
real ``src.writeback`` changesets against ``config/event_routing.yaml``:
it implements the same read/claim/release/commit/rollback surface as
``writeback.InMemoryStore`` / ``src.connectors.excel.ExcelWorkbookStore``,
rewriting only the exact ``autonomy_tier:`` token for one route (a scoped
regex substitution, not a YAML round-trip) so the file's hand-written
header comments and route-by-route documentation survive every promotion
or degradation untouched.

This module does not duplicate ``scm_agent/autonomy.py``'s T1/T2/T3
enforcement (``enforce_analysis_tier``/``enforce_writeback_tier``) -- it
reuses that module's tier constants and ``config/event_routing.yaml``
loader (``scm_agent.event_intent``) as its inputs, and produces the
evidence-gated *config change proposal* those functions read as data on
their next run. Like PR-1/PR-2/PR-6, there is no ``jobs/qa.py``-style
``verify_*``/``*_passed`` gate here: there is no new deliverable to QA, and
every ``GuidedOutcome`` returned is itself checked by
``src.guided.verify_guided`` at construction time.
"""

from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import tempfile
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import yaml

from src import writeback
from src.guided import GuidedOutcome, HandoffPacket, as_executed, as_handoff
from src.verify.reliability import ToolReliabilityReport

from .autonomy import DEFAULT_PATH, TIER_T1, TIER_T2
from .event_intent import DEFAULT_ROUTING_PATH, EventRoutingError, Route, load_routing

# Kinds a PromotionRecord can represent.
KIND_PROMOTION = "promotion"      # T2 -> T1, evidence-gated, human-approved
KIND_DEGRADATION = "degradation"  # T1 -> T2, immediate, auto-applied

# PromotionRecord.status values.
PROMOTION_STATUS_PENDING = "pending"            # promotion: awaiting a human approve()/reject()
PROMOTION_STATUS_APPROVED = "approved"          # promotion: a human approved it -- config was changed
PROMOTION_STATUS_REJECTED = "rejected"          # promotion: a human rejected it -- config untouched
PROMOTION_STATUS_AUTO_APPLIED = "auto_applied"  # degradation: applied immediately, no pending state

# Env-override convention matching scm_agent/autonomy.py's DEFAULT_PATH and
# scm_agent/event_intent.py's DEFAULT_ROUTING_PATH.
DEFAULT_AUTONOMY_CONFIG_PATH = (
    os.environ.get("LINCHPIN_AUTONOMY_CONFIG_PATH", "").strip() or "config/autonomy.yaml"
)


class AutonomyConfigError(RuntimeError):
    """Raised for anything malformed in ``config/autonomy.yaml``: missing
    file, missing ``promotion``/``degradation`` sections, or a value out of
    its valid range. A malformed threshold config must fail loudly at load
    time, not silently use a wrong number to gate a tier change."""


@dataclass(frozen=True)
class AutonomyConfig:
    """``config/autonomy.yaml``, parsed -- "ruteo es dato" extended to
    promotion/degradation tuning (see module docstring)."""

    min_consecutive_cycles: int
    min_precision: float
    degradation_floor: float


def load_autonomy_config(path: str | Path = DEFAULT_AUTONOMY_CONFIG_PATH) -> AutonomyConfig:
    """Parse ``config/autonomy.yaml`` into an :class:`AutonomyConfig`.

    Raises :class:`AutonomyConfigError` if the file is missing, not valid
    YAML, missing a required field, or a field is out of range.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise AutonomyConfigError(f"{path}: cannot read autonomy config: {exc}") from exc
    try:
        raw = yaml.safe_load(text) or {}
    except yaml.YAMLError as exc:
        raise AutonomyConfigError(f"{path}: invalid YAML: {exc}") from exc

    promotion = raw.get("promotion") or {}
    degradation = raw.get("degradation") or {}
    try:
        min_cycles = int(promotion["min_consecutive_cycles"])
        min_precision = float(promotion["min_precision"])
        degradation_floor = float(degradation["min_precision_floor"])
    except (KeyError, TypeError, ValueError) as exc:
        raise AutonomyConfigError(
            f"{path}: missing/invalid promotion.min_consecutive_cycles, promotion.min_precision, "
            "or degradation.min_precision_floor"
        ) from exc

    if min_cycles < 1:
        raise AutonomyConfigError(f"{path}: promotion.min_consecutive_cycles must be >= 1")
    if not 0.0 < min_precision <= 1.0:
        raise AutonomyConfigError(f"{path}: promotion.min_precision must be in (0, 1]")
    if not 0.0 <= degradation_floor <= 1.0:
        raise AutonomyConfigError(f"{path}: degradation.min_precision_floor must be in [0, 1]")

    return AutonomyConfig(
        min_consecutive_cycles=min_cycles, min_precision=min_precision, degradation_floor=degradation_floor,
    )


# ---- pure evaluation: evidence in, a candidate transition out -----------------


@dataclass(frozen=True)
class PromotionProposal:
    """A PURE, evidence-gated T2->T1 promotion candidate. Nothing here is
    persisted or applied -- :func:`propose_promotion` does that."""

    event_type: str
    tool: str
    from_tier: str
    to_tier: str
    evidence: tuple[ToolReliabilityReport, ...]
    rationale: str


@dataclass(frozen=True)
class DegradationFlag:
    """A PURE, IMMEDIATE T1->T2 degradation candidate -- a single bad
    reliability report, never held for N-cycle confirmation."""

    event_type: str
    tool: str
    from_tier: str
    to_tier: str
    evidence: tuple[ToolReliabilityReport, ...]
    rationale: str


def evaluate_promotion(
    route: Route,
    recent_reports: list[ToolReliabilityReport],
    *,
    config: AutonomyConfig | None = None,
) -> PromotionProposal | None:
    """Evidence-gated T2->T1 promotion candidate for ``route`` (Golden Rule 11).

    ``recent_reports`` must be in chronological order, OLDEST FIRST -- one
    ``ToolReliabilityReport`` per cycle/period (e.g. one per backtest
    window) for ``route.tool``. Only the LAST ``config.min_consecutive_cycles``
    entries are examined. Returns ``None`` when: ``route`` is already T1
    (nothing to promote), fewer than N cycles of history exist yet, or any
    of the last N cycles' ``headline_precision`` is missing (``None``, "no
    verifiable signal") or below ``config.min_precision``.

    Raises ``ValueError`` if any report in ``recent_reports`` names a tool
    other than ``route.tool`` -- mixing evidence across tools would let a
    promotion be silently justified by someone else's numbers.
    """
    cfg = config if config is not None else load_autonomy_config()
    if any(r.tool != route.tool for r in recent_reports):
        raise ValueError(f"recent_reports must all be for tool {route.tool!r}")
    if route.autonomy_tier == TIER_T1:
        return None

    n = cfg.min_consecutive_cycles
    if len(recent_reports) < n:
        return None
    tail = tuple(recent_reports[-n:])
    if not all(r.headline_precision is not None and r.headline_precision >= cfg.min_precision for r in tail):
        return None

    precisions = ", ".join(f"{r.headline_precision:.1%}" for r in tail)
    rationale = (
        f"{route.tool}: {n} consecutive cycles at/above {cfg.min_precision:.0%} precision ({precisions}) "
        f"-- evidence-gated promotion candidate {route.autonomy_tier} -> {TIER_T1} (plan Golden Rule 11)."
    )
    return PromotionProposal(
        event_type=route.event_type, tool=route.tool, from_tier=route.autonomy_tier, to_tier=TIER_T1,
        evidence=tail, rationale=rationale,
    )


def evaluate_degradation(
    route: Route,
    latest_report: ToolReliabilityReport,
    *,
    config: AutonomyConfig | None = None,
) -> DegradationFlag | None:
    """IMMEDIATE T1->T2 degradation candidate from a SINGLE bad reliability
    report -- no N-cycle confirmation (asymmetric vs :func:`evaluate_promotion`;
    plan section 5: "la degradacion T1->T2 SI es inmediata ante cualquier fallo").

    Returns ``None`` when ``route`` is not currently T1 (degradation only
    ever fires T1->T2) or ``latest_report`` clears the floor (including an
    honest "no signal yet" report with fewer than 2 matched observations --
    that is not itself a failure). Raises ``ValueError`` if
    ``latest_report.tool != route.tool``.
    """
    cfg = config if config is not None else load_autonomy_config()
    if latest_report.tool != route.tool:
        raise ValueError(f"latest_report is for tool {latest_report.tool!r}, not route.tool {route.tool!r}")
    if route.autonomy_tier != TIER_T1:
        return None

    failed = (
        latest_report.headline_precision is not None
        and latest_report.headline_precision < cfg.degradation_floor
    )
    if not failed:
        return None

    rationale = (
        f"{route.tool}: latest reliability cycle at {latest_report.headline_precision:.1%} precision -- "
        f"below the {cfg.degradation_floor:.0%} degradation floor. Immediate {TIER_T1} -> {TIER_T2} "
        "degradation (no N-cycle confirmation; plan section 5, Golden Rule 11)."
    )
    return DegradationFlag(
        event_type=route.event_type, tool=route.tool, from_tier=TIER_T1, to_tier=TIER_T2,
        evidence=(latest_report,), rationale=rationale,
    )


# ---- durable evidence: a PromotionRecord a human (or the auto-degrade path) --
# ---- can act on ---------------------------------------------------------------


def _ensure_utc(dt: datetime) -> datetime:
    """Treat a naive datetime as already-UTC; convert an aware one to UTC.
    Mirrors ``scm_agent.autonomy``'s own helper (not imported -- two lines,
    kept local rather than reaching into that module's private surface)."""
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _report_to_evidence_dict(report: ToolReliabilityReport) -> dict:
    """A JSON-safe snapshot of the numbers that justified a transition --
    what a human reviewing a pending promotion (or auditing a past
    degradation) actually sees. Non-finite floats (``mean_wape``/``mean_bias``
    can be ``+inf``/``nan`` per ``src/verify/backtest.py``'s own convention)
    are stringified rather than passed through raw, so this is always safe
    to re-serialize through an ``allow_nan=False`` JSON boundary later
    (``webapp/app.py``'s ``SafeJSONResponse``) without losing the value."""
    return {
        "tool": report.tool,
        "n_decisions": report.n_decisions,
        "n_skus": report.n_skus,
        "n_hits": report.n_hits,
        "n_excluded_zero_actual": report.n_excluded_zero_actual,
        "hit_rate": report.hit_rate,
        "mean_wape": report.mean_wape if math.isfinite(report.mean_wape) else str(report.mean_wape),
        "mean_bias": report.mean_bias if math.isfinite(report.mean_bias) else str(report.mean_bias),
        "headline_precision": report.headline_precision,
        "meets_threshold": report.meets_threshold,
        "threshold": report.threshold,
    }


@dataclass(frozen=True)
class PromotionRecord:
    """One persisted tier-transition decision -- a T2->T1 promotion PENDING
    a human's ``approve_promotion()``/``reject_promotion()`` call, or a
    T1->T2 degradation already ``PROMOTION_STATUS_AUTO_APPLIED`` (there is
    no pending state for a degradation to sit in)."""

    id: str
    kind: str
    status: str
    event_type: str
    tool: str
    from_tier: str
    to_tier: str
    rationale: str
    evidence: tuple[dict, ...]
    created_at: datetime
    resolved_by: str | None = None
    resolved_at: datetime | None = None


def _row_to_promotion_record(row: tuple) -> PromotionRecord:
    (id_, kind, status, event_type, tool, from_tier, to_tier, rationale, evidence_json, created_at,
     resolved_by, resolved_at) = row
    return PromotionRecord(
        id=id_, kind=kind, status=status, event_type=event_type, tool=tool, from_tier=from_tier,
        to_tier=to_tier, rationale=rationale, evidence=tuple(json.loads(evidence_json)),
        created_at=datetime.fromisoformat(created_at), resolved_by=resolved_by,
        resolved_at=datetime.fromisoformat(resolved_at) if resolved_at else None,
    )


class PromotionLedger:
    """SQLite-backed audit trail for A4-driven tier-transition decisions --
    alongside ``scm_agent.autonomy.AutonomyLedger`` (T1/T2/T3 routed-event
    decisions), ``scm_agent.events.EventLedger``, and
    ``src.writeback_store.SqliteAuditLedger``.

    Shares ``scm_agent.autonomy.AutonomyLedger``'s file convention (same
    ``DEFAULT_PATH``/``LINCHPIN_AUTONOMY_PATH`` env var by default) so one
    ``autonomy.sqlite3`` holds every A3 decision AND every A4-driven tier
    change -- but keeps its OWN table and connection rather than growing
    ``AutonomyLedger``'s schema: a tier-transition candidate is a decision
    ABOUT a route's tier, not a T1/T2/T3 routed-event decision, and the two
    tables have no columns in common beyond the shared audit shape
    (id/status/created_at). ``path`` is injectable so tests never touch a
    real data directory (pass ``":memory:"``), matching ``AutonomyLedger``.
    """

    def __init__(self, path: str | Path = DEFAULT_PATH) -> None:
        self._path = str(path)
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._path, timeout=30.0)
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS promotion_proposals ("
            " id TEXT PRIMARY KEY,"
            " kind TEXT NOT NULL,"
            " status TEXT NOT NULL,"
            " event_type TEXT NOT NULL,"
            " tool TEXT NOT NULL,"
            " from_tier TEXT NOT NULL,"
            " to_tier TEXT NOT NULL,"
            " rationale TEXT NOT NULL,"
            " evidence_json TEXT NOT NULL,"
            " created_at TEXT NOT NULL,"
            " resolved_by TEXT,"
            " resolved_at TEXT"
            ")"
        )
        self._conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_promotion_status ON promotion_proposals(status)"
        )
        self._conn.commit()

    def record(
        self,
        *,
        kind: str,
        status: str,
        event_type: str,
        tool: str,
        from_tier: str,
        to_tier: str,
        rationale: str,
        evidence: tuple[ToolReliabilityReport, ...],
        resolved_by: str | None = None,
        now: datetime | None = None,
    ) -> PromotionRecord:
        """Write one row and return it. ``resolved_by`` set at record time
        (only the degradation path does this -- there is no pending state
        for an auto-applied degradation) also stamps ``resolved_at``."""
        created_at = _ensure_utc(now) if now is not None else datetime.now(timezone.utc)
        resolved_at = created_at if resolved_by is not None else None
        record_id = uuid.uuid4().hex
        evidence_json = json.dumps([_report_to_evidence_dict(r) for r in evidence])
        self._conn.execute(
            "INSERT INTO promotion_proposals"
            " (id, kind, status, event_type, tool, from_tier, to_tier, rationale, evidence_json,"
            "  created_at, resolved_by, resolved_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (record_id, kind, status, event_type, tool, from_tier, to_tier, rationale, evidence_json,
             created_at.isoformat(), resolved_by, resolved_at.isoformat() if resolved_at else None),
        )
        self._conn.commit()
        return PromotionRecord(
            id=record_id, kind=kind, status=status, event_type=event_type, tool=tool, from_tier=from_tier,
            to_tier=to_tier, rationale=rationale, evidence=tuple(json.loads(evidence_json)),
            created_at=created_at, resolved_by=resolved_by, resolved_at=resolved_at,
        )

    def get(self, record_id: str) -> PromotionRecord | None:
        row = self._conn.execute(
            "SELECT id, kind, status, event_type, tool, from_tier, to_tier, rationale, evidence_json,"
            " created_at, resolved_by, resolved_at FROM promotion_proposals WHERE id = ?",
            (record_id,),
        ).fetchone()
        return _row_to_promotion_record(row) if row else None

    def list_pending(self) -> list[PromotionRecord]:
        """Every promotion still awaiting a human decision, oldest first --
        what PR-7's Tower tab lists as pending T2->T1 proposals."""
        rows = self._conn.execute(
            "SELECT id, kind, status, event_type, tool, from_tier, to_tier, rationale, evidence_json,"
            " created_at, resolved_by, resolved_at FROM promotion_proposals WHERE status = ? "
            "ORDER BY rowid ASC",
            (PROMOTION_STATUS_PENDING,),
        ).fetchall()
        return [_row_to_promotion_record(r) for r in rows]

    def list_all(self) -> list[PromotionRecord]:
        """Every recorded tier-transition decision, oldest first -- the
        full A4-driven promotion/degradation audit trail."""
        rows = self._conn.execute(
            "SELECT id, kind, status, event_type, tool, from_tier, to_tier, rationale, evidence_json,"
            " created_at, resolved_by, resolved_at FROM promotion_proposals ORDER BY rowid ASC"
        ).fetchall()
        return [_row_to_promotion_record(r) for r in rows]

    def resolve(
        self, record_id: str, resolved_by: str, status: str, *, now: datetime | None = None,
    ) -> PromotionRecord:
        """Flip a PENDING record to ``status`` (approved/rejected).

        Raises ``KeyError`` if ``record_id`` is unknown, or ``ValueError``
        if it is not currently pending -- a resolution must be a loud,
        actionable failure when it cannot legitimately apply, matching
        ``AutonomyLedger.acknowledge``'s convention.
        """
        existing = self.get(record_id)
        if existing is None:
            raise KeyError(f"no promotion record with id {record_id!r}")
        if existing.status != PROMOTION_STATUS_PENDING:
            raise ValueError(
                f"promotion record {record_id!r} is not pending (status={existing.status!r}) -- "
                "only a pending promotion can be approved or rejected"
            )
        resolved_at = _ensure_utc(now) if now is not None else datetime.now(timezone.utc)
        self._conn.execute(
            "UPDATE promotion_proposals SET status = ?, resolved_by = ?, resolved_at = ? WHERE id = ?",
            (status, resolved_by, resolved_at.isoformat(), record_id),
        )
        self._conn.commit()
        return self.get(record_id)

    def close(self) -> None:
        self._conn.close()


def propose_promotion(
    ledger: PromotionLedger, proposal: PromotionProposal, *, now: datetime | None = None,
) -> PromotionRecord:
    """Persist a :class:`PromotionProposal` as a PENDING record -- durable
    evidence a human can review and approve/reject later (Golden Rule 11:
    never a silent config edit). Does NOT touch
    ``config/event_routing.yaml``; only :func:`approve_promotion` does."""
    return ledger.record(
        kind=KIND_PROMOTION, status=PROMOTION_STATUS_PENDING, event_type=proposal.event_type,
        tool=proposal.tool, from_tier=proposal.from_tier, to_tier=proposal.to_tier,
        rationale=proposal.rationale, evidence=proposal.evidence, now=now,
    )


# ---- the one real config mutation: a src.writeback.Changeset against ---------
# ---- config/event_routing.yaml's autonomy_tier field --------------------------


def _rewrite_route_tier(text: str, event_type: str, new_tier: str) -> tuple[str, str]:
    """Rewrite exactly one route's ``autonomy_tier:`` VALUE in raw routing-YAML
    ``text`` -- a scoped regex substitution, not a ``yaml.safe_load``/``dump``
    round-trip, so every hand-written header/route comment in
    ``config/event_routing.yaml`` survives untouched (round-tripping through
    PyYAML would silently drop them). Returns ``(new_text, old_tier)``.

    Matches ``  {event_type}:`` (2-space indent, the route's own key) followed
    by zero or more 4-space-indented ``key: value`` lines (non-greedy, so it
    stops at the FIRST ``autonomy_tier:`` it reaches -- any line without the
    4-space indent, e.g. the blank line ending the block, breaks the match)
    up to ``    autonomy_tier: ``, capturing only the tier token itself.

    Raises :class:`~scm_agent.event_intent.EventRoutingError` if ``event_type``
    has no route in ``text``, or the route's ``autonomy_tier:`` line matches
    more than once (an ambiguous rewrite target -- refuse rather than guess).
    """
    pattern = re.compile(
        r"(?m)^(  " + re.escape(event_type) + r":\n(?:    [^\n]*\n)*?    autonomy_tier: )([A-Za-z0-9_]+)"
    )
    matches = list(pattern.finditer(text))
    if not matches:
        raise EventRoutingError(f"no route {event_type!r} found to rewrite in routing config")
    if len(matches) > 1:
        raise EventRoutingError(f"route {event_type!r} matched more than once -- refusing an ambiguous rewrite")
    match = matches[0]
    old_tier = match.group(2)
    new_text = text[: match.start(2)] + new_tier + text[match.end(2):]
    return new_text, old_tier


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` via temp-file-then-``os.replace`` -- a crash
    or a locked file can never leave a half-written routing config (same
    atomicity property ``src/connectors/excel.py``'s ``ExcelWorkbookStore``
    uses for its own writes)."""
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".autonomy-tmp-", suffix=".yaml")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        try:
            os.remove(tmp_name)
        except OSError:
            pass
        raise


class RoutingConfigStore:
    """``src.writeback`` system-of-record surface over
    ``config/event_routing.yaml``'s per-route ``autonomy_tier`` field --
    reuses the SAME safe-staging plane (stage/approve/apply/rollback) every
    other writeback-capable tool uses, for the one config mutation this
    module ever performs (Golden Rule 11: a promotion is a human-approved,
    auditable changeset, never a manual config edit).

    Entity id = ``event_type``; the only field ever staged is
    ``"autonomy_tier"``. Mirrors ``writeback.InMemoryStore`` /
    ``src.connectors.excel.ExcelWorkbookStore``'s read/claim/release/commit/
    rollback surface exactly, composing ``writeback.AuditBookkeeping`` for
    the claim/release/idempotency machinery instead of re-deriving it.
    """

    def __init__(self, path: str | Path = DEFAULT_ROUTING_PATH, *, ledger: object | None = None) -> None:
        self._path = Path(path)
        self._audit = writeback.AuditBookkeeping(ledger)

    def read(self, event_type: str) -> dict:
        routes = load_routing(self._path)
        route = routes.get(event_type)
        return {"autonomy_tier": route.autonomy_tier} if route is not None else {}

    def applied_keys(self) -> set[str]:
        return self._audit.applied_keys()

    def claim(self, idempotency_key: str, *, now: float | None = None) -> bool:
        return self._audit.claim(idempotency_key, now=now)

    def release(self, idempotency_key: str) -> None:
        self._audit.release(idempotency_key)

    def commit(self, changeset: writeback.Changeset, approved_by: str) -> writeback.AuditEntry:
        text = self._path.read_text(encoding="utf-8")
        restore: list[tuple[str, str, object]] = []
        for change in changeset.changes:
            if change.is_noop:
                continue
            text, old_tier = _rewrite_route_tier(text, change.entity_id, str(change.after))
            restore.append((change.entity_id, change.field, old_tier))
        _atomic_write_text(self._path, text)
        entry = writeback.AuditEntry(changeset.idempotency_key, changeset.target, approved_by, tuple(restore))
        self._audit.record(entry)
        return entry

    def rollback(self, idempotency_key: str) -> None:
        entry = self._audit.get(idempotency_key)
        if entry is None:
            raise KeyError(idempotency_key)
        text = self._path.read_text(encoding="utf-8")
        for entity_id, _field, original in entry.restore:
            text, _ = _rewrite_route_tier(text, entity_id, str(original))
        _atomic_write_text(self._path, text)
        self._audit.forget(idempotency_key)


def approve_promotion(
    ledger: PromotionLedger,
    record_id: str,
    approved_by: str,
    *,
    routing_path: str | Path = DEFAULT_ROUTING_PATH,
    now: float | None = None,
) -> GuidedOutcome:
    """The human sign-off Golden Rule 11 requires: apply a PENDING
    promotion's tier change to ``config/event_routing.yaml`` as a real,
    signed :class:`src.writeback.Changeset`/``Approval`` -- never a bare
    file edit. This is what actually makes a SUBSEQUENT event of
    ``record.event_type`` route T1 (any :class:`~scm_agent.event_intent.Route`
    resolved before this call keeps its old tier -- routes are frozen
    dataclasses read from the file at load time, matching
    ``config/event_routing.yaml``'s "ruteo como dato" convention: reload it
    to see the change).

    Raises ``KeyError``/``ValueError`` (from :meth:`PromotionLedger.resolve`)
    for an unknown or already-resolved id, or ``ValueError`` if the route's
    CURRENT tier in the file no longer matches the proposal's recorded
    ``from_tier`` -- the config changed since the proposal was created (e.g.
    a second approval, or a manual edit), and approving against a stale
    expectation must fail loudly rather than silently overwrite whatever is
    there now.
    """
    record = ledger.get(record_id)
    if record is None:
        raise KeyError(f"no promotion record with id {record_id!r}")
    if record.status != PROMOTION_STATUS_PENDING:
        raise ValueError(
            f"promotion record {record_id!r} is not pending (status={record.status!r}) -- "
            "only a pending promotion can be approved"
        )

    store = RoutingConfigStore(routing_path)
    current = store.read(record.event_type)
    if current.get("autonomy_tier") != record.from_tier:
        raise ValueError(
            f"route {record.event_type!r} is now {current.get('autonomy_tier')!r}, not the proposal's "
            f"expected {record.from_tier!r} -- re-evaluate before approving (config changed since proposal)"
        )

    changeset = writeback.stage(
        store, str(routing_path), {record.event_type: {"autonomy_tier": record.to_tier}},
        risk_tier=writeback.TIER_REVERSIBLE, idempotency_key=f"promote-{record.id}", reason=record.rationale,
    )
    approval = writeback.approve(changeset, approved_by, now=now)
    result = writeback.apply(store, changeset, approval=approval, now=now)
    resolved_at = _ensure_utc(datetime.fromtimestamp(now, tz=timezone.utc)) if now is not None else None
    ledger.resolve(record.id, approved_by, PROMOTION_STATUS_APPROVED, now=resolved_at)

    verb = "already applied (idempotent skip)" if result.idempotent_skip else "applied"
    return as_executed(
        f"Promotion approved by {approved_by}: {record.event_type} ({record.tool}) "
        f"{record.from_tier} -> {record.to_tier} {verb}."
    )


def reject_promotion(
    ledger: PromotionLedger, record_id: str, rejected_by: str, *, now: datetime | None = None,
) -> PromotionRecord:
    """Mark a PENDING promotion as rejected -- ``config/event_routing.yaml``
    is never touched. Raises the same ``KeyError``/``ValueError`` as
    :meth:`PromotionLedger.resolve` for an unknown or already-resolved id."""
    return ledger.resolve(record_id, rejected_by, PROMOTION_STATUS_REJECTED, now=now)


def apply_degradation(
    ledger: PromotionLedger,
    flag: DegradationFlag,
    *,
    routing_path: str | Path = DEFAULT_ROUTING_PATH,
    now: float | None = None,
) -> GuidedOutcome:
    """IMMEDIATE T1->T2 degradation -- no human approval gate (reducing
    autonomy is always safe to automate; Golden Rule 11 only gates GAINING
    it). Reuses ``writeback.apply(..., auto_apply_reversible=True)`` -- the
    exact mechanism ``scm_agent.autonomy.enforce_writeback_tier`` already
    uses for a T1 tool's own reversible auto-apply -- so this is not a
    second, parallel "auto-write config" code path.

    Still fully audited: a durable, already-resolved :class:`PromotionRecord`
    (``kind=KIND_DEGRADATION`` -- there is no pending state to approve) AND
    a real ``writeback.Changeset``/``AuditEntry``, never a silent file edit.

    Race-safe: if the route is no longer ``flag.from_tier`` by the time this
    runs (e.g. two callers evaluated the same stale route, or a human
    degraded it manually via some other path first), this is a no-op
    ``EXECUTED`` outcome -- nothing is written, and nothing is recorded as a
    second degradation of an already-degraded route.
    """
    store = RoutingConfigStore(routing_path)
    current = store.read(flag.event_type)
    if current.get("autonomy_tier") != flag.from_tier:
        return as_executed(
            f"{flag.event_type} is already {current.get('autonomy_tier')!r} -- no degradation needed."
        )

    ts = _ensure_utc(
        datetime.fromtimestamp(now, tz=timezone.utc) if now is not None else datetime.now(timezone.utc)
    )
    # Unique per occurrence (NOT a fixed "degrade-<event_type>" key): a route
    # can be promoted back to T1 later and degrade again in the future, and a
    # reused idempotency_key would make writeback.apply() treat that second,
    # genuinely different degradation as an idempotent skip of the first.
    idempotency_key = f"degrade-{flag.event_type}-{ts.isoformat()}"

    changeset = writeback.stage(
        store, str(routing_path), {flag.event_type: {"autonomy_tier": flag.to_tier}},
        risk_tier=writeback.TIER_REVERSIBLE, idempotency_key=idempotency_key, reason=flag.rationale,
    )
    result = writeback.apply(store, changeset, approval=None, now=now, auto_apply_reversible=True)
    ledger.record(
        kind=KIND_DEGRADATION, status=PROMOTION_STATUS_AUTO_APPLIED, event_type=flag.event_type,
        tool=flag.tool, from_tier=flag.from_tier, to_tier=flag.to_tier, rationale=flag.rationale,
        evidence=flag.evidence, resolved_by="system(auto-degrade)", now=ts,
    )

    verb = "already applied (idempotent skip)" if result.idempotent_skip else "applied"
    return as_executed(
        f"Immediate degradation {verb}: {flag.event_type} ({flag.tool}) {flag.from_tier} -> {flag.to_tier}. "
        f"{flag.rationale}"
    )


def evaluate_tier_transition(
    route: Route,
    recent_reports: list[ToolReliabilityReport],
    *,
    ledger: PromotionLedger | None = None,
    config: AutonomyConfig | None = None,
    routing_path: str | Path = DEFAULT_ROUTING_PATH,
    now: float | None = None,
) -> GuidedOutcome | None:
    """One-call A4-driven tier-transition cycle for one route -- what a
    scheduled job (mirroring ``scm_agent.autonomy.handle_event_tiered``'s
    role for A1-A3) calls once per route per cycle:

      - **T1 route**: checks ONLY the latest report (``recent_reports[-1]``)
        via :func:`evaluate_degradation` -- and, if flagged, applies it
        immediately via :func:`apply_degradation` (no human step).
      - **T2/T3 route**: checks the FULL ``recent_reports`` history via
        :func:`evaluate_promotion` -- and, if satisfied, stages it as a
        PENDING proposal a human must :func:`approve_promotion` (the config
        change itself does NOT happen here).

    Returns ``None`` when neither applies this cycle (nothing new to do),
    or ``recent_reports`` is empty for a T1 route (no evidence at all is not
    itself a failure).
    """
    cfg = config if config is not None else load_autonomy_config()
    ledger = ledger if ledger is not None else PromotionLedger()

    if route.autonomy_tier == TIER_T1:
        if not recent_reports:
            return None
        flag = evaluate_degradation(route, recent_reports[-1], config=cfg)
        if flag is None:
            return None
        return apply_degradation(ledger, flag, routing_path=routing_path, now=now)

    proposal = evaluate_promotion(route, recent_reports, config=cfg)
    if proposal is None:
        return None
    record = propose_promotion(ledger, proposal)
    return as_handoff(
        f"T2->T1 promotion proposed for {proposal.event_type} ({proposal.tool}) -- {proposal.rationale}",
        [HandoffPacket(
            title=f"Approve autonomy promotion: {proposal.event_type}",
            steps=[
                f"Review the evidence: {proposal.rationale}",
                f"Call scm_agent.autonomy_promotion.approve_promotion(ledger, {record.id!r}, approved_by) "
                "to apply it (PR-7's Tower tab wires this to a one-click approval).",
            ],
            data={"promotion_id": record.id, "event_type": proposal.event_type, "tool": proposal.tool},
            risk_if_skipped=f"{proposal.event_type} stays at {proposal.from_tier} until a human approves.",
        )],
    )
