"""The interface the setup loop and every recipe speaks to.

``BrowserDriver`` is deliberately small and role-shaped: it models what a
computer-use agent needs from a browser, not what a browser can do. The two
implementations (``cdp.CdpBrowser`` real, ``fake.FakeBrowser`` offline) share
this surface so a recipe written against ``FakeBrowser`` runs unchanged against
the user's real Chrome.

Every method is async because the real transport (CDP over a websocket) is;
the offline stand-in and the loop are async to match, and the repo already runs
async tests (``pytest-asyncio``, ``asyncio_mode = "auto"``).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


class BrowserError(RuntimeError):
    """A browser operation failed: transport, navigation, or bad selector."""


@dataclass(frozen=True)
class ElementInfo:
    """A single element the driver could address, as the loop/LLM sees it.

    ``value`` is the field's *current value if the driver itself typed it* —
    a password input always comes back as ``None`` here (the driver never reads
    passwords back), and ``redact_snapshot`` forces any stray value to the
    ``<redacted>`` placeholder as the defensive second layer.
    """

    selector: str
    tag: str
    role: str  # input/button/link/select/label or the element's own tag
    name: str | None
    value: str | None
    text: str | None
    visible: bool


@dataclass(frozen=True)
class PageSnapshot:
    """Everything about the current page that may go to the LLM, pre-redaction.

    ``text`` is the page's visible text (truncated); ``elements`` is the set of
    interactive/queryable elements with stable selectors; ``screenshot_b64`` is
    an optional full-page PNG the operator console can render live. Never
    include credentials here — see :mod:`src.connectors.browser.redact`.
    """

    url: str
    title: str
    text: str
    elements: tuple[ElementInfo, ...]
    screenshot_b64: str | None = None


@runtime_checkable
class BrowserDriver(Protocol):
    """Minimal computer-use surface shared by the real and fake transports.

    Contract shared by both implementations:

    - ``navigate`` applies the session's ``DomainAllowlist`` first and raises
      ``ScopeViolationError`` for anything outside it.
    - ``snapshot`` never includes raw password input values (see the ``value``
      note on :class:`ElementInfo`).
    - ``click``/``type`` raise ``BrowserError`` when the selector does not
      match a usable element.
    """

    async def navigate(self, url: str) -> None: ...

    async def snapshot(self) -> PageSnapshot: ...

    async def dom_query(self, selector: str) -> ElementInfo | None: ...

    async def click(self, selector: str) -> None: ...

    async def type(self, selector: str, text: str) -> None: ...

    async def eval_js(self, expression: str) -> Any: ...

    async def wait(self, selector: str, timeout_s: float) -> bool: ...
