"""Turning an HTTP request into a ``Principal`` the policy engine can reason about.

``nosilo_core.access`` supplies the vocabulary - who is acting, for which tenant,
with what capability - but deliberately knows nothing about HTTP. This module is
the adapter, and it is the only place in the app that decides *who someone is*.
Keeping that in one file is the point: identity derivation scattered across route
handlers is how an authorisation bypass ends up in a codebase without anyone
choosing it.

Three modes, in decreasing order of what they can prove:

**Trusted identity headers** (opt-in, off by default). A reverse proxy that has
already completed OIDC sets ``X-Auth-Subject`` / ``X-Auth-Roles`` /
``X-Auth-Tenant``, and this reads them. That is the shape every real deployment
ends up in, and it is also a **complete authentication bypass if the proxy is not
there** - a client can simply send the headers itself and become whoever it
likes. Hence: it requires an explicit ``LINCHPIN_TRUSTED_IDENTITY_HEADERS=1``,
the headers are ignored entirely without it, and
:func:`webapp.security.production_warnings` shouts if it is enabled in a shape
that cannot be safe.

**API key.** The caller proved it holds the shared secret, which identifies a
deployment, not a person. It therefore becomes a *service* principal - naming it
a human would put a name on an audit entry that no human is accountable for.

**Anonymous.** Everything else. This is what every request is today, and what
every request stays until an operator configures one of the modes above, which is
why this whole file changes nothing by default.

Nothing here raises. A request whose identity cannot be established is anonymous,
never a 500: an exception in identity derivation would turn a malformed header
into an outage, and the authorisation decision is the policy engine's job
anyway - unauthenticated is an answer, not an error.
"""

from __future__ import annotations

import hmac
import os

from nosilo_core.access import (
    ANY,
    DEFAULT_TENANT,
    PRINCIPAL_HUMAN,
    PRINCIPAL_SERVICE,
    Capability,
    Principal,
)

#: Read at call time via module globals so tests (and a reload) can flip them
#: with ``monkeypatch.setattr`` - the same convention ``webapp.security`` uses.
TRUSTED_IDENTITY_HEADERS = os.environ.get("LINCHPIN_TRUSTED_IDENTITY_HEADERS", "").strip().lower() in (
    "1",
    "true",
    "yes",
)
DEFAULT_TENANT_NAME = os.environ.get("LINCHPIN_TENANT", "").strip() or DEFAULT_TENANT

HEADER_SUBJECT = "x-auth-subject"
HEADER_ROLES = "x-auth-roles"
HEADER_TENANT = "x-auth-tenant"

#: Identity of a caller that presented no credentials.
ANONYMOUS_ID = "anonymous"
#: Identity of a caller that presented the shared API key.
API_KEY_SERVICE_ID = "api-key-holder"


def anonymous(tenant: str | None = None) -> Principal:
    """The principal every request gets when nothing better can be established."""
    return Principal(id=ANONYMOUS_ID, kind=PRINCIPAL_HUMAN, tenant=tenant or DEFAULT_TENANT_NAME)


def _roles_from_header(raw: str) -> frozenset[str]:
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


def principal_from_request(request, *, api_key: str = "") -> Principal:
    """Derive the caller's identity. Never raises.

    ``api_key`` is passed in rather than read from ``webapp.security`` so this
    module has no import cycle with it and can be tested without the app.
    """
    try:
        headers = request.headers
    except Exception:  # pragma: no cover - defensive: a request without headers
        return anonymous()

    if TRUSTED_IDENTITY_HEADERS:
        subject = (headers.get(HEADER_SUBJECT) or "").strip()
        if subject:
            return Principal(
                id=subject,
                kind=PRINCIPAL_HUMAN,
                tenant=(headers.get(HEADER_TENANT) or "").strip() or DEFAULT_TENANT_NAME,
                roles=_roles_from_header(headers.get(HEADER_ROLES) or ""),
                attributes={"source": "trusted_headers"},
            )

    if api_key:
        provided = headers.get("x-api-key", "")
        if hmac.compare_digest(provided, api_key):
            # A shared secret identifies a deployment, not a person. Calling this
            # a human would put a name on an audit entry nobody is accountable for.
            return Principal(
                id=API_KEY_SERVICE_ID,
                kind=PRINCIPAL_SERVICE,
                tenant=DEFAULT_TENANT_NAME,
                attributes={"source": "api_key"},
            )

    return anonymous()


#: What each kind of principal may do before policy narrows it further.
#:
#: Anonymous gets nothing: an empty action set denies, which is the correct
#: reading of "we do not know who you are". Note this only bites once a
#: PolicySet is configured - an ungoverned engine permits everything regardless,
#: which is what keeps today's behaviour unchanged.
ANONYMOUS_CAPABILITY_ACTIONS: frozenset[str] = frozenset()

#: The API-key holder inherits today's authority: the shared key is currently the
#: only gate, so anything behind it is already fully trusted. Narrowing it here
#: would silently break every existing deployment; narrowing it belongs in the
#: PolicySet an operator writes.
SERVICE_CAPABILITY_ACTIONS: frozenset[str] = frozenset({ANY})


def capability_for(principal: Principal, *, expires_at: str | None = None) -> Capability:
    """The starting authority for a principal, before policy narrows it.

    Roles are carried into the capability's purposes untouched. Mapping roles to
    finer grants is a deployment's decision and belongs in its ``PolicySet``,
    not hardcoded here - a role vocabulary baked into the application is one that
    cannot be changed without a release.
    """
    if principal.kind == PRINCIPAL_SERVICE:
        actions = SERVICE_CAPABILITY_ACTIONS
    elif principal.id == ANONYMOUS_ID:
        actions = ANONYMOUS_CAPABILITY_ACTIONS
    else:
        actions = frozenset({ANY})

    return Capability(
        tenant=principal.tenant,
        actions=actions,
        expires_at=expires_at,
        chain=(principal.id,),
    )
