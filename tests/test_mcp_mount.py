"""Tests for the mounted /mcp surface's auth gate (webapp/mcp_auth.py).

Covers only the auth boundary - the MCP protocol/tool behavior itself is
covered by tests/test_mcp_server.py against the FastMCP object directly (the
correct level for exercising tools/list, tools/call, and Pydantic validation).
Here we only need: no key -> 401, wrong key -> 401, valid key -> past the gate
(never a 401), and the existing rate limiter still applies underneath.
"""

import json

import pytest

pytest.importorskip("fastapi")
try:
    import python_multipart  # noqa: F401
except ImportError:
    pytest.importorskip("multipart")

from fastapi.testclient import TestClient  # noqa: E402

import webapp.app as app_module  # noqa: E402
from src.mcp_keys import McpKeyStore  # noqa: E402
from webapp import security  # noqa: E402
from webapp.mcp_server import SERVER_NAME  # noqa: E402

_MCP_RPC_BODY = {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
_MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
_INITIALIZE_BODY = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {"protocolVersion": "2024-11-05", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}},
}


@pytest.fixture(scope="module")
def client():
    """A context-managed TestClient, not the bare-object pattern used elsewhere in
    this repo (`client = TestClient(app)` at module scope): the mounted MCP
    sub-app only starts its session manager's task group on a real ASGI
    `lifespan.startup` event (see `_lifespan` in webapp/app.py), which a bare
    `TestClient(app)` never sends - only entering it as a context manager does.
    Every other surface in this app is stateless enough not to care; this one
    does, and a bare TestClient here would silently mask the exact class of bug
    `test_a_real_client_can_complete_the_mcp_handshake` below exists to catch.

    Module-scoped, not per-test: `StreamableHTTPSessionManager.run()` (entered
    by the lifespan) raises if called twice on the same instance, and
    `webapp/app.py` builds its MCP sub-app as a module-level singleton - a
    fresh TestClient per test would re-enter that same singleton's lifespan
    repeatedly and hit that guard on the second test.

    `base_url="http://127.0.0.1:8000"`, not TestClient's own default
    ("http://testserver"): the MCP sub-app's DNS-rebinding Host-header check
    (webapp/mcp_server.py's `transport_security`) only allows a handful of
    exact host:port patterns, and matching is literal - "testserver" and even
    a bare "localhost" (no port) both fail the "localhost:*" wildcard, which
    only matches when a port is actually present. An explicit port is required."""
    with TestClient(app_module.app, base_url="http://127.0.0.1:8000") as c:
        yield c


@pytest.fixture(autouse=True)
def _isolated_key_store(monkeypatch):
    """Every test gets its own in-memory key store - never touches a real key file,
    never leaks state between tests."""
    store = McpKeyStore(":memory:")
    monkeypatch.setattr(app_module, "_get_mcp_key_store", lambda: store)
    security.reset_rate_limit()
    monkeypatch.setattr(security, "RATE_LIMIT", 0)
    yield store


def test_missing_api_key_is_rejected(client):
    r = client.post("/mcp/", json=_MCP_RPC_BODY, headers=_MCP_HEADERS)
    assert r.status_code == 401
    assert "invalid or missing" in r.json()["error"]


def test_wrong_api_key_is_rejected(client, _isolated_key_store):
    _isolated_key_store.issue("Acme Co")  # some other client's key exists, irrelevant here

    r = client.post("/mcp/", json=_MCP_RPC_BODY, headers={**_MCP_HEADERS, "X-API-Key": "not-a-real-key"})

    assert r.status_code == 401


def test_revoked_api_key_is_rejected(client, _isolated_key_store):
    key = _isolated_key_store.issue("Acme Co")
    _isolated_key_store.revoke(key)

    r = client.post("/mcp/", json=_MCP_RPC_BODY, headers={**_MCP_HEADERS, "X-API-Key": key})

    assert r.status_code == 401


def test_valid_api_key_passes_the_auth_gate(client, _isolated_key_store):
    key = _isolated_key_store.issue("Acme Co")

    r = client.post("/mcp/", json=_MCP_RPC_BODY, headers={**_MCP_HEADERS, "X-API-Key": key})

    # The auth gate's only job is to not reject this - whatever the MCP protocol
    # layer does next (session handshake, etc.) is covered below and in
    # tests/test_mcp_server.py, not here.
    assert r.status_code != 401


def test_a_real_client_can_complete_the_mcp_handshake(client, _isolated_key_store):
    """Regression test for a real bug that shipped to production undetected:
    mounting the FastMCP sub-app at "/mcp" doubled onto its own default
    internal route (also "/mcp"), so the only path that actually worked was
    "/mcp/mcp" - one segment longer than the documented client URL
    (docs/MCP_SERVER.md) and every other test in this file. Separately,
    `app.mount()` never propagates ASGI lifespan events into a sub-app, so the
    session manager's task group was never started, 500ing every request that
    got that far. Both bugs land on a non-401 status (404 / 500), so
    `test_valid_api_key_passes_the_auth_gate`'s `!= 401` check passed cleanly
    right through them - a passing auth-gate test is necessary but not
    sufficient. `initialize` is the first real call any MCP client makes
    (before tools/list, before tools/call), so success here is the actual
    signal that a client landing on the documented URL works end to end."""
    key = _isolated_key_store.issue("Acme Co")

    r = client.post("/mcp/", json=_INITIALIZE_BODY, headers={**_MCP_HEADERS, "X-API-Key": key})

    assert r.status_code == 200
    assert "mcp-session-id" in r.headers
    data_line = next(line for line in r.text.splitlines() if line.startswith("data: "))
    payload = json.loads(data_line.removeprefix("data: "))
    assert "error" not in payload
    assert payload["result"]["serverInfo"]["name"] == SERVER_NAME


def test_rate_limit_applies_even_with_a_valid_key(client, monkeypatch, _isolated_key_store):
    key = _isolated_key_store.issue("Acme Co")
    monkeypatch.setattr(security, "RATE_LIMIT", 1)
    monkeypatch.setattr(security, "RATE_WINDOW", 60)

    first = client.post("/mcp/", json=_MCP_RPC_BODY, headers={**_MCP_HEADERS, "X-API-Key": key})
    second = client.post("/mcp/", json=_MCP_RPC_BODY, headers={**_MCP_HEADERS, "X-API-Key": key})

    assert first.status_code != 429
    assert second.status_code == 429


def test_rate_limit_quota_is_per_client_not_per_shared_ip(client, monkeypatch, _isolated_key_store):
    """Repro of an audited gap: TestClient (like a NAT'd office, or two tenants
    behind one proxy) always presents the same source IP. Two DISTINCT clients
    must not throttle each other just because their traffic looks same-origin -
    each gets its own quota, keyed by their resolved client_name post-auth."""
    key_a = _isolated_key_store.issue("Acme Co")
    key_b = _isolated_key_store.issue("Globex")
    monkeypatch.setattr(security, "RATE_LIMIT", 1)
    monkeypatch.setattr(security, "RATE_WINDOW", 60)

    a_first = client.post("/mcp/", json=_MCP_RPC_BODY, headers={**_MCP_HEADERS, "X-API-Key": key_a})
    b_first = client.post("/mcp/", json=_MCP_RPC_BODY, headers={**_MCP_HEADERS, "X-API-Key": key_b})
    a_second = client.post("/mcp/", json=_MCP_RPC_BODY, headers={**_MCP_HEADERS, "X-API-Key": key_a})

    assert a_first.status_code != 429  # Acme's first call: fine
    assert b_first.status_code != 429  # Globex's first call: fine too, not throttled by Acme's usage
    assert a_second.status_code == 429  # Acme's OWN second call within the window: throttled
