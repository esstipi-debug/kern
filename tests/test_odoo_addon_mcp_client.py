"""Tests for odoo_addon/19.0/kern_dry_run/lib/mcp_client.py.

This module has zero Odoo dependencies by design (see its own docstring), so
it's imported directly by file path here rather than as an installed addon -
no Odoo runtime is needed to exercise its logic. Verified once against a real
running Kern instance during development (session init, tools/call, and
an invalid-key 401 all behaved as these mocks assume) before writing these.
"""

from __future__ import annotations

import email.message
import socket
import sys
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, Mock

import pytest

_LIB_DIR = Path(__file__).resolve().parents[1] / "odoo_addon" / "19.0" / "kern_dry_run" / "lib"
if str(_LIB_DIR) not in sys.path:
    sys.path.insert(0, str(_LIB_DIR))

from mcp_client import KernMcpClient, KernMcpError  # noqa: E402

_INITIALIZE_SSE = (
    'event: message\ndata: {"jsonrpc":"2.0","id":"x","result":'
    '{"protocolVersion":"2024-11-05","serverInfo":{"name":"linchpin_mcp"}}}\n\n'
)
_PUBLIC_IP = "8.8.8.8"  # a real, unambiguously public address for mocked DNS resolution


def _tool_call_sse(inner: dict, *, is_error: bool = False) -> str:
    import json

    result = {"content": [{"type": "text", "text": json.dumps(inner)}], "isError": is_error}
    payload = {"jsonrpc": "2.0", "id": "x", "result": result}
    return f"event: message\ndata: {json.dumps(payload)}\n\n"


def _headers(mapping: dict) -> email.message.Message:
    msg = email.message.Message()
    for key, value in mapping.items():
        msg[key] = value
    return msg


def _mock_success(status_code=200, text="", headers=None):
    """A mock of what `with urllib.request.urlopen(...) as response:` yields."""
    response = MagicMock()
    response.status = status_code
    response.headers = _headers(headers or {})
    response.read.return_value = text.encode("utf-8")
    response.__enter__.return_value = response
    return response


def _http_error(status_code, text="", headers=None):
    return urllib.error.HTTPError(
        url="https://kern.example/mcp/",
        code=status_code,
        msg="error",
        hdrs=_headers(headers or {}),
        fp=__import__("io").BytesIO(text.encode("utf-8")),
    )


@pytest.fixture(autouse=True)
def _resolve_every_hostname_to_a_public_ip(monkeypatch):
    """The client validates base_url resolves to a public address (SSRF guard -
    see _assert_safe_base_url) before ever making a request. Tests use fake
    hostnames like "kern.example" that don't really resolve, so DNS
    resolution itself is mocked here - the SSRF-guard tests below override
    this per-test to exercise the rejection paths instead."""
    fake_getaddrinfo = Mock(return_value=[(socket.AF_INET, None, None, "", (_PUBLIC_IP, 443))])
    monkeypatch.setattr("mcp_client.socket.getaddrinfo", fake_getaddrinfo)


def test_missing_base_url_raises_before_any_network_call():
    with pytest.raises(KernMcpError, match="base URL"):
        KernMcpClient("", "some-key")


def test_missing_api_key_raises_before_any_network_call():
    with pytest.raises(KernMcpError, match="API key"):
        KernMcpClient("https://example.test", "")


def test_non_https_base_url_is_rejected(monkeypatch):
    with pytest.raises(KernMcpError, match="https://"):
        KernMcpClient("http://kern.example", "lpk_test")


@pytest.mark.parametrize(
    "private_ip",
    [
        "127.0.0.1",  # loopback
        "10.0.0.5",  # RFC1918 private
        "192.168.1.1",  # RFC1918 private
        "169.254.169.254",  # link-local / cloud metadata service
    ],
)
def test_base_url_resolving_to_a_private_address_is_rejected(monkeypatch, private_ip):
    monkeypatch.setattr(
        "mcp_client.socket.getaddrinfo",
        Mock(return_value=[(socket.AF_INET, None, None, "", (private_ip, 443))]),
    )

    with pytest.raises(KernMcpError, match="private/internal address"):
        KernMcpClient("https://attacker-controlled.example", "lpk_test")


def test_unresolvable_base_url_is_rejected(monkeypatch):
    monkeypatch.setattr("mcp_client.socket.getaddrinfo", Mock(side_effect=socket.gaierror("no such host")))

    with pytest.raises(KernMcpError, match="could not resolve"):
        KernMcpClient("https://does-not-exist.invalid", "lpk_test")


def test_call_tool_happy_path_returns_parsed_inner_json(monkeypatch):
    responses = [
        _mock_success(200, _INITIALIZE_SSE, headers={"Mcp-Session-Id": "sess-123"}),
        _mock_success(200, ""),  # notifications/initialized: no body
        _mock_success(200, _tool_call_sse({"status": "ok", "summary": "2 SKUs classified"})),
    ]
    urlopen = Mock(side_effect=responses)
    monkeypatch.setattr("mcp_client.urllib.request.urlopen", urlopen)

    client = KernMcpClient("https://kern.example/", "lpk_test")
    result = client.call_tool("linchpin_classify_abc_xyz", [{"product_id": "A", "quantity": 1, "unit_cost": 1}])

    assert result == {"status": "ok", "summary": "2 SKUs classified"}
    assert client._session_id == "sess-123"
    # Every call after initialize must echo the session id back. urllib.request.Request
    # normalizes header keys via str.capitalize() on storage (first char upper, rest
    # lower) but NOT on get_header() lookups - "Mcp-session-id" is the real stored form.
    assert urlopen.call_args_list[1].args[0].get_header("Mcp-session-id") == "sess-123"
    assert urlopen.call_args_list[2].args[0].get_header("Mcp-session-id") == "sess-123"


def test_invalid_api_key_raises_a_clear_error(monkeypatch):
    monkeypatch.setattr("mcp_client.urllib.request.urlopen", Mock(side_effect=_http_error(401, '{"error":"nope"}')))

    client = KernMcpClient("https://kern.example", "wrong-key")

    with pytest.raises(KernMcpError, match="API key"):
        client.call_tool("linchpin_classify_abc_xyz", [{"product_id": "A"}])


def test_rate_limit_raises_a_clear_error(monkeypatch):
    monkeypatch.setattr("mcp_client.urllib.request.urlopen", Mock(side_effect=_http_error(429, "")))

    client = KernMcpClient("https://kern.example", "lpk_test")

    with pytest.raises(KernMcpError, match="rate limit"):
        client.call_tool("linchpin_classify_abc_xyz", [{"product_id": "A"}])


def test_network_failure_is_wrapped_not_leaked(monkeypatch):
    monkeypatch.setattr(
        "mcp_client.urllib.request.urlopen", Mock(side_effect=urllib.error.URLError("connection refused"))
    )

    client = KernMcpClient("https://kern.example", "lpk_test")

    with pytest.raises(KernMcpError, match="could not reach Kern"):
        client.call_tool("linchpin_classify_abc_xyz", [{"product_id": "A"}])


def test_tool_level_error_result_raises(monkeypatch):
    responses = [
        _mock_success(200, _INITIALIZE_SSE, headers={"Mcp-Session-Id": "sess-123"}),
        _mock_success(200, ""),
        _mock_success(200, _tool_call_sse({"status": "error", "summary": "bad input"}, is_error=True)),
    ]
    monkeypatch.setattr("mcp_client.urllib.request.urlopen", Mock(side_effect=responses))

    client = KernMcpClient("https://kern.example", "lpk_test")

    with pytest.raises(KernMcpError, match="tool call failed"):
        client.call_tool("linchpin_classify_abc_xyz", [{"product_id": "A"}])


def test_malformed_sse_body_raises_instead_of_crashing(monkeypatch):
    responses = [
        _mock_success(200, _INITIALIZE_SSE, headers={"Mcp-Session-Id": "sess-123"}),
        _mock_success(200, ""),
        _mock_success(200, "not an sse body at all"),
    ]
    monkeypatch.setattr("mcp_client.urllib.request.urlopen", Mock(side_effect=responses))

    client = KernMcpClient("https://kern.example", "lpk_test")

    with pytest.raises(KernMcpError, match="no SSE"):
        client.call_tool("linchpin_classify_abc_xyz", [{"product_id": "A"}])


def test_http_error_does_not_leak_raw_response_body_to_the_user(monkeypatch):
    """Regression test for a confirmed finding: untrusted server response text
    used to be embedded verbatim in the user-facing error message."""
    monkeypatch.setattr(
        "mcp_client.urllib.request.urlopen",
        Mock(side_effect=_http_error(500, "<script>alert('internal stack trace, secret path')</script>")),
    )

    client = KernMcpClient("https://kern.example", "lpk_test")

    with pytest.raises(KernMcpError) as exc_info:
        client.call_tool("linchpin_classify_abc_xyz", [{"product_id": "A"}])

    assert "script" not in str(exc_info.value)
    assert "secret path" not in str(exc_info.value)
    assert "HTTP 500" in str(exc_info.value)
