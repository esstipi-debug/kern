"""Minimal, polite HTTP fetcher for one already-approved competitor PDP URL
(Linchpin 3.0 PR-13's one-shot ``acquire`` step -- plan section 6.1's
``Fetcher`` protocol applied to L1 structured-data extraction against a
client-supplied URL, not the later-PR L0 API / L2 watcher / L3 spider
fetchers named in the plan's file tree).

Deliberately thin, on purpose (plan S6.0 principle 5, "cortesia tecnica" /
"nunca se disfraza el fetcher"): one GET request, a fixed identifiable
User-Agent, a caller-set timeout, NO retries, NO proxy rotation, NO header
spoofing, NO cookie jar tricks. A blocked/degraded site is reported back to
the caller as a plain ``RawObservation`` -- ``acquire/base.py``'s
``classify_blocking_signal``/``CircuitBreaker`` decide what to do about it;
this module never works around a block itself.

Network I/O lives ONLY here (HARD RULE: fetches belong in
``src/pricing_intel/acquire/``, never in ``extract.py``/``normalize.py``).
``fetch_pdp_html`` takes an ``httpx.Client`` as a constructor-style
parameter (dependency injection) specifically so tests can pass
``httpx.Client(transport=httpx.MockTransport(handler))`` and this module
never performs a real network call in the test suite (see
``tests/test_pricing_intel_pdp_fetcher.py``).

Caller contract (matches ``acquire/base.py``'s ``Fetcher`` protocol
docstring): call ``require_approved_site(domain)`` once before ever calling
this function for that domain, and consult a ``CircuitBreaker`` before every
call (``allow_request`` first; ``record_success``/``record_failure`` after,
keyed off ``classify_blocking_signal``) -- this module does not do either of
those itself, it only performs the one HTTP call it is asked to.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from .base import RawObservation

# Identifiable, honest User-Agent (plan S6.0 #5: "user-agent identificable").
# No browser impersonation, no rotation -- a site operator inspecting logs
# can tell exactly what hit their server and why.
USER_AGENT = "LinchpinPricingIntel/1.0 (+https://kern.example/pricing-intel-bot)"

DEFAULT_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True)
class FetchError:
    """A fetch attempt that never got a well-formed HTTP response at all
    (DNS failure, connection refused, timeout, TLS error, ...) -- distinct
    from a successful-but-blocking response (403/429/captcha), which comes
    back as an ordinary ``RawObservation`` with that ``status_code`` for
    ``classify_blocking_signal`` to inspect. Per ``classify_blocking_signal``'s
    own docstring, a transport-level failure like this is NOT a blocking
    signal and must not trip the circuit breaker -- the caller should treat
    it as an ordinary transient failure (log/skip/retry-later-on-its-own-cycle),
    never as evidence the site is blocking us.
    """

    url: str
    fetched_at: datetime
    reason: str  # str(exception) -- human-readable, never a raw traceback


def fetch_pdp_html(
    url: str,
    *,
    client: httpx.Client,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    now: datetime | None = None,
) -> RawObservation | FetchError:
    """Perform exactly one polite GET against ``url`` and report the result.

    ``client`` is required (not defaulted to a fresh ``httpx.Client()``
    internally) so every call site is explicit about which client -- and
    therefore which timeout/headers/transport -- it is using; production
    callers construct one real ``httpx.Client(timeout=..., headers=
    {"User-Agent": USER_AGENT})`` per acquisition run and pass it through,
    tests construct one backed by ``httpx.MockTransport``.

    Returns a :class:`~..acquire.base.RawObservation` (``status_code`` +
    ``html`` -- ``html`` is the response body text on ANY status code, even
    403/429, so the caller's ``classify_blocking_signal`` can inspect it for
    a captcha marker) on a completed HTTP round-trip, or a :class:`FetchError`
    when the round-trip itself never completed. Never raises -- a network
    failure is data for the caller to act on, not an exception to catch.
    """
    now = now or datetime.now(timezone.utc)
    try:
        response = client.get(url, timeout=timeout, headers={"User-Agent": USER_AGENT})
    except httpx.HTTPError as exc:
        return FetchError(url=url, fetched_at=now, reason=str(exc))
    return RawObservation(sku_ref=url, fetched_at=now, status_code=response.status_code, html=response.text)
