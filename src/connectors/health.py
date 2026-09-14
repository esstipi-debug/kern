"""Connection-health plane: what a connector reports about its *link* to a
system of record, never about the data in it.

The distinction is the whole point. Repairing a connection -- opening a fresh
session, waiting out a throttle -- has no business consequence, so a connector
may do it unasked. Deciding whether Odoo's stock or Shopify's is the true one is
a business judgement and belongs to a human; a component that did both would
sooner or later "fix" the second while claiming the authority of the first.

**This plane reports; it does not retry.** ``on_auth_failure`` re-establishes a
session and returns whether a retry is warranted -- it does not re-issue the
operation. ``on_throttle`` returns how long to wait -- it does not sleep. That
split is load-bearing rather than stylistic: ``nosilo_core.writeback.apply``
releases the idempotency key when ``commit`` raises, so re-issuing a write after
a lost response DOES re-execute it, and ``odoo.py`` books purchase orders with
``execute_kw(..., "create")``. Only the caller knows whether the operation it
just lost is safe to repeat, so only the caller may repeat it.

Degrading gracefully is the house convention, and here it means: every helper
answers "no information" rather than raising. A health plane that threw while
being asked whether something was broken would be self-defeating.
"""

from __future__ import annotations

import logging
from typing import Any, Protocol, runtime_checkable

logger = logging.getLogger(__name__)

#: Ceiling for a computed wait. A throttle that suggests longer than this is
#: better surfaced to an operator than slept through inside a job.
MAX_BACKOFF_SECONDS = 60.0

#: Base of the exponential fallback used when a service throttles without
#: saying for how long.
BASE_BACKOFF_SECONDS = 1.0

_AUTH_STATUSES = frozenset({401, 403})
_THROTTLE_STATUSES = frozenset({429})


@runtime_checkable
class ConnectionHealth(Protocol):
    """The four questions every networked connector must answer."""

    def authenticate(self) -> None:
        """Open or renew the session. Idempotent: calling it twice is not an error."""

    def is_healthy(self) -> bool:
        """Cheap read-only probe. Returns False rather than raising when the link is down."""

    def on_auth_failure(self, exc: BaseException) -> bool:
        """True when the session was re-established and a retry is warranted.

        False when ``exc`` is not an auth failure, when this transport cannot
        mint a credential, or when re-authenticating has already been tried
        since the last healthy moment.
        """

    def on_throttle(self, exc: BaseException) -> float | None:
        """Seconds the caller should wait, or None when ``exc`` is not a throttle."""


def response_of(exc: BaseException) -> Any | None:
    """The response carried by an httpx-style error, or None.

    Duck-typed on purpose: the connectors accept any httpx-shaped object, so the
    health plane must not import httpx to interpret one.
    """
    return getattr(exc, "response", None)


def status_of(exc: BaseException) -> int | None:
    """HTTP status behind an error, or None when there is no HTTP in play."""
    response = response_of(exc)
    status = getattr(response, "status_code", None)
    try:
        return int(status) if status is not None else None
    except (TypeError, ValueError):
        return None


def is_auth_failure(exc: BaseException) -> bool:
    """A 401/403. 403 counts because Shopify answers a revoked token with it."""
    return status_of(exc) in _AUTH_STATUSES


def is_throttle(exc: BaseException) -> bool:
    return status_of(exc) in _THROTTLE_STATUSES


def retry_after_seconds(exc: BaseException) -> float | None:
    """The ``Retry-After`` delay in seconds, or None when absent or unparseable.

    Only the delta-seconds form is honoured. The HTTP-date form is legal but
    neither Shopify nor MercadoLibre emits it, and guessing at clock skew to
    support a form nobody sends would add a failure mode for no gain -- an
    unparseable value falls through to the bounded backoff, which is safe.
    """
    response = response_of(exc)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    raw = None
    try:
        for key, value in headers.items():
            if str(key).lower() == "retry-after":
                raw = value
                break
    except AttributeError:
        return None
    if raw is None:
        return None
    try:
        seconds = float(str(raw).strip())
    except (TypeError, ValueError):
        return None
    if seconds < 0:
        return None
    return min(seconds, MAX_BACKOFF_SECONDS)


def backoff_seconds(attempt: int = 0) -> float:
    """Bounded exponential fallback for a throttle that carries no hint.

    Bounded rather than unbounded because this number is handed to a caller that
    may well sleep on it inside a job with a deadline.
    """
    step = BASE_BACKOFF_SECONDS * (2 ** max(0, int(attempt)))
    return float(min(step, MAX_BACKOFF_SECONDS))


def throttle_wait(exc: BaseException, *, attempt: int = 0) -> float | None:
    """Seconds to wait for a throttling error, or None when it is not one.

    Never returns None *for a real throttle*: a missing ``Retry-After`` means
    "unspecified", not "no need to wait", and answering None there would read as
    "not throttled" and send the caller straight back at the endpoint.
    """
    if not is_throttle(exc):
        return None
    return retry_after_seconds(exc) or backoff_seconds(attempt)


class AuthRepairBudget:
    """One repair attempt per healthy streak.

    Without a budget, a credential that has been genuinely revoked produces an
    endless re-login loop: every attempt fails the same way and every failure
    looks freshly repairable. The budget is restored by evidence that the link
    works again -- a successful probe -- not by the passage of time.
    """

    def __init__(self) -> None:
        self._spent = False

    def spend(self) -> bool:
        """True if a repair may be attempted now; False once already spent."""
        if self._spent:
            return False
        self._spent = True
        return True

    def restore(self) -> None:
        self._spent = False


class NoCredentialRepair:
    """Mixin for transports handed a pre-built auth header at deploy time.

    Shopify and MercadoLibre receive ``X-Shopify-Access-Token`` /
    ``Authorization: Bearer`` already set on the injected HTTP object. There is
    no refresh token and no login endpoint reachable from here, so the honest
    answer to an auth failure is "not repairable by me". Saying otherwise would
    send the caller into a retry that re-fails identically.

    Fixing this properly means giving the connector the OAuth refresh flow --
    a credential-handling change that belongs to deployment wiring, not here.
    """

    def authenticate(self) -> None:
        """No-op: the credential arrives with the injected client."""

    def on_auth_failure(self, exc: BaseException) -> bool:
        """Always False -- but say why, at the moment it happens.

        An operator reading "401 and this transport cannot refresh" knows to go
        rotate a token; one reading a bare retry loop does not. The log line is
        the only place that distinction survives, since the return value cannot
        express it.
        """
        if is_auth_failure(exc):
            logger.warning(
                "%s: auth failed with HTTP %s and this transport holds no refreshable "
                "credential -- the token is wired at deploy time and must be rotated there",
                type(self).__name__,
                status_of(exc),
            )
        return False
