"""Structured request logging for the web app.

One log record per request on the ``linchpin.access`` logger, carrying a request
id (echoed as ``X-Request-ID``), method, path, status, and duration. Records
propagate by default so uvicorn / pytest see them; operators who want JSON lines
or a fixed level call :func:`configure_logging` (or wire their own handler).
"""

from __future__ import annotations

import json
import logging
import os
import time
import uuid

from fastapi import Request

_LOG = logging.getLogger("linchpin.access")
_FIELDS = ("request_id", "method", "path", "status", "duration_ms", "client")


def _request_id(request: Request) -> str:
    # Honour an upstream/proxy-supplied id for trace continuity, else mint one.
    return request.headers.get("x-request-id") or uuid.uuid4().hex[:16]


async def request_log_middleware(request: Request, call_next):
    rid = _request_id(request)
    start = time.monotonic()
    response = await call_next(request)
    duration_ms = round((time.monotonic() - start) * 1000.0, 1)
    response.headers.setdefault("X-Request-ID", rid)
    _LOG.info(
        "request",
        extra={
            "request_id": rid,
            "method": request.method,
            "path": request.url.path,
            "status": response.status_code,
            "duration_ms": duration_ms,
            "client": request.client.host if request.client else None,
        },
    )
    return response


class _JsonFormatter(logging.Formatter):
    """One JSON object per line, pulling the structured access fields off the record."""

    def format(self, record: logging.LogRecord) -> str:
        payload = {"ts": self.formatTime(record), "level": record.levelname, "msg": record.getMessage()}
        for key in _FIELDS:
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload, separators=(",", ":"))


def configure_logging() -> None:
    """Opt-in: route the access logger to stdout per env. Call once at startup.

    ``LINCHPIN_LOG_LEVEL`` (default ``INFO``) and ``LINCHPIN_LOG_JSON=1`` for JSON
    lines. Sets ``propagate = False`` to avoid double emission once a handler is
    attached, so this is for real deploys — tests/dev leave it unset.
    """
    level = os.environ.get("LINCHPIN_LOG_LEVEL", "INFO").upper()
    as_json = os.environ.get("LINCHPIN_LOG_JSON", "").strip().lower() in ("1", "true", "yes")
    handler = logging.StreamHandler()
    if as_json:
        handler.setFormatter(_JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s rid=%(request_id)s "
                "%(method)s %(path)s -> %(status)s %(duration_ms)sms"
            )
        )
    _LOG.handlers = [handler]
    _LOG.setLevel(level)
    _LOG.propagate = False


def should_configure_logging() -> bool:
    """True when the operator asked for explicit log config via env."""
    return bool(os.environ.get("LINCHPIN_LOG_JSON") or os.environ.get("LINCHPIN_LOG_LEVEL"))


# -- Error tracking (Sentry), gated entirely by SENTRY_DSN ---------------------
#
# Shipped OFF by default: with SENTRY_DSN unset (dev, CI, tests) init_observability()
# is a no-op and makes no network call. An operator turns it on by setting the
# SENTRY_DSN secret on the deployment -- the same "no-op until the operator opts
# in" pattern as LINCHPIN_API_KEY. This is what turns "prod went down and I found
# out by hand" into an alert.

_OBS_LOG = logging.getLogger("linchpin.observability")
_DEFAULT_SENTRY_ENVIRONMENT = "production"


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _env_sample_rate(name: str, default: float) -> float:
    """Parse a [0.0, 1.0] sample-rate env var; anything unparseable or
    out-of-range falls back to ``default`` (never raises). ``traces_sample_rate``
    only governs performance-tracing volume -- it never affects error capture."""
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        return default
    return value if 0.0 <= value <= 1.0 else default


def init_observability() -> bool:
    """Initialize Sentry error tracking iff ``SENTRY_DSN`` is set.

    Returns ``True`` when Sentry was actually initialized, ``False`` on every
    no-op / degraded path (DSN unset, or ``sentry-sdk`` not installed). **Never
    raises** -- observability must not be able to take down the app it observes.

    ``sentry-sdk`` is imported lazily and its absence tolerated (a warning, not a
    crash), so this boot-chain module never breaks the app's import even if the
    package is stripped (see the ``prod-boot`` CI gate). With ``fastapi``
    installed, ``sentry_sdk.init()`` auto-enables the FastAPI/Starlette
    integration -- unhandled exceptions that become 500s are captured with no
    per-route wiring. Performance tracing is OFF by default
    (``traces_sample_rate=0.0``); opt in via ``SENTRY_TRACES_SAMPLE_RATE``.

    PII: ``send_default_pii`` is ``False`` unless the operator explicitly opts in
    with ``SENTRY_SEND_DEFAULT_PII=1`` -- Sentry must never attach request
    bodies/headers/user ids to events (the repo's "never surface PII" rule).
    """
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return False  # shipped default: observability off, zero side effects
    try:
        import sentry_sdk
    except ImportError:
        _OBS_LOG.warning(
            "SENTRY_DSN is set but sentry-sdk is not installed; error tracking is OFF. "
            "Install the web extra (pip install '.[web]') to enable it."
        )
        return False

    environment = os.environ.get("SENTRY_ENVIRONMENT", "").strip() or _DEFAULT_SENTRY_ENVIRONMENT
    sentry_sdk.init(
        dsn=dsn,
        environment=environment,
        release=os.environ.get("SENTRY_RELEASE", "").strip() or None,
        traces_sample_rate=_env_sample_rate("SENTRY_TRACES_SAMPLE_RATE", 0.0),
        send_default_pii=_env_flag("SENTRY_SEND_DEFAULT_PII"),
    )
    _OBS_LOG.info("Sentry error tracking initialized (environment=%s).", environment)
    return True
