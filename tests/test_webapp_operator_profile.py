"""Tests for webapp/operator_profile.py: env-driven with TODO-OPERADOR placeholders."""

from __future__ import annotations

from webapp.operator_profile import get_operator_profile

_ENV_VARS = ("OPERATOR_NAME", "OPERATOR_BIO", "OPERATOR_PHOTO_URL", "OPERATOR_LINKEDIN", "OPERATOR_EMAIL")


def test_defaults_to_todo_operador_placeholders(monkeypatch) -> None:
    for var in _ENV_VARS:
        monkeypatch.delenv(var, raising=False)
    profile = get_operator_profile()
    assert profile.name == "TODO-OPERADOR"
    assert "TODO-OPERADOR" in profile.bio
    assert profile.photo_url == ""
    assert profile.linkedin_url == ""
    assert profile.email == ""


def test_reads_configured_env_vars(monkeypatch) -> None:
    monkeypatch.setenv("OPERATOR_NAME", "Jane Doe")
    monkeypatch.setenv("OPERATOR_BIO", "10 anos optimizando inventario en LatAm.")
    monkeypatch.setenv("OPERATOR_PHOTO_URL", "https://example.com/jane.jpg")
    monkeypatch.setenv("OPERATOR_LINKEDIN", "https://linkedin.com/in/janedoe")
    monkeypatch.setenv("OPERATOR_EMAIL", "jane@example.com")
    profile = get_operator_profile()
    assert profile.name == "Jane Doe"
    assert profile.bio == "10 anos optimizando inventario en LatAm."
    assert profile.photo_url == "https://example.com/jane.jpg"
    assert profile.linkedin_url == "https://linkedin.com/in/janedoe"
    assert profile.email == "jane@example.com"
