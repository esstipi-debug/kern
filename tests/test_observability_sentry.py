"""Tests for the env-gated Sentry error tracking in webapp/observability.py.

The `sentry_sdk` module is injected via sys.modules so the assertions are
deterministic and need neither the real package nor a network call:
- a fake module records the init() kwargs, or
- `None` in sys.modules makes `import sentry_sdk` raise ImportError (the
  "package stripped from the environment" case).
"""

from __future__ import annotations

import sys
import types

import pytest

from webapp import observability as obs

_DSN = "https://public@o1.ingest.sentry.io/1"


def _fake_sentry(monkeypatch) -> list[dict]:
    calls: list[dict] = []
    fake = types.ModuleType("sentry_sdk")
    fake.init = lambda **kwargs: calls.append(kwargs)  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "sentry_sdk", fake)
    return calls


def test_noop_when_dsn_unset(monkeypatch):
    calls = _fake_sentry(monkeypatch)
    monkeypatch.delenv("SENTRY_DSN", raising=False)

    assert obs.init_observability() is False
    assert calls == []  # sentry_sdk.init is never called with no DSN


def test_initializes_when_dsn_set(monkeypatch):
    calls = _fake_sentry(monkeypatch)
    monkeypatch.setenv("SENTRY_DSN", _DSN)
    monkeypatch.setenv("SENTRY_ENVIRONMENT", "staging")
    monkeypatch.delenv("SENTRY_TRACES_SAMPLE_RATE", raising=False)
    monkeypatch.delenv("SENTRY_SEND_DEFAULT_PII", raising=False)

    assert obs.init_observability() is True
    assert len(calls) == 1
    kw = calls[0]
    assert kw["dsn"] == _DSN
    assert kw["environment"] == "staging"
    # Safe defaults: PII never attached, performance tracing off (errors only).
    assert kw["send_default_pii"] is False
    assert kw["traces_sample_rate"] == 0.0


def test_environment_defaults_to_production(monkeypatch):
    calls = _fake_sentry(monkeypatch)
    monkeypatch.setenv("SENTRY_DSN", _DSN)
    monkeypatch.delenv("SENTRY_ENVIRONMENT", raising=False)

    obs.init_observability()
    assert calls[0]["environment"] == "production"


def test_pii_and_tracing_are_explicit_opt_in(monkeypatch):
    calls = _fake_sentry(monkeypatch)
    monkeypatch.setenv("SENTRY_DSN", _DSN)
    monkeypatch.setenv("SENTRY_SEND_DEFAULT_PII", "1")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.25")

    obs.init_observability()
    assert calls[0]["send_default_pii"] is True
    assert calls[0]["traces_sample_rate"] == 0.25


def test_degrades_gracefully_when_sentry_sdk_absent(monkeypatch):
    """SENTRY_DSN set but the package missing -> warn + no-op, never a crash
    (this module is on the app's boot import chain)."""
    monkeypatch.setenv("SENTRY_DSN", _DSN)
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)  # `import sentry_sdk` now raises ImportError

    assert obs.init_observability() is False


@pytest.mark.parametrize(
    "raw, expected",
    [("0.5", 0.5), ("0", 0.0), ("1", 1.0), ("1.5", 0.3), ("-0.1", 0.3), ("abc", 0.3), ("", 0.3)],
)
def test_env_sample_rate_clamps_and_falls_back(monkeypatch, raw, expected):
    if raw == "":
        monkeypatch.delenv("SENTRY_TRACES_SAMPLE_RATE", raising=False)
    else:
        monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", raw)
    assert obs._env_sample_rate("SENTRY_TRACES_SAMPLE_RATE", 0.3) == expected


@pytest.mark.parametrize(
    "raw, expected",
    [("1", True), ("true", True), ("YES", True), ("on", True), ("0", False), ("false", False), ("", False)],
)
def test_env_flag_parsing(monkeypatch, raw, expected):
    monkeypatch.setenv("SENTRY_SEND_DEFAULT_PII", raw)
    assert obs._env_flag("SENTRY_SEND_DEFAULT_PII") is expected
