"""The session allowlist — the hard boundary on the setup agent's browser power.

The driver can never wander off the admin it is onboarding. These tests lock in
the matching rules (module docstring of ``src/connectors/browser/scope.py``) as
the trust boundary they are: scheme restrictions, subdomain matching, and the
convenience defaults per known system.
"""

from __future__ import annotations

import pytest

from src.connectors.browser.scope import (
    DomainAllowlist,
    ScopeViolationError,
    allowed_domains_for,
)


def test_exact_host_is_allowed() -> None:
    allowlist = DomainAllowlist(["admin.shopify.com"])
    assert allowlist.allows("https://admin.shopify.com/products")


def test_subdomain_of_allowed_host_is_allowed() -> None:
    allowlist = DomainAllowlist(["shopify.com"])
    assert allowlist.allows("https://admin.shopify.com/products")
    assert allowlist.allows("https://admin.shopify.com/settings")
    # myshopify.com is a *separate* host from shopify.com — a session scoped to
    # shopify.com must not allow it, and the shopify defaults include it
    # explicitly (see allowed_domains_for).
    assert not allowlist.allows("https://nombretienda.myshopify.com/admin")
    full = DomainAllowlist(["shopify.com", "myshopify.com"])
    assert full.allows("https://nombretienda.myshopify.com/admin")


def test_unrelated_host_is_blocked() -> None:
    allowlist = DomainAllowlist(["shopify.com"])
    assert not allowlist.allows("https://evil.example.org/phish")
    assert not allowlist.allows("https://shopify.com.evil.example.org/")


def test_wildcard_prefix_is_normalized_to_same_rule() -> None:
    assert DomainAllowlist(["*.shopify.com"]).allows("https://admin.shopify.com/")
    assert DomainAllowlist(["*.shopify.com"]).allows("https://shopify.com/")


def test_non_http_schemes_are_always_blocked() -> None:
    allowlist = DomainAllowlist(["shopify.com"])
    for url in (
        "file:///C:/Windows/win.ini",
        "chrome://settings/",
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "ftp://shopify.com/file",
    ):
        assert not allowlist.allows(url), url


def test_port_and_path_do_not_matter() -> None:
    allowlist = DomainAllowlist(["shopify.com"])
    assert allowlist.allows("https://admin.shopify.com:8443/products/123?x=1#top")


def test_empty_allowlist_allows_nothing() -> None:
    allowlist = DomainAllowlist([])
    assert not allowlist.allows("https://shopify.com/")
    with pytest.raises(ScopeViolationError):
        allowlist.check("https://shopify.com/")


def test_check_raises_with_hostname_and_allowlist_in_message() -> None:
    allowlist = DomainAllowlist(["shopify.com"])
    with pytest.raises(ScopeViolationError) as excinfo:
        allowlist.check("https://evil.example.org/")
    assert "evil.example.org" in str(excinfo.value)
    assert "shopify.com" in str(excinfo.value)


def test_case_and_whitespace_are_normalized() -> None:
    allowlist = DomainAllowlist(["  Shopify.COM  "])
    assert allowlist.allows("https://ADMIN.SHOPIFY.COM/")


def test_allowed_domains_for_known_systems() -> None:
    assert "shopify.com" in allowed_domains_for("Shopify")
    assert "odoo.com" in allowed_domains_for("odoo")


def test_allowed_domains_for_unknown_system_is_empty_not_guessed() -> None:
    # The empty list is load-bearing: an unknown system must be scoped
    # explicitly by the operator rather than inheriting a guessed domain.
    assert allowed_domains_for("mystery-erp") == []
