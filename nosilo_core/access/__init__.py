"""Identity and policy, in front of everything else in the core.

Kern authenticates with one shared API key today: there is no principal, so there
is nothing to authorise against, and "can this agent see supplier bank details"
has no answer the product can give. This package supplies the missing nouns and
the decision point that uses them.

``principal``
    Who is acting (human / service / agent), for which tenant, on whose behalf -
    and :class:`~.principal.Capability`, whose only composition operation
    *narrows* authority. There is no widening path in the module, so privilege
    escalation is absent from the algebra rather than prevented by a check
    somebody has to remember.
``classification``
    How sensitive each field is, and what a reader below that bar sees instead.
    Per field rather than per record, because a buyer legitimately needs a
    supplier's lead time while having no business seeing its bank account.
``policy``
    The decision point. Rules are versioned data, every decision carries a
    ``reason_code`` and a ``policy_version``, deny beats allow, and no matching
    rule denies.

**Nothing changes for existing callers.** A :class:`~.policy.PolicyEngine`
constructed with no policy allows everything and reports
:data:`~.policy.REASON_UNGOVERNED` - the same "empty disables the gate"
convention ``webapp/security.py`` already uses for the API key, the rate limit
and the approval secret. :func:`~.policy.ungoverned_warning` is how a production
deploy finds out it is still in that state.

Deliberately absent, each being an integration rather than a primitive: OIDC
federation (a real deployment maps its IdP's claims onto a ``Principal``),
workload identity, and the wiring into ``webapp``. The policy engine is also
deliberately not OPA/Rego - see ``policy.py`` for why, and for the trigger that
would change the answer.
"""

from .classification import (
    BUCKET,
    CONFIDENTIAL,
    DROP,
    INTERNAL,
    MASK,
    PUBLIC,
    REDACT,
    RESTRICTED,
    SENSITIVITY_RANK,
    Classification,
    ClassificationError,
    FieldPolicy,
    Filtered,
    filter_values,
    permits,
    rank,
)
from .policy import (
    ALLOW,
    ALLOW_WITH_TRANSFORM,
    DENY,
    REASON_CAPABILITY_ACTION,
    REASON_CAPABILITY_EXPIRED,
    REASON_CAPABILITY_PURPOSE,
    REASON_CAPABILITY_RESOURCE,
    REASON_EXPLICIT_DENY,
    REASON_NO_MATCHING_RULE,
    REASON_RULE_ALLOW,
    REASON_TENANT_MISMATCH,
    REASON_UNGOVERNED,
    REQUIRE_APPROVAL,
    AccessRequest,
    Decision,
    PolicyEngine,
    PolicySet,
    Rule,
    ungoverned_warning,
)
from .principal import (
    ANY,
    DEFAULT_TENANT,
    PRINCIPAL_AGENT,
    PRINCIPAL_HUMAN,
    PRINCIPAL_SERVICE,
    Capability,
    DelegationError,
    Principal,
    delegate_to_agent,
)

__all__ = [
    "ALLOW",
    "ALLOW_WITH_TRANSFORM",
    "ANY",
    "AccessRequest",
    "BUCKET",
    "CONFIDENTIAL",
    "Capability",
    "Classification",
    "ClassificationError",
    "DEFAULT_TENANT",
    "DENY",
    "DROP",
    "Decision",
    "DelegationError",
    "FieldPolicy",
    "Filtered",
    "INTERNAL",
    "MASK",
    "PRINCIPAL_AGENT",
    "PRINCIPAL_HUMAN",
    "PRINCIPAL_SERVICE",
    "PUBLIC",
    "PolicyEngine",
    "PolicySet",
    "Principal",
    "REASON_CAPABILITY_ACTION",
    "REASON_CAPABILITY_EXPIRED",
    "REASON_CAPABILITY_PURPOSE",
    "REASON_CAPABILITY_RESOURCE",
    "REASON_EXPLICIT_DENY",
    "REASON_NO_MATCHING_RULE",
    "REASON_RULE_ALLOW",
    "REASON_TENANT_MISMATCH",
    "REASON_UNGOVERNED",
    "REDACT",
    "REQUIRE_APPROVAL",
    "RESTRICTED",
    "Rule",
    "SENSITIVITY_RANK",
    "delegate_to_agent",
    "filter_values",
    "permits",
    "rank",
    "ungoverned_warning",
]
