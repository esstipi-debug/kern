"""Retention job -- ``jobs/retention_job.py``.

The I/O half of ``src/retention.py``: reads ``config/retention.yaml``, asks
each store for its row counts, runs those past ``retention.decide``, and
deletes only what the decision authorized. A plain, idempotent, no-required-
args callable, so it registers as a :class:`~jobs.scheduler.ScheduledJob` and
runs through ``JobRegistry.run_once()`` under golden rule 9 -- no background
thread, nothing to deploy, and CI can run the whole thing synchronously.

Two properties worth stating outright, because this is the one job in the
repo that destroys data:

**It is dry-run by default.** ``run_retention_cycle()`` reports what it would
remove and removes nothing; deletion needs an explicit ``dry_run=False``.
:data:`RETENTION_JOB` -- the registrable form, the one a cron would drive --
passes ``dry_run=False``, so scheduling it is the deliberate act, not
importing it. Combined with every rule shipping ``enabled: false``, adopting
this takes two conscious edits, and a mistake in either direction fails toward
keeping data rather than losing it.

**A blocked rule is a result, not an error.** If a store has more expired rows
than its ``max_delete_ratio`` allows, the cycle reports ``blocked_bulk`` for
that rule, deletes nothing for it, and carries on with the others. It does not
raise -- a scheduled job that dies on rule 2 would silently stop pruning rules
3+ forever.

Wiring a new store in is one entry in :data:`PRUNERS` plus one rule in the
YAML. A rule naming a store with no pruner raises ``ValueError`` rather than
being skipped, so the config file can never quietly claim coverage it does not
have.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from src import retention
from src.retention import RetentionDecision, RetentionRule

from .scheduler import ScheduledJob


@dataclass(frozen=True)
class StorePruner:
    """How to count and prune one named store.

    Three callables rather than a store instance so nothing here opens a
    database at import time (``jobs/`` is on the app's boot chain) and so tests
    inject a fake without a data directory. ``open_store`` is called once per
    cycle and the result closed afterwards.
    """

    name: str
    open_store: Callable[[], object]
    count_all: Callable[[object], int]
    count_before: Callable[[object, datetime], int]
    prune_before: Callable[[object, datetime], int]


def _open_event_ledger() -> object:
    from scm_agent.events import EventLedger

    return EventLedger()


def _open_state_store() -> object:
    from src.state.store import StateStore

    return StateStore()


# The store registry. Imports are deferred into the factories above: this
# module is imported by jobs/scheduled_jobs.py, which webapp/app.py imports at
# boot, and constructing a store touches the filesystem.
PRUNERS: dict[str, StorePruner] = {
    "events": StorePruner(
        name="events",
        open_store=_open_event_ledger,
        count_all=lambda s: s.count_all(),
        count_before=lambda s, c: s.count_before(c),
        prune_before=lambda s, c: s.prune_before(c),
    ),
    "state_snapshots": StorePruner(
        name="state_snapshots",
        open_store=_open_state_store,
        count_all=lambda s: s.count_all(),
        count_before=lambda s, c: s.count_before(c),
        prune_before=lambda s, c: s.prune_before(c),
    ),
}


@dataclass(frozen=True)
class RuleOutcome:
    """What one rule actually did this cycle."""

    rule: RetentionRule
    decision: RetentionDecision
    deleted_rows: int
    dry_run: bool

    @property
    def status(self) -> str:
        return self.decision.status


@dataclass(frozen=True)
class RetentionReport:
    """Everything one cycle decided and did -- the job's return value.

    Returned rather than printed so ``JobRegistry.run_once()``'s
    ``{job_id: return_value}`` carries it, the same shape the price jobs use.
    """

    ran_at: datetime
    dry_run: bool
    outcomes: tuple[RuleOutcome, ...]

    @property
    def deleted_rows(self) -> int:
        return sum(o.deleted_rows for o in self.outcomes)

    @property
    def blocked(self) -> tuple[RuleOutcome, ...]:
        """Rules that hit their cap and need an operator to look -- the part of
        this report that is not just bookkeeping."""
        return tuple(o for o in self.outcomes if o.status == retention.STATUS_BLOCKED_BULK)

    def summary_lines(self) -> list[str]:
        """ASCII-only one-liners for a console or a log (Windows cp1252 --
        see CLAUDE.md's gotchas)."""
        mode = "DRY-RUN" if self.dry_run else "APPLIED"
        lines = [f"retention {mode} at {self.ran_at.isoformat()}"]
        for outcome in self.outcomes:
            verb = "would delete" if self.dry_run else "deleted"
            count = outcome.decision.expired_rows if self.dry_run else outcome.deleted_rows
            lines.append(f"  {outcome.rule.name}: {outcome.status} - {verb} {count} row(s); {outcome.decision.reason}")
        return lines


def run_retention_cycle(
    *,
    policy_path: str | Path = retention.DEFAULT_POLICY_PATH,
    pruners: dict[str, StorePruner] | None = None,
    now: datetime | None = None,
    dry_run: bool = True,
) -> RetentionReport:
    """Evaluate every rule in the policy and, unless ``dry_run``, apply it.

    Raises ``ValueError`` if a rule names a store with no pruner (see the
    module docstring). Everything else -- a disabled rule, an empty store, a
    rule over its cap -- is reported, not raised.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    registry = PRUNERS if pruners is None else pruners

    rules = retention.load_policy(policy_path)
    unknown = sorted({r.name for r in rules} - set(registry))
    if unknown:
        raise ValueError(
            f"config/retention.yaml names store(s) with no pruner in jobs/retention_job.py: {', '.join(unknown)}"
        )

    outcomes: list[RuleOutcome] = []
    for rule in rules:
        if not rule.enabled:
            # Skip before opening anything: a disabled rule must not so much as
            # touch its store, or "disabled" would still create data/ on boot.
            decision = retention.decide(rule, now=now, total_rows=0, expired_rows=0)
            outcomes.append(RuleOutcome(rule=rule, decision=decision, deleted_rows=0, dry_run=dry_run))
            continue

        pruner = registry[rule.name]
        store = pruner.open_store()
        try:
            cutoff = rule.cutoff(now)
            decision = retention.decide(
                rule,
                now=now,
                total_rows=pruner.count_all(store),
                expired_rows=pruner.count_before(store, cutoff),
            )
            deleted = 0
            if decision.may_delete and not dry_run:
                deleted = pruner.prune_before(store, cutoff)
        finally:
            close = getattr(store, "close", None)
            if callable(close):
                close()

        outcomes.append(RuleOutcome(rule=rule, decision=decision, deleted_rows=deleted, dry_run=dry_run))

    return RetentionReport(ran_at=now, dry_run=dry_run, outcomes=tuple(outcomes))


def run_retention_cycle_applied() -> RetentionReport:
    """``run_retention_cycle(dry_run=False)`` as a no-required-args callable --
    what :data:`RETENTION_JOB` schedules. Kept as a named function rather than
    a lambda so APScheduler's SQLAlchemyJobStore can pickle a reference to it
    (``JobRegistry.build_scheduler``)."""
    return run_retention_cycle(dry_run=False)


# Daily at 03:15 UTC: after any nightly ingest has landed, well away from the
# price crons. Registered nowhere yet -- adding it to jobs/scheduled_jobs.py's
# production_registry() is the deliberate act that turns retention on.
RETENTION_JOB = ScheduledJob(
    id="retention_cycle",
    func=run_retention_cycle_applied,
    trigger="cron",
    trigger_args={"hour": 3, "minute": 15},
)
