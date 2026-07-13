"""Tests for the webhook notifier (Linchpin 3.0 PR-3, F0 -- jobs/notify.py).

Guarantees under test:
- with no webhook URL configured (env var unset, none passed explicitly),
  notify() no-ops: returns False, never raises, and never touches the
  network -- safe to call from any job in tests/CI with zero config;
- with a webhook URL configured, notify() POSTs {"text": message, **kwargs}
  as JSON to that exact URL, using httpx.MockTransport so no real network
  call ever happens;
- a failing response (or a transport-level error) is retried up to
  max_attempts times with a linear backoff, and notify() returns True only
  once an attempt gets a 2xx back;
- once max_attempts is exhausted without a 2xx, notify() returns False
  (never raises);
- notify() also no-ops when httpx itself is unavailable (the 'tower' extra
  not installed), exercised by monkeypatching the availability flag.
"""

from __future__ import annotations

import json

import httpx
import pytest

from jobs import notify as notify_module
from jobs.notify import WEBHOOK_URL_ENV, notify

_URL = "https://hooks.example.com/services/T000/B000/xxxxxxxx"


def _ok_transport(captured: dict | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if captured is not None:
            captured["url"] = str(request.url)
            captured["json"] = json.loads(request.content)
        return httpx.Response(200)

    return httpx.MockTransport(handler)


# -- no-op when unconfigured -------------------------------------------------


def test_notify_no_ops_and_returns_false_when_no_env_var_and_no_explicit_url(monkeypatch):
    monkeypatch.delenv(WEBHOOK_URL_ENV, raising=False)

    result = notify("hello")

    assert result is False


def test_notify_no_ops_when_env_var_is_set_but_blank(monkeypatch):
    monkeypatch.setenv(WEBHOOK_URL_ENV, "   ")

    assert notify("hello") is False


def test_notify_never_raises_when_unconfigured(monkeypatch):
    monkeypatch.delenv(WEBHOOK_URL_ENV, raising=False)
    # No transport, no network available in this sandbox -- if notify() tried
    # to reach the network here it would raise/hang, not just return False.
    assert notify("hello", **{"severity": "high"}) is False


def test_notify_no_ops_when_httpx_is_unavailable(monkeypatch):
    monkeypatch.setenv(WEBHOOK_URL_ENV, _URL)
    monkeypatch.setattr(notify_module, "_HAS_HTTPX", False)

    assert notify("hello") is False


# -- posts correctly when configured, via httpx.MockTransport --------------


def test_notify_posts_the_message_as_json_to_the_configured_env_var_url(monkeypatch):
    monkeypatch.setenv(WEBHOOK_URL_ENV, _URL)
    captured: dict = {}

    result = notify("stock alert: SKU-A", transport=_ok_transport(captured))

    assert result is True
    assert captured["url"] == _URL
    assert captured["json"]["text"] == "stock alert: SKU-A"


def test_notify_uses_the_explicit_webhook_url_over_the_env_var(monkeypatch):
    monkeypatch.setenv(WEBHOOK_URL_ENV, "https://hooks.example.com/wrong")
    captured: dict = {}

    result = notify("hi", webhook_url=_URL, transport=_ok_transport(captured))

    assert result is True
    assert captured["url"] == _URL


def test_notify_forwards_extra_kwargs_into_the_json_payload():
    captured: dict = {}

    notify("hi", webhook_url=_URL, transport=_ok_transport(captured), channel="#tower-alerts")

    assert captured["json"]["channel"] == "#tower-alerts"


# -- retry behavior -----------------------------------------------------------


def test_notify_retries_a_failing_response_then_succeeds():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 2:
            return httpx.Response(500)
        return httpx.Response(200)

    result = notify(
        "hi", webhook_url=_URL, transport=httpx.MockTransport(handler), max_attempts=3, backoff_seconds=0.0
    )

    assert result is True
    assert attempts["n"] == 2


def test_notify_retries_a_transport_error_then_succeeds():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 2:
            raise httpx.ConnectError("connection refused", request=request)
        return httpx.Response(200)

    result = notify(
        "hi", webhook_url=_URL, transport=httpx.MockTransport(handler), max_attempts=3, backoff_seconds=0.0
    )

    assert result is True
    assert attempts["n"] == 2


def test_notify_returns_false_once_max_attempts_is_exhausted():
    attempts = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(503)

    result = notify(
        "hi", webhook_url=_URL, transport=httpx.MockTransport(handler), max_attempts=2, backoff_seconds=0.0
    )

    assert result is False
    assert attempts["n"] == 2  # tried exactly max_attempts times, no more


@pytest.mark.parametrize("status", [400, 404, 500, 503])
def test_notify_treats_any_non_2xx_status_as_a_failure(status):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status)

    result = notify("hi", webhook_url=_URL, transport=httpx.MockTransport(handler), max_attempts=1)

    assert result is False
