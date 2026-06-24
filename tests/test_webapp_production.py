"""Production-readiness: structured request logging + the prod fail-safe guard."""

import logging

import pytest

pytest.importorskip("fastapi")
try:
    import python_multipart  # noqa: F401
except ImportError:
    pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

from webapp import security  # noqa: E402
from webapp.app import app  # noqa: E402

client = TestClient(app)


# ---- structured request logging ----------------------------------------------

def test_response_carries_a_request_id():
    r = client.get("/api/health")
    assert r.headers.get("X-Request-ID")


def test_upstream_request_id_is_honoured():
    r = client.get("/api/health", headers={"X-Request-ID": "trace-abc-123"})
    assert r.headers["X-Request-ID"] == "trace-abc-123"


def test_request_is_logged_with_structured_fields(caplog):
    with caplog.at_level(logging.INFO, logger="linchpin.access"):
        client.get("/api/health")
    recs = [r for r in caplog.records if r.name == "linchpin.access"]
    assert recs, "expected an access log record"
    rec = recs[-1]
    assert rec.method == "GET"
    assert rec.path == "/api/health"
    assert rec.status == 200
    assert isinstance(rec.duration_ms, float)


# ---- production fail-safe guard ----------------------------------------------

def test_production_without_controls_warns(monkeypatch):
    monkeypatch.setattr(security, "ENV", "production")
    monkeypatch.setattr(security, "API_KEY", "")
    monkeypatch.setattr(security, "RATE_LIMIT", 0)
    warns = security.production_warnings()
    assert any("API_KEY" in w for w in warns)
    assert any("RATE_LIMIT" in w for w in warns)


def test_development_has_no_production_warnings(monkeypatch):
    monkeypatch.setattr(security, "ENV", "development")
    monkeypatch.setattr(security, "API_KEY", "")
    monkeypatch.setattr(security, "RATE_LIMIT", 0)
    assert security.production_warnings() == []


def test_secured_production_has_no_warnings(monkeypatch):
    monkeypatch.setattr(security, "ENV", "production")
    monkeypatch.setattr(security, "API_KEY", "a-real-key")
    monkeypatch.setattr(security, "RATE_LIMIT", 120)
    assert security.production_warnings() == []
