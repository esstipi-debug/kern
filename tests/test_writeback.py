"""Tests for the safe-staging writeback control plane (M15).

Guarantees under test:
- staging is a dry-run: it never mutates the system of record;
- irreversible writes are refused without a valid, matching, unexpired approval;
- reversible writes may auto-apply only when policy allows;
- applies are idempotent (same changeset key never lands twice);
- every applied changeset is auditable and can be rolled back.
"""

import pytest

from src.writeback import (
    TIER_IRREVERSIBLE,
    TIER_READ,
    TIER_REVERSIBLE,
    InMemoryStore,
    WritebackRefused,
    apply,
    approve,
    requires_approval,
    stage,
)


def _store():
    return InMemoryStore({"SKU-A": {"reorder_point": 100, "safety_stock": 30}})


def test_read_tier_needs_no_approval():
    assert requires_approval(TIER_READ) is False


def test_irreversible_always_needs_approval():
    assert requires_approval(TIER_IRREVERSIBLE) is True
    assert requires_approval(TIER_IRREVERSIBLE, auto_apply_reversible=True) is True


def test_reversible_can_auto_apply_only_when_allowed():
    assert requires_approval(TIER_REVERSIBLE) is True
    assert requires_approval(TIER_REVERSIBLE, auto_apply_reversible=True) is False


def test_stage_does_not_mutate_the_store():
    store = _store()
    stage(store, "erp", {"SKU-A": {"reorder_point": 120}},
          risk_tier=TIER_IRREVERSIBLE, idempotency_key="cs1")
    assert store.read("SKU-A")["reorder_point"] == 100  # untouched


def test_changeset_is_noop_when_value_unchanged():
    store = _store()
    cs = stage(store, "erp", {"SKU-A": {"reorder_point": 100}},
               risk_tier=TIER_REVERSIBLE, idempotency_key="cs0")
    assert cs.is_noop


def test_irreversible_apply_without_approval_is_refused():
    store = _store()
    cs = stage(store, "erp", {"SKU-A": {"reorder_point": 120}},
               risk_tier=TIER_IRREVERSIBLE, idempotency_key="cs1")
    with pytest.raises(WritebackRefused):
        apply(store, cs, now=0.0)
    assert store.read("SKU-A")["reorder_point"] == 100  # still untouched


def test_apply_with_valid_approval_writes():
    store = _store()
    cs = stage(store, "erp", {"SKU-A": {"reorder_point": 120}},
               risk_tier=TIER_IRREVERSIBLE, idempotency_key="cs1")
    appr = approve(cs, "stipi", now=0.0, ttl_seconds=900)
    result = apply(store, cs, approval=appr, now=10.0)
    assert result.applied
    assert store.read("SKU-A")["reorder_point"] == 120


def test_expired_approval_is_refused():
    store = _store()
    cs = stage(store, "erp", {"SKU-A": {"reorder_point": 120}},
               risk_tier=TIER_IRREVERSIBLE, idempotency_key="cs1")
    appr = approve(cs, "stipi", now=0.0, ttl_seconds=900)
    with pytest.raises(WritebackRefused):
        apply(store, cs, approval=appr, now=1000.0)  # past expiry


def test_approval_for_another_changeset_is_refused():
    store = _store()
    cs = stage(store, "erp", {"SKU-A": {"reorder_point": 120}},
               risk_tier=TIER_IRREVERSIBLE, idempotency_key="cs1")
    other = stage(store, "erp", {"SKU-A": {"safety_stock": 50}},
                  risk_tier=TIER_IRREVERSIBLE, idempotency_key="cs2")
    appr = approve(other, "stipi", now=0.0)
    with pytest.raises(WritebackRefused):
        apply(store, cs, approval=appr, now=1.0)


def test_apply_is_idempotent():
    store = _store()
    cs = stage(store, "erp", {"SKU-A": {"reorder_point": 120}},
               risk_tier=TIER_REVERSIBLE, idempotency_key="cs1")
    first = apply(store, cs, now=0.0, auto_apply_reversible=True)
    second = apply(store, cs, now=0.0, auto_apply_reversible=True)
    assert first.applied
    assert second.idempotent_skip
    assert not second.applied


def test_rollback_restores_prior_values():
    store = _store()
    cs = stage(store, "erp", {"SKU-A": {"reorder_point": 120}},
               risk_tier=TIER_REVERSIBLE, idempotency_key="cs1")
    apply(store, cs, now=0.0, auto_apply_reversible=True)
    assert store.read("SKU-A")["reorder_point"] == 120
    store.rollback("cs1")
    assert store.read("SKU-A")["reorder_point"] == 100


def test_reversible_auto_apply_writes_without_approval():
    store = _store()
    cs = stage(store, "erp", {"SKU-A": {"safety_stock": 45}},
               risk_tier=TIER_REVERSIBLE, idempotency_key="cs1")
    result = apply(store, cs, now=0.0, auto_apply_reversible=True)
    assert result.applied
    assert store.read("SKU-A")["safety_stock"] == 45


def test_rollback_unknown_key_raises():
    with pytest.raises(KeyError):
        _store().rollback("does-not-exist")


def test_rollback_removes_a_newly_added_field():
    store = _store()
    cs = stage(store, "erp", {"SKU-A": {"max_stock": 500}},  # field absent before
               risk_tier=TIER_REVERSIBLE, idempotency_key="cs1")
    apply(store, cs, now=0.0, auto_apply_reversible=True)
    assert store.read("SKU-A")["max_stock"] == 500
    store.rollback("cs1")
    assert "max_stock" not in store.read("SKU-A")  # cleanly removed, not left as None


def test_changeset_summary_reports_tier_and_key():
    cs = stage(_store(), "erp", {"SKU-A": {"reorder_point": 120}},
               risk_tier=TIER_IRREVERSIBLE, idempotency_key="cs9")
    s = cs.summary()
    assert "irreversible" in s and "cs9" in s


# -- content-hash binding: "approve X, apply Y" must be refused ---------------


def test_approval_is_refused_when_content_changes_under_the_same_key():
    """A changeset re-staged with the same idempotency_key but DIFFERENT edits must
    not be applicable under an approval granted for the original content."""
    store = _store()
    original = stage(store, "erp", {"SKU-A": {"reorder_point": 120}},
                      risk_tier=TIER_IRREVERSIBLE, idempotency_key="cs1")
    appr = approve(original, "stipi", now=0.0)

    swapped = stage(store, "erp", {"SKU-A": {"reorder_point": 999999}},
                     risk_tier=TIER_IRREVERSIBLE, idempotency_key="cs1")  # same key, different edit
    with pytest.raises(WritebackRefused):
        apply(store, swapped, approval=appr, now=1.0)
    assert store.read("SKU-A")["reorder_point"] == 100  # untouched


def test_approval_content_hash_matches_the_approved_changeset():
    cs = stage(_store(), "erp", {"SKU-A": {"reorder_point": 120}},
               risk_tier=TIER_IRREVERSIBLE, idempotency_key="cs1")
    appr = approve(cs, "stipi", now=0.0)
    assert appr.content_hash == cs.content_hash
    assert appr.matches(cs)


# -- real clock by default: an omitted `now` must not freeze approvals open ---


def test_apply_defaults_to_the_real_clock_not_a_frozen_zero():
    """Regression: apply()'s `now` used to default to 0.0, so an approval's 900s TTL
    was never actually checked against a real clock by any caller that omitted it."""
    store = _store()
    cs = stage(store, "erp", {"SKU-A": {"reorder_point": 120}},
               risk_tier=TIER_IRREVERSIBLE, idempotency_key="cs1")
    already_expired = approve(cs, "stipi", now=0.0, ttl_seconds=900.0)  # expired since 1970

    with pytest.raises(WritebackRefused):
        apply(store, cs, approval=already_expired)  # no `now` passed -> must use the real clock
    assert store.read("SKU-A")["reorder_point"] == 100


def test_approve_defaults_to_the_real_clock_not_a_frozen_zero():
    store = _store()
    cs = stage(store, "erp", {"SKU-A": {"reorder_point": 120}},
               risk_tier=TIER_IRREVERSIBLE, idempotency_key="cs1")
    appr = approve(cs, "stipi", ttl_seconds=900.0)  # no `now` passed
    result = apply(store, cs, approval=appr)  # no `now` passed either
    assert result.applied  # both defaulted to "now" and a 900s-out approval is still valid
