"""The decision point: may this principal do this, to this, for this purpose?

A small, declarative policy engine. Rules are *data* - a list of frozen
dataclasses, versioned as a set - not Python callbacks, because a decision has to
be reproducible from a recorded ``policy_version`` months later during an audit,
and a callback cannot be replayed from an audit log.

**Why not OPA/Rego.** It is the right answer at scale and the wrong dependency
here: ``nosilo_core`` is stdlib-only by contract (``tests/test_core_boundary.py``
enforces it), and a sidecar process is a large operational ask for a rule set
this size. The trigger to move: when policy needs to be authored and deployed by
someone who is not shipping the application, or when rules start needing data
joins this cannot express. Until then the shape here maps onto Rego cleanly, so
the migration is a translation rather than a redesign.

**Every decision explains itself.** ``reason_code`` and ``policy_version`` are
required on the way out, including on denials. "Access denied" with no reason is
unactionable for the user and unauditable for the reviewer, and it is the single
most common way an authorisation layer becomes hated.

**Deny wins.** An explicit DENY beats any number of ALLOWs, and no matching rule
denies. Both are the safe direction, and the second one means adding a new action
or resource type does not silently become permitted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from .classification import SENSITIVITY_RANK, Classification, Filtered, filter_values
from .principal import ANY, Capability, Principal

#: Permitted outright.
ALLOW = "allow"
#: Refused.
DENY = "deny"
#: Permitted, but some fields come back transformed.
ALLOW_WITH_TRANSFORM = "allow_with_transform"
#: Permitted only once a human signs off. The caller routes to an approval queue;
#: it is not a soft allow and must never be treated as one.
REQUIRE_APPROVAL = "require_approval"

EFFECTS = frozenset({ALLOW, DENY, ALLOW_WITH_TRANSFORM, REQUIRE_APPROVAL})

# Reason codes. Deliberately an enumeration rather than free text so the UI can
# translate them and an auditor can count them.
REASON_NO_MATCHING_RULE = "no_matching_rule"
REASON_EXPLICIT_DENY = "explicit_deny"
REASON_RULE_ALLOW = "rule_allow"
REASON_CAPABILITY_ACTION = "capability_forbids_action"
REASON_CAPABILITY_RESOURCE = "capability_forbids_resource"
REASON_CAPABILITY_PURPOSE = "capability_forbids_purpose"
REASON_CAPABILITY_EXPIRED = "capability_expired"
REASON_TENANT_MISMATCH = "tenant_mismatch"
REASON_UNGOVERNED = "no_policy_configured"


@dataclass(frozen=True)
class AccessRequest:
    """One thing a principal wants to do, with the purpose it declared."""

    principal: Principal
    action: str
    resource: str
    purpose: str
    tenant: str
    context: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Rule:
    """One declarative policy rule.

    Empty sets mean "matches anything", which reads naturally: a rule that names
    no roles applies to every role. ``ANY`` is accepted too, for symmetry with
    :class:`~.principal.Capability`.
    """

    effect: str
    actions: frozenset[str] = frozenset()
    resources: frozenset[str] = frozenset()
    purposes: frozenset[str] = frozenset()
    roles: frozenset[str] = frozenset()
    principal_kinds: frozenset[str] = frozenset()
    description: str = ""

    def __post_init__(self) -> None:
        if self.effect not in EFFECTS:
            raise ValueError(f"unknown effect {self.effect!r}; expected one of {sorted(EFFECTS)}")

    @staticmethod
    def _matches(declared: frozenset[str], candidates: frozenset[str]) -> bool:
        if not declared or ANY in declared:
            return True
        return bool(declared & candidates)

    def matches(self, request: AccessRequest) -> bool:
        return (
            self._matches(self.actions, frozenset({request.action}))
            and self._matches(self.resources, frozenset({request.resource}))
            and self._matches(self.purposes, frozenset({request.purpose}))
            and self._matches(self.roles, frozenset(request.principal.roles))
            and self._matches(self.principal_kinds, frozenset({request.principal.kind}))
        )


@dataclass(frozen=True)
class Decision:
    """What the policy engine decided, and enough to defend it later."""

    effect: str
    reason_code: str
    policy_version: str
    transforms: Mapping[str, str] = field(default_factory=dict)
    obligations: tuple[str, ...] = ()
    detail: str = ""

    @property
    def allowed(self) -> bool:
        """``REQUIRE_APPROVAL`` is deliberately not allowed.

        Treating "a human must sign this off" as a soft yes is how approval gates
        get quietly bypassed; a caller must handle it explicitly.
        """
        return self.effect in (ALLOW, ALLOW_WITH_TRANSFORM)

    def explain(self) -> str:
        base = f"{self.effect} ({self.reason_code}) under policy {self.policy_version}"
        return f"{base}: {self.detail}" if self.detail else base


@dataclass(frozen=True)
class PolicySet:
    """A versioned, ordered set of rules.

    ``version`` is recorded on every decision. Change the rules, change the
    version - otherwise two different decisions cite the same policy and the
    audit trail cannot be replayed.
    """

    version: str
    rules: tuple[Rule, ...] = ()
    #: ``entity type -> Classification``, consulted when a decision needs to say
    #: which fields come back transformed.
    classifications: Mapping[str, Classification] = field(default_factory=dict)
    #: Obligations attached to every allow, e.g. ``("log_full",)``.
    default_obligations: tuple[str, ...] = ()

    def classification_for(self, resource: str) -> Classification:
        return self.classifications.get(resource, Classification())


class PolicyEngine:
    """The decision point (PDP).

    Construct with ``policy=None`` for an ungoverned engine that allows
    everything and says so via :data:`REASON_UNGOVERNED`. That is the
    "empty disables the gate" convention the rest of the repo already uses
    (``webapp/security.py``'s ``API_KEY`` / ``RATE_LIMIT`` /
    ``APPROVAL_SECRET``), and it is what lets this layer land without changing
    the behaviour of a single existing caller. It is also exactly the
    configuration a production deploy must not be left in - see
    :func:`ungoverned_warning`.
    """

    def __init__(self, policy: PolicySet | None = None) -> None:
        self._policy = policy

    @property
    def policy(self) -> PolicySet | None:
        return self._policy

    @property
    def is_governed(self) -> bool:
        return self._policy is not None

    def decide(
        self,
        request: AccessRequest,
        *,
        capability: Capability | None = None,
        now: str | None = None,
    ) -> Decision:
        """Evaluate one request.

        The capability is checked *before* the rules, not after: a rule can only
        ever permit something inside the capability's bounds, so a capability
        failure is both cheaper and more specific to report.
        """
        version = self._policy.version if self._policy else "ungoverned"

        if capability is not None:
            denial = self._capability_denial(request, capability, now, version)
            if denial is not None:
                return denial

        if self._policy is None:
            return Decision(
                effect=ALLOW,
                reason_code=REASON_UNGOVERNED,
                policy_version=version,
                detail="no policy configured; every request is permitted",
            )

        if request.principal.tenant != request.tenant:
            return Decision(
                effect=DENY,
                reason_code=REASON_TENANT_MISMATCH,
                policy_version=version,
                detail=f"principal is in tenant {request.principal.tenant!r}, request targets {request.tenant!r}",
            )

        matched = [rule for rule in self._policy.rules if rule.matches(request)]

        for rule in matched:
            if rule.effect == DENY:
                return Decision(
                    effect=DENY,
                    reason_code=REASON_EXPLICIT_DENY,
                    policy_version=version,
                    detail=rule.description,
                )

        for rule in matched:
            if rule.effect == REQUIRE_APPROVAL:
                return Decision(
                    effect=REQUIRE_APPROVAL,
                    reason_code=REASON_RULE_ALLOW,
                    policy_version=version,
                    obligations=self._policy.default_obligations,
                    detail=rule.description,
                )

        for rule in matched:
            if rule.effect in (ALLOW, ALLOW_WITH_TRANSFORM):
                return Decision(
                    effect=rule.effect,
                    reason_code=REASON_RULE_ALLOW,
                    policy_version=version,
                    obligations=self._policy.default_obligations,
                    detail=rule.description,
                )

        return Decision(
            effect=DENY,
            reason_code=REASON_NO_MATCHING_RULE,
            policy_version=version,
            detail=(
                f"no rule permits {request.action!r} on {request.resource!r} "
                f"for purpose {request.purpose!r}"
            ),
        )

    @staticmethod
    def _capability_denial(
        request: AccessRequest, capability: Capability, now: str | None, version: str
    ) -> Decision | None:
        if capability.tenant != request.tenant:
            return Decision(
                effect=DENY,
                reason_code=REASON_TENANT_MISMATCH,
                policy_version=version,
                detail=f"capability is scoped to tenant {capability.tenant!r}",
            )
        if now is not None and capability.is_expired(now):
            return Decision(
                effect=DENY,
                reason_code=REASON_CAPABILITY_EXPIRED,
                policy_version=version,
                detail=f"capability expired at {capability.expires_at}",
            )
        if not capability.permits_action(request.action):
            return Decision(
                effect=DENY,
                reason_code=REASON_CAPABILITY_ACTION,
                policy_version=version,
                detail=f"capability does not carry action {request.action!r}",
            )
        if not capability.permits_resource(request.resource):
            return Decision(
                effect=DENY,
                reason_code=REASON_CAPABILITY_RESOURCE,
                policy_version=version,
                detail=f"capability does not carry resource {request.resource!r}",
            )
        if not capability.permits_purpose(request.purpose):
            return Decision(
                effect=DENY,
                reason_code=REASON_CAPABILITY_PURPOSE,
                policy_version=version,
                detail=f"capability does not cover purpose {request.purpose!r}",
            )
        return None

    def filter_fields(
        self,
        resource: str,
        values: Mapping[str, Any],
        *,
        clearance: str | None,
    ) -> Filtered:
        """Apply the resource's classification for a reader at ``clearance``."""
        classification = self._policy.classification_for(resource) if self._policy else Classification()
        if self._policy is None:
            return Filtered(values=dict(values), transformed={})
        return filter_values(values, classification, clearance)


def ungoverned_warning(engine: PolicyEngine, environment: str) -> list[str]:
    """Production misconfiguration warnings, in ``webapp.security`` house style.

    An ungoverned engine is the correct default for development and a serious
    problem in production, where it means every principal can read every field of
    every entity. Surfaced the same way the API-key and rate-limit gaps already
    are, so a single startup check covers all of them.
    """
    if environment.strip().lower() != "production" or engine.is_governed:
        return []
    return [
        "nosilo_core.access: no PolicySet configured - every principal is permitted "
        "every action on every entity, and no field classification is applied"
    ]


#: Re-exported so callers can hand the ordering to ``Capability.attenuate``
#: without importing the classification module directly.
DEFAULT_SENSITIVITY_RANK = SENSITIVITY_RANK
