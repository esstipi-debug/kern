"""Real transport: drives the user's Chrome over the DevTools Protocol.

This is the actual "see the client's screen" implementation. It talks to a
Chrome/Chromium launched with ``--remote-debugging-port=9222`` (the same
endpoint the Phase-2 browser extension will proxy), by default discovering the
first open page target via ``GET /json/list`` and then driving it over the
target's websocket:

- ``snapshot()`` runs a single ``Runtime.evaluate`` that serializes the
  page (URL, title, visible text, interactive elements with stable selectors)
  and optionally captures a screenshot.
- ``dom_query/click/type/eval_js/wait`` are the action surface the recipes use;
  ``click``/``type`` raise :class:`BrowserError` when the selector matches
  nothing usable.
- ``navigate`` applies the session's :class:`DomainAllowlist` first — the
  driver can never wander off the admin it is onboarding.

Dependencies: ``websockets`` + ``httpx``, both optional-extra deps imported
lazily (this module is never on the app boot chain; prod boots without the
``[browser]`` extra). The CDP protocol logic itself is tested offline against a
fake websocket endpoint (see ``tests/test_browser_cdp.py``), exactly like the
other connectors test their transports against ``InMemory`` stand-ins.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any

from src.connectors.browser.base import BrowserError, ElementInfo, PageSnapshot
from src.connectors.browser.scope import DomainAllowlist

#: Default host/port of a Chrome launched with --remote-debugging-port. The
#: same values Chrome prints to stderr at startup.
_DEFAULT_CDP_HTTP = "http://127.0.0.1:9222"
_DEFAULT_TIMEOUT_S = 15.0
_READ_BUFFER_EVENTS = 256

# -- JS snippets the driver evaluates in the page. Kept here (not inline in
# -- methods) so tests can assert on them and the fake endpoint can reuse them.


def _js_snapshot() -> str:
    """Serialize the page into the snapshot shape the protocol expects.

    Returns an object with ``url``, ``title``, ``text`` (visible text,
    truncated) and ``elements`` (list of ``ElementInfo``-shaped dicts). The one
    hard rule: a ``type="password"`` input's ``value`` is never included.
    """
    return r"""
(() => {
  const MAX_TEXT = 12000;
  const visible = (e) => {
    const r = e.getBoundingClientRect();
    const st = getComputedStyle(e);
    return r.width > 0 && r.height > 0 && st.visibility !== 'hidden' && st.display !== 'none';
  };
  const selectorFor = (e) => {
    if (e.id) return '#' + CSS.escape(e.id);
    const parts = [];
    let n = e;
    while (n && n !== document.body && n.nodeType === 1) {
      let part = n.tagName.toLowerCase();
      if (n.id) { parts.unshift('#' + CSS.escape(n.id)); break; }
      const parent = n.parentElement;
      if (parent) {
        const siblings = Array.from(parent.children).filter((c) => c.tagName === n.tagName);
        if (siblings.length > 1) part += ':nth-of-type(' + (siblings.indexOf(n) + 1) + ')';
      }
      if (n.name) part += '[name=' + JSON.stringify(String(n.name)) + ']';
      parts.unshift(part);
      n = n.parentElement;
    }
    return parts.join(' > ');
  };
  const nodes = Array.from(document.querySelectorAll('a,button,input,select,textarea,[role]'));
  const elements = [];
  for (const e of nodes) {
    if (!visible(e)) continue;
    const tag = e.tagName.toLowerCase();
    let role = tag;
    let value = null;
    if (tag === 'input') {
      role = e.type || 'text';
      if (e.type !== 'password' && e.type !== 'new-password' && e.type !== 'current-password') value = e.value;
    } else if (tag === 'textarea') {
      role = 'textarea';
      value = e.value;
    } else if (e.getAttribute('role')) {
      role = e.getAttribute('role');
    }
    const name = e.getAttribute('name') || e.id || e.getAttribute('aria-label') ||
      (tag === 'a' || tag === 'button' ? (e.textContent || '').trim().slice(0, 60) : null);
    const text = (e.textContent || '').trim().slice(0, 120) || null;
    elements.push({selector: selectorFor(e), tag, role, name, value, text, visible: true});
  }
  const bodyText = (document.body && document.body.innerText || '').replace(/\s+\n/g, '\n').trim().slice(0, MAX_TEXT);
  return {url: location.href, title: document.title || '', text: bodyText, elements};
})()
"""


def _js_dom_query() -> str:
    """Return one element's info (or ``None``) for a selector."""
    return r"""
(() => {
  const SEL = SEL_HERE;
  const e = document.querySelector(SEL);
  if (!e) return null;
  const tag = e.tagName.toLowerCase();
  let role = tag, value = null;
  if (tag === 'input') {
    role = e.type || 'text';
    if (e.type !== 'password' && e.type !== 'new-password' && e.type !== 'current-password') value = e.value;
  } else if (tag === 'textarea') { role = 'textarea'; value = e.value; }
  else if (e.getAttribute('role')) role = e.getAttribute('role');
  const name = e.getAttribute('name') || e.id || e.getAttribute('aria-label') ||
    (tag === 'a' || tag === 'button' ? (e.textContent || '').trim().slice(0, 60) : null);
  return {selector: SEL, tag, role, name, value, text: (e.textContent || '').trim().slice(0, 120) || null, visible: true};
})()
"""


def _js_click() -> str:
    return r"""
(() => {
  const e = document.querySelector(SEL_HERE);
  if (!e) return false;
  if (e.scrollIntoView) e.scrollIntoView({block: 'center', inline: 'nearest'});
  e.click();
  return true;
})()
"""


def _js_type() -> str:
    """Type into an input/textarea via the native value setter (React-safe).

    Setting ``value`` through the element's prototype setter is what makes this
    work on React/Vue-controlled inputs; dispatching ``input``/``change`` then
    notifies the framework. Returns ``false`` when the selector matches nothing
    typeable.
    """
    return r"""
(() => {
  const e = document.querySelector(SEL_HERE);
  if (!e) return false;
  if (e.isContentEditable) { e.focus(); e.textContent = TEXT_HERE; return true; }
  if (e.tagName !== 'INPUT' && e.tagName !== 'TEXTAREA') return false;
  e.focus();
  const proto = e.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
  const setter = Object.getOwnPropertyDescriptor(proto, 'value').set;
  setter.call(e, TEXT_HERE);
  e.dispatchEvent(new Event('input', {bubbles: true}));
  e.dispatchEvent(new Event('change', {bubbles: true}));
  return true;
})()
"""


def _js_wait() -> str:
    return r"""
(() => { return !!document.querySelector(SEL_HERE); })()
"""


def _js_eval_result() -> str:
    return r"""
(() => {
  const r = EVAL_HERE;
  return typeof r === 'object' && r !== null ? JSON.parse(JSON.stringify(r)) : r;
})()
"""


def _with_selector(script: str, selector: str) -> str:
    """Inject a JSON-stringified selector into a ``SEL_HERE`` placeholder."""
    return script.replace("SEL_HERE", json.dumps(selector))


class CdpBrowser:
    """A ``BrowserDriver`` over a real Chrome DevTools Protocol target.

    Call :meth:`connect` (or use :func:`connect_url` for tests) before use, and
    :meth:`close` when done. Not an async context manager because a session may
    need to reconnect after the operator's Chrome restarts; the setup loop owns
    the lifecycle.
    """

    def __init__(
        self,
        allowed_domains: DomainAllowlist | list[str],
        *,
        http_base: str | None = None,
        timeout_s: float = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self._allowlist = (
            allowed_domains
            if isinstance(allowed_domains, DomainAllowlist)
            else DomainAllowlist(allowed_domains)
        )
        self._http_base = http_base or os.environ.get("KERN_CDP_HTTP", _DEFAULT_CDP_HTTP)
        self._timeout = timeout_s
        self._endpoint: str | None = None
        self._ws: Any = None
        self._reader: asyncio.Task | None = None
        self._pending: dict[int, asyncio.Future] = {}
        self._events: asyncio.Queue = asyncio.Queue(maxsize=_READ_BUFFER_EVENTS)
        self._id_counter = 0

    # -- lifecycle ------------------------------------------------------------

    async def connect(self) -> None:
        """Discover and open the first page target, enable CDP domains."""
        endpoint = self._endpoint or await self._discover_endpoint()
        await self._open_websocket(endpoint)

    async def connect_url(self, endpoint: str) -> None:
        """Connect directly to a target's ``webSocketDebuggerUrl`` (tests, extension)."""
        self._endpoint = endpoint
        await self._open_websocket(endpoint)

    async def close(self) -> None:
        if self._reader is not None:
            self._reader.cancel()
            self._reader = None
        if self._ws is not None:
            await self._ws.close()
            self._ws = None

    async def _discover_endpoint(self) -> str:
        try:
            import httpx  # optional-extra dep, imported lazily
        except ImportError as exc:  # pragma: no cover - exercised by CI without the extra
            raise BrowserError(
                "httpx is required for CDP discovery — install the '[browser]' extra"
            ) from exc
        try:
            resp = await httpx.AsyncClient(timeout=self._timeout).get(
                f"{self._http_base.rstrip('/')}/json/list"
            )
            resp.raise_for_status()
            targets = resp.json()
        except Exception as exc:
            raise BrowserError(
                f"could not reach Chrome DevTools at {self._http_base} — start Chrome "
                "with --remote-debugging-port=9222"
            ) from exc
        for target in targets:
            if target.get("type") == "page" and target.get("webSocketDebuggerUrl"):
                return target["webSocketDebuggerUrl"]
        raise BrowserError(
            f"no page target at {self._http_base} — open a tab in the debugged Chrome first"
        )

    async def _open_websocket(self, endpoint: str) -> None:
        try:
            import websockets
            try:
                from websockets.asyncio.client import connect as _connect
            except ImportError:  # websockets < 14 legacy top-level alias
                _connect = websockets.connect  # type: ignore[attr-defined]
        except ImportError as exc:  # pragma: no cover - exercised by CI without the extra
            raise BrowserError(
                "websockets is required for CDP — install the '[browser]' extra"
            ) from exc
        try:
            ws = await _connect(endpoint, open_timeout=self._timeout)
        except Exception as exc:
            raise BrowserError(f"could not open CDP websocket {endpoint}: {exc}") from exc
        self._ws = ws
        self._reader = asyncio.create_task(self._read_loop())
        await self._call("Page.enable")
        await self._call("Runtime.enable")

    async def _read_loop(self) -> None:
        ws, pending = self._ws, self._pending
        try:
            async for raw in ws:
                try:
                    message = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if "id" in message:
                    future = pending.pop(message["id"], None)
                    if future is None:
                        continue
                    if "error" in message:
                        future.set_exception(BrowserError(str(message["error"])))
                    else:
                        future.set_result(message.get("result", {}))
                else:
                    # Events (Page.loadEventFired, ...) — queued for recipes that
                    # need them; the driver's own waits are poll-based.
                    try:
                        self._events.put_nowait(message)
                    except asyncio.QueueFull:
                        try:
                            self._events.get_nowait()
                        except asyncio.QueueEmpty:
                            pass
                        self._events.put_nowait(message)
        finally:
            # A closed websocket must unblock every in-flight call.
            error = BrowserError("CDP websocket closed")
            for future in pending.values():
                if not future.done():
                    future.set_exception(error)
            pending.clear()

    def _next_id(self) -> int:
        self._id_counter += 1
        return self._id_counter

    async def _call(self, method: str, params: dict | None = None) -> Any:
        if self._ws is None:
            raise BrowserError("CdpBrowser is not connected — call connect() first")
        msg_id = self._next_id()
        loop = asyncio.get_running_loop()
        future: asyncio.Future = loop.create_future()
        self._pending[msg_id] = future
        try:
            await self._ws.send(json.dumps({"id": msg_id, "method": method, "params": params or {}}))
            return await asyncio.wait_for(future, timeout=self._timeout)
        except asyncio.TimeoutError as exc:
            self._pending.pop(msg_id, None)
            raise BrowserError(f"CDP call {method} timed out after {self._timeout}s") from exc

    # -- BrowserDriver surface -------------------------------------------------

    async def navigate(self, url: str) -> None:
        self._allowlist.check(url)
        await self._call("Page.navigate", {"url": url})

    async def snapshot(self) -> PageSnapshot:
        result = await self._evaluate(_js_snapshot())
        if not isinstance(result, dict):
            raise BrowserError("page serialization returned an unexpected value")
        elements = tuple(
            ElementInfo(
                selector=str(item.get("selector", "")),
                tag=str(item.get("tag", "")),
                role=str(item.get("role", "")),
                name=item.get("name"),
                value=item.get("value"),
                text=item.get("text"),
                visible=bool(item.get("visible", True)),
            )
            for item in result.get("elements", [])
            if isinstance(item, dict)
        )
        screenshot = await self._screenshot()
        return PageSnapshot(
            url=str(result.get("url", "")),
            title=str(result.get("title", "")),
            text=str(result.get("text", "")),
            elements=elements,
            screenshot_b64=screenshot,
        )

    async def dom_query(self, selector: str) -> ElementInfo | None:
        result = await self._evaluate(_with_selector(_js_dom_query(), selector))
        if result is None:
            return None
        if not isinstance(result, dict):
            raise BrowserError("dom_query returned an unexpected value")
        return ElementInfo(
            selector=str(result.get("selector", selector)),
            tag=str(result.get("tag", "")),
            role=str(result.get("role", "")),
            name=result.get("name"),
            value=result.get("value"),
            text=result.get("text"),
            visible=bool(result.get("visible", True)),
        )

    async def click(self, selector: str) -> None:
        ok = await self._evaluate(_with_selector(_js_click(), selector))
        if not ok:
            raise BrowserError(f"no element matched selector {selector!r} for click")

    async def type(self, selector: str, text: str) -> None:
        script = _with_selector(_js_type(), selector).replace("TEXT_HERE", json.dumps(text))
        ok = await self._evaluate(script)
        if not ok:
            raise BrowserError(f"no typeable element matched selector {selector!r}")

    async def eval_js(self, expression: str) -> Any:
        return await self._evaluate(_js_eval_result().replace("EVAL_HERE", expression))

    async def wait(self, selector: str, timeout_s: float) -> bool:
        deadline = asyncio.get_running_loop().time() + timeout_s
        script = _with_selector(_js_wait(), selector)
        while True:
            if await self._evaluate(script):
                return True
            if asyncio.get_running_loop().time() >= deadline:
                return False
            await asyncio.sleep(0.2)

    # -- internals -------------------------------------------------------------

    async def _evaluate(self, expression: str) -> Any:
        result = await self._call(
            "Runtime.evaluate",
            {"expression": expression, "returnByValue": True, "awaitPromise": True},
        )
        if result.get("exceptionDetails"):
            raise BrowserError(f"page evaluation failed: {result['exceptionDetails']}")
        return result.get("result", {}).get("value")

    async def _screenshot(self) -> str | None:
        try:
            result = await self._call(
                "Page.captureScreenshot",
                {"format": "png", "captureBeyondViewport": False},
            )
        except BrowserError:
            return None
        return result.get("data")
