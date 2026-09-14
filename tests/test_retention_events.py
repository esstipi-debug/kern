"""Retention's EventLedger half (scm_agent/events.py prune path).

Split out of test_retention.py because importing scm_agent pulls in
nosilo_core (private repo, needs a token), which makes this module
uncollectable in any environment that does not have it -- the same
preexisting condition as the other ~180 test modules here. Everything that
CAN be tested without that dependency, including the bulk-delete cap, stays
in test_retention.py so it runs everywhere.

Guarantees under test:
- prune_before deletes exactly the rows past the cutoff and returns the count;
- count_before reports the same number without deleting anything;
- a second prune at the same cutoff is a no-op (idempotent);
- a row written from a non-UTC offset is judged by its INSTANT, not by how its
  ISO text happens to sort -- the reason _seqs_before parses in Python rather
  than comparing strings in SQL;
- the job wires through to the real ledger end to end.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from jobs.retention_job import StorePruner, run_retention_cycle
from scm_agent.events import Event, EventLedger

_NOW = datetime(2026, 8, 7, 12, 0, 0, tzinfo=timezone.utc)


def _seed_events(ledger: EventLedger, ages_in_days: list[int]) -> None:
    for index, age in enumerate(ages_in_days):
        ledger.emit(
            Event(
                type="stock_below_rop",
                severity="warning",
                source="test",
                dedup_key=f"k{index}",
                payload={"sku": f"SKU-{index}"},
                ts=_NOW - timedelta(days=age),
            )
        )


def _event_pruner(path) -> StorePruner:
    return StorePruner(
        name="events",
        open_store=lambda: EventLedger(path),
        count_all=lambda s: s.count_all(),
        count_before=lambda s, c: s.count_before(c),
        prune_before=lambda s, c: s.prune_before(c),
    )


def test_event_prune_deletes_only_rows_past_the_cutoff(tmp_path):
    # Arrange -- two old, two recent
    ledger = EventLedger(tmp_path / "events.sqlite3")
    _seed_events(ledger, [200, 150, 10, 1])

    # Act
    removed = ledger.prune_before(_NOW - timedelta(days=90))

    # Assert
    assert removed == 2
    assert ledger.count_all() == 2
    assert {e.payload["sku"] for e in ledger.list_all()} == {"SKU-2", "SKU-3"}
    ledger.close()


def test_event_count_before_does_not_delete(tmp_path):
    # Arrange
    ledger = EventLedger(tmp_path / "events.sqlite3")
    _seed_events(ledger, [200, 150, 1])

    # Act
    counted = ledger.count_before(_NOW - timedelta(days=90))

    # Assert
    assert counted == 2
    assert ledger.count_all() == 3  # nothing removed
    ledger.close()


def test_event_prune_is_idempotent(tmp_path):
    ledger = EventLedger(tmp_path / "events.sqlite3")
    _seed_events(ledger, [200, 1])
    cutoff = _NOW - timedelta(days=90)

    assert ledger.prune_before(cutoff) == 1
    assert ledger.prune_before(cutoff) == 0  # second run finds nothing
    assert ledger.count_all() == 1
    ledger.close()


def test_event_prune_handles_a_mixed_offset_ts_column(tmp_path):
    """A row written from a non-UTC offset must still be judged by its instant.

    03:00-04:00 is 07:00 UTC, i.e. AFTER a 06:00 UTC cutoff, even though the
    string "2026-08-07T03:00" sorts before "2026-08-07T06:00". A SQL
    `WHERE ts < ?` would delete this row; parsing keeps it.
    """
    # Arrange
    ledger = EventLedger(tmp_path / "events.sqlite3")
    ledger.emit(
        Event(
            type="t",
            severity="info",
            source="s",
            dedup_key="keep",
            ts=datetime(2026, 8, 7, 3, 0, tzinfo=timezone(timedelta(hours=-4))),
        )
    )

    # Act
    removed = ledger.prune_before(datetime(2026, 8, 7, 6, 0, tzinfo=timezone.utc))

    # Assert
    assert removed == 0
    assert ledger.count_all() == 1
    ledger.close()


def test_job_end_to_end_against_the_real_ledger(tmp_path):
    # Arrange
    ledger = EventLedger(tmp_path / "events.sqlite3")
    _seed_events(ledger, [200, 1])
    ledger.close()
    policy = tmp_path / "retention.yaml"
    policy.write_text(
        "rules:\n  - name: events\n    retention_days: 90\n    enabled: true\n    max_delete_ratio: 0.9\n",
        encoding="utf-8",
    )

    # Act
    report = run_retention_cycle(
        policy_path=policy, pruners={"events": _event_pruner(tmp_path / "events.sqlite3")}, now=_NOW, dry_run=False
    )

    # Assert
    assert report.deleted_rows == 1
    survivors = EventLedger(tmp_path / "events.sqlite3")
    assert survivors.count_all() == 1
    survivors.close()
