from datetime import datetime, timedelta, timezone

import pytest

from scm_agent.events import Event, EventLedger


def _ev(key: str, ts: datetime, *, type_: str = "rop_breach") -> Event:
    return Event(type=type_, severity="medium", source="test", dedup_key=key, sku=key, ts=ts)


@pytest.fixture()
def ledger():
    led = EventLedger(":memory:", dedup_window_seconds=0.0)
    yield led
    led.close()


def test_list_between_excludes_rows_outside_the_window(ledger):
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    for offset_days in (0, 5, 40):
        ledger.emit(_ev(f"sku-{offset_days}", base + timedelta(days=offset_days)))

    got = ledger.list_between(base + timedelta(days=1), base + timedelta(days=10))

    assert [e.sku for e in got] == ["sku-5"]


def test_list_between_orders_by_ts_not_insertion_order(ledger):
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    ledger.emit(_ev("late", base + timedelta(days=3)))
    ledger.emit(_ev("early", base + timedelta(days=1)))

    got = ledger.list_between(base, base + timedelta(days=10))

    assert [e.sku for e in got] == ["early", "late"]


def test_list_between_end_is_exclusive_and_start_inclusive(ledger):
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    ledger.emit(_ev("at-start", base))
    ledger.emit(_ev("at-end", base + timedelta(days=2)))

    got = ledger.list_between(base, base + timedelta(days=2))

    assert [e.sku for e in got] == ["at-start"]


def test_list_between_filters_by_type(ledger):
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    ledger.emit(_ev("a", base, type_="rop_breach"))
    ledger.emit(_ev("b", base + timedelta(hours=1), type_="excess_growing"))

    got = ledger.list_between(base, base + timedelta(days=1), types=("excess_growing",))

    assert [e.sku for e in got] == ["b"]


def test_list_between_accepts_naive_datetimes_as_utc(ledger):
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    ledger.emit(_ev("a", base + timedelta(days=1)))

    got = ledger.list_between(datetime(2026, 7, 1), datetime(2026, 7, 5))

    assert [e.sku for e in got] == ["a"]


def test_count_between_matches_list_between(ledger):
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    for offset_days in (0, 1, 2, 40):
        ledger.emit(_ev(f"sku-{offset_days}", base + timedelta(days=offset_days)))

    window = (base, base + timedelta(days=10))

    assert ledger.count_between(*window) == len(ledger.list_between(*window))


def test_epoch_returns_the_oldest_ts(ledger):
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    ledger.emit(_ev("late", base + timedelta(days=9)))
    ledger.emit(_ev("early", base))

    assert ledger.epoch() == base


def test_epoch_is_none_on_an_empty_ledger(ledger):
    assert ledger.epoch() is None


def test_list_between_does_not_parse_payload_json(ledger):
    """Una fila con payload corrupto se lee igual: list_between no hace json.loads."""
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    ledger.emit(_ev("ok", base))
    ledger._conn.execute("UPDATE events SET payload_json = ? WHERE sku = ?", ("{not json", "ok"))
    ledger._conn.commit()

    got = ledger.list_between(base, base + timedelta(days=1))

    assert [e.sku for e in got] == ["ok"]
    assert got[0].payload == {}


def test_row_with_corrupt_payload_json_is_flagged_payload_corrupt(ledger):
    """Una fila con payload_json corrupto vuelve marcada, no solo silenciosamente vaciada:
    perder la marca dejaria la fila indistinguible de una que legitimamente no tiene payload."""
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    ledger.emit(_ev("corrupt", base))
    ledger._conn.execute("UPDATE events SET payload_json = ? WHERE sku = ?", ("{not json", "corrupt"))
    ledger._conn.commit()

    got = ledger.list_between(base, base + timedelta(days=1))

    assert len(got) == 1
    assert got[0].payload == {}
    assert got[0].payload_corrupt is True


def test_row_with_healthy_payload_json_is_not_flagged_payload_corrupt(ledger):
    """Una fila sana (payload_json valido, incluso vacio) vuelve con payload_corrupt=False."""
    base = datetime(2026, 7, 1, tzinfo=timezone.utc)
    ledger.emit(_ev("healthy", base))

    got = ledger.list_between(base, base + timedelta(days=1))

    assert len(got) == 1
    assert got[0].payload == {}
    assert got[0].payload_corrupt is False
