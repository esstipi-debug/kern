"""An ``InventorySource`` backed by a remote MCP server (brecha 6).

Kern publishes MCP tools (``webapp/mcp_server.py``) and, until this module,
consumed none: a grep for ``mcp.client`` / ``ClientSession`` / ``stdio_client``
/ ``streamablehttp_client`` over product code returned zero. Every backend was
therefore a hand-written adapter -- ``src/connectors/`` is 21 files and ~7.4k
lines, not the three the "odoo, shopify, meli" shorthand suggests -- and each
new provider meant new HTTP or XML-RPC. This module makes any MCP server that
exposes three read tools a first-class ``InventorySource`` with no new
transport code per provider.

NO NEW DEPENDENCY. The client half was already installed and unused: ``mcp``
1.28.1 ships ``mcp/client/`` complete (``session.py``, ``sse.py``, ``stdio/``,
``streamable_http.py``, ``websocket.py``) and ``pyproject.toml``'s ``[mcp]``
extra already pins ``mcp>=1.0,<2``. The cap is untouched.

SYNC FACADE OVER AN ASYNC SESSION. ``InventorySource`` is a sync Protocol and
``ClientSession`` is async, so this module does not try to hide the seam: the
caller owns the session and hands in a ``call_tool`` callable.
:func:`sync_tool_caller` builds that callable from a live session plus an
``anyio`` blocking portal, which is what lets one event loop serve many sync
calls without a loop per call. A test -- or a caller that already has its own
transport -- can pass any callable with the same shape.

THE WIRE CONTRACT, MEASURED NOT ASSUMED. ``CallToolResult`` carries the payload
in two places and they do not agree in shape. Probed against FastMCP 1.28.1 with
a tool returning ``list[dict]``:

    structuredContent -> {"result": [ {...}, {...} ]}     # the whole list, wrapped
    content           -> one text block PER ITEM          # NOT the whole list

The ``"result"`` wrapper is the MCP spec's own rule for a non-object return
type, not a FastMCP quirk, so :func:`decode_tool_result` unwraps it and falls
back to parsing the text blocks one per row for servers that ship no structured
content. Both paths are exercised by tests.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from . import (
    COVERAGE_MEASURED,
    WRITE_NONE,
    FieldCoverage,
    InventoryLevel,
    Order,
    OrderLine,
    Product,
    ServiceCoverage,
    WriteSurface,
)

#: The three read tools a remote server must expose. Names are overridable
#: because a third-party server names its tools for its own domain, not ours.
DEFAULT_PRODUCTS_TOOL = "list_products"
DEFAULT_LEVELS_TOOL = "inventory_levels"
DEFAULT_ORDERS_TOOL = "orders"


class McpToolError(RuntimeError):
    """A remote tool failed, or answered in a shape this adapter cannot read.

    Always names the tool: a decode failure with no tool name sends whoever
    reads the traceback to grep three call sites.
    """


def decode_tool_result(result: Any, *, tool: str) -> list[dict]:
    """The rows a remote tool returned, whatever transport carried them.

    Accepts an already-decoded ``list`` unchanged, so a caller that owns a
    different transport (or a unit test) does not have to build a
    ``CallToolResult`` just to be understood.
    """
    if isinstance(result, list):
        rows = result
    else:
        if getattr(result, "isError", False):
            raise McpToolError(f"{tool}: the remote server reported an error: {result}")
        structured = getattr(result, "structuredContent", None)
        if isinstance(structured, dict) and set(structured) == {"result"}:
            rows = structured["result"]
        elif structured is not None:
            rows = structured
        else:
            rows = [_json_block(b, tool=tool) for b in getattr(result, "content", []) or []]

    if not isinstance(rows, list):
        raise McpToolError(f"{tool}: expected a list of rows, got {type(rows).__name__}")
    for row in rows:
        if not isinstance(row, dict):
            raise McpToolError(f"{tool}: expected each row to be an object, got {type(row).__name__}")
    return rows


def _json_block(block: Any, *, tool: str) -> Any:
    text = getattr(block, "text", None)
    if text is None:
        raise McpToolError(f"{tool}: content block of type {getattr(block, 'type', '?')!r} is not text")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise McpToolError(f"{tool}: content block is not JSON: {exc}") from exc


def sync_tool_caller(session: Any, portal: Any) -> Callable[[str, dict], Any]:
    """A sync ``call_tool(name, arguments)`` over an async ``ClientSession``.

    ``portal`` is an ``anyio`` blocking portal (``anyio.from_thread.
    start_blocking_portal``) already running the loop the session was opened
    on. Keeping both in the caller's hands is deliberate: a source that opened
    its own loop per call could not hold a stdio subprocess or an HTTP session
    open across the three reads.
    """

    def call(name: str, arguments: dict) -> Any:
        return portal.call(session.call_tool, name, arguments)

    return call


def _num(row: dict, key: str, *, tool: str, default: float | None = None) -> float:
    if key not in row:
        if default is not None:
            return default
        raise McpToolError(f"{tool}: row is missing the required field {key!r}: {row}")
    try:
        return float(row[key])
    except (TypeError, ValueError) as exc:
        raise McpToolError(f"{tool}: field {key!r} is not a number: {row[key]!r}") from exc


def _num_opt(row: dict, key: str, *, tool: str) -> float | None:
    """Un numero que el servidor puede legitimamente no traer.

    Distinto de ``_num``: la ausencia no es un error de contrato sino un dato -
    nadie lo reporto. Un valor presente pero no numerico si lo es, y se levanta
    igual que en ``_num``, porque eso es un campo mal mapeado y no un silencio.
    """
    if key not in row or row[key] is None:
        return None
    return _num(row, key, tool=tool)


def _text_opt(row: dict, key: str) -> str | None:
    value = row.get(key)
    return None if value is None else str(value)


def _text(row: dict, key: str, *, tool: str, default: str | None = None) -> str:
    if key not in row:
        if default is not None:
            return default
        raise McpToolError(f"{tool}: row is missing the required field {key!r}: {row}")
    return str(row[key])


class McpInventorySource:
    """Reads a client's catalog, stock and orders through MCP tool calls.

    Satisfies ``src.connectors.InventorySource`` structurally -- it is a
    runtime-checkable Protocol, so ``isinstance`` holds without inheritance.
    """

    def __init__(
        self,
        call_tool: Callable[[str, dict], Any],
        *,
        products_tool: str = DEFAULT_PRODUCTS_TOOL,
        levels_tool: str = DEFAULT_LEVELS_TOOL,
        orders_tool: str = DEFAULT_ORDERS_TOOL,
    ) -> None:
        self._call = call_tool
        self._products_tool = products_tool
        self._levels_tool = levels_tool
        self._orders_tool = orders_tool

    def _rows(self, tool: str, arguments: dict | None = None) -> list[dict]:
        return decode_tool_result(self._call(tool, arguments or {}), tool=tool)

    def list_products(self) -> list[Product]:
        tool = self._products_tool
        return [
            Product(
                sku=_text(r, "sku", tool=tool),
                title=_text(r, "title", tool=tool, default=""),
                price=_num(r, "price", tool=tool, default=0.0),
                cost=_num(r, "cost", tool=tool, default=0.0),
            )
            for r in self._rows(tool)
        ]

    def inventory_levels(self) -> list[InventoryLevel]:
        tool = self._levels_tool
        return [
            InventoryLevel(
                sku=_text(r, "sku", tool=tool),
                available=_num(r, "available", tool=tool),
                location=_text(r, "location", tool=tool, default="default"),
            )
            for r in self._rows(tool)
        ]

    def orders(self, *, since: str | None = None) -> list[Order]:
        tool = self._orders_tool
        # `since` only travels when set: a server whose tool takes no such
        # argument would reject the call outright on every unfiltered read.
        rows = self._rows(tool, {"since": since} if since is not None else {})
        return [
            Order(
                order_id=_text(r, "order_id", tool=tool),
                created_at=_text(r, "created_at", tool=tool, default=""),
                lines=tuple(
                    OrderLine(
                        sku=_text(line, "sku", tool=tool),
                        quantity=_num(line, "quantity", tool=tool),
                        price=_num(line, "price", tool=tool, default=0.0),
                        # El lado de servicio es opcional: un ERP que no lo publica
                        # deja las tres en None y el servicio sale `not_measured`,
                        # que es lo que efectivamente pasa. Rellenarlas con la
                        # cantidad pedida daria 100% de fill rate sin medir nada.
                        delivered_qty=_num_opt(line, "delivered_qty", tool=tool),
                        promised_date=_text_opt(line, "promised_date"),
                        delivered_date=_text_opt(line, "delivered_date"),
                    )
                    for line in r.get("lines", [])
                ),
            )
            for r in rows
        ]


#: El techo del servidor MCP remoto, que no controlamos. Las tres son opcionales en el
#: esquema de la tool y llegan como ``None`` cuando el servidor las omite (ver
#: ``_num_opt`` / ``_text_opt``); se declaran MEASURED porque un servidor que las publica
#: es el caso para el que existe este adaptador, y uno que no lo hace sale no medido.
SERVICE_COVERAGE = ServiceCoverage(
    backend="servidor MCP remoto (esquema de tool, no un vendor fijo)",
    delivered_qty=FieldCoverage(
        COVERAGE_MEASURED,
        "tool de ordenes -> lines[].delivered_qty",
        "unidades que el servidor dice haber servido; ausente -> None, nunca rellenada "
        "con la cantidad pedida",
    ),
    promised_date=FieldCoverage(
        COVERAGE_MEASURED,
        "tool de ordenes -> lines[].promised_date",
        "la fecha de entrega que el servidor dice haber prometido",
    ),
    delivered_date=FieldCoverage(
        COVERAGE_MEASURED,
        "tool de ordenes -> lines[].delivered_date",
        "la fecha en que el servidor dice que la linea se sirvio",
    ),
)


#: Que escribe este conector en el sistema del cliente (ver ``WriteSurface``).
WRITE_SURFACE = WriteSurface(
    WRITE_NONE,
    means=(
        'solo lectura: otro servidor MCP como fuente de productos, stock y pedidos. Kern nunca invoca herramientas de escritura de un MCP ajeno.'
    ),
)
