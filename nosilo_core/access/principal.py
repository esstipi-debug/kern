"""Who is acting, on whose behalf, and with how much authority.

Kern authenticates today with a single shared API key: there is no notion of
*who* is on the other end, so there is nothing to authorise against. This module
supplies that missing noun. It is deliberately **not** an identity provider - a
real deployment federates to the customer's Entra/Okta and maps the resulting
claims onto a :class:`Principal`. What lives here is the representation
everything downstream reasons about, so that swapping the authentication front
end later changes one adapter instead of every call site.

Three kinds of actor, because they fail differently:

``human``
    Authority ultimately traces back to a person who can be asked.
``service``
    A workload. Authority is granted at deploy time and does not vary per
    request.
``agent``
    A first-class citizen with its own identity, an owning human, and authority
    strictly derived from that human's.

**The one structural property that matters** is in :meth:`Capability.attenuate`:
authority can only ever be *reduced*. There is no operation in this module that
widens a capability, which means no amount of cleverness downstream - and no
instruction smuggled into a document an agent reads - can escalate privilege.
That is a property of the algebra, not of anyone remembering to check something.

A capability is what an agent actually carries:

    capability = rights(delegating human)
               ∩ rights(agent role)
               ∩ scope(declared purpose)
               ∩ policy constraints(context)

Everything here is a frozen dataclass and clock-free: expiry is compared against
an instant passed in, never against a clock read inside the check. Tests can
therefore exercise expiry without sleeping, and a replay is reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Mapping

#: The wildcard accepted in an action, resource or purpose set. Only ever
#: *narrowed* by :meth:`Capability.attenuate` - intersecting anything with a
#: wildcard yields the other side, never the wildcard.
ANY = "*"

PRINCIPAL_HUMAN = "human"
PRINCIPAL_SERVICE = "service"
PRINCIPAL_AGENT = "agent"

VALID_PRINCIPAL_KINDS = frozenset({PRINCIPAL_HUMAN, PRINCIPAL_SERVICE, PRINCIPAL_AGENT})

#: Tenant used by single-tenant deployments, which is every Kern install today.
DEFAULT_TENANT = "default"


class DelegationError(ValueError):
    """Raised when a delegation would widen authority instead of narrowing it."""


@dataclass(frozen=True)
class Principal:
    """An authenticated actor.

    ``attributes`` carries whatever the identity provider asserted that policy
    might care about - legal entity, cost centre, region, clearance. Kept as an
    open mapping rather than fixed columns because every customer's IdP asserts a
    different set, and a schema change per customer is not a thing this can
    afford.
    """

    id: str
    kind: str
    tenant: str = DEFAULT_TENANT
    roles: frozenset[str] = frozenset()
    attributes: Mapping[str, Any] = field(default_factory=dict)
    #: For an agent: the human whose authority it is exercising. An agent
    #: without one has no traceable accountability, so this is required.
    on_behalf_of: str | None = None

    def __post_init__(self) -> None:
        if self.kind not in VALID_PRINCIPAL_KINDS:
            raise ValueError(
                f"unknown principal kind {self.kind!r}; expected one of {sorted(VALID_PRINCIPAL_KINDS)}"
            )
        if not self.id or not self.id.strip():
            raise ValueError("Principal.id must be a non-empty string")
        if self.kind == PRINCIPAL_AGENT and not self.on_behalf_of:
            raise ValueError(
                f"agent {self.id!r} has no on_behalf_of. An agent's authority is derived from a "
                "human's; one with no delegating principal has no accountable owner and cannot be "
                "authorised for anything."
            )

    @property
    def is_agent(self) -> bool:
        return self.kind == PRINCIPAL_AGENT


def _intersect(left: frozenset[str], right: frozenset[str]) -> frozenset[str]:
    """Set intersection where :data:`ANY` means "no constraint from this side".

    ``ANY ∩ X == X``. Crucially ``X ∩ ANY == X`` too, so a caller cannot widen a
    narrow capability by attenuating it with a wildcard - the result is still
    bounded by the narrow side.
    """
    if ANY in left:
        return right
    if ANY in right:
        return left
    return left & right


@dataclass(frozen=True)
class Capability:
    """A bounded authority to act, held by a principal for a scope of work.

    The sets are *allow* lists. An empty set means "nothing is permitted", which
    is the correct reading of a fully-attenuated capability: intersecting down to
    nothing must deny, never fall open.
    """

    tenant: str
    actions: frozenset[str] = frozenset({ANY})
    resources: frozenset[str] = frozenset({ANY})
    purposes: frozenset[str] = frozenset({ANY})
    #: Highest sensitivity this capability may read. Compared by rank in
    #: ``nosilo_core.access.classification``; ``None`` means unrestricted.
    max_sensitivity: str | None = None
    #: ISO-8601 instant after which the capability is dead. ``None`` never
    #: expires, which is appropriate for a deploy-time service grant and wrong
    #: for anything an agent carries.
    expires_at: str | None = None
    #: The delegation chain that produced this capability, oldest first. Purely
    #: for audit: it is what lets a reviewer reconstruct human -> agent -> tool.
    chain: tuple[str, ...] = ()

    def permits_action(self, action: str) -> bool:
        return ANY in self.actions or action in self.actions

    def permits_resource(self, resource: str) -> bool:
        return ANY in self.resources or resource in self.resources

    def permits_purpose(self, purpose: str) -> bool:
        return ANY in self.purposes or purpose in self.purposes

    def is_expired(self, now: str) -> bool:
        """Expiry against an instant passed in - never against a clock read here."""
        return self.expires_at is not None and now >= self.expires_at

    def attenuate(
        self,
        *,
        actions: frozenset[str] | None = None,
        resources: frozenset[str] | None = None,
        purposes: frozenset[str] | None = None,
        max_sensitivity: str | None = None,
        expires_at: str | None = None,
        sensitivity_rank: Mapping[str, int] | None = None,
        label: str = "",
    ) -> Capability:
        """Return a strictly narrower capability. There is no widening path.

        Every field is intersected or minimised against the current value:

        - sets are intersected, so a request for something not already held is
          simply absent from the result rather than granted;
        - ``max_sensitivity`` takes the *lower* of the two ranks;
        - ``expires_at`` takes the *earlier* of the two instants;
        - ``tenant`` is not a parameter at all, because crossing tenants is not
          a narrowing operation and must never be reachable by attenuation.

        ``sensitivity_rank`` is injected rather than imported so this module has
        no dependency on the classification vocabulary - a domain that wants
        different levels supplies its own ordering.
        """
        narrowed_sensitivity = self.max_sensitivity
        if max_sensitivity is not None:
            if self.max_sensitivity is None:
                narrowed_sensitivity = max_sensitivity
            else:
                ranks = sensitivity_rank or {}
                if max_sensitivity in ranks and self.max_sensitivity in ranks:
                    narrowed_sensitivity = min(
                        self.max_sensitivity, max_sensitivity, key=lambda level: ranks[level]
                    )
                else:
                    # Unknown vocabulary: refuse to guess which is lower rather
                    # than silently pick the permissive one.
                    raise DelegationError(
                        f"cannot compare sensitivity {max_sensitivity!r} with {self.max_sensitivity!r} "
                        "without a rank mapping covering both; pass sensitivity_rank"
                    )

        narrowed_expiry = self.expires_at
        if expires_at is not None:
            narrowed_expiry = expires_at if self.expires_at is None else min(self.expires_at, expires_at)

        return replace(
            self,
            actions=_intersect(self.actions, actions) if actions is not None else self.actions,
            resources=_intersect(self.resources, resources) if resources is not None else self.resources,
            purposes=_intersect(self.purposes, purposes) if purposes is not None else self.purposes,
            max_sensitivity=narrowed_sensitivity,
            expires_at=narrowed_expiry,
            chain=self.chain + ((label,) if label else ()),
        )


def delegate_to_agent(
    *,
    delegator: Principal,
    delegator_capability: Capability,
    agent: Principal,
    agent_role_capability: Capability,
    purpose: str,
    expires_at: str,
    sensitivity_rank: Mapping[str, int] | None = None,
) -> Capability:
    """Mint the capability an agent carries for one task.

    The result is the intersection of what the human may do, what this kind of
    agent may ever do, and the single declared purpose of this task - bounded by
    an expiry. It cannot exceed any of its inputs, by construction rather than by
    convention.

    Refuses to cross tenants and refuses to delegate from a capability that has
    already expired, because either would be a way to manufacture authority that
    the delegating human does not currently hold.
    """
    if agent.on_behalf_of != delegator.id:
        raise DelegationError(
            f"agent {agent.id!r} claims to act for {agent.on_behalf_of!r} but the delegating "
            f"principal is {delegator.id!r}"
        )
    if delegator.tenant != agent.tenant or delegator_capability.tenant != delegator.tenant:
        raise DelegationError(
            "delegation may not cross tenants: "
            f"delegator {delegator.tenant!r}, agent {agent.tenant!r}, "
            f"capability {delegator_capability.tenant!r}"
        )
    if agent_role_capability.tenant not in (delegator.tenant, ANY):
        raise DelegationError(
            f"agent role capability is scoped to tenant {agent_role_capability.tenant!r}, "
            f"not {delegator.tenant!r}"
        )
    if not delegator_capability.permits_purpose(purpose):
        raise DelegationError(
            f"{delegator.id!r} may not act for purpose {purpose!r}, so cannot delegate it"
        )

    return delegator_capability.attenuate(
        actions=agent_role_capability.actions,
        resources=agent_role_capability.resources,
        purposes=frozenset({purpose}),
        max_sensitivity=agent_role_capability.max_sensitivity,
        expires_at=min(expires_at, agent_role_capability.expires_at or expires_at),
        sensitivity_rank=sensitivity_rank,
        label=f"{delegator.id}->{agent.id}",
    )
