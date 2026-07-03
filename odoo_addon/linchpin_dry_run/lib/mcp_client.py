"""A minimal, hand-rolled MCP (Model Context Protocol) Streamable HTTP client.

Deliberately NOT built on the official `mcp` Python SDK, and deliberately
stdlib-only (`urllib.request`, not `requests`): a self-hosted Odoo instance
installing this module shouldn't need to assume ANY third-party package is
present just to call one HTTPS endpoint - not even `requests`, despite Odoo
core itself depending on it, since this addon's own tests (see
tests/test_odoo_addon_mcp_client.py at the repo root) import this module
directly into Linchpin's own pytest suite, which has no reason to carry a
dependency it doesn't otherwise need. This module has zero Odoo imports -
it's plain Python, unit-testable outside any Odoo runtime.

Wire format (verified against a real Linchpin deployment, not guessed):
POST {base_url}/mcp/ with header X-API-Key, Accept: "application/json,
text/event-stream", Content-Type: application/json. Every response is
Server-Sent Events (both Accept types are mandatory - the server 406s a
plain "application/json" Accept). `initialize` returns an `Mcp-Session-Id`
response header that every subsequent call on that session must echo back.
Per the MCP spec, the client must also send `notifications/initialized`
after a successful `initialize` before the server treats later calls as
part of an established session.
"""

from __future__ import annotations

import ipaddress
import json
import logging
import socket
import urllib.error
import urllib.request
import uuid
from urllib.parse import urlparse

_logger = logging.getLogger(__name__)

_MCP_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}
_PROTOCOL_VERSION = "2024-11-05"
_CLIENT_INFO = {"name": "linchpin-odoo-addon", "version": "1.0.0"}


class LinchpinMcpError(Exception):
    """Raised for any failure talking to Linchpin's MCP server: network errors,
    HTTP errors, JSON-RPC error responses, or a malformed reply. The Odoo
    wizard layer catches this and re-raises as a user-facing `UserError` -
    this module itself never depends on Odoo, so it can't raise one directly.
    """


def _assert_safe_base_url(base_url: str) -> None:
    """Rejects anything that isn't a real https:// host resolving to a public
    address - the Linchpin URL setting is admin-configurable (see
    models/res_config_settings.py), and this module sends the customer's own
    product/cost/sales data plus their live API key to whatever it's set to.
    Without this, a fat-fingered or malicious value (an internal ERP host, a
    cloud metadata address like 169.254.169.254, localhost) would silently
    exfiltrate both to wherever that host actually is - confirmed exploitable
    during this module's own security review, not a hypothetical.

    This is defense in depth, not an airtight SSRF guarantee (DNS could
    change between this check and the actual request) - but it closes the
    straightforward case: a plain wrong/malicious hostname or a private/
    loopback/link-local target.
    """
    parsed = urlparse(base_url)
    if parsed.scheme != "https":
        raise LinchpinMcpError("Linchpin URL must start with https://")
    hostname = parsed.hostname
    if not hostname:
        raise LinchpinMcpError(f"Linchpin URL is not a valid URL: {base_url!r}")

    try:
        resolved = {info[4][0] for info in socket.getaddrinfo(hostname, None)}
    except socket.gaierror as exc:
        raise LinchpinMcpError(f"could not resolve Linchpin URL host {hostname!r}: {exc}") from exc

    for ip_str in resolved:
        ip = ipaddress.ip_address(ip_str)
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved or ip.is_unspecified:
            raise LinchpinMcpError(
                f"Linchpin URL host {hostname!r} resolves to a private/internal address ({ip_str}) "
                "- refusing to send data there. Check Settings > Inventory > Linchpin."
            )


def _parse_sse_json_rpc(body: str) -> dict:
    """Extract the JSON-RPC payload from an SSE response body.

    A single non-streaming JSON-RPC response arrives as one `data: {...}`
    line (possibly preceded by an `event: message` line) - never multiple
    frames, since none of these calls ask the server to stream partial
    results.
    """
    for line in body.splitlines():
        if line.startswith("data:"):
            return json.loads(line[len("data:"):].strip())
    raise LinchpinMcpError(f"no SSE 'data:' line in MCP response body: {body[:200]!r}")


def _raise_for_rpc_error(payload: dict) -> None:
    error = payload.get("error")
    if error:
        message = error.get("message", "unknown MCP error")
        raise LinchpinMcpError(f"Linchpin MCP server error: {message}")


class LinchpinMcpClient:
    """One client = one short-lived MCP session against a single tool call.

    Not meant to be kept alive across requests - Odoo wizard actions are
    one-shot, so a fresh client (and a fresh MCP session) per analysis run
    keeps this simple and avoids any session-expiry bookkeeping.
    """

    def __init__(self, base_url: str, api_key: str, timeout: float = 90.0) -> None:
        if not base_url:
            raise LinchpinMcpError("Linchpin base URL is not configured")
        if not api_key:
            raise LinchpinMcpError("Linchpin API key is not configured")
        _assert_safe_base_url(base_url)
        self._url = base_url.rstrip("/") + "/mcp/"
        self._headers = {**_MCP_HEADERS, "X-API-Key": api_key}
        self._timeout = timeout
        self._session_id: str | None = None

    def _post(self, method: str, params: dict | None = None, *, is_notification: bool = False) -> dict | None:
        body: dict = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            body["params"] = params
        if not is_notification:
            body["id"] = str(uuid.uuid4())

        headers = dict(self._headers)
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id

        request = urllib.request.Request(
            self._url, data=json.dumps(body).encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout) as response:
                status_code = response.status
                response_headers = response.headers
                text = response.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            status_code = exc.code
            response_headers = exc.headers
            text = exc.read().decode("utf-8", errors="replace")
        except urllib.error.URLError as exc:
            raise LinchpinMcpError(f"could not reach Linchpin ({self._url}): {exc.reason}") from exc
        except OSError as exc:  # timeouts and other low-level socket failures
            raise LinchpinMcpError(f"could not reach Linchpin ({self._url}): {exc}") from exc

        if status_code == 401:
            raise LinchpinMcpError("Linchpin rejected the API key - check the key in Settings")
        if status_code == 429:
            raise LinchpinMcpError("Linchpin rate limit reached - try again shortly")
        if status_code >= 400:
            # The response body is untrusted content from whatever host base_url
            # points at - never surface it verbatim in the user-facing error
            # (it would otherwise land inside a trusted-looking Odoo dialog).
            # Full detail goes to the server log only, where an admin can see it.
            _logger.warning("Linchpin MCP call failed: HTTP %s - %s", status_code, text[:500])
            raise LinchpinMcpError(
                f"Linchpin returned an unexpected error (HTTP {status_code}). Check the Odoo server log for details."
            )

        session_id = response_headers.get("Mcp-Session-Id")
        if session_id:
            self._session_id = session_id

        if is_notification or not text.strip():
            return None

        payload = _parse_sse_json_rpc(text)
        _raise_for_rpc_error(payload)
        return payload

    def _initialize(self) -> None:
        self._post(
            "initialize",
            {"protocolVersion": _PROTOCOL_VERSION, "capabilities": {}, "clientInfo": _CLIENT_INFO},
        )
        # Required by the MCP spec before the server treats the session as
        # established - some server implementations tolerate skipping this,
        # but this client doesn't rely on that leniency.
        self._post("notifications/initialized", is_notification=True)

    def call_tool(self, tool_name: str, rows: list[dict], params: dict | None = None, client_label: str = "Odoo") -> dict:
        """Runs one MCP tool call end to end (session init + the call itself)
        and returns the tool's own parsed JSON result (status/summary/
        report_markdown/... - see webapp/mcp_server.py's tool docstrings for
        the exact shape each tool returns).
        """
        self._initialize()

        tool_args = {"params": {"rows": rows, "params": params or {}, "client_label": client_label}}
        payload = self._post("tools/call", {"name": tool_name, "arguments": tool_args})
        if payload is None:
            raise LinchpinMcpError("Linchpin returned an empty response to tools/call")

        result = payload.get("result", {})
        if result.get("isError"):
            text = next((b.get("text", "") for b in result.get("content", []) if b.get("type") == "text"), "")
            raise LinchpinMcpError(f"Linchpin tool call failed: {text or 'unknown error'}")

        content_blocks = result.get("content", [])
        text = next((b.get("text") for b in content_blocks if b.get("type") == "text" and b.get("text")), None)
        if text is None:
            raise LinchpinMcpError(f"Linchpin tool response had no text content: {result!r}")

        try:
            return json.loads(text)
        except (TypeError, ValueError) as exc:
            raise LinchpinMcpError(f"Linchpin tool response was not valid JSON: {exc}") from exc
