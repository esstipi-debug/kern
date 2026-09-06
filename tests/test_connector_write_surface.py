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
READ_METHODS = ("orders", "arrivals", "inventory_levels")
WRITE_METHODS = ("commit", "apply_restock", "stage_restock", "apply_draft_purchase_orders")
#: Modulos del paquete que no son un conector: protocolos, utilidades, el emulador HTTP.
NOT_CONNECTORS = {
    "__init__", "arrivals", "emulator", "ledger", "replenish", "service_scan",
    "shopify_api_version", "shopify_connect", "shopify_service", "odoo_connect",
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
    assert len(MODULES) >= 15, sorted(MODULES)


@pytest.mark.parametrize("name", sorted(MODULES))
def test_todo_conector_declara_que_escribe(name):
    mod = MODULES[name]
    methods = _methods(mod)
    lee = bool(methods & set(READ_METHODS))
    escribe = bool(methods & set(WRITE_METHODS))
    if not (lee or escribe):
        pytest.skip(f"{name} no lee ni escribe un sistema")
    ws = getattr(mod, "WRITE_SURFACE", None)
    assert isinstance(ws, WriteSurface), (
        f"src/connectors/{name}.py lee o escribe un sistema y no exporta WRITE_SURFACE"
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
