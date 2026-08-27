"""Brecha 6: Kern publicaba herramientas MCP y no consumia ninguna.

El grep de `mcp.client` / `ClientSession` / `stdio_client` /
`streamablehttp_client` sobre codigo de producto daba CERO, asi que cada backend
era un adaptador escrito a mano y cada proveedor nuevo significaba HTTP o
XML-RPC nuevo. `src/connectors/` no son tres conectores sino 21 archivos y ~7.400
lineas.

El "cerrada cuando" de la ficha pide exactamente lo que prueba este archivo: una
implementacion de `InventorySource` respaldada por una SESION CLIENTE MCP REAL
contra un servidor stub, que pase `isinstance`, y cuyos tres metodos devuelvan
valores que provengan demostrablemente del servidor remoto -- sin HTTP ni XML-RPC
nuevo.

El servidor stub es un FastMCP de verdad y el cliente una `ClientSession` de
verdad; lo unico que se evita es el transporte de red, con el pareo en memoria
que el propio paquete `mcp` provee (`mcp.shared.memory`). No hay dependencia
nueva: `mcp` 1.28.1 ya estaba instalado y su mitad cliente sin usar.
"""
from __future__ import annotations

import json

import pytest
from anyio.from_thread import start_blocking_portal
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session as _conectar

from src.connectors import InventorySource
from src.connectors.mcp_source import (
    McpInventorySource,
    McpToolError,
    decode_tool_result,
    sync_tool_caller,
)

PRODUCTOS = [
    {"sku": "A-1", "title": "Filtro 20mm", "price": 10.5, "cost": 4.25},
    {"sku": "B-2", "title": "Correa", "price": 31.0, "cost": 12.0},
]
NIVELES = [
    {"sku": "A-1", "available": 140.0, "location": "CD-Santiago"},
    {"sku": "B-2", "available": 3.0, "location": "CD-Santiago"},
]
PEDIDOS = [
    {"order_id": "SO-1", "created_at": "2026-08-01",
     "lines": [{"sku": "A-1", "quantity": 12.0, "price": 10.5}]},
    {"order_id": "SO-2", "created_at": "2026-08-20",
     "lines": [{"sku": "B-2", "quantity": 2.0, "price": 31.0}]},
]


def _servidor_stub() -> tuple[FastMCP, list[dict]]:
    """Un ERP ficticio que habla MCP. `vistos` registra lo que le llego."""
    srv = FastMCP("erp-stub")
    vistos: list[dict] = []

    @srv.tool()
    def list_products() -> list[dict]:
        vistos.append({"tool": "list_products"})
        return PRODUCTOS

    @srv.tool()
    def inventory_levels() -> list[dict]:
        vistos.append({"tool": "inventory_levels"})
        return NIVELES

    @srv.tool()
    def orders(since: str | None = None) -> list[dict]:
        vistos.append({"tool": "orders", "since": since})
        return [p for p in PEDIDOS if since is None or p["created_at"] >= since]

    return srv, vistos


class _Fuente:
    """Abre el par servidor-cliente en memoria y lo mantiene vivo."""

    def __enter__(self):
        srv, self.vistos = _servidor_stub()
        self._portal_cm = start_blocking_portal()
        portal = self._portal_cm.__enter__()
        self._ses_cm = portal.wrap_async_context_manager(_conectar(srv))
        ses = self._ses_cm.__enter__()
        return McpInventorySource(sync_tool_caller(ses, portal)), self.vistos

    def __exit__(self, *exc):
        self._ses_cm.__exit__(*exc)
        self._portal_cm.__exit__(*exc)
        return False


# --- el "cerrada cuando" -----------------------------------------------------


def test_una_fuente_mcp_es_un_inventory_source():
    fuente = McpInventorySource(lambda name, args: [])
    assert isinstance(fuente, InventorySource)


def test_los_tres_metodos_traen_datos_del_servidor_remoto():
    """EL TEST QUE IMPORTA.

    Sesion cliente MCP real contra un servidor stub real. Los valores no estan
    fabricados de este lado: salen del servidor, y `vistos` prueba que las tres
    herramientas se invocaron de verdad.
    """
    with _Fuente() as (fuente, vistos):
        productos = fuente.list_products()
        niveles = fuente.inventory_levels()
        pedidos = fuente.orders()

    assert [p.sku for p in productos] == ["A-1", "B-2"]
    assert productos[0].title == "Filtro 20mm"
    assert productos[0].price == 10.5 and productos[0].cost == 4.25

    assert [(n.sku, n.available, n.location) for n in niveles] == [
        ("A-1", 140.0, "CD-Santiago"), ("B-2", 3.0, "CD-Santiago"),
    ]

    assert [o.order_id for o in pedidos] == ["SO-1", "SO-2"]
    assert pedidos[0].lines[0].sku == "A-1"
    assert pedidos[0].lines[0].quantity == 12.0

    assert [v["tool"] for v in vistos] == ["list_products", "inventory_levels", "orders"]


def test_el_filtro_since_viaja_hasta_el_servidor():
    """Contraprueba de que el dato viene del remoto y no de una cache local:
    cambiar el argumento cambia lo que devuelve el servidor."""
    with _Fuente() as (fuente, vistos):
        todos = fuente.orders()
        recientes = fuente.orders(since="2026-08-10")

    assert [o.order_id for o in todos] == ["SO-1", "SO-2"]
    assert [o.order_id for o in recientes] == ["SO-2"]
    assert vistos[-1] == {"tool": "orders", "since": "2026-08-10"}


def test_sin_since_no_se_manda_el_argumento():
    """Un servidor cuya herramienta no acepta `since` rechazaria toda lectura
    sin filtro si lo mandaramos siempre en None."""
    with _Fuente() as (_, vistos):
        pass
    enviados = []
    fuente = McpInventorySource(lambda name, args: enviados.append(args) or [])
    fuente.orders()
    assert enviados == [{}]


def test_el_modulo_no_agrega_http_ni_xmlrpc():
    """La ficha lo pide explicitamente: "sin HTTP ni XML-RPC nuevo por proveedor".

    Es la mitad del valor de la brecha -- si cada servidor MCP necesitara su
    propio transporte, esto seria el conector 22.
    """
    from pathlib import Path

    fuente = Path(__file__).resolve().parents[1] / "src" / "connectors" / "mcp_source.py"
    codigo = "\n".join(
        linea for linea in fuente.read_text(encoding="utf-8").splitlines()
        if not linea.lstrip().startswith("#")
    )
    for prohibido in ("import requests", "import httpx", "xmlrpc", "urllib.request", "http.client"):
        assert prohibido not in codigo, prohibido


# --- el decodificador, con las dos formas que trae el protocolo ---------------


class _Bloque:
    type = "text"

    def __init__(self, obj):
        self.text = json.dumps(obj)


class _Resultado:
    def __init__(self, *, structured=None, content=(), is_error=False):
        self.structuredContent = structured
        self.content = list(content)
        self.isError = is_error


def test_desenvuelve_el_result_del_structured_content():
    """Medido contra FastMCP 1.28.1: un retorno `list[dict]` llega envuelto en
    {"result": [...]} -- es la regla del propio protocolo para un tipo de
    retorno que no es objeto, no una rareza de la implementacion."""
    r = decode_tool_result(_Resultado(structured={"result": PRODUCTOS}), tool="t")
    assert r == PRODUCTOS


def test_cae_a_los_bloques_de_texto_uno_por_fila():
    """El otro hallazgo de la misma medicion: `content` NO trae la lista entera
    sino un bloque de texto POR FILA. Un servidor sin structured content se lee
    igual."""
    r = decode_tool_result(_Resultado(content=[_Bloque(PRODUCTOS[0]), _Bloque(PRODUCTOS[1])]), tool="t")
    assert r == PRODUCTOS


def test_un_error_remoto_se_reporta_nombrando_la_herramienta():
    with pytest.raises(McpToolError, match="inventory_levels"):
        decode_tool_result(_Resultado(is_error=True), tool="inventory_levels")


def test_una_fila_que_no_es_objeto_se_rechaza():
    with pytest.raises(McpToolError, match="orders"):
        decode_tool_result(["no soy un objeto"], tool="orders")


def test_un_campo_obligatorio_faltante_nombra_el_campo_y_la_herramienta():
    fuente = McpInventorySource(lambda name, args: [{"sku": "A-1"}])
    with pytest.raises(McpToolError, match="available"):
        fuente.inventory_levels()


def test_los_campos_opcionales_tienen_default():
    fuente = McpInventorySource(lambda name, args: [{"sku": "A-1"}])
    (p,) = fuente.list_products()
    assert (p.title, p.price, p.cost) == ("", 0.0, 0.0)
