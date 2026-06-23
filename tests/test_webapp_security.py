"""Security-layer tests for the FastAPI backend.

Covers the always-on security headers + path-aware CSP, and the opt-in
rate limiter and API-key gate (both default-off so local/dev use is unchanged).
"""

import pytest

pytest.importorskip("fastapi")
# /api/jobs is a multipart route, so importing webapp.app needs python-multipart.
try:
    import python_multipart  # noqa: F401  (canonical name)
except ImportError:
    pytest.importorskip("multipart")  # legacy name

from fastapi.testclient import TestClient  # noqa: E402

from webapp import security  # noqa: E402
from webapp.app import app  # noqa: E402

client = TestClient(app)

# A leadership brief runs with no uploaded file, so it exercises POST /api/jobs
# end to end without needing a data fixture.
LEADERSHIP = {"brief": "evaluate our SC leadership", "params": '{"scores": "3 2 3 1 1", "name": "T"}'}


@pytest.fixture(autouse=True)
def _open_defaults(monkeypatch):
    """Every test starts from the shipped default: no throttle, no API key."""
    security.reset_rate_limit()
    monkeypatch.setattr(security, "RATE_LIMIT", 0)
    monkeypatch.setattr(security, "API_KEY", "")
    yield


def test_security_headers_present_on_dashboard():
    r = client.get("/")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "strict-origin" in r.headers["Referrer-Policy"]
    csp = r.headers["Content-Security-Policy"]
    assert "default-src 'self'" in csp
    assert "frame-ancestors 'none'" in csp
    # The dashboard must NOT carry the loose console allowances.
    assert "unpkg.com" not in csp
    assert "unsafe-eval" not in csp


def test_console_csp_allows_react_cdn_and_eval():
    # The /console prototype loads React + Babel from unpkg and compiles JSX at
    # runtime, so its CSP is deliberately relaxed where the dashboard's is not.
    csp = client.get("/console").headers["Content-Security-Policy"]
    assert "https://unpkg.com" in csp
    assert "'unsafe-eval'" in csp


def test_api_key_enforced_when_configured(monkeypatch):
    monkeypatch.setattr(security, "API_KEY", "s3cret")
    assert client.post("/api/jobs", data=LEADERSHIP).status_code == 401
    assert client.post("/api/jobs", data=LEADERSHIP, headers={"X-API-Key": "nope"}).status_code == 401
    # Correct key: never an auth failure (job outcome may vary, but never 401).
    assert client.post("/api/jobs", data=LEADERSHIP, headers={"X-API-Key": "s3cret"}).status_code != 401


def test_api_open_when_no_key_set():
    assert client.post("/api/jobs", data=LEADERSHIP).status_code != 401


def test_rate_limit_trips_after_threshold(monkeypatch):
    security.reset_rate_limit()
    monkeypatch.setattr(security, "RATE_LIMIT", 2)
    monkeypatch.setattr(security, "RATE_WINDOW", 60)
    assert client.get("/api/portfolio").status_code == 200
    assert client.get("/api/portfolio").status_code == 200
    blocked = client.get("/api/portfolio")
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers


def test_rate_limit_disabled_by_default():
    # RATE_LIMIT=0 (the shipped default) -> never throttled.
    for _ in range(12):
        assert client.get("/api/portfolio").status_code == 200


def test_no_cors_header_by_default():
    r = client.get("/api/portfolio", headers={"Origin": "https://evil.example"})
    assert "access-control-allow-origin" not in {k.lower() for k in r.headers}
