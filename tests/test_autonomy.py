"""Tests for the A3 "execute" autonomy tiers (Linchpin 3.0 PR-6, plan S5).

Guarantees under test:
- AutonomyLedger records/reads/acknowledges audit rows, and refuses to
  acknowledge an unknown or already-resolved id (loud, not silent);
- enforce_analysis_tier(): T1 auto-executes AND is audited (a durable
  AutonomyRecord exists); T2 does NOT auto-notify as done and produces a
  pending/HANDOFF outcome that acknowledge_pending() later completes; T3
  always escalates through the real src/escalation.py machinery, never a
  fabricated ESCALATED outcome; a non-ok JobResult gates nothing;
- enforce_writeback_tier(): T1 auto-applies a REVERSIBLE changeset but an
  IRREVERSIBLE changeset is NEVER auto-applied even when routed T1 -- the
  one test that most directly proves the safety invariant holds; T2 stages
  only; T3 escalates before any apply is attempted;
- handle_event_tiered(): a full monitor-to-outcome cycle actually respects
  T1/T2/T3 (notify only fires for T1; T2/T3 suppress it), not just carries
  the tier as inert data (config/event_routing.yaml's own pre-PR-6 caveat).
"""

from __future__ import annotations

import pytest

from scm_agent import event_intent as event_intent_module
from scm_agent import llm, tools
from scm_agent.autonomy import (
    STATUS_ACKNOWLEDGED,
    STATUS_AUTO_EXECUTED,
    STATUS_ESCALATED,
    STATUS_PENDING,
    TIER_T1,
    TIER_T2,
    TIER_T3,
    AutonomyLedger,
    acknowledge_pending,
    enforce_analysis_tier,
    enforce_writeback_tier,
    handle_event_tiered,
)
from scm_agent.event_intent import DEFAULT_ROUTING_PATH, Route, RoutedResult
from scm_agent.events import Event
from scm_agent.orchestrator import Orchestrator
from scm_agent.types import STATUS_NEEDS_DATA, STATUS_OK, JobResult
from src import writeback
from src.guided import ESCALATED, EXECUTED, HANDOFF, ExecutionOption, as_executed, as_options, passed_guided

PORTFOLIO = "data/sample_demand_portfolio.csv"  # same fixture tests/test_event_intent.py uses


def _test_orchestrator() -> Orchestrator:
    return Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback(), clients_root=None)


def _event(*, event_type: str = "stock_below_rop", sku: str = "SKU-A", data_path: str = PORTFOLIO) -> Event:
    return Event(
        type=event_type, severity="high", source="monitors",
        dedup_key=f"{sku}:{event_type}", sku=sku,
        payload={"on_hand": 12.0, "reorder_point": 20.0, "data_path": data_path},
    )


def _route(tier: str, *, event_type: str = "stock_below_rop") -> Route:
    return Route(event_type=event_type, tool="inventory_optimization",
                 param_builder="inventory_from_stock_event", autonomy_tier=tier)


def _ok_result(*, guided=None) -> JobResult:
    return JobResult(
        status=STATUS_OK, tool="inventory_optimization", confidence=0.9,
        deliverables={"excel": "deliverables/agent/plan.xlsx"}, summary="Reorder plan computed.",
        guided=guided or as_executed("Reorder plan computed."),
    )


def _routed(tier: str, *, result: JobResult | None = None) -> RoutedResult:
    return RoutedResult(route=_route(tier), result=result or _ok_result(), notified=False)


# -- AutonomyLedger -------------------------------------------------------------


def test_ledger_record_and_get_roundtrip():
    ledger = AutonomyLedger(":memory:")
    record = ledger.record(
        tier=TIER_T1, status=STATUS_AUTO_EXECUTED, event_type="stock_below_rop",
        event_id="evt-1", sku="SKU-A", tool="inventory_optimization", summary="ran autonomously",
    )
    fetched = ledger.get(record.id)
    assert fetched is not None
    assert fetched.tier == TIER_T1
    assert fetched.status == STATUS_AUTO_EXECUTED
    assert fetched.sku == "SKU-A"
    assert fetched.summary == "ran autonomously"
    assert fetched.acknowledged_by is None


def test_ledger_get_unknown_id_returns_none():
    ledger = AutonomyLedger(":memory:")
    assert ledger.get("does-not-exist") is None


def test_ledger_list_pending_only_returns_pending_status():
    ledger = AutonomyLedger(":memory:")
    ledger.record(tier=TIER_T1, status=STATUS_AUTO_EXECUTED, event_type="a", event_id="1", summary="x")
    pending = ledger.record(tier=TIER_T2, status=STATUS_PENDING, event_type="b", event_id="2", summary="y")
    ledger.record(tier=TIER_T3, status=STATUS_ESCALATED, event_type="c", event_id="3", summary="z")

    result = ledger.list_pending()
    assert [r.id for r in result] == [pending.id]


def test_ledger_list_all_returns_every_record_oldest_first():
    ledger = AutonomyLedger(":memory:")
    r1 = ledger.record(tier=TIER_T1, status=STATUS_AUTO_EXECUTED, event_type="a", event_id="1", summary="x")
    r2 = ledger.record(tier=TIER_T2, status=STATUS_PENDING, event_type="b", event_id="2", summary="y")
    assert [r.id for r in ledger.list_all()] == [r1.id, r2.id]


def test_ledger_acknowledge_flips_status_and_stamps_who():
    ledger = AutonomyLedger(":memory:")
    record = ledger.record(tier=TIER_T2, status=STATUS_PENDING, event_type="a", event_id="1", summary="x")

    acked = ledger.acknowledge(record.id, "alice")

    assert acked.status == STATUS_ACKNOWLEDGED
    assert acked.acknowledged_by == "alice"
    assert acked.acknowledged_at is not None
    assert ledger.list_pending() == []  # no longer pending


def test_ledger_acknowledge_unknown_id_raises_keyerror():
    ledger = AutonomyLedger(":memory:")
    with pytest.raises(KeyError):
        ledger.acknowledge("nope", "alice")


def test_ledger_acknowledge_already_acknowledged_raises_valueerror():
    ledger = AutonomyLedger(":memory:")
    record = ledger.record(tier=TIER_T2, status=STATUS_PENDING, event_type="a", event_id="1", summary="x")
    ledger.acknowledge(record.id, "alice")

    with pytest.raises(ValueError, match="not pending"):
        ledger.acknowledge(record.id, "bob")


# -- enforce_analysis_tier(): T1 auto-executes and is audited -------------------


def test_t1_returns_the_tools_own_guided_outcome_unchanged():
    outcome_in = as_executed("Reorder plan computed.")
    routed = _routed(TIER_T1, result=_ok_result(guided=outcome_in))
    ledger = AutonomyLedger(":memory:")

    outcome = enforce_analysis_tier(routed, _event(), ledger=ledger)

    assert outcome is outcome_in
    assert outcome.status == EXECUTED
    assert passed_guided(outcome)


def test_t1_writes_an_auto_executed_audit_record():
    routed = _routed(TIER_T1)
    ledger = AutonomyLedger(":memory:")
    event = _event()

    enforce_analysis_tier(routed, event, ledger=ledger)

    records = ledger.list_all()
    assert len(records) == 1
    assert records[0].tier == TIER_T1
    assert records[0].status == STATUS_AUTO_EXECUTED
    assert records[0].event_id == event.id
    assert records[0].sku == "SKU-A"
    assert records[0].tool == "inventory_optimization"


# -- enforce_analysis_tier(): T2 holds a pending acknowledgment -----------------


def test_t2_does_not_return_an_executed_outcome():
    routed = _routed(TIER_T2)
    ledger = AutonomyLedger(":memory:")

    outcome = enforce_analysis_tier(routed, _event(), ledger=ledger)

    assert outcome.status == HANDOFF  # not EXECUTED -- never announced as done
    assert passed_guided(outcome)
    assert len(outcome.handoffs) == 1
    assert outcome.handoffs[0].data["pending_id"]


def test_t2_writes_a_pending_record_matching_the_handoffs_pending_id():
    routed = _routed(TIER_T2)
    ledger = AutonomyLedger(":memory:")

    outcome = enforce_analysis_tier(routed, _event(), ledger=ledger)

    pending = ledger.list_pending()
    assert len(pending) == 1
    assert pending[0].status == STATUS_PENDING
    assert pending[0].id == outcome.handoffs[0].data["pending_id"]


def test_acknowledge_pending_completes_a_t2_item():
    routed = _routed(TIER_T2)
    ledger = AutonomyLedger(":memory:")
    outcome = enforce_analysis_tier(routed, _event(), ledger=ledger)
    pending_id = outcome.handoffs[0].data["pending_id"]

    completed = acknowledge_pending(ledger, pending_id, "alice")

    assert completed.status == EXECUTED
    assert passed_guided(completed)
    assert ledger.get(pending_id).status == STATUS_ACKNOWLEDGED
    assert ledger.get(pending_id).acknowledged_by == "alice"
    assert ledger.list_pending() == []


def test_acknowledge_pending_unknown_id_raises():
    ledger = AutonomyLedger(":memory:")
    with pytest.raises(KeyError):
        acknowledge_pending(ledger, "nope", "alice")


# -- enforce_analysis_tier(): T3 always escalates via real src/escalation.py ----


def test_t3_returns_a_real_escalated_outcome():
    routed = _routed(TIER_T3)
    ledger = AutonomyLedger(":memory:")

    outcome = enforce_analysis_tier(routed, _event(), ledger=ledger)

    assert outcome.status == ESCALATED
    assert passed_guided(outcome)
    assert outcome.escalation is not None       # a real EscalationPacket, not fabricated
    assert outcome.escalation.route_to          # src/escalation.py filled a default route
    assert outcome.escalation.sla                # ...and a default SLA
    assert outcome.escalation.recommendation == "Reorder plan computed."


def test_t3_always_escalates_regardless_of_the_analysis_result():
    """T3 escalates even when the underlying analysis outcome was itself a
    clean, ranked-options result -- the tier decides, not the content."""
    options_outcome = as_options("pick one", [ExecutionOption("a", "option a", score=1.0)])
    routed = _routed(TIER_T3, result=_ok_result(guided=options_outcome))
    ledger = AutonomyLedger(":memory:")

    outcome = enforce_analysis_tier(routed, _event(), ledger=ledger)

    assert outcome.status == ESCALATED


def test_t3_writes_an_escalated_audit_record():
    routed = _routed(TIER_T3)
    ledger = AutonomyLedger(":memory:")
    event = _event()

    enforce_analysis_tier(routed, event, ledger=ledger)

    records = ledger.list_all()
    assert len(records) == 1
    assert records[0].status == STATUS_ESCALATED
    assert records[0].tier == TIER_T3


# -- enforce_analysis_tier(): non-ok results and invalid tiers ------------------


def test_non_ok_result_gates_nothing():
    result = JobResult(status=STATUS_NEEDS_DATA, tool="inventory_optimization", confidence=0.0,
                        deliverables={}, summary="needs a data file",
                        guided=as_executed("needs a data file"))
    routed = _routed(TIER_T1, result=result)
    ledger = AutonomyLedger(":memory:")

    outcome = enforce_analysis_tier(routed, _event(), ledger=ledger)

    assert outcome is result.guided
    assert ledger.list_all() == []  # nothing was delivered, so nothing is gated/audited


def test_enforce_analysis_tier_rejects_an_unknown_tier():
    route = Route(event_type="stock_below_rop", tool="inventory_optimization",
                   param_builder="inventory_from_stock_event", autonomy_tier="T9")
    routed = RoutedResult(route=route, result=_ok_result(), notified=False)

    with pytest.raises(ValueError, match="T9"):
        enforce_analysis_tier(routed, _event(), ledger=AutonomyLedger(":memory:"))


# -- enforce_writeback_tier(): the safety-invariant tests -----------------------


def _store():
    return writeback.InMemoryStore({"SKU-A": {"reorder_point": 100}})


def _reversible_changeset(store):
    return writeback.stage(store, "erp", {"SKU-A": {"reorder_point": 120}},
                            risk_tier=writeback.TIER_REVERSIBLE, idempotency_key="cs-reversible")


def _irreversible_changeset(store):
    return writeback.stage(store, "erp", {"SKU-A": {"reorder_point": 120}},
                            risk_tier=writeback.TIER_IRREVERSIBLE, idempotency_key="cs-irreversible")


def test_t1_auto_applies_a_reversible_changeset():
    store = _store()
    cs = _reversible_changeset(store)

    outcome = enforce_writeback_tier(store, cs, TIER_T1, now=0.0)

    assert outcome.status == EXECUTED
    assert passed_guided(outcome)
    assert store.read("SKU-A")["reorder_point"] == 120
    assert cs.idempotency_key in store.applied_keys()


def test_t1_never_auto_applies_an_irreversible_changeset():
    """THE safety-invariant test: writeback.requires_approval() hard-codes
    'irreversible always needs a human' regardless of auto_apply_reversible
    (src/writeback.py). enforce_writeback_tier's T1 branch checks
    changeset.risk_tier BEFORE ever calling writeback.apply() -- so an
    irreversible changeset never even attempts the write path under T1, and
    the invariant holds at two independent layers: this explicit tier check,
    AND (if this check were ever removed) apply()'s own WritebackRefused.
    Proven here by asserting BOTH the outcome shape (never EXECUTED) AND the
    store's real state (nothing was actually written / nothing claimed) --
    not just that no exception happened to propagate.
    """
    store = _store()
    cs = _irreversible_changeset(store)

    outcome = enforce_writeback_tier(store, cs, TIER_T1, now=0.0)

    assert outcome.status == HANDOFF  # never EXECUTED, even though tier == T1
    assert passed_guided(outcome)
    assert store.read("SKU-A")["reorder_point"] == 100  # untouched
    assert store.applied_keys() == set()                # nothing claimed or committed


def test_t2_stages_an_irreversible_changeset_without_applying():
    store = _store()
    cs = _irreversible_changeset(store)

    outcome = enforce_writeback_tier(store, cs, TIER_T2, now=0.0)

    assert outcome.status == HANDOFF
    assert store.read("SKU-A")["reorder_point"] == 100
    assert store.applied_keys() == set()


def test_t2_stages_a_reversible_changeset_without_applying():
    """T2 never auto-applies even a reversible changeset -- staging only,
    an explicit human approve()+apply() step is always required."""
    store = _store()
    cs = _reversible_changeset(store)

    outcome = enforce_writeback_tier(store, cs, TIER_T2, now=0.0)

    assert outcome.status == HANDOFF
    assert store.read("SKU-A")["reorder_point"] == 100
    assert store.applied_keys() == set()


def test_t3_escalates_before_any_apply_is_attempted():
    store = _store()
    cs = _reversible_changeset(store)

    outcome = enforce_writeback_tier(store, cs, TIER_T3, now=0.0)

    assert outcome.status == ESCALATED
    assert outcome.escalation is not None
    assert store.read("SKU-A")["reorder_point"] == 100
    assert store.applied_keys() == set()


def test_enforce_writeback_tier_rejects_an_unknown_tier():
    store = _store()
    cs = _reversible_changeset(store)
    with pytest.raises(ValueError, match="T9"):
        enforce_writeback_tier(store, cs, "T9")


def test_t1_reversible_apply_is_idempotent_on_repeat():
    store = _store()
    cs = _reversible_changeset(store)
    enforce_writeback_tier(store, cs, TIER_T1, now=0.0)

    outcome = enforce_writeback_tier(store, cs, TIER_T1, now=1.0)  # same idempotency_key again

    assert outcome.status == EXECUTED
    assert "idempotent skip" in outcome.summary.lower()
    assert store.read("SKU-A")["reorder_point"] == 120  # unchanged by the repeat


# -- handle_event_tiered(): full monitor-to-outcome cycle, tiers enforced ------


def test_handle_event_tiered_t1_auto_notifies_and_audits(tmp_path, monkeypatch):
    captured = {"n": 0}
    monkeypatch.setattr(event_intent_module, "notify", lambda message, **kwargs: captured.__setitem__("n", captured["n"] + 1) or True)
    routes = {"stock_below_rop": _route(TIER_T1)}
    ledger = AutonomyLedger(":memory:")

    tiered = handle_event_tiered(
        _event(), routes=routes, orchestrator=_test_orchestrator(), out_dir=tmp_path, ledger=ledger,
    )

    assert tiered.routed.result.status == STATUS_OK
    assert tiered.routed.notified is True   # T1 -> the automatic notify() fired
    assert captured["n"] == 1
    assert tiered.outcome is tiered.routed.result.guided
    audit = ledger.list_all()
    assert len(audit) == 1
    assert audit[0].status == STATUS_AUTO_EXECUTED


def test_handle_event_tiered_t2_suppresses_notify_and_holds_pending(tmp_path, monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(event_intent_module, "notify", lambda message, **kwargs: called.__setitem__("n", called["n"] + 1) or True)
    routes = {"stock_below_rop": _route(TIER_T2)}
    ledger = AutonomyLedger(":memory:")

    tiered = handle_event_tiered(
        _event(), routes=routes, orchestrator=_test_orchestrator(), out_dir=tmp_path, ledger=ledger,
    )

    assert tiered.routed.result.status == STATUS_OK   # the analysis DID run and pass QA
    assert tiered.routed.notified is False             # ...but was never announced as done
    assert called["n"] == 0
    assert tiered.outcome.status == HANDOFF
    assert len(ledger.list_pending()) == 1


def test_handle_event_tiered_t3_suppresses_notify_and_escalates(tmp_path, monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(event_intent_module, "notify", lambda message, **kwargs: called.__setitem__("n", called["n"] + 1) or True)
    routes = {"stock_below_rop": _route(TIER_T3)}
    ledger = AutonomyLedger(":memory:")

    tiered = handle_event_tiered(
        _event(), routes=routes, orchestrator=_test_orchestrator(), out_dir=tmp_path, ledger=ledger,
    )

    assert tiered.routed.result.status == STATUS_OK
    assert tiered.routed.notified is False
    assert called["n"] == 0
    assert tiered.outcome.status == ESCALATED


def test_handle_event_tiered_uses_the_real_config_routing_when_routes_not_supplied(tmp_path, monkeypatch):
    """stock_below_rop is configured T2 in the real config/event_routing.yaml
    -- exercises routes=None -> load_routing(DEFAULT_ROUTING_PATH) with the
    real file, end to end."""
    monkeypatch.setattr(event_intent_module, "notify", lambda message, **kwargs: True)

    tiered = handle_event_tiered(
        _event(), routing_path=DEFAULT_ROUTING_PATH, orchestrator=_test_orchestrator(), out_dir=tmp_path,
    )

    assert tiered.routed.route.autonomy_tier == "T2"
    assert tiered.routed.notified is False
    assert tiered.outcome.status == HANDOFF


# -- handle_event(): notify_on_ok backward compatibility (PR-6 addition) -------


def test_handle_event_notify_on_ok_true_is_the_unchanged_default(tmp_path, monkeypatch):
    """Every pre-PR-6 caller that never passes notify_on_ok keeps the exact
    old behavior: an ok result still notifies."""
    monkeypatch.setattr(event_intent_module, "notify", lambda message, **kwargs: True)

    routed = event_intent_module.handle_event(
        _event(), routing_path=DEFAULT_ROUTING_PATH, orchestrator=_test_orchestrator(), out_dir=tmp_path,
    )

    assert routed.result.status == STATUS_OK
    assert routed.notified is True


def test_handle_event_notify_on_ok_false_suppresses_notification_even_on_ok_status(tmp_path, monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(event_intent_module, "notify", lambda message, **kwargs: called.__setitem__("n", called["n"] + 1) or True)

    routed = event_intent_module.handle_event(
        _event(), routing_path=DEFAULT_ROUTING_PATH, orchestrator=_test_orchestrator(), out_dir=tmp_path,
        notify_on_ok=False,
    )

    assert routed.result.status == STATUS_OK  # the tool still ran and passed QA
    assert routed.notified is False
    assert called["n"] == 0
