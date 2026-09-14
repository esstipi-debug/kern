"""Browser driver for the setup agent — see the client's screen, act on it safely.

Closes the last mile of connector onboarding (spec
``docs/superpowers/specs/2026-08-11-connector-setup-agent-design.md``, plan Fase 1):
a recipe needs to *watch* the client's own browser (Shopify admin, Odoo backend,
...) and *act* in it. That is a computer-use loop, so the transport must be
explicitly bounded:

- ``base.BrowserDriver`` is the only interface the loop and recipes speak to.
- ``scope.DomainAllowlist`` bounds navigation to a per-session allowlist — the
  driver can never wander off the admin it is onboarding.
- ``redact.redact_snapshot`` scrubs password values and PII out of everything
  that is handed to the LLM. The snapshot the driver produces already never
  *reads* a password field; redaction is the second, defensive layer.

Three transports implement ``BrowserDriver``:

- ``cdp.CdpBrowser`` — the real thing: drives the user's Chrome through the
  Chrome DevTools Protocol (``--remote-debugging-port``), the same target the
  Phase-2 browser extension will expose. ``websockets`` + ``httpx``, both
  imported lazily (the module is never on the app boot chain; prod boots
  without this extra).
- ``fake.FakeBrowser`` — the offline stand-in. The full recipe loop and every
  test run against it with zero network, zero credentials, in the same style as
  ``InMemoryOdoo`` / ``InMemoryShopifyInventory`` in the connectors package.

The two-layer pattern mirrors ``src/connectors/odoo.py``: a real transport
implementing a protocol, plus an in-memory twin so the whole pipeline runs and
is tested offline.
"""

from __future__ import annotations

from src.connectors.browser.base import (
    BrowserDriver,
    BrowserError,
    ElementInfo,
    PageSnapshot,
)
from src.connectors.browser.fake import FakeBrowser
from src.connectors.browser.redact import PASSWORD_PLACEHOLDER, redact_snapshot, redact_text
from src.connectors.browser.scope import (
    DomainAllowlist,
    ScopeViolationError,
    allowed_domains_for,
)

__all__ = [
    "BrowserDriver",
    "BrowserError",
    "DomainAllowlist",
    "ElementInfo",
    "FakeBrowser",
    "PageSnapshot",
    "PASSWORD_PLACEHOLDER",
    "ScopeViolationError",
    "allowed_domains_for",
    "redact_snapshot",
    "redact_text",
]

# ``cdp.CdpBrowser`` is intentionally NOT re-exported here: importing it is
# cheap (no heavy deps at import time), but keeping the heavy-transport import
# at the call site keeps "offline first" legible — tests and recipes import
# ``FakeBrowser`` from here and ``CdpBrowser`` explicitly when they mean the
# real browser.
