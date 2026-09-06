"""Per-session navigation allowlist — the browser driver can never wander.

The setup agent watches the client's admin backend (Shopify, Odoo, ...) and
acts in it. The single hard boundary on that power is here: a session is
created with a list of allowed hosts, and every ``navigate`` is checked against
it. A recipe bug, a hallucinated URL, or a compromised LLM prompt can send the
browser to the wrong place; the allowlist turns that into a raised
:class:`ScopeViolationError` instead of a navigation.

Matching rules (documented because they are the trust boundary):

- Only ``http(s)`` URLs are ever allowed — ``file://``, ``chrome://``,
  ``javascript:`` and any other scheme are rejected outright.
- A bare entry ``example.com`` matches ``example.com`` and any of its
  subdomains (``admin.example.com``), so onboarding a Shopify store can allow
  the whole ``shopify.com`` tree with one entry.
- A ``*.``-prefixed entry is normalized to the same rule (``*.example.com`` ==
  ``example.com``); the wildcard prefix is accepted for readability, the
  matching is identical. Ports, paths and query strings are ignored — the
  allowlist scopes the host, not the page.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlsplit

from src.connectors.browser.base import BrowserError

#: Convenience defaults for known backends. A session is always created with an
#: explicit list (unknown systems resolve to ``[]`` and must be scoped by the
#: operator); these are just the sane starting points for the two ERP/e-com
#: systems the recipes target first.
KNOWN_SYSTEM_DOMAINS: dict[str, tuple[str, ...]] = {
    # admin.shopify.com is a subdomain of shopify.com; storefronts live under
    # *.myshopify.com; checkout/payments under shopifypayments.com.
    "shopify": ("shopify.com", "myshopify.com", "shopifypayments.com"),
    # Odoo SaaS admin is served from *.odoo.com; a self-hosted instance gets a
    # session-specific entry (its own hostname) instead.
    "odoo": ("odoo.com",),
}


class ScopeViolationError(BrowserError):
    """A navigation was attempted outside the session's allowed hosts."""


class DomainAllowlist:
    """Host-level allowlist with exact/subdomain matching (see module docstring)."""

    def __init__(self, domains: Iterable[str]) -> None:
        entries: set[str] = set()
        for raw in domains:
            domain = (raw or "").strip().lower()
            if not domain:
                continue
            # Accept "*.example.com" and "example.com" alike; the matching rule
            # below already covers subdomains of a bare entry, so the wildcard
            # prefix carries no extra meaning.
            entries.add(domain.lstrip("*."))
        self._entries = frozenset(entries)

    @property
    def entries(self) -> frozenset[str]:
        return self._entries

    def allows(self, url: str) -> bool:
        """Whether ``url`` may be navigated to under this allowlist."""
        parts = urlsplit(url)
        if parts.scheme not in ("http", "https"):
            return False
        host = (parts.hostname or "").lower()
        if not host:
            return False
        return any(host == entry or host.endswith("." + entry) for entry in self._entries)

    def check(self, url: str) -> None:
        """Raise :class:`ScopeViolationError` if ``url`` is not allowed."""
        if not self.allows(url):
            raise ScopeViolationError(
                f"navigation to {url!r} is outside the session allowlist "
                f"({sorted(self._entries)}); a session is scoped to its admin backend"
            )


def allowed_domains_for(system: str) -> list[str]:
    """Starting allowlist for a known system (``shopify``/``odoo``), else ``[]``.

    The empty list is deliberate and load-bearing: an unknown system must be
    explicitly scoped by whoever opens the session rather than silently
    inheriting a guessed domain.
    """
    return list(KNOWN_SYSTEM_DOMAINS.get(system.strip().lower(), ()))
