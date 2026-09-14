"""Identity, capability attenuation, classification and the decision point.

The load-bearing test in this file is
:func:`test_no_sequence_of_attenuations_can_widen_authority` - everything else
guards a rule, but that one guards the *property* the design rests on: there is
no path through the algebra that produces more authority than you started with.
"""

from __future__ import annotations

import itertools

import pytest

from nosilo_core.access import (
    ALLOW,
    ANY,
    BUCKET,
    CONFIDENTIAL,
    DENY,
    DROP,
    INTERNAL,
    MASK,
    PUBLIC,
    REASON_CAPABILITY_ACTION,
    REASON_CAPABILITY_EXPIRED,
    REASON_CAPABILITY_PURPOSE,
    REASON_CAPABILITY_RESOURCE,
    REASON_EXPLICIT_DENY,
    REASON_NO_MATCHING_RULE,
    REASON_TENANT_MISMATCH,
    REASON_UNGOVERNED,
    REDACT,
    REQUIRE_APPROVAL,
    RESTRICTED,
    SENSITIVITY_RANK,
    AccessRequest,
    Capability,
    Classification,
    ClassificationError,
    DelegationError,
    FieldPolicy,
    PolicyEngine,
    PolicySet,
    Principal,
    Rule,
    delegate_to_agent,
    filter_values,
    permits,
    ungoverned_warning,
)

TENANT = "acme"

ANALYST = Principal(id="u1", kind="human", tenant=TENANT, roles=frozenset({"ap_analyst"}))
AGENT = Principal(id="a1", kind="agent", tenant=TENANT, roles=frozenset({"procurement"}), on_behalf_of="u1")


def _cap(**kwargs: object) -> Capability:
    return Capability(tenant=TENANT, **kwargs)  # type: ignore[arg-type]


# ---- principals -------------------------------------------------------------


def test_an_agent_without_a_delegating_human_cannot_exist() -> None:
    """No owner means no accountability, so there is nothing to authorise."""
    with pytest.raises(ValueError, match="on_behalf_of"):
        Principal(id="a1", kind="agent", tenant=TENANT)


def test_an_unknown_principal_kind_is_rejected() -> None:
    with pytest.raises(ValueError, match="unknown principal kind"):
        Principal(id="x", kind="robot", tenant=TENANT)


def test_a_blank_principal_id_is_rejected() -> None:
    with pytest.raises(ValueError, match="non-empty"):
        Principal(id="  ", kind="human")


# ---- the attenuation algebra ------------------------------------------------


def test_attenuation_intersects_rather_than_replaces() -> None:
    base = _cap(actions=frozenset({"read", "write"}))
    assert base.attenuate(actions=frozenset({"read", "delete"})).actions == frozenset({"read"})


def test_asking_for_something_not_held_simply_does_not_grant_it() -> None:
    base = _cap(actions=frozenset({"read"}))
    assert base.attenuate(actions=frozenset({"write"})).actions == frozenset()


def test_a_wildcard_cannot_widen_a_narrow_capability() -> None:
    """``X ∩ ANY == X``, in both directions - the load-bearing half."""
    narrow = _cap(actions=frozenset({"read"}))
    assert narrow.attenuate(actions=frozenset({ANY})).actions == frozenset({"read"})


def test_attenuating_a_wildcard_narrows_it() -> None:
    assert _cap().attenuate(actions=frozenset({"read"})).actions == frozenset({"read"})


def test_expiry_takes_the_earlier_of_the_two() -> None:
    base = _cap(expires_at="2026-06-01")
    assert base.attenuate(expires_at="2026-12-01").expires_at == "2026-06-01"
    assert base.attenuate(expires_at="2026-01-01").expires_at == "2026-01-01"


def test_sensitivity_takes_the_lower_of_the_two_ranks() -> None:
    base = _cap(max_sensitivity=CONFIDENTIAL)
    lowered = base.attenuate(max_sensitivity=RESTRICTED, sensitivity_rank=SENSITIVITY_RANK)
    assert lowered.max_sensitivity == CONFIDENTIAL  # cannot be raised to RESTRICTED
    assert base.attenuate(max_sensitivity=PUBLIC, sensitivity_rank=SENSITIVITY_RANK).max_sensitivity == PUBLIC


def test_comparing_unknown_sensitivity_levels_refuses_to_guess() -> None:
    with pytest.raises(DelegationError, match="rank mapping"):
        _cap(max_sensitivity=CONFIDENTIAL).attenuate(max_sensitivity="mystery")


def test_there_is_no_tenant_parameter_on_attenuate() -> None:
    """Crossing tenants is not a narrowing operation and must be unreachable."""
    with pytest.raises(TypeError):
        _cap().attenuate(tenant="other")  # type: ignore[call-arg]


def test_the_delegation_chain_is_recorded_for_audit() -> None:
    chained = _cap().attenuate(actions=frozenset({"read"}), label="u1->a1").attenuate(label="a1->tool")
    assert chained.chain == ("u1->a1", "a1->tool")


def test_no_sequence_of_attenuations_can_widen_authority() -> None:
    """The property the whole design rests on, checked exhaustively.

    Every ordering of every subset of a set of attenuation requests must land
    inside the original grant on every axis. If any path widened, an agent could
    manufacture authority by chaining innocuous-looking narrowings.
    """
    base = _cap(
        actions=frozenset({"read", "write"}),
        resources=frozenset({"sku", "supplier"}),
        purposes=frozenset({"p1", "p2"}),
        max_sensitivity=CONFIDENTIAL,
        expires_at="2026-06-01",
    )
    requests = [
        {"actions": frozenset({"read", "delete"})},
        {"resources": frozenset({ANY})},
        {"purposes": frozenset({"p2", "p3"})},
        {"max_sensitivity": RESTRICTED, "sensitivity_rank": SENSITIVITY_RANK},
        {"expires_at": "2027-01-01"},
        {"actions": frozenset({ANY}), "resources": frozenset({"invoice"})},
    ]

    for size in range(1, len(requests) + 1):
        for ordering in itertools.permutations(requests, size):
            result = base
            for step in ordering:
                result = result.attenuate(**step)  # type: ignore[arg-type]
            assert result.actions <= base.actions
            assert result.resources <= base.resources
            assert result.purposes <= base.purposes
            assert result.tenant == base.tenant
            assert SENSITIVITY_RANK[result.max_sensitivity] <= SENSITIVITY_RANK[base.max_sensitivity]
            assert result.expires_at <= base.expires_at


# ---- delegation -------------------------------------------------------------


def test_delegating_to_an_agent_intersects_human_role_and_purpose() -> None:
    human = _cap(actions=frozenset({"read", "write"}), purposes=frozenset({"p1", "p2"}))
    role = _cap(actions=frozenset({"read"}), resources=frozenset({"sku"}))
    minted = delegate_to_agent(
        delegator=ANALYST, delegator_capability=human, agent=AGENT,
        agent_role_capability=role, purpose="p1", expires_at="2026-01-01T00:15:00+00:00",
    )
    assert minted.actions == frozenset({"read"})
    assert minted.resources == frozenset({"sku"})
    assert minted.purposes == frozenset({"p1"})
    assert minted.expires_at == "2026-01-01T00:15:00+00:00"
    assert minted.chain == ("u1->a1",)


def test_an_agent_cannot_be_given_a_purpose_its_human_lacks() -> None:
    human = _cap(purposes=frozenset({"p1"}))
    with pytest.raises(DelegationError, match="may not act for purpose"):
        delegate_to_agent(
            delegator=ANALYST, delegator_capability=human, agent=AGENT,
            agent_role_capability=_cap(), purpose="p9", expires_at="2026-01-01",
        )


def test_delegation_cannot_cross_tenants() -> None:
    outsider = Principal(id="a1", kind="agent", tenant="other", on_behalf_of="u1")
    with pytest.raises(DelegationError, match="cross tenants"):
        delegate_to_agent(
            delegator=ANALYST, delegator_capability=_cap(), agent=outsider,
            agent_role_capability=_cap(), purpose="p1", expires_at="2026-01-01",
        )


def test_an_agent_cannot_claim_a_human_that_did_not_delegate_to_it() -> None:
    impostor = Principal(id="a2", kind="agent", tenant=TENANT, on_behalf_of="someone-else")
    with pytest.raises(DelegationError, match="claims to act for"):
        delegate_to_agent(
            delegator=ANALYST, delegator_capability=_cap(), agent=impostor,
            agent_role_capability=_cap(), purpose="p1", expires_at="2026-01-01",
        )


# ---- classification ---------------------------------------------------------


def test_clearance_is_compared_by_rank() -> None:
    assert permits(RESTRICTED, CONFIDENTIAL)
    assert not permits(INTERNAL, CONFIDENTIAL)
    assert permits(None, RESTRICTED)  # unrestricted


def test_unknown_levels_and_transforms_are_rejected_at_declaration() -> None:
    with pytest.raises(ClassificationError, match="unknown sensitivity level"):
        FieldPolicy(level="cosmic")
    with pytest.raises(ClassificationError, match="unknown transform"):
        FieldPolicy(below_clearance="shred")


def test_an_unclassified_field_defaults_to_internal_not_public() -> None:
    """A field nobody has thought about must not be the one that leaks."""
    assert Classification().policy_for("surprise").level == INTERNAL


def test_drop_removes_the_field_entirely() -> None:
    out = filter_values(
        {"secret": "x", "ok": "y"},
        Classification(fields={"secret": FieldPolicy(RESTRICTED, DROP), "ok": FieldPolicy(PUBLIC)}),
        clearance=PUBLIC,
    )
    assert "secret" not in out.values  # not even its existence
    assert out.values["ok"] == "y"
    assert out.transformed == {"secret": DROP}


def test_mask_keeps_only_a_short_suffix() -> None:
    out = filter_values(
        {"tax_id": "B12345678"},
        Classification(fields={"tax_id": FieldPolicy(RESTRICTED, MASK)}),
        clearance=PUBLIC,
    )
    assert out.values["tax_id"] == "*****5678"


def test_mask_does_not_leak_a_short_value() -> None:
    out = filter_values(
        {"pin": "123"}, Classification(fields={"pin": FieldPolicy(RESTRICTED, MASK)}), clearance=PUBLIC
    )
    assert out.values["pin"] == "***"


def test_redact_leaves_a_placeholder() -> None:
    out = filter_values(
        {"note": "sensitive"},
        Classification(fields={"note": FieldPolicy(RESTRICTED, REDACT)}),
        clearance=PUBLIC,
    )
    assert out.values["note"] == "[redacted]"


def test_bucket_keeps_order_of_magnitude_only() -> None:
    policy = Classification(fields={"amount": FieldPolicy(RESTRICTED, BUCKET)})
    assert filter_values({"amount": 4200}, policy, clearance=PUBLIC).values["amount"] == "1000-10000"
    assert filter_values({"amount": 0}, policy, clearance=PUBLIC).values["amount"] == "0"


def test_bucket_falls_back_to_redaction_for_a_non_number() -> None:
    policy = Classification(fields={"amount": FieldPolicy(RESTRICTED, BUCKET)})
    assert filter_values({"amount": "n/a"}, policy, clearance=PUBLIC).values["amount"] == "[redacted]"


def test_a_cleared_reader_sees_everything_untouched() -> None:
    out = filter_values(
        {"secret": "x"},
        Classification(fields={"secret": FieldPolicy(RESTRICTED, DROP)}),
        clearance=RESTRICTED,
    )
    assert out.values == {"secret": "x"}
    assert not out.was_filtered


# ---- the decision point -----------------------------------------------------


def _request(**kwargs: object) -> AccessRequest:
    defaults: dict[str, object] = {
        "principal": ANALYST, "action": "entity.read", "resource": "sku",
        "purpose": "planning", "tenant": TENANT,
    }
    return AccessRequest(**{**defaults, **kwargs})  # type: ignore[arg-type]


def test_an_unconfigured_engine_permits_everything_and_says_so() -> None:
    """The convention that lets this land without breaking a single caller."""
    decision = PolicyEngine().decide(_request())
    assert decision.effect == ALLOW
    assert decision.reason_code == REASON_UNGOVERNED


def test_no_matching_rule_denies() -> None:
    engine = PolicyEngine(PolicySet(version="v1", rules=(Rule(effect=ALLOW, actions=frozenset({"other"})),)))
    decision = engine.decide(_request())
    assert decision.effect == DENY
    assert decision.reason_code == REASON_NO_MATCHING_RULE


def test_an_explicit_deny_beats_any_allow() -> None:
    engine = PolicyEngine(
        PolicySet(version="v1", rules=(
            Rule(effect=ALLOW, description="broad allow"),
            Rule(effect=DENY, resources=frozenset({"sku"}), description="no skus"),
        ))
    )
    decision = engine.decide(_request())
    assert decision.effect == DENY
    assert decision.reason_code == REASON_EXPLICIT_DENY
    assert "no skus" in decision.explain()


def test_require_approval_is_not_treated_as_allowed() -> None:
    engine = PolicyEngine(PolicySet(version="v1", rules=(Rule(effect=REQUIRE_APPROVAL),)))
    decision = engine.decide(_request())
    assert decision.effect == REQUIRE_APPROVAL
    assert not decision.allowed


def test_every_decision_carries_a_reason_and_a_policy_version() -> None:
    """A denial with no reason is unactionable for the user and unauditable."""
    engine = PolicyEngine(PolicySet(version="2026-08-01.3"))
    for decision in (engine.decide(_request()), PolicyEngine().decide(_request())):
        assert decision.reason_code
        assert decision.policy_version


def test_a_principal_from_another_tenant_is_denied() -> None:
    engine = PolicyEngine(PolicySet(version="v1", rules=(Rule(effect=ALLOW),)))
    outsider = Principal(id="u9", kind="human", tenant="other")
    decision = engine.decide(_request(principal=outsider))
    assert decision.effect == DENY
    assert decision.reason_code == REASON_TENANT_MISMATCH


@pytest.mark.parametrize(
    ("capability", "expected"),
    [
        (Capability(tenant=TENANT, actions=frozenset({"write"})), REASON_CAPABILITY_ACTION),
        (Capability(tenant=TENANT, resources=frozenset({"invoice"})), REASON_CAPABILITY_RESOURCE),
        (Capability(tenant=TENANT, purposes=frozenset({"audit"})), REASON_CAPABILITY_PURPOSE),
        (Capability(tenant="other"), REASON_TENANT_MISMATCH),
    ],
)
def test_the_capability_is_checked_before_the_rules(capability: Capability, expected: str) -> None:
    engine = PolicyEngine(PolicySet(version="v1", rules=(Rule(effect=ALLOW),)))
    decision = engine.decide(_request(), capability=capability)
    assert decision.effect == DENY
    assert decision.reason_code == expected


def test_an_expired_capability_is_denied() -> None:
    engine = PolicyEngine(PolicySet(version="v1", rules=(Rule(effect=ALLOW),)))
    expired = Capability(tenant=TENANT, expires_at="2026-01-01T00:00:00+00:00")
    decision = engine.decide(_request(), capability=expired, now="2026-06-01T00:00:00+00:00")
    assert decision.reason_code == REASON_CAPABILITY_EXPIRED
    # ...and still valid before that instant.
    assert engine.decide(_request(), capability=expired, now="2025-12-01T00:00:00+00:00").allowed


def test_an_ungoverned_engine_is_flagged_in_production_only() -> None:
    engine = PolicyEngine()
    assert ungoverned_warning(engine, "development") == []
    assert len(ungoverned_warning(engine, "production")) == 1
    assert ungoverned_warning(PolicyEngine(PolicySet(version="v1")), "production") == []
