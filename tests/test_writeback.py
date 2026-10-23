"""Tests for the safe-staging writeback control plane (M15).

Guarantees under test:
- staging is a dry-run: it never mutates the system of record;
- irreversible writes are refused without a valid, matching, unexpired approval;
- reversible writes may auto-apply only when policy allows;
- applies are idempotent (same changeset key never lands twice);
- every applied changeset is auditable and can be rolled back.
"""

import threading

import pytest

from nosilo_core.writeback import (
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


# -- an Approval cannot be forged by constructing it directly -----------------


def test_directly_constructed_approval_with_wrong_signature_is_refused():
    """Regression: Approval used to be a plain dataclass - since content_hash is a
    deterministic hash of fields the caller already has, code could construct
    Approval(key, hash, "nobody", far_future_expiry) directly and satisfy the old
    matches() check with no call to approve() and no human ever involved."""
    from nosilo_core.writeback import Approval

    store = _store()
    cs = stage(store, "erp", {"SKU-A": {"reorder_point": 120}},
               risk_tier=TIER_IRREVERSIBLE, idempotency_key="cs1")

    forged = Approval(cs.idempotency_key, cs.content_hash, "nobody", 10_000_000_000.0, signature="not-a-real-signature")

    with pytest.raises(WritebackRefused):
        apply(store, cs, approval=forged, now=1.0)
    assert store.read("SKU-A")["reorder_point"] == 100


def test_approval_cannot_be_forged_even_by_recomputing_the_correct_content_hash():
    """Even computing the REAL content_hash (fully public: SHA-256 of fields the
    caller already has) and picking an arbitrary signature must not validate -
    only approve() (which holds the secret) can produce a signature matches()
    accepts."""
    from nosilo_core.writeback import Approval

    store = _store()
    cs = stage(store, "erp", {"SKU-A": {"reorder_point": 120}},
               risk_tier=TIER_IRREVERSIBLE, idempotency_key="cs1")

    forged = Approval(cs.idempotency_key, cs.content_hash, "nobody", 10_000_000_000.0, signature="")

    with pytest.raises(WritebackRefused):
        apply(store, cs, approval=forged, now=1.0)


def test_approval_is_unforgeable_once_a_server_secret_is_configured(monkeypatch):
    """With LINCHPIN_APPROVAL_SECRET set (the recommended production config), a
    caller who does not know the secret cannot mint a valid signature even
    knowing every other field, including the real content_hash."""
    import hashlib
    import hmac as hmac_module

    from nosilo_core.writeback import Approval

    monkeypatch.setenv("LINCHPIN_APPROVAL_SECRET", "top-secret-value")
    store = _store()
    cs = stage(store, "erp", {"SKU-A": {"reorder_point": 120}},
               risk_tier=TIER_IRREVERSIBLE, idempotency_key="cs1")

    # A genuine approve() call works end to end with the secret configured.
    real = approve(cs, "stipi", now=0.0)
    assert apply(store, cs, approval=real, now=1.0).applied

    # An attacker who knows every public field but not the secret guesses wrong.
    payload = f"{cs.idempotency_key}:{cs.content_hash}:nobody:20.0".encode()
    wrong_secret_signature = hmac_module.new(b"attacker-guess", payload, hashlib.sha256).hexdigest()
    forged = Approval(cs.idempotency_key, cs.content_hash, "nobody", 20.0, signature=wrong_secret_signature)

    store2 = _store()
    cs2 = stage(store2, "erp", {"SKU-A": {"reorder_point": 120}},
                risk_tier=TIER_IRREVERSIBLE, idempotency_key="cs1")
    with pytest.raises(WritebackRefused):
        apply(store2, cs2, approval=forged, now=1.0)


# -- fail closed: without a server secret, nothing approval-gated is applied --

_SECRET_ENV = "LINCHPIN_APPROVAL_SECRET"
_UNSIGNED_OPT_IN_ENV = "LINCHPIN_ALLOW_UNSIGNED_APPROVALS"


def _production_defaults(monkeypatch):
    """No secret and no development opt-in: what a deployment that never set
    either variable runs with."""
    monkeypatch.delenv(_SECRET_ENV, raising=False)
    monkeypatch.delenv(_UNSIGNED_OPT_IN_ENV, raising=False)


def _forge(cs, key: bytes):
    """An Approval signed by hand with ``key`` - no call to approve(), just the
    payload format any code that can read this module already knows."""
    import hashlib
    import hmac as hmac_module

    from nosilo_core.writeback import Approval

    payload = f"{cs.idempotency_key}:{cs.content_hash}:nobody:20.0".encode()
    signature = hmac_module.new(key, payload, hashlib.sha256).hexdigest()
    return Approval(cs.idempotency_key, cs.content_hash, "nobody", 20.0, signature=signature)


def _irreversible_changeset(store):
    return stage(store, "erp", {"SKU-A": {"reorder_point": 120}},
                 risk_tier=TIER_IRREVERSIBLE, idempotency_key="cs1")


def test_forged_approval_is_refused_when_no_secret_is_configured(monkeypatch):
    """The hole this guards: with LINCHPIN_APPROVAL_SECRET unset the signing key
    is the empty string, so an Approval signed by hand with b"" matched and
    apply() wrote it - no approve(), no human."""
    _production_defaults(monkeypatch)
    store = _store()
    cs = _irreversible_changeset(store)

    with pytest.raises(WritebackRefused) as refused:
        apply(store, cs, approval=_forge(cs, b""), now=1.0)

    from nosilo_core.writeback import ApprovalSecretNotConfigured

    assert isinstance(refused.value, ApprovalSecretNotConfigured)
    assert store.read("SKU-A")["reorder_point"] == 100
    assert store.applied_keys() == set()


@pytest.mark.parametrize("secret", ["", "   "])
def test_without_a_secret_even_a_genuine_approval_is_refused_until_one_is_set(monkeypatch, secret):
    """Fail closed, not just forgery-proof: approve() signs with the same empty key
    a forger uses, so its approval cannot be told apart and is refused too. A
    whitespace-only secret is no secret. The refusal leaves nothing behind - once
    a secret is configured, the very same change goes through."""
    from nosilo_core.writeback import ApprovalSecretNotConfigured

    _production_defaults(monkeypatch)
    monkeypatch.setenv(_SECRET_ENV, secret)
    store = _store()
    cs = _irreversible_changeset(store)

    with pytest.raises(ApprovalSecretNotConfigured) as refused:
        apply(store, cs, approval=approve(cs, "stipi", now=0.0), now=1.0)
    message = str(refused.value)
    assert _SECRET_ENV in message and _UNSIGNED_OPT_IN_ENV in message

    monkeypatch.setenv(_SECRET_ENV, "configured-later")
    assert apply(store, cs, approval=approve(cs, "stipi", now=0.0), now=1.0).applied
    assert store.read("SKU-A")["reorder_point"] == 120


@pytest.mark.parametrize("value", ["1", "true", " YES "])
def test_dev_opt_in_applies_an_unsigned_approval(monkeypatch, value):
    """Local development and tests keep working, with one explicit switch."""
    _production_defaults(monkeypatch)
    monkeypatch.setenv(_UNSIGNED_OPT_IN_ENV, value)
    store = _store()
    cs = _irreversible_changeset(store)

    assert apply(store, cs, approval=approve(cs, "stipi", now=0.0), now=1.0).applied
    assert store.read("SKU-A")["reorder_point"] == 120


@pytest.mark.parametrize("value", ["0", "false", ""])
def test_opt_in_values_that_read_as_off_keep_writes_closed(monkeypatch, value):
    """Whoever writes LINCHPIN_ALLOW_UNSIGNED_APPROVALS=0 means "off"."""
    from nosilo_core.writeback import ApprovalSecretNotConfigured

    _production_defaults(monkeypatch)
    monkeypatch.setenv(_UNSIGNED_OPT_IN_ENV, value)
    store = _store()
    cs = _irreversible_changeset(store)

    with pytest.raises(ApprovalSecretNotConfigured):
        apply(store, cs, approval=approve(cs, "stipi", now=0.0), now=1.0)
    assert store.applied_keys() == set()


def test_opt_in_never_weakens_a_configured_secret(monkeypatch):
    """With a real secret the development switch changes nothing: a hand-signed
    approval is still a forgery, refused for its signature rather than for a
    missing secret, and a genuine one still applies."""
    from nosilo_core.writeback import ApprovalSecretNotConfigured

    monkeypatch.setenv(_SECRET_ENV, "top-secret-value")
    monkeypatch.setenv(_UNSIGNED_OPT_IN_ENV, "1")
    store = _store()
    cs = _irreversible_changeset(store)

    with pytest.raises(WritebackRefused) as refused:
        apply(store, cs, approval=_forge(cs, b""), now=1.0)
    assert not isinstance(refused.value, ApprovalSecretNotConfigured)
    assert store.applied_keys() == set()

    assert apply(store, cs, approval=approve(cs, "stipi", now=0.0), now=1.0).applied


def test_writes_that_need_no_approval_do_not_need_a_secret(monkeypatch):
    """The gate sits behind requires_approval(): a read, or a reversible change
    under an auto-apply policy (e.g. the T1->T2 autonomy degradation), never
    carried an approval and keeps working on a deployment with no secret."""
    _production_defaults(monkeypatch)
    store = _store()

    read = stage(store, "erp", {"SKU-A": {"reorder_point": 100}},
                 risk_tier=TIER_READ, idempotency_key="r1")
    assert apply(store, read, approval=None, now=1.0).applied

    reversible = stage(store, "erp", {"SKU-A": {"safety_stock": 45}},
                       risk_tier=TIER_REVERSIBLE, idempotency_key="v1")
    assert apply(store, reversible, approval=None, now=1.0, auto_apply_reversible=True).applied
    assert store.read("SKU-A")["safety_stock"] == 45


# -- concurrency: check-then-act must not allow a double-commit ---------------


class _GatedCommitStore(InMemoryStore):
    """InMemoryStore whose commit() pauses just after being entered (before doing any
    work) until the test calls ``proceed.set()``. Lets a test force two concurrent
    apply() calls to both be "in flight" at the same time - the first caller is known
    to be inside commit() (entered, not yet recorded) while a second concurrent apply()
    call runs its own idempotency check against that exact state - reproducing the
    check-then-act race deterministically instead of relying on thread-scheduling luck.
    """

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.commit_entries = 0
        self._entries_lock = threading.Lock()
        self.first_entry = threading.Event()
        self.proceed = threading.Event()

    def commit(self, changeset, approved_by):
        with self._entries_lock:
            self.commit_entries += 1
            entered_first = self.commit_entries == 1
        if entered_first:
            self.first_entry.set()
        self.proceed.wait(timeout=5)
        return super().commit(changeset, approved_by)


def test_concurrent_apply_never_commits_the_same_key_twice():
    """Regression: apply() used to check ``idempotency_key in store.applied_keys()``
    and only THEN call store.commit() - two concurrent callers applying the same
    changeset (e.g. two web workers both processing a retried HTTP request before
    either finishes committing) could both pass that check and both reach
    store.commit(), which for a real connector means duplicating the side-effecting
    write (e.g. creating two purchase orders in Odoo).
    """
    store = _GatedCommitStore({"SKU-A": {"reorder_point": 100}})
    cs = stage(store, "erp", {"SKU-A": {"reorder_point": 120}},
               risk_tier=TIER_REVERSIBLE, idempotency_key="cs1")
    results: list = []
    results_lock = threading.Lock()

    def caller():
        r = apply(store, cs, now=0.0, auto_apply_reversible=True)
        with results_lock:
            results.append(r)

    t1 = threading.Thread(target=caller)
    t2 = threading.Thread(target=caller)
    t1.start()
    assert store.first_entry.wait(timeout=5), "first caller never reached commit()"
    t2.start()
    t2.join(timeout=0.5)  # give a buggy implementation time to also enter commit()
    store.proceed.set()   # release whoever is paused inside commit()
    t1.join(timeout=5)
    t2.join(timeout=5)

    assert not t1.is_alive() and not t2.is_alive()
    assert store.commit_entries == 1, "commit() must run at most once per idempotency_key"
    assert sum(1 for r in results if r.applied) == 1
    assert sum(1 for r in results if r.idempotent_skip) == 1
    assert store.read("SKU-A")["reorder_point"] == 120


def test_apply_releases_the_claim_when_commit_raises_so_a_retry_can_proceed():
    """A failed commit (e.g. a transient connector error) must not permanently strand
    the idempotency key - it must be released so a legitimate retry can still apply."""

    class _BoomOnce(InMemoryStore):
        def __init__(self, *a, **kw) -> None:
            super().__init__(*a, **kw)
            self.attempts = 0

        def commit(self, changeset, approved_by):
            self.attempts += 1
            if self.attempts == 1:
                raise RuntimeError("simulated transient failure")
            return super().commit(changeset, approved_by)

    store = _BoomOnce({"SKU-A": {"reorder_point": 100}})
    cs = stage(store, "erp", {"SKU-A": {"reorder_point": 120}},
               risk_tier=TIER_REVERSIBLE, idempotency_key="cs1")

    with pytest.raises(RuntimeError):
        apply(store, cs, now=0.0, auto_apply_reversible=True)

    retry = apply(store, cs, now=0.0, auto_apply_reversible=True)
    assert retry.applied
    assert store.read("SKU-A")["reorder_point"] == 120


def test_apply_calls_release_even_when_claim_itself_raises():
    """apply()'s cleanup-on-failure must cover claim() too, not just commit() - a
    transient ledger error (e.g. SQLite lock contention surfacing as
    sqlite3.OperationalError, which is not the sqlite3.IntegrityError the ledger's
    claim() already handles internally) must not bypass the same release()-then-
    reraise contract a failing commit() already gets, or the key could be left
    stranded with no claim ever actually held."""

    class _FlakyClaimStore(InMemoryStore):
        def __init__(self, *a, **kw) -> None:
            super().__init__(*a, **kw)
            self.claim_attempts = 0
            self.release_calls: list[str] = []

        def claim(self, idempotency_key, *, now=None):
            self.claim_attempts += 1
            if self.claim_attempts == 1:
                raise RuntimeError("simulated transient ledger error")
            return super().claim(idempotency_key, now=now)

        def release(self, idempotency_key):
            self.release_calls.append(idempotency_key)
            super().release(idempotency_key)

    store = _FlakyClaimStore({"SKU-A": {"reorder_point": 100}})
    cs = stage(store, "erp", {"SKU-A": {"reorder_point": 120}},
               risk_tier=TIER_REVERSIBLE, idempotency_key="cs1")

    with pytest.raises(RuntimeError):
        apply(store, cs, now=0.0, auto_apply_reversible=True)
    assert store.release_calls == ["cs1"]

    retry = apply(store, cs, now=0.0, auto_apply_reversible=True)
    assert retry.applied
    assert store.read("SKU-A")["reorder_point"] == 120


def test_claim_then_release_allows_a_fresh_claim():
    store = _store()
    assert store.claim("cs1") is True
    store.release("cs1")
    assert store.claim("cs1") is True


def test_claim_refuses_a_second_concurrent_claim_for_the_same_key():
    store = _store()
    assert store.claim("cs1") is True
    assert store.claim("cs1") is False


def test_claim_refuses_a_key_that_is_already_recorded():
    store = _store()
    cs = stage(store, "erp", {"SKU-A": {"reorder_point": 120}},
               risk_tier=TIER_REVERSIBLE, idempotency_key="cs1")
    apply(store, cs, now=0.0, auto_apply_reversible=True)
    assert store.claim("cs1") is False
