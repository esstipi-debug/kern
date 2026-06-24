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
