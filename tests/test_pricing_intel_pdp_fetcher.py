"""Tests for src/pricing_intel/acquire/pdp_fetcher.py (Linchpin 3.0 PR-13).

No real network call ever happens here -- every ``httpx.Client`` is built on
an ``httpx.MockTransport`` (stdlib-adjacent, ships with httpx itself, no new
test dependency) whose handler asserts on the request it received and
returns a canned response. Guarantees under test:
- a 200 response round-trips into a RawObservation with the exact status
  code and body text;
- a 403/429 response is NOT raised -- it comes back as an ordinary
  RawObservation so the caller's classify_blocking_signal can inspect it
  (this module never treats a status code as a Python exception);
- the identifiable User-Agent header is actually sent, never spoofed;
- a transport-level failure (connection error) returns a FetchError, never
  raises and never gets confused with an HTTP-level response.
"""

from __future__ import annotations

from datetime import datetime, timezone

import httpx
import pytest

from src.pricing_intel.acquire.base import RawObservation
from src.pricing_intel.acquire.pdp_fetcher import USER_AGENT, FetchError, fetch_pdp_html

FIXED_NOW = datetime(2026, 7, 12, 12, 0, 0, tzinfo=timezone.utc)


def _client(handler) -> httpx.Client:
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_successful_fetch_returns_raw_observation_with_status_and_html() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>hello</html>")

    result = fetch_pdp_html("https://shop.example.com/p/widget", client=_client(handler), now=FIXED_NOW)
    assert isinstance(result, RawObservation)
    assert result.status_code == 200
    assert result.html == "<html>hello</html>"
    assert result.sku_ref == "https://shop.example.com/p/widget"
    assert result.fetched_at == FIXED_NOW


def test_user_agent_is_sent_and_identifiable_not_spoofed() -> None:
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ua"] = request.headers.get("user-agent")
        return httpx.Response(200, text="ok")

    fetch_pdp_html("https://shop.example.com/p/widget", client=_client(handler), now=FIXED_NOW)
    assert seen["ua"] == USER_AGENT
    assert "Chrome" not in seen["ua"]
    assert "Mozilla" not in seen["ua"]


@pytest.mark.parametrize("status", [403, 429])
def test_blocking_status_codes_come_back_as_a_plain_observation_not_an_exception(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text="blocked")

    result = fetch_pdp_html("https://shop.example.com/p/widget", client=_client(handler), now=FIXED_NOW)
    assert isinstance(result, RawObservation)
    assert result.status_code == status
    assert result.html == "blocked"


def test_transport_failure_returns_fetch_error_never_raises() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    result = fetch_pdp_html("https://shop.example.com/p/widget", client=_client(handler), now=FIXED_NOW)
    assert isinstance(result, FetchError)
    assert result.url == "https://shop.example.com/p/widget"
    assert "connection refused" in result.reason


def test_fetched_at_defaults_to_now_when_not_supplied() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="ok")

    before = datetime.now(timezone.utc)
    result = fetch_pdp_html("https://shop.example.com/p/widget", client=_client(handler))
    after = datetime.now(timezone.utc)
    assert isinstance(result, RawObservation)
    assert before <= result.fetched_at <= after
