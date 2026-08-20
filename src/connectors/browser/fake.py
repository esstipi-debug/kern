"""Offline stand-in for the browser driver — the full recipe loop runs without a browser.

Mirrors ``InMemoryOdoo`` / ``InMemoryShopifyInventory`` in the rest of the
connectors package: the same :class:`BrowserDriver` surface, driven by an
in-memory page model, zero network, zero credentials. A recipe written against
``FakeBrowser`` runs unchanged against the user's real Chrome via
``cdp.CdpBrowser`` — that is the contract the tests lock in.

What the fake faithfully models (the semantics recipes depend on):

- ``navigate`` applies the session allowlist and raises
  :class:`ScopeViolationError` outside it; an unknown URL is a
  :class:`BrowserError` (404), not a silent no-op.
- ``snapshot`` never includes raw values for ``type="password"`` inputs.
- ``click``/``type`` raise :class:`BrowserError` when the selector matches
  nothing usable.
- ``eval_js`` is *not* implemented — the fake has no JavaScript engine, and any
  recipe that needs arbitrary JS is expressing something the offline world
  cannot verify, so it fails loudly instead of pretending.

The page model is deliberately plain (dict-like, no HTML parser): a page is a
title, visible text, and a list of elements. ``FakeBrowser.add_page`` makes
building a Shopify/Odoo admin fixture a few lines.
"""

from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Any

from src.connectors.browser.base import BrowserError, ElementInfo, PageSnapshot
from src.connectors.browser.scope import DomainAllowlist, ScopeViolationError

_PASSWORD_TYPES = frozenset({"password", "new-password", "current-password"})
_CLICKABLE_TAGS = frozenset({"a", "button"})
_INPUT_TYPES = frozenset(
    {"submit", "button", "reset", "checkbox", "radio", "file", "image"}
)


@dataclass
class FakeElement:
    """A page element the fake can address, query, click and type into."""

    selector: str
    tag: str = "input"
    role: str = "text"
    name: str | None = None
    value: str | None = None
    text: str | None = None
    visible: bool = True
    checked: bool = False

    def as_info(self) -> ElementInfo:
        value = None if self.role in _PASSWORD_TYPES else self.value
        return ElementInfo(
            selector=self.selector,
            tag=self.tag,
            role=self.role,
            name=self.name,
            value=value,
            text=self.text,
            visible=self.visible,
        )

    def typeable(self) -> bool:
        return self.tag in ("input", "textarea") and self.role not in _INPUT_TYPES

    def clickable(self) -> bool:
        return self.tag in _CLICKABLE_TAGS or (self.tag == "input" and self.role in _INPUT_TYPES)


@dataclass
class FakePage:
    """One URL's worth of page state."""

    url: str
    title: str = ""
    text: str = ""
    elements: list[FakeElement] = field(default_factory=list)


class FakeBrowser:
    """A :class:`BrowserDriver` that drives an in-memory page model offline."""

    def __init__(self, allowed_domains: DomainAllowlist | list[str]) -> None:
        self._allowlist = (
            allowed_domains
            if isinstance(allowed_domains, DomainAllowlist)
            else DomainAllowlist(allowed_domains)
        )
        self._pages: dict[str, FakePage] = {}
        self.current_url: str | None = None
        self.click_log: list[str] = []

    # -- fixture building ------------------------------------------------------

    def add_page(self, page: FakePage) -> None:
        self._pages[page.url] = page

    def reset(self) -> None:
        self.current_url = None
        self.click_log.clear()

    # -- BrowserDriver surface -------------------------------------------------

    async def navigate(self, url: str) -> None:
        self._allowlist.check(url)
        page = self._pages.get(url)
        if page is None:
            raise BrowserError(f"FakeBrowser has no page for {url!r}")
        self.current_url = url

    async def snapshot(self) -> PageSnapshot:
        page = self._require_page()
        return PageSnapshot(
            url=page.url,
            title=page.title,
            text=page.text,
            elements=tuple(e.as_info() for e in page.elements if e.visible),
        )

    async def dom_query(self, selector: str) -> ElementInfo | None:
        page = self._require_page()
        element = self._find(page, selector)
        return element.as_info() if element is not None else None

    async def click(self, selector: str) -> None:
        page = self._require_page()
        element = self._find(page, selector)
        if element is None or not element.clickable():
            raise BrowserError(f"no clickable element matched selector {selector!r}")
        if element.role in ("checkbox", "radio"):
            element.checked = not element.checked
        self.click_log.append(selector)

    async def type(self, selector: str, text: str) -> None:
        page = self._require_page()
        element = self._find(page, selector)
        if element is None or not element.typeable():
            raise BrowserError(f"no typeable element matched selector {selector!r}")
        element.value = text

    async def eval_js(self, expression: str) -> Any:
        raise BrowserError(
            "FakeBrowser has no JavaScript engine; a recipe calling eval_js cannot "
            "be verified offline — either drop the eval_js step or run it against "
            "the real CdpBrowser"
        )

    async def wait(self, selector: str, timeout_s: float) -> bool:
        page = self._require_page()
        deadline = time.monotonic() + timeout_s
        while self._find(page, selector) is None:
            if time.monotonic() >= deadline:
                return False
            await asyncio.sleep(0.02)
        return True

    # -- internals -------------------------------------------------------------

    def _require_page(self) -> FakePage:
        if self.current_url is None:
            raise BrowserError("FakeBrowser has not navigated yet")
        return self._pages[self.current_url]

    def _find(self, page: FakePage, selector: str) -> FakeElement | None:
        # Exact selector first (recipes pass back what the snapshot reported).
        for element in page.elements:
            if element.selector == selector:
                return element
        # Then the small CSS-subset used in hand-written recipe steps.
        for element in page.elements:
            if _matches(element, selector):
                return element
        return None


def _parse_selector(selector: str) -> tuple[str | None, str | None, str | None, str | None, bool]:
    """Best-effort parse of a tiny CSS subset into (tag, id, name, type, strict).

    Handles ``#id``, bare ``tag``, ``tag[name=x]``, ``tag[name="x"]``,
    ``input[type="password"]`` and ``[name=x]`` — the forms recipes actually
    write. Anything with a class, pseudo-class or descendant combinator
    (``button.nope``, ``a:hover``, ``form > button``) returns ``strict=False``:
    only an exact match on the snapshot-reported selector is trustworthy for
    those offline, and the loose matcher refuses them instead of guessing.
    """
    selector = selector.strip()
    if not selector:
        return None, None, None, None, False
    tag: str | None = None
    rest = selector
    match = re.match(r"^([a-z]+)", selector, flags=re.I)
    if match:
        tag = match.group(1).lower()
        rest = selector[match.end():]
    if "." in rest or ":" in rest or ">" in rest:
        return None, None, None, None, False
    element_id: str | None = None
    if rest.startswith("#"):
        element_id = rest[1:].strip().strip("\"'")
        rest = ""
    name: str | None = None
    input_type: str | None = None
    for attr in re.findall(r"\[([a-zA-Z]+)=([^\]]+)\]", rest):
        key, value = attr
        value = value.strip().strip("\"'")
        if key == "name":
            name = value
        elif key == "type":
            input_type = value
    return tag, element_id, name, input_type, True


def _matches(element: FakeElement, selector: str) -> bool:
    tag, element_id, name, input_type, strict = _parse_selector(selector)
    if not strict:
        return False
    if tag is not None and element.tag != tag:
        return False
    if element_id is not None and f"#{element_id}" not in element.selector:
        return False
    if name is not None and element.name != name:
        return False
    if input_type is not None and element.role != input_type:
        return False
    return tag is not None or element_id is not None or name is not None or input_type is not None


# Re-export the shared exception so recipes can import both from one place.
__all__ = ["FakeBrowser", "FakeElement", "FakePage", "ScopeViolationError"]
