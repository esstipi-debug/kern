"""Tests for the retention policy engine, the job, and StateStore's prune path
(src/retention.py, jobs/retention_job.py, src/state/store.py).

Deliberately imports NOTHING from scm_agent, so this file collects and runs in
an environment without the private nosilo_core dependency -- which is most of
them, including a fresh clone before the token is configured. The safety
property this whole feature exists for (a mistyped window must not empty a
store) is therefore verifiable locally, not only in CI. The EventLedger half,
which cannot avoid importing scm_agent, lives in test_retention_events.py.

Guarantees under test:
- a cutoff is computed identically from a naive and an aware `now`, so a caller
  using datetime.utcnow() and one using an aware UTC clock never drift;
- a malformed policy raises ValueError instead of defaulting around the typo
  (bad retention_days, bad ratio, non-bool flag, duplicate rule name);
- a missing policy file yields no rules (deletes nothing) rather than raising;
- the shipped config/retention.yaml parses AND ships fully disabled;
- decide() classifies disabled / nothing_expired / delete / blocked_bulk, and
  confirm_bulk is what releases the last one;
- StateStore.prune_before removes the payload FILE as well as the index row,
  keeps in-window rows untouched, cleans up emptied partitions, tolerates an
  already-missing file, and refuses a file_path resolving outside the store;
- the job is dry-run by default, applies only with dry_run=False, never opens a
  store for a disabled rule, reports a blocked rule without raising and without
  stopping later rules, and raises on a rule naming a store it cannot prune;
- the cap leaves a real StateStore intact when retention_days is wrong, and
  confirm_bulk is the one edit that releases it.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from jobs.retention_job import RETENTION_JOB, StorePruner, run_retention_cycle
from src import retention
from src.retention import RetentionRule, decide, load_policy, parse_policy
from src.state.store import StateStore

_NOW = datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)


def _write_policy(tmp_path, body: str):
    path = tmp_path / "retention.yaml"
    path.write_text(body, encoding="utf-8")
    return path


def _rule(**kwargs) -> RetentionRule:
    base = {"name": "events", "retention_days": 90, "enabled": True}
    base.update(kwargs)
    return RetentionRule(**base)


def _seed_snapshot(store: StateStore, domain: str, cycle: str, age_days: int):
    payload = pd.DataFrame({"sku": ["A"], "qty": [1]})
    return store.append_snapshot(domain, cycle, payload, now=_NOW - timedelta(days=age_days))


def _state_pruner(base_path) -> StorePruner:
    return StorePruner(
        name="state_snapshots",
        open_store=lambda: StateStore(base_path),
        count_all=lambda s: s.count_all(),
        count_before=lambda s, c: s.count_before(c),
        prune_before=lambda s, c: s.prune_before(c),
    )


# ---------------------------------------------------------------- cutoff ----


def test_cutoff_treats_naive_now_as_utc():
    # Arrange
    rule = _rule(retention_days=30)

    # Act
    from_aware = rule.cutoff(_NOW)
    from_naive = rule.cutoff(_NOW.replace(tzinfo=None))

    # Assert
    assert from_aware == from_naive == _NOW - timedelta(days=30)


def test_cutoff_converts_a_non_utc_now():
    # Arrange
    rule = _rule(retention_days=1)
    other_zone = _NOW.astimezone(timezone(timedelta(hours=-4)))

    # Act / Assert -- same instant, so same cutoff
    assert rule.cutoff(other_zone) == rule.cutoff(_NOW)


# ---------------------------------------------------------------- parsing ---


def test_parse_policy_reads_rules_and_defaults():
    # Act
    rules = parse_policy(
        {
            "defaults": {"max_delete_ratio": 0.5},
            "rules": [
                {"name": "events", "retention_days": 90, "enabled": True, "description": "x"},
                {"name": "state_snapshots", "retention_days": 365, "max_delete_ratio": 0.1},
            ],
        }
    )

    # Assert
    assert [r.name for r in rules] == ["events", "state_snapshots"]
    assert rules[0].max_delete_ratio == 0.5  # inherited from defaults
    assert rules[0].enabled is True
    assert rules[1].max_delete_ratio == 0.1  # per-rule override wins
    assert rules[1].enabled is False  # rules are off unless switched on


def test_parse_policy_with_no_rules_key_is_empty():
    assert parse_policy({"version": 1}) == ()


@pytest.mark.parametrize(
    "entry, fragment",
    [
        ({"name": "events"}, "retention_days must be an integer"),
        ({"name": "events", "retention_days": 0}, "retention_days must be > 0"),
        ({"name": "events", "retention_days": -5}, "retention_days must be > 0"),
        ({"name": "events", "retention_days": "90"}, "retention_days must be an integer"),
        ({"name": "events", "retention_days": True}, "retention_days must be an integer"),
        ({"name": "", "retention_days": 90}, "needs a non-empty 'name'"),
        ({"name": "events", "retention_days": 90, "enabled": "yes"}, "enabled must be true or false"),
        ({"name": "events", "retention_days": 90, "max_delete_ratio": 0}, "max_delete_ratio must be in (0, 1]"),
        ({"name": "events", "retention_days": 90, "max_delete_ratio": 1.5}, "max_delete_ratio must be in (0, 1]"),
    ],
)
def test_parse_policy_rejects_malformed_rules(entry, fragment):
    with pytest.raises(ValueError) as excinfo:
        parse_policy({"rules": [entry]})
    assert fragment in str(excinfo.value)


def test_parse_policy_rejects_duplicate_rule_names():
    with pytest.raises(ValueError, match="duplicate rule name 'events'"):
        parse_policy({"rules": [{"name": "events", "retention_days": 1}, {"name": "events", "retention_days": 2}]})


def test_load_policy_missing_file_is_empty_not_an_error(tmp_path):
    assert load_policy(tmp_path / "nope.yaml") == ()


def test_load_policy_reads_the_shipped_config():
    """The real config/retention.yaml must stay parseable and, crucially, must
    ship with every rule disabled -- this repo's copy is a template, not a
    switched-on policy."""
    rules = load_policy("config/retention.yaml")
    assert {r.name for r in rules} == {"events", "state_snapshots"}
    assert all(not r.enabled for r in rules)
    assert all(not r.confirm_bulk for r in rules)


def test_load_policy_from_file(tmp_path):
    path = _write_policy(tmp_path, "version: 1\nrules:\n  - name: events\n    retention_days: 7\n    enabled: true\n")
    (rule,) = load_policy(path)
    assert rule.retention_days == 7 and rule.enabled is True


# --------------------------------------------------------------- decide -----


def test_decide_disabled_examines_nothing():
    decision = decide(_rule(enabled=False), now=_NOW, total_rows=0, expired_rows=0)
    assert decision.status == retention.STATUS_DISABLED
    assert decision.may_delete is False
    assert decision.cutoff is None


def test_decide_nothing_expired():
    decision = decide(_rule(), now=_NOW, total_rows=100, expired_rows=0)
    assert decision.status == retention.STATUS_NOTHING_EXPIRED
    assert decision.may_delete is False


def test_decide_allows_a_normal_sized_delete():
    decision = decide(_rule(max_delete_ratio=0.25), now=_NOW, total_rows=100, expired_rows=10)
    assert decision.status == retention.STATUS_DELETE
    assert decision.may_delete is True
    assert decision.expired_rows == 10


def test_decide_blocks_a_bulk_delete_over_the_cap():
    # Arrange -- 90 of 100 rows expired is the misconfiguration case
    decision = decide(_rule(max_delete_ratio=0.25), now=_NOW, total_rows=100, expired_rows=90)

    # Assert
    assert decision.status == retention.STATUS_BLOCKED_BULK
    assert decision.may_delete is False
    assert "confirm_bulk" in decision.reason


def test_decide_at_exactly_the_cap_is_allowed():
    """The cap is a strict '>' -- 25 of 100 at a 0.25 ratio is normal, not bulk."""
    decision = decide(_rule(max_delete_ratio=0.25), now=_NOW, total_rows=100, expired_rows=25)
    assert decision.status == retention.STATUS_DELETE


def test_confirm_bulk_releases_a_blocked_delete():
    decision = decide(_rule(max_delete_ratio=0.25, confirm_bulk=True), now=_NOW, total_rows=100, expired_rows=90)
    assert decision.status == retention.STATUS_DELETE


def test_decide_rejects_impossible_counts():
    with pytest.raises(ValueError, match="exceeds total_rows"):
        decide(_rule(), now=_NOW, total_rows=1, expired_rows=5)


# ------------------------------------------------------- StateStore prune ---


def test_state_prune_removes_the_row_and_the_payload_file(tmp_path):
    # Arrange
    store = StateStore(tmp_path / "state")
    old = _seed_snapshot(store, "demand", "c-old", 400)
    kept = _seed_snapshot(store, "demand", "c-new", 10)
    old_file = store.base_path / old.file_path
    kept_file = store.base_path / kept.file_path
    assert old_file.exists() and kept_file.exists()

    # Act
    removed = store.prune_before(_NOW - timedelta(days=365))

    # Assert -- the file is the point, not just the row
    assert removed == 1
    assert not old_file.exists()
    assert kept_file.exists()
    assert [r.cycle_id for r in store.list_records("demand")] == ["c-new"]
    store.close()


def test_state_prune_cleans_up_the_emptied_date_partition(tmp_path):
    # Arrange
    store = StateStore(tmp_path / "state")
    old = _seed_snapshot(store, "demand", "c-old", 400)
    partition = (store.base_path / old.file_path).parent

    # Act
    store.prune_before(_NOW - timedelta(days=365))

    # Assert
    assert not partition.exists()
    store.close()


def test_state_prune_tolerates_an_already_missing_file(tmp_path):
    # Arrange -- somebody hand-deleted the payload; retention must still converge
    store = StateStore(tmp_path / "state")
    old = _seed_snapshot(store, "demand", "c-old", 400)
    (store.base_path / old.file_path).unlink()

    # Act
    removed = store.prune_before(_NOW - timedelta(days=365))

    # Assert
    assert removed == 1
    assert store.count_all() == 0
    store.close()


def test_state_prune_refuses_a_path_outside_the_store(tmp_path):
    # Arrange -- a poisoned file_path must not get to unlink an arbitrary file
    outsider = tmp_path / "important.txt"
    outsider.write_text("do not delete", encoding="utf-8")
    store = StateStore(tmp_path / "state")
    _seed_snapshot(store, "demand", "c-old", 400)
    store._conn.execute("UPDATE snapshots SET file_path = ?", ("../important.txt",))
    store._conn.commit()

    # Act
    removed = store.prune_before(_NOW - timedelta(days=365))

    # Assert -- file untouched AND the row kept, so the anomaly stays visible
    assert removed == 0
    assert outsider.read_text(encoding="utf-8") == "do not delete"
    assert store.count_all() == 1
    store.close()


def test_state_prune_is_idempotent(tmp_path):
    store = StateStore(tmp_path / "state")
    _seed_snapshot(store, "demand", "c-old", 400)
    _seed_snapshot(store, "demand", "c-new", 1)
    cutoff = _NOW - timedelta(days=365)

    assert store.prune_before(cutoff) == 1
    assert store.prune_before(cutoff) == 0  # second run finds nothing
    assert store.count_all() == 1
    store.close()


def test_state_count_before_does_not_delete(tmp_path):
    store = StateStore(tmp_path / "state")
    _seed_snapshot(store, "demand", "c-old", 400)
    _seed_snapshot(store, "demand", "c-new", 1)

    assert store.count_before(_NOW - timedelta(days=365)) == 1
    assert store.count_all() == 2
    store.close()


# ------------------------------------------------------------------ job -----


class _FakeStore:
    """Counts calls so a test can prove the job never opened a disabled rule's
    store and never pruned when it said it would not."""

    def __init__(self, total: int, expired: int) -> None:
        self.total = total
        self.expired = expired
        self.pruned_with: list[datetime] = []
        self.closed = False

    def close(self) -> None:
        self.closed = True


def _fake_pruner(name: str, store: _FakeStore, opened: list[str]) -> StorePruner:
    def _open():
        opened.append(name)
        return store

    def _prune(s, cutoff):
        s.pruned_with.append(cutoff)
        return s.expired

    return StorePruner(
        name=name,
        open_store=_open,
        count_all=lambda s: s.total,
        count_before=lambda s, c: s.expired,
        prune_before=_prune,
    )


def test_job_is_dry_run_by_default(tmp_path):
    # Arrange
    path = _write_policy(tmp_path, "rules:\n  - name: events\n    retention_days: 90\n    enabled: true\n")
    store = _FakeStore(total=100, expired=10)
    opened: list[str] = []

    # Act
    report = run_retention_cycle(policy_path=path, pruners={"events": _fake_pruner("events", store, opened)}, now=_NOW)

    # Assert -- it looked, it decided to delete, it deleted nothing
    assert report.dry_run is True
    assert report.outcomes[0].status == retention.STATUS_DELETE
    assert report.outcomes[0].decision.expired_rows == 10
    assert report.deleted_rows == 0
    assert store.pruned_with == []
    assert store.closed is True


def test_job_applies_when_dry_run_is_false(tmp_path):
    # Arrange
    path = _write_policy(tmp_path, "rules:\n  - name: events\n    retention_days: 90\n    enabled: true\n")
    store = _FakeStore(total=100, expired=10)

    # Act
    report = run_retention_cycle(
        policy_path=path, pruners={"events": _fake_pruner("events", store, [])}, now=_NOW, dry_run=False
    )

    # Assert
    assert report.deleted_rows == 10
    assert store.pruned_with == [_NOW - timedelta(days=90)]


def test_job_never_opens_the_store_for_a_disabled_rule(tmp_path):
    # Arrange
    path = _write_policy(tmp_path, "rules:\n  - name: events\n    retention_days: 90\n    enabled: false\n")
    store = _FakeStore(total=100, expired=100)
    opened: list[str] = []

    # Act
    report = run_retention_cycle(
        policy_path=path, pruners={"events": _fake_pruner("events", store, opened)}, now=_NOW, dry_run=False
    )

    # Assert
    assert opened == []
    assert report.outcomes[0].status == retention.STATUS_DISABLED
    assert report.deleted_rows == 0


def test_job_reports_a_blocked_rule_without_raising_and_keeps_going(tmp_path):
    # Arrange -- rule 1 is over its cap, rule 2 is normal
    path = _write_policy(
        tmp_path,
        "defaults:\n  max_delete_ratio: 0.25\n"
        "rules:\n"
        "  - name: events\n    retention_days: 90\n    enabled: true\n"
        "  - name: state_snapshots\n    retention_days: 365\n    enabled: true\n",
    )
    blocked = _FakeStore(total=100, expired=90)
    normal = _FakeStore(total=100, expired=5)

    # Act
    report = run_retention_cycle(
        policy_path=path,
        pruners={
            "events": _fake_pruner("events", blocked, []),
            "state_snapshots": _fake_pruner("state_snapshots", normal, []),
        },
        now=_NOW,
        dry_run=False,
    )

    # Assert -- blocked one untouched, the later rule still ran
    assert [o.status for o in report.outcomes] == [retention.STATUS_BLOCKED_BULK, retention.STATUS_DELETE]
    assert blocked.pruned_with == []
    assert normal.pruned_with == [_NOW - timedelta(days=365)]
    assert report.deleted_rows == 5
    assert len(report.blocked) == 1


def test_job_raises_on_a_rule_with_no_pruner(tmp_path):
    # Arrange
    path = _write_policy(tmp_path, "rules:\n  - name: mystery_store\n    retention_days: 90\n")

    # Act / Assert -- config must never claim coverage the code does not have
    with pytest.raises(ValueError, match="mystery_store"):
        run_retention_cycle(policy_path=path, pruners={}, now=_NOW)


# ------------------------------------------------- the cap, on a real store --


def test_the_cap_protects_a_real_store_from_a_mistyped_window(tmp_path):
    """The headline safety property, against the real StateStore rather than a
    fake: `retention_days: 1` on a store whose rows are all older than that
    would wipe it, files included. The cap must stop the run and leave
    everything in place -- this is the failure mode the SQL prototype this
    replaced actually had."""
    # Arrange -- 10 snapshots, all ~400 days old
    store = StateStore(tmp_path / "state")
    files = [store.base_path / _seed_snapshot(store, "demand", f"c{i}", 400).file_path for i in range(10)]
    store.close()

    path = _write_policy(
        tmp_path,
        "defaults:\n  max_delete_ratio: 0.25\n"
        "rules:\n  - name: state_snapshots\n    retention_days: 1\n    enabled: true\n",
    )

    # Act -- note dry_run=False: this is the applying path, and it still refuses
    report = run_retention_cycle(
        policy_path=path, pruners={"state_snapshots": _state_pruner(tmp_path / "state")}, now=_NOW, dry_run=False
    )

    # Assert
    assert report.outcomes[0].status == retention.STATUS_BLOCKED_BULK
    assert report.deleted_rows == 0
    survivor = StateStore(tmp_path / "state")
    assert survivor.count_all() == 10  # no row lost
    survivor.close()
    assert all(f.exists() for f in files)  # no file lost


def test_confirm_bulk_lets_the_operator_release_that_same_run(tmp_path):
    """...and the escape hatch works, so a legitimate first prune over a legacy
    store is one config edit away rather than a dead end."""
    # Arrange -- same setup, plus the acknowledgement
    store = StateStore(tmp_path / "state")
    files = [store.base_path / _seed_snapshot(store, "demand", f"c{i}", 400).file_path for i in range(10)]
    store.close()

    path = _write_policy(
        tmp_path,
        "defaults:\n  max_delete_ratio: 0.25\n"
        "rules:\n  - name: state_snapshots\n    retention_days: 1\n    enabled: true\n    confirm_bulk: true\n",
    )

    # Act
    report = run_retention_cycle(
        policy_path=path, pruners={"state_snapshots": _state_pruner(tmp_path / "state")}, now=_NOW, dry_run=False
    )

    # Assert
    assert report.outcomes[0].status == retention.STATUS_DELETE
    assert report.deleted_rows == 10
    survivor = StateStore(tmp_path / "state")
    assert survivor.count_all() == 0
    survivor.close()
    assert not any(f.exists() for f in files)  # files went too, not just rows


# ------------------------------------------------------------- plumbing -----


def test_summary_lines_are_ascii_only(tmp_path):
    """CLAUDE.md gotcha: console prints must survive Windows cp1252."""
    path = _write_policy(tmp_path, "rules:\n  - name: events\n    retention_days: 90\n    enabled: true\n")
    report = run_retention_cycle(
        policy_path=path, pruners={"events": _fake_pruner("events", _FakeStore(100, 10), [])}, now=_NOW
    )
    for line in report.summary_lines():
        line.encode("ascii")  # raises UnicodeEncodeError if not


def test_scheduled_job_is_registrable_and_applies():
    """RETENTION_JOB must be a no-required-args callable (golden rule 9) and
    must be the APPLYING form -- scheduling it is the deliberate act."""
    assert RETENTION_JOB.id == "retention_cycle"
    assert RETENTION_JOB.trigger == "cron"
    assert RETENTION_JOB.func.__name__ == "run_retention_cycle_applied"
