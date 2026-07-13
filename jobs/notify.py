"""Webhook notification (Linchpin 3.0 PR-3, F0 -- ``jobs/notify.py``).

Notification v1 (plan S4.3): a single ``notify()`` posts a plain JSON payload
to a Slack-compatible incoming webhook via httpx, with basic retry. Every
other caller in this PR (the daily digest job) goes through this one
function, so swapping the transport later (plan: "todo detras de una unica
funcion notify() para que el swap sea trivial") touches one file.

Safe-by-default, matching the repo's existing "empty env var disables the
feature" convention (``LINCHPIN_API_KEY`` in ``webapp/security.py``,
``LINCHPIN_APPROVAL_SECRET`` in ``src/writeback.py``): with no webhook URL
configured, ``notify()`` no-ops and returns ``False`` -- it never raises, so
it is always safe to call from a job in tests/CI with zero config. httpx (the
``tower`` extra) is imported lazily and guarded the same way; without it,
``notify()`` also no-ops rather than failing the whole job.
"""

from __future__ import annotations

import os
import time

try:  # optional: the 'tower' extra
    import httpx

    _HAS_HTTPX = True
except ImportError:
    httpx = None
    _HAS_HTTPX = False

# Env var convention matching LINCHPIN_API_KEY / LINCHPIN_STATE_PATH / etc.
WEBHOOK_URL_ENV = "LINCHPIN_SLACK_WEBHOOK_URL"

DEFAULT_TIMEOUT_SECONDS = 10.0
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BACKOFF_SECONDS = 0.5


def notify(
    message: str,
    *,
    webhook_url: str | None = None,
    max_attempts: int = DEFAULT_MAX_ATTEMPTS,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    backoff_seconds: float = DEFAULT_BACKOFF_SECONDS,
    transport: object | None = None,
    **kwargs: object,
) -> bool:
    """POST ``{"text": message, **kwargs}`` to the webhook URL, with retry.

    Resolution order for the URL: the explicit ``webhook_url`` argument, else
    the ``LINCHPIN_SLACK_WEBHOOK_URL`` env var. No-ops (returns ``False``,
    never raises) when:

    - neither yields a non-blank URL, or
    - httpx is not installed (the ``tower`` extra is absent).

    Retries up to ``max_attempts`` times on a network error or a non-2xx
    response, sleeping ``backoff_seconds`` between attempts (skipped after
    the last attempt, so a same-first-try success or a no-op never sleeps).
    ``transport`` is an ``httpx`` transport (e.g. ``httpx.MockTransport``) for
    tests -- production code should leave it unset so httpx uses a real
    connection. Returns ``True`` only once an attempt gets a 2xx response.
    """
    url = (webhook_url if webhook_url is not None else os.environ.get(WEBHOOK_URL_ENV, "")).strip()
    if not url or not _HAS_HTTPX:
        return False

    payload: dict[str, object] = {"text": message, **kwargs}
    client_kwargs: dict[str, object] = {"timeout": timeout}
    if transport is not None:
        client_kwargs["transport"] = transport

    for attempt in range(max_attempts):
        try:
            with httpx.Client(**client_kwargs) as client:
                response = client.post(url, json=payload)
            if response.status_code // 100 == 2:
                return True
        except httpx.HTTPError:
            pass  # network/timeout/protocol error -- fall through to retry
        if attempt + 1 < max_attempts:
            time.sleep(backoff_seconds)
    return False
