"""The deployment's access policy: the one rule, and the fields it classifies.

``webapp.security.POLICY`` ships as ``None`` - ungoverned, every principal
permitted everything, no field classification applied. That default is
deliberate and it is what let the access layer land without changing a single
caller. It is also the one state a production boot must not be left in, which is
why ``production_warnings()`` shouts about it and why ``LINCHPIN_REQUIRE_SECURE``
refuses to start in it.

Nothing in the tree ever set it. That is why deploying ``537a852`` on 2026-08-03
crashed the app at import with ``LINCHPIN_REQUIRE_SECURE is set but:
webapp.security.POLICY is not set`` and production has been running a
pre-merge image since. This module is the missing assignment.

**One rule, and why it is only one.** The API-key holder keeps exactly the
authority it has today. It is a *service* principal because a shared secret
identifies a deployment rather than a person (``webapp/principal.py``), and
narrowing it here would silently break every existing caller - the module that
derives it says so in as many words, and points at this file as the place where
narrowing belongs.

There is no rule for humans and none for agents because ``principal_from_request``
derives neither: ``LINCHPIN_TRUSTED_IDENTITY_HEADERS`` is off in production, so
every unauthenticated caller is anonymous and every authenticated one is the same
service. Rules for principals that cannot exist yet would read as an
authorisation model and decide nothing - config that looks alive.

**Anonymous is denied by the capability, not by a rule.**
``principal.ANONYMOUS_CAPABILITY_ACTIONS`` is empty and
``PolicyEngine.decide`` checks the capability *before* the rules, so an anonymous
caller is refused before rule matching begins. That is what lets this rule list be
one line long and still fail closed - the "no matching rule denies" default does
the rest.

**Declared debt, and it is the important part of this file.** This policy is
*configured, not enforced*. No production route calls
``security.policy_engine()`` - only tests do, which
``test_policy_is_declared_not_enforced`` pins so that the day somebody wires the
first call site, a test fails and sends them back here to rewrite this paragraph.
Setting ``POLICY`` therefore satisfies the boot guard and gives every future
decision a ``policy_version`` to cite; it does not change what any request
receives today. The honest reading: this unblocks the deploy and buys the
vocabulary. Wiring the first call site - the writeback approval path, or the
deliverable download - is the change that makes the boot guard tell the whole
truth, and it is deliberately not in this commit, because a security seam and the
route that uses it should be reviewable apart.
"""

from __future__ import annotations

from nosilo_core.access import ALLOW, PRINCIPAL_SERVICE, Classification, PolicySet, Rule
from packs.source_to_pay.entities import S2P_CLASSIFICATIONS
from src.entities import SCM_CLASSIFICATIONS

#: Recorded on every decision this policy produces. Change the rules, change this
#: - otherwise two different decisions cite the same version and the audit trail
#: cannot be replayed. Date plus a counter, so two changes in one day stay
#: distinguishable.
POLICY_VERSION = "2026-08-28.1"


SERVICE_KEEPS_TODAYS_AUTHORITY = Rule(
    effect=ALLOW,
    principal_kinds=frozenset({PRINCIPAL_SERVICE}),
    description=(
        "The API-key holder keeps the authority the shared key already grants. "
        "Narrowing it belongs in a rule per action once there is more than one "
        "service principal to tell apart; today there is exactly one."
    ),
)

#: The whole rule set. Ordered, because ``PolicySet`` records it as data and an
#: audit replay has to see the same list this deployment saw.
RULES: tuple[Rule, ...] = (SERVICE_KEEPS_TODAYS_AUTHORITY,)


class ClassificationCollision(ValueError):
    """Two packs classify the same entity type differently."""


def merged_classifications() -> dict[str, Classification]:
    """Every pack's field classifications, in one mapping keyed by entity type.

    Collisions raise instead of resolving. ``supplier`` is registered by the SCM
    pack and the source-to-pay pack deliberately reuses it rather than declaring
    its own, so the two mappings are disjoint today - but a silent ``update()``
    would let a laxer classification win a key it shares, and the field that
    changes level in that merge is exactly the kind nobody notices until it is in
    a response. Failing at import is the cheap end of that.
    """
    merged: dict[str, Classification] = dict(SCM_CLASSIFICATIONS)
    for entity_type, classification in S2P_CLASSIFICATIONS.items():
        existing = merged.get(entity_type)
        if existing is not None and existing != classification:
            raise ClassificationCollision(
                f"entity type {entity_type!r} is classified differently by two packs; "
                "register the shared type once and have both packs observe into it"
            )
        merged[entity_type] = classification
    return merged


def build_policy() -> PolicySet:
    """The ``PolicySet`` this deployment boots with."""
    return PolicySet(
        version=POLICY_VERSION,
        rules=RULES,
        classifications=merged_classifications(),
    )
