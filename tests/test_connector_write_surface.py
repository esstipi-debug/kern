"""Cada conector declara que escribe, la declaracion dice la verdad, y el documento
publico se genera de ella.

Freno mecanico #6 del plan del 2026-09-05 ("un conector nuevo no entra sin
``SERVICE_COVERAGE`` + ``WRITE_SURFACE``"). Tres cosas se exigen:

1. Todo modulo de ``src/connectors/`` que lea o escriba un sistema (define una clase
   con ``orders``/``arrivals``/``inventory_levels`` o con ``commit``/``apply_*``/
   ``stage_*``) exporta ``WRITE_SURFACE``.
2. La declaracion no miente: un modulo con metodos de escritura no puede decir
   ``none``; uno sin ellos no puede decir ``approval_gated``.
3. ``documentation/CONNECTED_SYSTEMS.md`` es byte a byte lo que emite
   ``scripts/connected_systems.py``: nadie lo edita a mano.
"""

from __future__ import annotations

import importlib
import inspect
import pkgutil
from pathlib import Path

import pytest

import src.connectors as connectors_pkg
from src.connectors import WRITE_APPROVAL_GATED, WRITE_NONE, WriteSurface

REPO = Path(__file__).resolve().parents[1]
WRITE_METHODS = ("commit", "apply_restock", "stage_restock", "apply_draft_purchase_orders")

#: Modulos del paquete que NO son un conector a un sistema del cliente, con el motivo.
#: Esta lista es parte del freno: sumar un modulo aca es una decision visible en el
#: diff, igual que declarar un WRITE_SURFACE. El criterio deliberadamente NO mira que
#: metodos tiene el modulo -- una lista de metodos de lectura ("orders", "arrivals",
#: "inventory_levels") dejaba pasar cualquier conector nuevo que llamara distinto a lo
#: suyo, y eso paso: ``meli_catalog`` (#337) entro sin declarar nada porque sus metodos
#: se llaman ``search_products`` y ``discover_category``.
NOT_CONNECTORS = {
    "__init__": "el paquete: define los contratos, no habla con ningun sistema",
    "arrivals": "el Protocol que implementan los transportistas, no un backend",
    "emulator": "servidor HTTP de juguete para probar el cliente REST, no un sistema real",
    "lectura": (
        "el indice de lectura de Kern (44 libros de supply chain partidos en pasajes con "
        "su pagina), no un sistema del cliente: es nuestra propia biblioteca, servida por "
        "un MCP local y solo de lectura. Declarar un WRITE_SURFACE aca lo publicaria en "
        "CONNECTED_SYSTEMS.md como si Kern se conectara a algo del cliente, que es "
        "exactamente lo contrario de lo que pasa"
    ),
    "health": (
        "el contrato de salud de conexion que implementan los conectores (sondas, "
        "backoff, clasificacion de errores), no un backend. Declarar un WRITE_SURFACE "
        "aca publicaria en CONNECTED_SYSTEMS.md un sistema del cliente que no existe"
    ),
    "ledger": "registro local de lo que Kern leyo y escribio; no habla con el cliente",
    "replenish": "el planificador que consume conectores; no habla con ningun sistema",
    "service_scan": "orquesta conectores para medir servicio; no habla con un sistema",
    "shopify_api_version": "una constante de version de API",
    "shopify_connect": "flujo de credenciales de Shopify, sin lado de datos",
    "shopify_service": "lee servicio DESDE el conector de Shopify, no del sistema",
    "odoo_connect": "flujo de credenciales de Odoo, sin lado de datos",
    "browser": (
        "driver de navegador del agente de setup (computer-use con allowlist de "
        "dominios): actua en la PANTALLA del cliente durante el onboarding, no en un "
        "sistema por API, y no pasa por el contrato de writeback. Sus guardas son otras "
        "(scope.DomainAllowlist, redact.redact_snapshot) y viven en su propio paquete"
    ),
}


def _modules():
    for info in pkgutil.iter_modules(connectors_pkg.__path__):
        if info.name in NOT_CONNECTORS:
            continue
        yield info.name, importlib.import_module(f"src.connectors.{info.name}")


def _methods(mod) -> set[str]:
    names: set[str] = set()
    for _n, cls in inspect.getmembers(mod, inspect.isclass):
        if cls.__module__ != mod.__name__:
            continue
        names |= {m for m in dir(cls) if not m.startswith("__")}
    return names


MODULES = dict(_modules())


def test_hay_conectores_que_revisar():
    assert len(MODULES) >= 16, sorted(MODULES)


def test_cada_exclusion_dice_por_que():
    """Excluir un modulo es una decision, no un olvido: sin motivo no entra."""
    assert all(motivo.strip() for motivo in NOT_CONNECTORS.values())
    paquete = {i.name for i in pkgutil.iter_modules(connectors_pkg.__path__)}
    huerfanas = set(NOT_CONNECTORS) - paquete - {"__init__"}
    assert not huerfanas, f"NOT_CONNECTORS nombra modulos que ya no existen: {sorted(huerfanas)}"


@pytest.mark.parametrize("name", sorted(MODULES))
def test_todo_conector_declara_que_escribe(name):
    mod = MODULES[name]
    escribe = bool(_methods(mod) & set(WRITE_METHODS))
    ws = getattr(mod, "WRITE_SURFACE", None)
    assert isinstance(ws, WriteSurface), (
        f"src/connectors/{name}.py es un conector y no exporta WRITE_SURFACE. O lo declara "
        f"(solo lectura con su motivo, o con aprobacion nombrando lo que escribe), o entra a "
        f"NOT_CONNECTORS de este archivo con el motivo por el que no habla con un sistema."
    )
    if escribe:
        assert ws.mode == WRITE_APPROVAL_GATED, f"{name} tiene metodos de escritura y se declara {ws.mode}"
        assert ws.objects
    else:
        assert ws.mode == WRITE_NONE, f"{name} no tiene metodos de escritura y se declara {ws.mode}"


def test_la_declaracion_rechaza_las_medias_tintas():
    with pytest.raises(ValueError):
        WriteSurface(WRITE_APPROVAL_GATED, objects=(), means="escribe algo")
    with pytest.raises(ValueError):
        WriteSurface(WRITE_NONE, objects=("x",), means="no escribe")
    with pytest.raises(ValueError):
        WriteSurface(WRITE_NONE, means="   ")
    with pytest.raises(ValueError):
        WriteSurface("write_only", means="sin compuerta")


def test_lo_que_dice_el_odoo_es_lo_que_hace():
    from src.connectors.odoo import WRITE_SURFACE

    assert WRITE_SURFACE.writes
    assert any("orderpoint" in o for o in WRITE_SURFACE.objects)
    assert any("purchase.order" in o for o in WRITE_SURFACE.objects)
    assert "borrador" in WRITE_SURFACE.means.lower()


def test_el_documento_publico_es_lo_que_el_codigo_declara():
    from scripts.connected_systems import OUT, render

    assert OUT.exists(), "falta documentation/CONNECTED_SYSTEMS.md: correr python scripts/connected_systems.py"
    assert OUT.read_text(encoding="utf-8") == render(), (
        "documentation/CONNECTED_SYSTEMS.md no coincide con lo que declaran los conectores: "
        "correr python scripts/connected_systems.py y commitear el resultado"
    )


def test_el_documento_nombra_cada_sistema_y_su_compuerta():
    from scripts.connected_systems import MODULES as PUBLISHED
    from scripts.connected_systems import render

    text = render()
    assert {m for m, _t in PUBLISHED} <= set(MODULES)
    assert "| Odoo (ERP) |" in text and "con aprobacion" in text
    assert "| Unleashed (ERP de inventario) |" in text and "solo lectura" in text
