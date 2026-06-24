"""Opt-in security layer for the Inventory Planner web app.

Three controls, all configured by environment variable so local/dev use is
unchanged out of the box:

* **Security headers + path-aware CSP** — always on. The dashboard gets a strict
  policy; only the `/console` React/Babel prototype gets the relaxed allowances
  it needs (unpkg CDN + runtime ``eval``).
* **Rate limiting** — sliding window per client IP. Disabled unless
  ``LINCHPIN_RATE_LIMIT`` > 0.
* **API-key gate** — required only when ``LINCHPIN_API_KEY`` is set.

Config is read at *call time* via module globals so tests (and a reload) can
flip it with ``monkeypatch.setattr(security, "API_KEY", ...)``.
"""

from __future__ import annotations

import hmac
import os
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default


# ---- configuration (module-level so it stays monkeypatchable) -----------------
RATE_LIMIT = _int_env("LINCHPIN_RATE_LIMIT", 0)  # max requests per window; 0 disables
RATE_WINDOW = _int_env("LINCHPIN_RATE_WINDOW", 60)  # window length, seconds
API_KEY = os.environ.get("LINCHPIN_API_KEY", "").strip()  # empty disables the gate
CORS_ORIGINS = [o.strip() for o in os.environ.get("LINCHPIN_CORS_ORIGINS", "").split(",") if o.strip()]
ENV = os.environ.get("LINCHPIN_ENV", "development").strip().lower()  # "production" tightens checks
REQUIRE_SECURE = os.environ.get("LINCHPIN_REQUIRE_SECURE", "").strip().lower() in ("1", "true", "yes")


def production_warnings() -> list[str]:
    """Misconfiguration warnings for a production deploy.

    Empty in development (or when production is fully secured). The app logs these
    loudly at startup and, when ``LINCHPIN_REQUIRE_SECURE`` is set, refuses to
    boot - so an unauthenticated public deploy fails *loud*, not silent.
    """
    if ENV != "production":
        return []
    out: list[str] = []
    if not API_KEY:
        out.append("LINCHPIN_API_KEY is not set - POST /api/jobs is unauthenticated")
    if RATE_LIMIT <= 0:
        out.append("LINCHPIN_RATE_LIMIT is 0 - POST /api/jobs is not rate limited")
    return out


# ---- rate-limiter state -------------------------------------------------------
_BUCKETS: dict[str, deque[float]] = defaultdict(deque)


def reset_rate_limit() -> None:
    """Drop all rate-limiter state (used by tests for isolation)."""
    _BUCKETS.clear()


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "anon"


def rate_limit(request: Request) -> None:
    """FastAPI dependency: 429 once a client exceeds RATE_LIMIT within RATE_WINDOW."""
    limit = RATE_LIMIT
    if limit <= 0:  # feature off
        return
    window = RATE_WINDOW
    now = time.monotonic()
    bucket = _BUCKETS[_client_key(request)]
    cutoff = now - window
    while bucket and bucket[0] < cutoff:
        bucket.popleft()
    if len(bucket) >= limit:
        retry_after = max(1, int(bucket[0] + window - now))
        raise HTTPException(
            status_code=429,
            detail="rate limit exceeded",
            headers={"Retry-After": str(retry_after)},
        )
    bucket.append(now)


def require_api_key(request: Request) -> None:
    """FastAPI dependency: 401 unless ``X-API-Key`` matches LINCHPIN_API_KEY.

    A no-op when no key is configured (the shipped default), so the app stays
    open for local use.
    """
    expected = API_KEY
    if not expected:  # gate off
        return
    provided = request.headers.get("x-api-key", "")
    if not hmac.compare_digest(provided, expected):
        raise HTTPException(status_code=401, detail="invalid or missing API key")


# ---- security headers + path-aware CSP ----------------------------------------
_BASE_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "frame-ancestors 'none'"
)
# The /console prototype boots React + ReactDOM + Babel-standalone from unpkg and
# compiles JSX in the browser, which needs 'unsafe-eval' and the CDN origin. Scope
# those allowances to that surface only — the dashboard keeps the strict policy.
_CONSOLE_CSP = _BASE_CSP.replace(
    "script-src 'self' 'unsafe-inline'",
    "script-src 'self' 'unsafe-inline' 'unsafe-eval' https://unpkg.com",
)


def csp_for_path(path: str) -> str:
    if path.startswith("/console") or path.startswith("/static/prototype"):
        return _CONSOLE_CSP
    return _BASE_CSP


async def security_headers_middleware(request: Request, call_next):
    """Attach hardening headers to every response (idempotent via setdefault)."""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("Content-Security-Policy", csp_for_path(request.url.path))
    return response
