"""Retention policy engine -- ``src/retention.py``.

One module, one question: *given a policy and a clock, what is expired and may
it be deleted?* Everything here is pure -- no sqlite3, no filesystem, no
``datetime.now()``. The clock enters as an argument exactly like
``Deliverable.prepared`` and ``StateStore.append_snapshot(now=...)`` do, so a
test pins a date instead of sleeping, and the same policy evaluated twice at
the same instant always decides the same thing.

The I/O half -- reading ``config/retention.yaml``, asking each store how many
rows it holds, and actually deleting -- is ``jobs/retention_job.py``. That
split is why this module can be exercised without a data directory.

Why a policy engine and not three ``DELETE FROM ... WHERE ts < ?`` statements:
the interesting part of retention is not the delete, it is refusing to do the
delete when the numbers look wrong. A rule whose ``retention_days`` was
mistyped, or that runs for the first time against a store which predates any
policy, will find nearly everything expired. A plain DELETE happily removes
it. :func:`decide` classifies that case as ``blocked_bulk`` and hands it to a
human instead -- the same never-unprotected contract ``nosilo_core.writeback``
applies to irreversible changes, which is what a deletion is.

Statuses returned by :func:`decide`, mirroring ``GuidedOutcome``'s vocabulary:

``disabled``        the rule exists but is switched off; nothing was examined
``nothing_expired`` the rule ran and found no row past its cutoff
``delete``          go ahead, ``expired_rows`` may be removed
``blocked_bulk``    too large a share is expired; needs ``confirm_bulk``
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

# Env-override convention matching src/state/store.py's LINCHPIN_STATE_PATH and
# scm_agent/monitors.py's LINCHPIN_MONITORS_CONFIG_PATH.
DEFAULT_POLICY_PATH = os.environ.get("LINCHPIN_RETENTION_POLICY_PATH", "").strip() or "config/retention.yaml"

# Used when config/retention.yaml's `defaults` block omits max_delete_ratio.
# Conservative on purpose: a store shedding more than a quarter of itself in
# one scheduled run is worth a human glance even when it turns out to be fine.
DEFAULT_MAX_DELETE_RATIO = 0.25

STATUS_DISABLED = "disabled"
STATUS_NOTHING_EXPIRED = "nothing_expired"
STATUS_DELETE = "delete"
STATUS_BLOCKED_BULK = "blocked_bulk"


@dataclass(frozen=True)
class RetentionRule:
    """One store's retention policy, as declared in ``config/retention.yaml``.

    ``name`` is the join key to a pruner in ``jobs/retention_job.py``; a rule
    naming a store nothing knows how to prune is a configuration error, not a
    silent no-op (see that module's ``PRUNERS``).

    ``retention_days`` must be a positive integer. There is deliberately no
    "keep forever" sentinel: a store that should never be pruned simply has no
    rule, which is easier to read than ``retention_days: null`` and impossible
    to confuse with "the value is missing".
    """

    name: str
    retention_days: int
    enabled: bool = False
    confirm_bulk: bool = False
    max_delete_ratio: float = DEFAULT_MAX_DELETE_RATIO
    description: str = ""

    def cutoff(self, now: datetime) -> datetime:
        """The instant before which a row counts as expired.

        ``now`` may be naive (treated as UTC) or aware (converted to UTC), the
        same normalization ``scm_agent.events._ensure_utc`` applies, so a
        caller passing ``datetime.utcnow()`` and one passing an aware UTC clock
        get identical cutoffs instead of a silent one-offset drift.
        """
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        else:
            now = now.astimezone(timezone.utc)
        return now - timedelta(days=self.retention_days)


@dataclass(frozen=True)
class RetentionDecision:
    """What :func:`decide` concluded for one rule at one instant."""

    rule: RetentionRule
    status: str
    cutoff: datetime | None
    total_rows: int
    expired_rows: int
    reason: str

    @property
    def may_delete(self) -> bool:
        return self.status == STATUS_DELETE


def _require_positive_int(value: Any, field: str, rule_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"retention rule '{rule_name}': {field} must be an integer, got {value!r}")
    if value <= 0:
        raise ValueError(f"retention rule '{rule_name}': {field} must be > 0, got {value}")
    return value


def _require_ratio(value: Any, rule_name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"retention rule '{rule_name}': max_delete_ratio must be a number, got {value!r}")
    ratio = float(value)
    if not 0.0 < ratio <= 1.0:
        raise ValueError(f"retention rule '{rule_name}': max_delete_ratio must be in (0, 1], got {ratio}")
    return ratio


def _require_bool(value: Any, field: str, rule_name: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"retention rule '{rule_name}': {field} must be true or false, got {value!r}")
    return value


def parse_policy(raw: Any) -> tuple[RetentionRule, ...]:
    """Validate a parsed ``retention.yaml`` mapping into ordered rules.

    Raises ``ValueError`` on anything malformed rather than defaulting around
    it: a typo in a file whose whole job is bounding deletion should stop the
    job, not quietly widen or narrow a window. Duplicate rule names are an
    error for the same reason ``JobRegistry.register`` rejects duplicate ids --
    the second one would silently win.
    """
    if not isinstance(raw, dict):
        raise ValueError(f"retention policy must be a mapping, got {type(raw).__name__}")

    defaults = raw.get("defaults") or {}
    if not isinstance(defaults, dict):
        raise ValueError(f"retention policy: 'defaults' must be a mapping, got {type(defaults).__name__}")
    default_ratio = _require_ratio(defaults.get("max_delete_ratio", DEFAULT_MAX_DELETE_RATIO), "defaults")

    entries = raw.get("rules")
    if entries is None:
        return ()
    if not isinstance(entries, list):
        raise ValueError(f"retention policy: 'rules' must be a list, got {type(entries).__name__}")

    rules: list[RetentionRule] = []
    seen: set[str] = set()
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"retention policy: rule #{index} must be a mapping, got {type(entry).__name__}")
        name = entry.get("name")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"retention policy: rule #{index} needs a non-empty 'name'")
        name = name.strip()
        if name in seen:
            raise ValueError(f"retention policy: duplicate rule name '{name}'")
        seen.add(name)

        rules.append(
            RetentionRule(
                name=name,
                retention_days=_require_positive_int(entry.get("retention_days"), "retention_days", name),
                enabled=_require_bool(entry.get("enabled", False), "enabled", name),
                confirm_bulk=_require_bool(entry.get("confirm_bulk", False), "confirm_bulk", name),
                max_delete_ratio=_require_ratio(entry.get("max_delete_ratio", default_ratio), name),
                description=str(entry.get("description") or "").strip(),
            )
        )
    return tuple(rules)


def load_policy(path: str | Path = DEFAULT_POLICY_PATH) -> tuple[RetentionRule, ...]:
    """Read and validate ``path``. A missing file yields no rules.

    Missing-is-empty rather than missing-is-an-error because a deployment that
    has not opted into retention yet should still boot; an empty policy deletes
    nothing, which is the safe direction to fail in. PyYAML is a core runtime
    dependency (``requirements.txt``), so this needs no lazy-import guard.
    """
    import yaml

    policy_path = Path(path)
    if not policy_path.exists():
        return ()
    with policy_path.open("r", encoding="utf-8") as handle:
        return parse_policy(yaml.safe_load(handle) or {})


def decide(rule: RetentionRule, *, now: datetime, total_rows: int, expired_rows: int) -> RetentionDecision:
    """Classify one rule against the counts a store just reported.

    ``expired_rows`` is how many rows sit before ``rule.cutoff(now)``;
    ``total_rows`` is how many the store holds in total. Both come from the
    caller because this module never opens a database.
    """
    if total_rows < 0 or expired_rows < 0:
        raise ValueError(f"retention rule '{rule.name}': row counts must be >= 0")
    if expired_rows > total_rows:
        raise ValueError(
            f"retention rule '{rule.name}': expired_rows ({expired_rows}) exceeds total_rows ({total_rows})"
        )

    if not rule.enabled:
        return RetentionDecision(
            rule=rule,
            status=STATUS_DISABLED,
            cutoff=None,
            total_rows=total_rows,
            expired_rows=0,
            reason="rule is disabled in config/retention.yaml; nothing examined",
        )

    cutoff = rule.cutoff(now)

    if expired_rows == 0:
        return RetentionDecision(
            rule=rule,
            status=STATUS_NOTHING_EXPIRED,
            cutoff=cutoff,
            total_rows=total_rows,
            expired_rows=0,
            reason=f"no row older than {cutoff.isoformat()}",
        )

    ratio = expired_rows / total_rows if total_rows else 0.0
    if ratio > rule.max_delete_ratio and not rule.confirm_bulk:
        return RetentionDecision(
            rule=rule,
            status=STATUS_BLOCKED_BULK,
            cutoff=cutoff,
            total_rows=total_rows,
            expired_rows=expired_rows,
            reason=(
                f"{expired_rows}/{total_rows} rows ({ratio:.1%}) are past the cutoff, over the "
                f"{rule.max_delete_ratio:.0%} cap -- check retention_days, then set confirm_bulk: true "
                f"on rule '{rule.name}' to allow this run"
            ),
        )

    return RetentionDecision(
        rule=rule,
        status=STATUS_DELETE,
        cutoff=cutoff,
        total_rows=total_rows,
        expired_rows=expired_rows,
        reason=f"{expired_rows}/{total_rows} rows are older than {cutoff.isoformat()}",
    )
