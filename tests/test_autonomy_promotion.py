"""Tests for T2->T1 autonomy promotion / immediate T1->T2 degradation
(Linchpin 3.0 PR-9, plan section 5, Golden Rule 11).

Guarantees under test:
- load_autonomy_config() parses the real config/autonomy.yaml (and rejects a
  malformed one loudly);
- evaluate_promotion(): a route with 4+ consecutive cycles >=92% precision
  (the plan's own worked example, section 2.3) produces a PromotionProposal
  with the evidence attached; only 2 consecutive good cycles (below N)
  produces NO promotion; an already-T1 route never gets a promotion
  candidate; mismatched-tool evidence raises loudly instead of silently
  justifying a promotion with someone else's numbers;
- evaluate_degradation(): a single bad cycle on a T1 tool IMMEDIATELY flags
  degradation (no N-cycle confirmation, asymmetric vs promotion); a T2/T3
  route is never degraded (nothing to degrade FROM); an honest "no signal"
  report (headline_precision is None) is not itself a failure;
- PromotionLedger records/reads/resolves proposals, and refuses to resolve
  an unknown or already-resolved id (loud, not silent);
- RoutingConfigStore's regex-scoped rewrite touches ONLY the target route's
  autonomy_tier token, leaving every comment and every other route's block
  byte-for-byte untouched;
- approve_promotion(): a PENDING proposal, once approved, actually mutates
  config/event_routing.yaml as a real, signed src.writeback.Changeset --
  END TO END: propose -> approve -> reload routing -> resolve_route() on a
  brand-new synthetic event of that type now resolves T1, not T2;
- apply_degradation(): T1->T2 degradation happens immediately (no approval
  call anywhere in the path) and is still fully audited (a durable
  PromotionRecord + a real Changeset/AuditEntry);
- evaluate_tier_transition(): the one-call cycle picks promotion vs
  degradation correctly based on the route's CURRENT tier.
"""

from __future__ import annotations

import pytest

from scm_agent.autonomy import TIER_T1, TIER_T2, TIER_T3
from scm_agent.autonomy_promotion import (
    DEFAULT_AUTONOMY_CONFIG_PATH,
    KIND_DEGRADATION,
    KIND_PROMOTION,
    PROMOTION_STATUS_APPROVED,
    PROMOTION_STATUS_AUTO_APPLIED,
    PROMOTION_STATUS_PENDING,
    PROMOTION_STATUS_REJECTED,
    AutonomyConfig,
    AutonomyConfigError,
    PromotionLedger,
    RoutingConfigStore,
    apply_degradation,
    approve_promotion,
    evaluate_degradation,
    evaluate_promotion,
    evaluate_tier_transition,
    load_autonomy_config,
    propose_promotion,
    reject_promotion,
)
from scm_agent.event_intent import DEFAULT_ROUTING_PATH, Event, EventRoutingError, Route, load_routing, resolve_route
from src.guided import EXECUTED, HANDOFF, passed_guided
from src.verify.reliability import ToolReliabilityReport

TOOL = "inventory_optimization"

_CONFIG = AutonomyConfig(min_consecutive_cycles=4, min_precision=0.92, degradation_floor=0.80)

_FIXTURE_ROUTING_YAML = """\
# header comment -- must survive every rewrite untouched
version: 1

routes:
  stock_below_rop:
    tool: inventory_optimization
    param_builder: inventory_from_stock_event
    autonomy_tier: T2

  rop_breach:
    tool: inventory_optimization
    param_builder: inventory_from_state_stock_event
    autonomy_tier: T2

  excess_growing:
    tool: excess_obsolete
    param_builder: excess_obsolete_from_state_stock_event
    autonomy_tier: T3
"""


def _report(precision: float | None, *, tool: str = TOOL, n_decisions: int = 50) -> ToolReliabilityReport:
    """A hand-constructed reliability cycle -- avoids building a full
    MatchedObservation list per cycle (tests/test_verify_reliability.py
    already covers that construction path)."""
    n_hits = int(round((precision or 0.0) * n_decisions))
    return ToolReliabilityReport(
        tool=tool, n_decisions=n_decisions, n_skus=10, n_hits=n_hits, n_excluded_zero_actual=0,
        hit_rate=precision, mean_wape=0.05, mean_bias=0.0, headline_precision=precision,
        meets_threshold=bool(precision is not None and precision >= 0.85), threshold=0.85,
    )


def _route(tier: str, *, event_type: str = "stock_below_rop", tool: str = TOOL) -> Route:
    return Route(event_type=event_type, tool=tool, param_builder="inventory_from_stock_event", autonomy_tier=tier)


# -- load_autonomy_config() ------------------------------------------------------


def test_load_autonomy_config_reads_the_real_config_file():
    cfg = load_autonomy_config(DEFAULT_AUTONOMY_CONFIG_PATH)
    assert cfg.min_consecutive_cycles == 4
    assert cfg.min_precision == pytest.approx(0.92)
    assert cfg.degradation_floor == pytest.approx(0.80)


def test_load_autonomy_config_missing_file_raises(tmp_path):
    with pytest.raises(AutonomyConfigError):
        load_autonomy_config(tmp_path / "does_not_exist.yaml")


def test_load_autonomy_config_rejects_missing_field(tmp_path):
    path = tmp_path / "autonomy.yaml"
    path.write_text("promotion:\n  min_consecutive_cycles: 4\n", encoding="utf-8")
    with pytest.raises(AutonomyConfigError):
        load_autonomy_config(path)


def test_load_autonomy_config_rejects_out_of_range_precision(tmp_path):
    path = tmp_path / "autonomy.yaml"
    path.write_text(
        "promotion:\n  min_consecutive_cycles: 4\n  min_precision: 1.5\n"
        "degradation:\n  min_precision_floor: 0.8\n",
        encoding="utf-8",
    )
    with pytest.raises(AutonomyConfigError):
        load_autonomy_config(path)


# -- evaluate_promotion(): evidence-gated, N-cycle -------------------------------


def test_evaluate_promotion_with_4_consecutive_92pct_cycles_produces_a_proposal_with_evidence():
    """The plan's own worked example (section 2.3), pushed past the
    promotion bar and sustained: 4 consecutive cycles at/above 92%."""
    route = _route(TIER_T2)
    reports = [_report(0.94), _report(0.93), _report(0.95), _report(0.92)]

    proposal = evaluate_promotion(route, reports, config=_CONFIG)

    assert proposal is not None
    assert proposal.event_type == "stock_below_rop"
    assert proposal.tool == TOOL
    assert proposal.from_tier == TIER_T2
    assert proposal.to_tier == TIER_T1
    assert proposal.evidence == tuple(reports)
    assert "92%" in proposal.rationale or "4 consecutive" in proposal.rationale


def test_evaluate_promotion_with_only_2_consecutive_good_cycles_produces_no_promotion():
    """Below N=4 -- not enough evidence yet, even though both cycles clear
    the precision bar."""
    route = _route(TIER_T2)
    reports = [_report(0.95), _report(0.96)]

    assert evaluate_promotion(route, reports, config=_CONFIG) is None


def test_evaluate_promotion_one_bad_cycle_within_the_tail_blocks_it():
    route = _route(TIER_T2)
    reports = [_report(0.95), _report(0.60), _report(0.95), _report(0.95)]  # a miss in the last N

    assert evaluate_promotion(route, reports, config=_CONFIG) is None


def test_evaluate_promotion_only_examines_the_tail_not_older_history():
    """An old bad cycle OUTSIDE the last N does not block a promotion --
    only the most recent N consecutive cycles matter."""
    route = _route(TIER_T2)
    reports = [_report(0.10), _report(0.94), _report(0.93), _report(0.95), _report(0.92)]

    proposal = evaluate_promotion(route, reports, config=_CONFIG)

    assert proposal is not None
    assert len(proposal.evidence) == 4


def test_evaluate_promotion_none_headline_precision_blocks_it():
    """An honest 'no verifiable signal' cycle (headline_precision is None)
    never silently counts as a good cycle."""
    route = _route(TIER_T2)
    reports = [_report(0.95), _report(0.95), _report(0.95), _report(None)]

    assert evaluate_promotion(route, reports, config=_CONFIG) is None


def test_evaluate_promotion_already_t1_returns_none():
    route = _route(TIER_T1)
    reports = [_report(0.99) for _ in range(4)]

    assert evaluate_promotion(route, reports, config=_CONFIG) is None


def test_evaluate_promotion_rejects_mismatched_tool_evidence():
    route = _route(TIER_T2, tool=TOOL)
    reports = [_report(0.95, tool="some_other_tool") for _ in range(4)]

    with pytest.raises(ValueError, match="some_other_tool|inventory_optimization"):
        evaluate_promotion(route, reports, config=_CONFIG)


def test_evaluate_promotion_uses_the_real_config_when_none_given():
    """No config= override -- reads the real config/autonomy.yaml (N=4,
    92%), matching load_autonomy_config()'s own test above."""
    route = _route(TIER_T2)
    reports = [_report(0.94) for _ in range(3)]  # 3 < real N=4

    assert evaluate_promotion(route, reports) is None


# -- evaluate_degradation(): immediate, single-cycle -----------------------------


def test_evaluate_degradation_flags_immediately_on_a_single_bad_report():
    """A single failing cycle on a T1 tool -- no waiting for N cycles
    (asymmetric vs promotion)."""
    route = _route(TIER_T1)
    bad_report = _report(0.50)  # well below the 0.80 floor

    flag = evaluate_degradation(route, bad_report, config=_CONFIG)

    assert flag is not None
    assert flag.event_type == "stock_below_rop"
    assert flag.from_tier == TIER_T1
    assert flag.to_tier == TIER_T2
    assert flag.evidence == (bad_report,)


def test_evaluate_degradation_a_report_above_the_floor_does_not_flag():
    route = _route(TIER_T1)
    good_report = _report(0.90)

    assert evaluate_degradation(route, good_report, config=_CONFIG) is None


def test_evaluate_degradation_only_applies_to_a_currently_t1_route():
    """A T2 (or T3) route has nothing to degrade FROM."""
    bad_report = _report(0.10)

    assert evaluate_degradation(_route(TIER_T2), bad_report, config=_CONFIG) is None
    assert evaluate_degradation(_route(TIER_T3), bad_report, config=_CONFIG) is None


def test_evaluate_degradation_none_headline_precision_is_not_itself_a_failure():
    """An honest 'no signal yet' report (too few matched observations) must
    not be treated as a reliability FAILURE -- that would degrade a tool for
    lack of data, not for a real bad outcome."""
    route = _route(TIER_T1)
    no_signal_report = _report(None)

    assert evaluate_degradation(route, no_signal_report, config=_CONFIG) is None


def test_evaluate_degradation_rejects_mismatched_tool():
    route = _route(TIER_T1, tool=TOOL)
    other_tool_report = _report(0.10, tool="some_other_tool")

    with pytest.raises(ValueError):
        evaluate_degradation(route, other_tool_report, config=_CONFIG)


# -- PromotionLedger --------------------------------------------------------------


def test_promotion_ledger_record_and_get_roundtrip():
    ledger = PromotionLedger(":memory:")
    reports = (_report(0.94), _report(0.93))

    record = ledger.record(
        kind=KIND_PROMOTION, status=PROMOTION_STATUS_PENDING, event_type="stock_below_rop", tool=TOOL,
        from_tier=TIER_T2, to_tier=TIER_T1, rationale="test rationale", evidence=reports,
    )
    fetched = ledger.get(record.id)

    assert fetched is not None
    assert fetched.status == PROMOTION_STATUS_PENDING
    assert fetched.tool == TOOL
    assert len(fetched.evidence) == 2
    assert fetched.evidence[0]["headline_precision"] == pytest.approx(0.94)
    assert fetched.resolved_by is None


def test_promotion_ledger_get_unknown_id_returns_none():
    assert PromotionLedger(":memory:").get("nope") is None


def test_promotion_ledger_list_pending_only_returns_pending_promotions():
    ledger = PromotionLedger(":memory:")
    pending = ledger.record(
        kind=KIND_PROMOTION, status=PROMOTION_STATUS_PENDING, event_type="a", tool=TOOL,
        from_tier=TIER_T2, to_tier=TIER_T1, rationale="x", evidence=(),
    )
    ledger.record(
        kind=KIND_DEGRADATION, status=PROMOTION_STATUS_AUTO_APPLIED, event_type="b", tool=TOOL,
        from_tier=TIER_T1, to_tier=TIER_T2, rationale="y", evidence=(), resolved_by="system(auto-degrade)",
    )

    assert [r.id for r in ledger.list_pending()] == [pending.id]


def test_promotion_ledger_resolve_flips_status_and_stamps_who():
    ledger = PromotionLedger(":memory:")
    record = ledger.record(
        kind=KIND_PROMOTION, status=PROMOTION_STATUS_PENDING, event_type="a", tool=TOOL,
        from_tier=TIER_T2, to_tier=TIER_T1, rationale="x", evidence=(),
    )

    resolved = ledger.resolve(record.id, "alice", PROMOTION_STATUS_APPROVED)

    assert resolved.status == PROMOTION_STATUS_APPROVED
    assert resolved.resolved_by == "alice"
    assert resolved.resolved_at is not None
    assert ledger.list_pending() == []


def test_promotion_ledger_resolve_unknown_id_raises_keyerror():
    with pytest.raises(KeyError):
        PromotionLedger(":memory:").resolve("nope", "alice", PROMOTION_STATUS_APPROVED)


def test_promotion_ledger_resolve_already_resolved_raises_valueerror():
    ledger = PromotionLedger(":memory:")
    record = ledger.record(
        kind=KIND_PROMOTION, status=PROMOTION_STATUS_PENDING, event_type="a", tool=TOOL,
        from_tier=TIER_T2, to_tier=TIER_T1, rationale="x", evidence=(),
    )
    ledger.resolve(record.id, "alice", PROMOTION_STATUS_APPROVED)

    with pytest.raises(ValueError, match="not pending"):
        ledger.resolve(record.id, "bob", PROMOTION_STATUS_APPROVED)


def test_reject_promotion_marks_rejected_and_never_touches_config(tmp_path):
    routing_path = tmp_path / "routing.yaml"
    routing_path.write_text(_FIXTURE_ROUTING_YAML, encoding="utf-8")
    before = routing_path.read_text(encoding="utf-8")
    ledger = PromotionLedger(":memory:")
    record = ledger.record(
        kind=KIND_PROMOTION, status=PROMOTION_STATUS_PENDING, event_type="stock_below_rop", tool=TOOL,
        from_tier=TIER_T2, to_tier=TIER_T1, rationale="x", evidence=(),
    )

    rejected = reject_promotion(ledger, record.id, "bob")

    assert rejected.status == PROMOTION_STATUS_REJECTED
    assert routing_path.read_text(encoding="utf-8") == before  # untouched


# -- RoutingConfigStore / _rewrite_route_tier: scoped, comment-preserving -------


def test_routing_config_store_read_reflects_current_file():
    store = RoutingConfigStore(DEFAULT_ROUTING_PATH)
    assert store.read("stock_below_rop") == {"autonomy_tier": "T2"}


def test_routing_config_store_read_unknown_event_type_returns_empty_dict():
    store = RoutingConfigStore(DEFAULT_ROUTING_PATH)
    assert store.read("no_such_event_type") == {}


def test_routing_config_store_commit_rewrites_only_the_targeted_route(tmp_path):
    routing_path = tmp_path / "routing.yaml"
    routing_path.write_text(_FIXTURE_ROUTING_YAML, encoding="utf-8")
    store = RoutingConfigStore(routing_path)
    import src.writeback as writeback

    changeset = writeback.stage(
        store, str(routing_path), {"stock_below_rop": {"autonomy_tier": "T1"}},
        risk_tier=writeback.TIER_REVERSIBLE, idempotency_key="k1",
    )
    store.commit(changeset, "alice")

    new_text = routing_path.read_text(encoding="utf-8")
    assert "# header comment -- must survive every rewrite untouched" in new_text
    routes = load_routing(routing_path)
    assert routes["stock_below_rop"].autonomy_tier == "T1"
    assert routes["rop_breach"].autonomy_tier == "T2"       # untouched
    assert routes["excess_growing"].autonomy_tier == "T3"   # untouched


def test_rewrite_route_tier_raises_for_unknown_event_type():
    from scm_agent.autonomy_promotion import _rewrite_route_tier

    with pytest.raises(EventRoutingError):
        _rewrite_route_tier(_FIXTURE_ROUTING_YAML, "no_such_route", "T1")


# -- approve_promotion(): END TO END -- config actually changes -----------------


def test_approve_promotion_end_to_end_changes_the_enforced_tier_for_a_new_event(tmp_path):
    """propose -> approve -> reload routing -> resolve_route() on a NEW
    synthetic event of that type now goes T1, not T2."""
    routing_path = tmp_path / "routing.yaml"
    routing_path.write_text(_FIXTURE_ROUTING_YAML, encoding="utf-8")
    route = _route(TIER_T2, event_type="stock_below_rop")
    reports = [_report(0.94), _report(0.93), _report(0.95), _report(0.92)]
    ledger = PromotionLedger(":memory:")

    proposal = evaluate_promotion(route, reports, config=_CONFIG)
    assert proposal is not None
    record = propose_promotion(ledger, proposal)
    assert record.status == PROMOTION_STATUS_PENDING

    outcome = approve_promotion(ledger, record.id, "alice", routing_path=routing_path, now=0.0)

    assert outcome.status == EXECUTED
    assert passed_guided(outcome)
    assert ledger.get(record.id).status == PROMOTION_STATUS_APPROVED
    assert ledger.get(record.id).resolved_by == "alice"

    reloaded_routes = load_routing(routing_path)
    assert reloaded_routes["stock_below_rop"].autonomy_tier == TIER_T1

    new_event = Event(type="stock_below_rop", severity="high", source="monitors",
                       dedup_key="SKU-NEW:stock_below_rop", sku="SKU-NEW", payload={})
    resolved = resolve_route(new_event, reloaded_routes)
    assert resolved.autonomy_tier == TIER_T1  # the T1 path, not T2


def test_approve_promotion_unknown_id_raises_keyerror(tmp_path):
    routing_path = tmp_path / "routing.yaml"
    routing_path.write_text(_FIXTURE_ROUTING_YAML, encoding="utf-8")
    with pytest.raises(KeyError):
        approve_promotion(PromotionLedger(":memory:"), "nope", "alice", routing_path=routing_path)


def test_approve_promotion_already_resolved_raises_valueerror(tmp_path):
    routing_path = tmp_path / "routing.yaml"
    routing_path.write_text(_FIXTURE_ROUTING_YAML, encoding="utf-8")
    ledger = PromotionLedger(":memory:")
    record = ledger.record(
        kind=KIND_PROMOTION, status=PROMOTION_STATUS_PENDING, event_type="stock_below_rop", tool=TOOL,
        from_tier=TIER_T2, to_tier=TIER_T1, rationale="x", evidence=(),
    )
    approve_promotion(ledger, record.id, "alice", routing_path=routing_path, now=0.0)

    with pytest.raises(ValueError, match="not pending"):
        approve_promotion(ledger, record.id, "bob", routing_path=routing_path, now=1.0)


def test_approve_promotion_rejects_a_stale_proposal_whose_config_already_moved(tmp_path):
    """The route was already promoted to T1 by some other path -- approving
    a proposal that still expects T2 must fail loudly, not silently
    re-apply (or worse, mis-rewrite) an already-changed route."""
    routing_path = tmp_path / "routing.yaml"
    routing_path.write_text(_FIXTURE_ROUTING_YAML, encoding="utf-8")
    ledger = PromotionLedger(":memory:")
    record = ledger.record(
        kind=KIND_PROMOTION, status=PROMOTION_STATUS_PENDING, event_type="stock_below_rop", tool=TOOL,
        from_tier=TIER_T2, to_tier=TIER_T1, rationale="x", evidence=(),
    )
    # Simulate the config having already moved (e.g. a prior approval).
    store = RoutingConfigStore(routing_path)
    import src.writeback as writeback
    cs = writeback.stage(store, str(routing_path), {"stock_below_rop": {"autonomy_tier": "T1"}},
                          risk_tier=writeback.TIER_REVERSIBLE, idempotency_key="k-preexisting")
    store.commit(cs, "someone-else")

    with pytest.raises(ValueError, match="now"):
        approve_promotion(ledger, record.id, "alice", routing_path=routing_path, now=0.0)


# -- apply_degradation(): immediate, no human approval ---------------------------


def test_apply_degradation_immediately_changes_config_with_no_approval_call(tmp_path):
    routing_path = tmp_path / "routing.yaml"
    routing_path.write_text(_FIXTURE_ROUTING_YAML.replace("autonomy_tier: T2\n\n  rop_breach",
                                                            "autonomy_tier: T1\n\n  rop_breach"),
                             encoding="utf-8")
    route = _route(TIER_T1, event_type="stock_below_rop")
    bad_report = _report(0.40)
    flag = evaluate_degradation(route, bad_report, config=_CONFIG)
    assert flag is not None
    ledger = PromotionLedger(":memory:")

    outcome = apply_degradation(ledger, flag, routing_path=routing_path, now=0.0)

    assert outcome.status == EXECUTED
    assert passed_guided(outcome)
    reloaded = load_routing(routing_path)
    assert reloaded["stock_below_rop"].autonomy_tier == TIER_T2

    records = ledger.list_all()
    assert len(records) == 1
    assert records[0].kind == KIND_DEGRADATION
    assert records[0].status == PROMOTION_STATUS_AUTO_APPLIED
    assert records[0].resolved_by == "system(auto-degrade)"
    assert records[0].evidence[0]["headline_precision"] == pytest.approx(0.40)


def test_apply_degradation_is_a_race_safe_noop_when_route_already_moved(tmp_path):
    routing_path = tmp_path / "routing.yaml"
    routing_path.write_text(_FIXTURE_ROUTING_YAML, encoding="utf-8")  # stock_below_rop is T2, not T1
    flag_from_stale_t1_route = evaluate_degradation(
        _route(TIER_T1, event_type="stock_below_rop"), _report(0.10), config=_CONFIG,
    )
    ledger = PromotionLedger(":memory:")

    outcome = apply_degradation(ledger, flag_from_stale_t1_route, routing_path=routing_path, now=0.0)

    assert outcome.status == EXECUTED
    assert "already" in outcome.summary.lower()
    reloaded = load_routing(routing_path)
    assert reloaded["stock_below_rop"].autonomy_tier == "T2"  # unchanged
    assert ledger.list_all() == []  # nothing recorded for a no-op


# -- evaluate_tier_transition(): the one-call cycle picks the right side -------


def test_evaluate_tier_transition_promotes_a_t2_route_with_enough_evidence(tmp_path):
    routing_path = tmp_path / "routing.yaml"
    routing_path.write_text(_FIXTURE_ROUTING_YAML, encoding="utf-8")
    route = _route(TIER_T2, event_type="stock_below_rop")
    reports = [_report(0.94), _report(0.93), _report(0.95), _report(0.92)]
    ledger = PromotionLedger(":memory:")

    outcome = evaluate_tier_transition(
        route, reports, ledger=ledger, config=_CONFIG, routing_path=routing_path,
    )

    assert outcome is not None
    assert outcome.status == HANDOFF  # awaiting a human's approve_promotion(), not auto-applied
    assert len(ledger.list_pending()) == 1
    # config is untouched until a human approves
    assert load_routing(routing_path)["stock_below_rop"].autonomy_tier == "T2"


def test_evaluate_tier_transition_degrades_a_t1_route_immediately(tmp_path):
    routing_path = tmp_path / "routing.yaml"
    routing_path.write_text(_FIXTURE_ROUTING_YAML.replace("autonomy_tier: T2\n\n  rop_breach",
                                                            "autonomy_tier: T1\n\n  rop_breach"),
                             encoding="utf-8")
    route = _route(TIER_T1, event_type="stock_below_rop")
    reports = [_report(0.95), _report(0.95), _report(0.40)]  # only the LATEST matters
    ledger = PromotionLedger(":memory:")

    outcome = evaluate_tier_transition(
        route, reports, ledger=ledger, config=_CONFIG, routing_path=routing_path, now=0.0,
    )

    assert outcome is not None
    assert outcome.status == EXECUTED  # no human step
    assert load_routing(routing_path)["stock_below_rop"].autonomy_tier == "T2"


def test_evaluate_tier_transition_returns_none_when_nothing_qualifies(tmp_path):
    routing_path = tmp_path / "routing.yaml"
    routing_path.write_text(_FIXTURE_ROUTING_YAML, encoding="utf-8")
    route = _route(TIER_T2, event_type="stock_below_rop")
    reports = [_report(0.50), _report(0.50)]  # not enough evidence, and too weak anyway
    ledger = PromotionLedger(":memory:")

    assert evaluate_tier_transition(route, reports, ledger=ledger, config=_CONFIG, routing_path=routing_path) is None
