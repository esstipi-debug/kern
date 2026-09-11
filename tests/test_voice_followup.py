"""Tests for the linchpin-voice-followup orchestration (capability M16).

Closes the voice loop credential-free: compliance gate -> build_agent_config ->
place call (via an injected VoiceCaller) -> capture CallOutcome -> safe-staged sync
to the system of record, with a never-unprotected GuidedOutcome on every branch.
"""

import pytest

from nosilo_core.guided import ESCALATED, EXECUTED, HANDOFF, passed_guided
from nosilo_core.writeback import InMemoryStore
from src.voice.caller import CALL_COMPLETED, CallOutcome, DryRunCaller
from src.voice.followup import Contact, run_voice_followup


def _run(caller, store, *, contact=None, dynamic_vars=None, **kw):
    contact = contact or Contact(name="Carrier X", phone="+15551234567", consent=True)
    base = dict(
        playbook_key="eta_check",
        shipper="Acme",
        contact=contact,
        dynamic_vars=dynamic_vars if dynamic_vars is not None else {"bl_no": "BL1"},
        caller=caller,
        store=store,
        now_local_hour=10,
        sync_entity_id="SHIP1",
    )
    base.update(kw)
    return run_voice_followup(**base)


def test_blocked_call_is_not_placed_and_escalates():
    caller, store = DryRunCaller(), InMemoryStore()
    blocked = Contact(name="Carrier X", phone="+15551234567", consent=False)

    result = _run(caller, store, contact=blocked)

    assert caller.placed_calls == []          # never dialed
    assert result.outcome is None
    assert not result.decision.allowed
    assert result.guided.status == ESCALATED
    assert passed_guided(result.guided)       # still protected


def test_allowed_call_is_placed_with_playbook_agent_config():
    scripted = CallOutcome(CALL_COMPLETED, placed=True, captured={"new_eta": "2026-07-01"}, call_id="c1")
    caller, store = DryRunCaller(scripted), InMemoryStore()

    result = _run(caller, store, dynamic_vars={"bl_no": "BL1"})

    assert len(caller.placed_calls) == 1
    req = caller.placed_calls[0]
    assert req.agent_config["name"] == "linchpin-logistics-eta_check"
    assert req.to == "+15551234567"
    assert req.dynamic_vars == {"bl_no": "BL1"}
    assert result.outcome is scripted


def test_completed_call_syncs_captured_fields_to_store():
    scripted = CallOutcome(
        CALL_COMPLETED, placed=True,
        captured={"new_eta": "2026-07-01", "status_code": "ONTIME"}, call_id="c1",
    )
    caller, store = DryRunCaller(scripted), InMemoryStore()

    result = _run(caller, store)

    assert store.read("SHIP1")["new_eta"] == "2026-07-01"
    assert store.read("SHIP1")["status_code"] == "ONTIME"
    assert result.apply_result.applied
    assert result.changeset is not None
    assert result.guided.status == EXECUTED
    assert passed_guided(result.guided)


def test_call_with_escalation_condition_routes_to_human_without_writing():
    scripted = CallOutcome(
        CALL_COMPLETED, placed=True, captured={"delay_reason": "blank sailing"},
        needs_human=True, call_id="c2",
    )
    caller, store = DryRunCaller(scripted), InMemoryStore()

    result = _run(caller, store)

    assert result.guided.status == ESCALATED
    assert store.read("SHIP1") == {}          # disputed facts are NOT auto-written
    assert result.apply_result is None
    assert passed_guided(result.guided)


def test_dry_run_with_no_completion_prepares_manual_handoff():
    caller, store = DryRunCaller(), InMemoryStore()  # default: placed=False

    result = _run(caller, store)

    assert len(caller.placed_calls) == 1      # it was attempted (compliance allowed)
    assert result.outcome.placed is False
    assert result.guided.status == HANDOFF
    assert store.read("SHIP1") == {}          # nothing synced
    assert passed_guided(result.guided)


def test_completed_call_with_no_capture_is_executed_without_sync():
    scripted = CallOutcome(CALL_COMPLETED, placed=True, captured={}, call_id="c3")
    caller, store = DryRunCaller(scripted), InMemoryStore()

    result = _run(caller, store)

    assert result.apply_result is None        # nothing to stage
    assert result.changeset is None
    assert store.read("SHIP1") == {}
    assert result.guided.status == EXECUTED    # the call itself was the deliverable
    assert passed_guided(result.guided)


def test_sync_is_idempotent_on_call_id():
    scripted = CallOutcome(CALL_COMPLETED, placed=True, captured={"new_eta": "2026-07-01"}, call_id="c1")
    store = InMemoryStore()

    first = _run(DryRunCaller(scripted), store)
    second = _run(DryRunCaller(scripted), store)  # same call_id -> same idempotency key

    assert first.apply_result.applied
    assert second.apply_result.idempotent_skip
    assert store.read("SHIP1")["new_eta"] == "2026-07-01"


def test_unknown_playbook_key_raises():
    with pytest.raises(KeyError):
        _run(DryRunCaller(), InMemoryStore(), playbook_key="does_not_exist")
