"""Tests for the event bus ledger (Linchpin 3.0 PR-2, F0 -- scm_agent/events.py).

Guarantees under test (plan S4.2 QA invariant):
- emitting two Events with the SAME dedup_key within the dedup window results
  in only ONE row recorded -- the second emit() is a no-op and returns False;
- emitting after the window elapses (same dedup_key) records a second row;
- emitting with a DIFFERENT dedup_key always records, regardless of timing;
- basic emit -> list_by_type retrieval round-trips id/type/severity/sku/
  source/payload/dedup_key/ts;
- the dedup window is configurable, not hardcoded;
- the ledger persists across independent connections to the same file
  (simulating a process restart), matching SqliteAuditLedger's contract.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from scm_agent.events import DEFAULT_DEDUP_WINDOW_SECONDS, Event, EventLedger

_T0 = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)


def _ledger(**kwargs) -> EventLedger:
    return EventLedger(":memory:", **kwargs)


def _rop_event(*, ts: datetime, dedup_key: str = "SKU-A:stock_below_rop") -> Event:
    return Event(
        type="stock_below_rop",
        severity="high",
        source="monitors",
        dedup_key=dedup_key,
        sku="SKU-A",
        payload={"on_hand": 12.0, "reorder_point": 20.0},
        ts=ts,
    )


# -- the must-have QA invariant: idempotent dedup within the window ----------


def test_duplicate_dedup_key_within_the_window_records_only_one_row():
    ledger = _ledger(dedup_window_seconds=300.0)

    first = ledger.emit(_rop_event(ts=_T0))
    second = ledger.emit(_rop_event(ts=_T0 + timedelta(seconds=60)))  # inside the 300s window

    assert first is True
    assert second is False
    assert len(ledger.list_by_type("stock_below_rop")) == 1


def test_same_dedup_key_after_the_window_elapses_records_a_second_row():
    ledger = _ledger(dedup_window_seconds=300.0)

    first = ledger.emit(_rop_event(ts=_T0))
    second = ledger.emit(_rop_event(ts=_T0 + timedelta(seconds=301)))  # just past the 300s window

    assert first is True
    assert second is True
    assert len(ledger.list_by_type("stock_below_rop")) == 2


def test_different_dedup_key_always_records_even_at_the_same_instant():
    ledger = _ledger(dedup_window_seconds=300.0)

    first = ledger.emit(_rop_event(ts=_T0, dedup_key="SKU-A:stock_below_rop"))
    second = ledger.emit(_rop_event(ts=_T0, dedup_key="SKU-B:stock_below_rop"))

    assert first is True
    assert second is True
    assert len(ledger.list_by_type("stock_below_rop")) == 2


def test_dedup_window_is_configurable_not_hardcoded():
    """A tiny window means an event 2 seconds later is no longer a duplicate --
    proves the window is an actual parameter, not a hardcoded constant."""
    ledger = _ledger(dedup_window_seconds=1.0)

    first = ledger.emit(_rop_event(ts=_T0))
    second = ledger.emit(_rop_event(ts=_T0 + timedelta(seconds=2)))

    assert first is True
    assert second is True


def test_default_dedup_window_is_a_positive_sensible_default():
    assert EventLedger(":memory:").dedup_window_seconds == DEFAULT_DEDUP_WINDOW_SECONDS
    assert DEFAULT_DEDUP_WINDOW_SECONDS > 0


# -- basic emit / list-by-type retrieval --------------------------------------


def test_emit_then_list_by_type_round_trips_the_event_fields():
    ledger = _ledger()
    event = _rop_event(ts=_T0)

    recorded = ledger.emit(event)
    got = ledger.list_by_type("stock_below_rop")

    assert recorded is True
    assert len(got) == 1
    back = got[0]
    assert back.id == event.id
    assert back.type == "stock_below_rop"
    assert back.severity == "high"
    assert back.sku == "SKU-A"
    assert back.source == "monitors"
    assert back.payload == {"on_hand": 12.0, "reorder_point": 20.0}
    assert back.dedup_key == "SKU-A:stock_below_rop"
    assert back.ts == _T0


def test_list_by_type_returns_events_oldest_first():
    ledger = _ledger(dedup_window_seconds=0.0)
    ledger.emit(_rop_event(ts=_T0, dedup_key="k1"))
    ledger.emit(_rop_event(ts=_T0 + timedelta(hours=1), dedup_key="k2"))
    ledger.emit(_rop_event(ts=_T0 + timedelta(hours=2), dedup_key="k3"))

    got = ledger.list_by_type("stock_below_rop")

    assert [e.dedup_key for e in got] == ["k1", "k2", "k3"]


def test_list_by_type_only_returns_the_matching_type():
    ledger = _ledger()
    ledger.emit(_rop_event(ts=_T0, dedup_key="k1"))
    ledger.emit(Event(type="excess", severity="low", source="monitors", dedup_key="k2", sku="SKU-B", ts=_T0))

    assert [e.type for e in ledger.list_by_type("stock_below_rop")] == ["stock_below_rop"]
    assert [e.type for e in ledger.list_by_type("excess")] == ["excess"]
    assert ledger.list_by_type("does_not_exist") == []


def test_list_by_type_respects_limit():
    ledger = _ledger(dedup_window_seconds=0.0)
    for i in range(5):
        ledger.emit(_rop_event(ts=_T0 + timedelta(hours=i), dedup_key=f"k{i}"))

    assert len(ledger.list_by_type("stock_below_rop", limit=2)) == 2


def test_list_all_returns_every_event_across_types_oldest_first():
    ledger = _ledger()
    ledger.emit(_rop_event(ts=_T0, dedup_key="k1"))
    ledger.emit(Event(type="excess", severity="low", source="monitors", dedup_key="k2", ts=_T0 + timedelta(hours=1)))

    assert [e.dedup_key for e in ledger.list_all()] == ["k1", "k2"]


# -- list_recent(): the GET /api/events windowing query (PR-7) ----------------


def test_list_recent_keeps_the_newest_rows_when_limit_is_below_the_table_size():
    """The must-have behavior a live 'recent events' feed needs -- unlike
    list_by_type(limit=...), which keeps the OLDEST rows (see that method's
    own test above), list_recent() must keep the NEWEST ones as the table
    grows past `limit`."""
    ledger = _ledger(dedup_window_seconds=0.0)
    for i in range(5):
        ledger.emit(_rop_event(ts=_T0 + timedelta(hours=i), dedup_key=f"k{i}"))

    got = ledger.list_recent(limit=2)

    assert [e.dedup_key for e in got] == ["k3", "k4"]  # the 2 newest, oldest-first


def test_list_recent_returns_oldest_first_within_the_window():
    ledger = _ledger(dedup_window_seconds=0.0)
    ledger.emit(_rop_event(ts=_T0, dedup_key="k1"))
    ledger.emit(_rop_event(ts=_T0 + timedelta(hours=1), dedup_key="k2"))
    ledger.emit(_rop_event(ts=_T0 + timedelta(hours=2), dedup_key="k3"))

    assert [e.dedup_key for e in ledger.list_recent(limit=10)] == ["k1", "k2", "k3"]


def test_list_recent_filters_by_event_type():
    ledger = _ledger()
    ledger.emit(_rop_event(ts=_T0, dedup_key="k1"))
    ledger.emit(Event(type="excess", severity="low", source="monitors", dedup_key="k2", ts=_T0 + timedelta(hours=1)))

    assert [e.type for e in ledger.list_recent(event_type="stock_below_rop", limit=10)] == ["stock_below_rop"]
    assert [e.type for e in ledger.list_recent(event_type="excess", limit=10)] == ["excess"]
    assert ledger.list_recent(event_type="does_not_exist", limit=10) == []


def test_list_recent_default_limit_covers_a_small_table():
    ledger = _ledger()
    for i in range(3):
        ledger.emit(_rop_event(ts=_T0 + timedelta(hours=i), dedup_key=f"k{i}"))

    assert len(ledger.list_recent()) == 3


def test_list_recent_rejects_a_non_positive_limit():
    ledger = _ledger()
    with pytest.raises(ValueError, match="limit"):
        ledger.list_recent(limit=0)


def test_event_without_a_sku_is_allowed_for_non_sku_scoped_events():
    ledger = _ledger()
    site_event = Event(
        type="site_degraded", severity="medium", source="pricing_intel", dedup_key="competitor.example.com", ts=_T0
    )

    assert ledger.emit(site_event) is True
    assert ledger.list_by_type("site_degraded")[0].sku is None


def test_event_id_and_ts_default_when_not_provided():
    event_a = Event(type="excess", severity="low", source="monitors", dedup_key="a")
    event_b = Event(type="excess", severity="low", source="monitors", dedup_key="b")

    assert event_a.id != event_b.id  # each construction gets a fresh id
    assert event_a.ts.tzinfo is not None  # defaults to an aware UTC datetime


# -- naive datetimes are treated as already-UTC -------------------------------


def test_naive_ts_is_treated_as_already_utc_for_dedup_comparison():
    ledger = _ledger(dedup_window_seconds=300.0)
    naive_t0 = _T0.replace(tzinfo=None)

    first = ledger.emit(_rop_event(ts=naive_t0))
    second = ledger.emit(_rop_event(ts=naive_t0 + timedelta(seconds=60)))

    assert first is True
    assert second is False


# -- persistence across independent connections (simulated process restart) --


def test_ledger_persists_dedup_state_across_a_simulated_restart(tmp_path):
    path = tmp_path / "events.sqlite3"

    ledger1 = EventLedger(path, dedup_window_seconds=300.0)
    assert ledger1.emit(_rop_event(ts=_T0)) is True
    ledger1.close()

    ledger2 = EventLedger(path, dedup_window_seconds=300.0)
    still_duplicate = ledger2.emit(_rop_event(ts=_T0 + timedelta(seconds=60)))
    later = ledger2.emit(_rop_event(ts=_T0 + timedelta(seconds=600)))

    assert still_duplicate is False
    assert later is True
    assert len(ledger2.list_by_type("stock_below_rop")) == 2
