"""Tests for the persistent (SQLite) writeback audit/idempotency ledger.

Guarantees under test:
- a ledger persists applied entries across independent connections (simulating
  a process restart), so idempotency and rollback survive a crash/redeploy;
- InMemoryStore behaves identically with or without a ledger;
- the ABSENT sentinel round-trips through JSON without losing its meaning.
"""

from __future__ import annotations

import pytest

from src.writeback import (
    ABSENT,
    TIER_IRREVERSIBLE,
    TIER_REVERSIBLE,
    InMemoryStore,
    apply,
    approve,
    stage,
)
from src.writeback_store import SqliteAuditLedger


def _mem_ledger() -> SqliteAuditLedger:
    return SqliteAuditLedger(":memory:")


def test_ledger_starts_empty():
    assert _mem_ledger().applied_keys() == set()


def test_store_with_ledger_applies_and_reports_idempotent_skip():
    store = InMemoryStore({"SKU-A": {"reorder_point": 100}}, ledger=_mem_ledger())
    cs = stage(store, "erp", {"SKU-A": {"reorder_point": 120}},
               risk_tier=TIER_REVERSIBLE, idempotency_key="cs1")

    first = apply(store, cs, now=0.0, auto_apply_reversible=True)
    second = apply(store, cs, now=0.0, auto_apply_reversible=True)

    assert first.applied and not second.applied and second.idempotent_skip
    assert store.read("SKU-A")["reorder_point"] == 120


def test_ledger_persists_across_a_simulated_restart(tmp_path):
    """A fresh InMemoryStore + a fresh SqliteAuditLedger pointed at the same file must
    still know a key was already applied - the whole point of persistence."""
    path = tmp_path / "ledger.sqlite3"
    records = {"SKU-A": {"reorder_point": 100}}

    store1 = InMemoryStore(records, ledger=SqliteAuditLedger(path))
    cs = stage(store1, "erp", {"SKU-A": {"reorder_point": 120}},
               risk_tier=TIER_REVERSIBLE, idempotency_key="cs1")
    result1 = apply(store1, cs, now=0.0, auto_apply_reversible=True)
    assert result1.applied

    # Simulate a restart: brand-new store + ledger objects, same backing file.
    store2 = InMemoryStore(records, ledger=SqliteAuditLedger(path))
    cs_again = stage(store2, "erp", {"SKU-A": {"reorder_point": 120}},
                      risk_tier=TIER_REVERSIBLE, idempotency_key="cs1")
    result2 = apply(store2, cs_again, now=100.0, auto_apply_reversible=True)

    assert result2.idempotent_skip and not result2.applied


def test_ledger_backed_rollback_restores_prior_value(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    store = InMemoryStore({"SKU-A": {"reorder_point": 100}}, ledger=SqliteAuditLedger(path))
    cs = stage(store, "erp", {"SKU-A": {"reorder_point": 120}},
               risk_tier=TIER_REVERSIBLE, idempotency_key="cs1")
    apply(store, cs, now=0.0, auto_apply_reversible=True)

    store.rollback("cs1")

    assert store.read("SKU-A")["reorder_point"] == 100
    assert "cs1" not in store.applied_keys()


def test_ledger_backed_rollback_of_a_newly_created_field_removes_it(tmp_path):
    """The ABSENT sentinel must survive a JSON round-trip through the ledger."""
    path = tmp_path / "ledger.sqlite3"
    store = InMemoryStore({"SKU-A": {}}, ledger=SqliteAuditLedger(path))
    cs = stage(store, "erp", {"SKU-A": {"max_stock": 500}},  # field absent before
               risk_tier=TIER_REVERSIBLE, idempotency_key="cs1")
    apply(store, cs, now=0.0, auto_apply_reversible=True)
    assert store.read("SKU-A")["max_stock"] == 500

    store.rollback("cs1")

    assert "max_stock" not in store.read("SKU-A")


def test_ledger_get_returns_none_for_unknown_key():
    assert _mem_ledger().get("does-not-exist") is None


def test_ledger_record_round_trips_the_absent_sentinel_through_json(tmp_path):
    from src.writeback import AuditEntry

    ledger = SqliteAuditLedger(tmp_path / "l.sqlite3")
    entry = AuditEntry("k1", "erp", "stipi", (("SKU-A", "max_stock", ABSENT),))

    ledger.record(entry)
    back = ledger.get("k1")

    assert back is not None
    assert back.restore[0][2] is ABSENT


def test_apply_still_refuses_an_expired_approval_with_a_persistent_ledger(tmp_path):
    store = InMemoryStore({"SKU-A": {"reorder_point": 100}}, ledger=SqliteAuditLedger(tmp_path / "l.sqlite3"))
    cs = stage(store, "erp", {"SKU-A": {"reorder_point": 120}},
               risk_tier=TIER_IRREVERSIBLE, idempotency_key="cs1")
    appr = approve(cs, "stipi", now=0.0, ttl_seconds=900.0)

    from src.writeback import WritebackRefused

    with pytest.raises(WritebackRefused):
        apply(store, cs, approval=appr, now=1000.0)  # past expiry
    assert store.read("SKU-A")["reorder_point"] == 100
