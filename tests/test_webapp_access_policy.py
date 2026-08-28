"""The access policy the app boots with, and the debt it does not pay.

Every test here except the last one fails on the tree before
``webapp/access_policy.py`` existed - that is the point of them. The last one
passes before and after on purpose: it is a tripwire on the declared debt, not
evidence of the fix.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from nosilo_core.access import (
    ALLOW,
    DENY,
    DROP,
    RESTRICTED,
    AccessRequest,
    Classification,
    FieldPolicy,
    PolicyEngine,
)

from webapp import access_policy, principal, security

REPO_ROOT = Path(__file__).resolve().parents[1]

#: Where a production call site would live if one existed. `tests/` is out
#: because exercising the engine is what tests are for.
SEARCHED_PACKAGES = ("webapp", "src", "packs", "jobs", "scm_agent")


def _called_name(func: ast.expr) -> str | None:
    """The bare name of whatever is being called.

    An AST walk rather than a substring scan, because `webapp/access_policy.py`
    and `webapp/security.py` both write `policy_engine()` in prose - a text
    search reports the docstring that *describes* the absence of a call site as
    if it were one, which is a tripwire that fires on its own explanation.
    """
    if isinstance(func, ast.Name):
        return func.id
    if isinstance(func, ast.Attribute):
        return func.attr
    return None


def _decide(a_principal, action: str = "read", resource: str = "sku"):
    engine = PolicyEngine(access_policy.build_policy())
    request = AccessRequest(
        principal=a_principal,
        action=action,
        resource=resource,
        purpose="planning",
        tenant=a_principal.tenant,
    )
    return engine.decide(request, capability=principal.capability_for(a_principal))


# ---- the boot ----------------------------------------------------------------


def test_importing_the_app_leaves_a_policy_configured() -> None:
    """The whole reason this module exists.

    Before it, `import webapp.app` left `security.POLICY` at None, and a
    production boot with LINCHPIN_REQUIRE_SECURE raised at import instead of
    binding a port.
    """
    import webapp.app  # noqa: F401  - imported for its side effect, which is the subject

    assert security.POLICY is not None
    assert security.POLICY.version == access_policy.POLICY_VERSION


def test_a_require_secure_production_boot_has_nothing_left_to_warn_about(monkeypatch) -> None:
    monkeypatch.setattr(security, "ENV", "production")
    monkeypatch.setattr(security, "API_KEY", "a-real-key")
    monkeypatch.setattr(security, "RATE_LIMIT", 120)
    monkeypatch.setattr(security, "APPROVAL_SECRET", "a-real-secret")
    monkeypatch.setattr(security, "POLICY", access_policy.build_policy())
    monkeypatch.setattr(principal, "TRUSTED_IDENTITY_HEADERS", False)

    assert security.production_warnings() == []


def test_the_version_is_recorded_on_the_decision() -> None:
    """An audit replay cites a version; a decision that cannot name one is unauditable."""
    decision = _decide(principal.anonymous())
    assert decision.policy_version == access_policy.POLICY_VERSION


# ---- what the one rule actually decides --------------------------------------


def test_the_api_key_holder_keeps_todays_authority() -> None:
    service = principal.Principal(
        id=principal.API_KEY_SERVICE_ID,
        kind=principal.PRINCIPAL_SERVICE,
        tenant=principal.DEFAULT_TENANT_NAME,
    )
    assert _decide(service).effect == ALLOW


def test_an_anonymous_caller_is_refused_before_the_rules_are_consulted() -> None:
    """Denied by the empty capability, not by a rule.

    Worth pinning separately: it is what lets the rule list be one line long and
    still fail closed. If someone ever widens ANONYMOUS_CAPABILITY_ACTIONS, the
    single ALLOW rule below it does NOT deny anonymous - this test is the alarm.
    """
    decision = _decide(principal.anonymous())
    assert decision.effect == DENY
    assert "capability" in decision.reason_code


def test_an_unknown_principal_kind_gets_nothing_from_this_rule_set() -> None:
    """No matching rule denies. Adding an agent principal must not silently permit it."""
    agent = principal.Principal(
        id="agent-1",
        kind="agent",
        tenant=principal.DEFAULT_TENANT_NAME,
        on_behalf_of="a-human",
    )
    assert _decide(agent).effect == DENY


# ---- the fields --------------------------------------------------------------


def test_the_policy_carries_the_field_classifications() -> None:
    policy = access_policy.build_policy()
    supplier = policy.classification_for("supplier")

    assert supplier.policy_for("bank_account").level == RESTRICTED
    assert supplier.policy_for("bank_account").below_clearance == DROP


def test_the_source_to_pay_fields_travel_with_the_same_policy() -> None:
    """Both packs' classifications, in one PolicySet. A pack whose fields fall out
    of the merge is a pack whose sensitive fields are classified by the default."""
    policy = access_policy.build_policy()
    invoice = policy.classification_for("supplier_invoice")

    assert invoice.policy_for("remit_to_account").level == RESTRICTED
    assert invoice.policy_for("remit_to_account").below_clearance == DROP


def test_an_undeclared_field_falls_to_internal_and_is_dropped() -> None:
    """Fail closed: a field nobody has classified yet must not be the one that leaks."""
    policy = access_policy.build_policy()
    unknown = policy.classification_for("supplier").policy_for("something_nobody_declared")

    assert unknown.below_clearance == DROP


def test_two_packs_classifying_one_entity_differently_raises(monkeypatch) -> None:
    """A silent update() would let the laxer classification win a shared key."""
    monkeypatch.setattr(
        access_policy,
        "S2P_CLASSIFICATIONS",
        {"supplier": Classification(fields={"bank_account": FieldPolicy(level="public")})},
    )
    with pytest.raises(access_policy.ClassificationCollision):
        access_policy.merged_classifications()


# ---- the tripwire on the declared debt ---------------------------------------


def test_the_policy_is_declared_and_not_yet_enforced() -> None:
    """Passes before and after the fix, on purpose.

    `webapp/access_policy.py` says in its docstring that the policy is configured
    and not enforced, because no production route calls `policy_engine()`. The
    day one does, that paragraph becomes false - and a docstring nobody rereads
    is exactly how a security note rots into a lie. So the claim is a test: when
    the first call site lands, this fails and sends whoever wrote it back to
    rewrite the paragraph and delete this test.
    """
    call_sites = []
    for package in SEARCHED_PACKAGES:
        for path in (REPO_ROOT / package).rglob("*.py"):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Call) and _called_name(node.func) == "policy_engine":
                    call_sites.append(f"{path.relative_to(REPO_ROOT)}:{node.lineno}")

    assert call_sites == [], (
        "policy_engine() now has a production call site: "
        f"{call_sites}. The policy is no longer merely declared - update the "
        "declared-debt paragraph in webapp/access_policy.py and delete this test."
    )
