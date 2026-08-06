"""El addon se importa como paquete, sin tocar ``sys.path``.

**Por que este fichero existe.** El wizard importaba su libreria insertando
``lib/`` al principio de ``sys.path`` global. Eso significa que, en una
instancia de Odoo con varios addons, un modulo llamado ``mcp_client`` de
cualquier otro origen queda ensombrecido por este -- o al reves. Ademas es de
las primeras cosas que marca una revision de apps.odoo.com.

El arreglo es un import relativo (``from ..lib.mcp_client import ...``), que
funciona porque Odoo carga cada addon como el paquete ``odoo.addons.<modulo>``.
Pero eso es un razonamiento, no una medicion, y este repo no acepta cambiar un
import que funciona basandose en como *deberia* comportarse el cargador.

Aqui se mide: se simula el modulo ``odoo`` con la superficie minima que el
addon usa, se importa el paquete entero, y se comprueba que resuelve **y que
no dejo nada en sys.path**. No sustituye a instalarlo en un Odoo real -- eso
lo hace ``tests/integracion/test_odoo_real.py``, que necesita un servidor --
pero cierra la unica pregunta que el cambio de import abre.
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

RAIZ_ADDONS = Path(__file__).resolve().parents[1] / "odoo_addon"
MODULO = "linchpin_dry_run"


def _odoo_simulado() -> dict[str, types.ModuleType]:
    """La superficie de Odoo que el addon toca, y nada mas.

    Deliberadamente escueto: si el addon empieza a usar una parte de Odoo que
    no esta aqui, este test falla con un ``AttributeError`` que nombra
    exactamente que se anadio. Un simulacro permisivo (``MagicMock`` de todo)
    haria pasar el test sin comprobar nada.
    """
    odoo = types.ModuleType("odoo")

    class _Descriptor:
        """Sustituto de un campo de Odoo: acepta cualquier kwarg y no hace nada."""

        def __init__(self, *args, **kwargs):
            self.args, self.kwargs = args, kwargs

    class _Date(_Descriptor):
        @staticmethod
        def today():
            import datetime
            return datetime.date.today()

        @staticmethod
        def to_string(value):
            return str(value)

    fields = types.ModuleType("odoo.fields")
    for nombre in ("Char", "Text", "Integer", "Float", "Boolean", "Selection",
                   "Many2one", "One2many", "Many2many", "Datetime", "Binary"):
        setattr(fields, nombre, _Descriptor)
    fields.Date = _Date

    class _TransientModel:
        pass

    modelos = types.ModuleType("odoo.models")
    modelos.TransientModel = _TransientModel
    modelos.Model = type("Model", (), {})
    modelos.AbstractModel = type("AbstractModel", (), {})

    excepciones = types.ModuleType("odoo.exceptions")
    excepciones.UserError = type("UserError", (Exception,), {})

    api = types.ModuleType("odoo.api")
    api.model = staticmethod
    api.depends = lambda *a, **k: (lambda f: f)

    odoo.fields = fields
    odoo.models = modelos
    odoo.exceptions = excepciones
    odoo.api = api
    odoo._ = lambda s, *a, **k: s

    return {"odoo": odoo, "odoo.fields": fields, "odoo.models": modelos,
            "odoo.exceptions": excepciones, "odoo.api": api}


@pytest.fixture
def addon_importado():
    """Importa el addon con Odoo simulado y deja sys.modules/sys.path como estaba."""
    modulos_previos = dict(sys.modules)
    path_previo = list(sys.path)
    sys.modules.update(_odoo_simulado())
    sys.path.insert(0, str(RAIZ_ADDONS))
    try:
        import importlib
        for nombre in [m for m in list(sys.modules) if m.startswith(MODULO)]:
            del sys.modules[nombre]
        yield importlib.import_module(MODULO), path_previo
    finally:
        sys.modules.clear()
        sys.modules.update(modulos_previos)
        sys.path[:] = path_previo


def test_el_addon_se_importa_entero(addon_importado):
    """Recorre __init__ -> models -> wizards -> lib. Si un import relativo no
    resolviera, esto levanta ImportError."""
    modulo, _ = addon_importado
    assert modulo.__name__ == MODULO
    assert f"{MODULO}.wizards.linchpin_analysis_wizard" in sys.modules
    assert f"{MODULO}.lib.mcp_client" in sys.modules
    assert f"{MODULO}.models.res_config_settings" in sys.modules


def test_importar_el_addon_no_deja_nada_en_sys_path(addon_importado):
    """La regresion concreta: la version anterior insertaba lib/ en sys.path
    global y no lo quitaba nunca."""
    _, path_previo = addon_importado
    # El unico anadido permitido es la raiz de addons que pone este propio test.
    anadidos = [p for p in sys.path if p not in path_previo]
    assert anadidos == [str(RAIZ_ADDONS)], f"sys.path contaminado: {anadidos}"


def test_la_libreria_no_queda_como_modulo_de_nivel_superior(addon_importado):
    """``mcp_client`` a secas en sys.modules es lo que ensombrece a otros addons."""
    assert "mcp_client" not in sys.modules


def test_la_libreria_no_importa_nada_de_odoo():
    """El invariante que justifica que lib/ sea un subpaquete separado.

    Se lee el fuente en vez de importarlo: importarlo con el simulacro puesto
    no probaria nada, porque el simulacro haria que un ``import odoo`` colara.
    """
    fuente = (RAIZ_ADDONS / MODULO / "lib" / "mcp_client.py").read_text(encoding="utf-8")
    lineas = [ln.strip() for ln in fuente.splitlines()]
    ofensivas = [ln for ln in lineas
                 if ln.startswith(("import odoo", "from odoo"))]
    assert not ofensivas, f"lib/ importa Odoo: {ofensivas}"


def test_lib_es_un_paquete_de_verdad():
    """Sin __init__.py el import relativo del wizard no resuelve, y la unica
    salida vuelve a ser el hack de sys.path."""
    assert (RAIZ_ADDONS / MODULO / "lib" / "__init__.py").is_file()


def test_ningun_fichero_del_addon_manipula_sys_path():
    """Cubre los ficheros que este test no importa hoy y los que se anadan.

    Camina el AST en vez de buscar el texto: la primera version buscaba la
    cadena ``sys.path`` y se disparo con los propios comentarios que explican
    por que ya no se usa. Un guardarrail que no distingue codigo de prosa
    obliga a redactar los comentarios para no despertarlo, que es exactamente
    al reves de lo que hace falta.
    """
    import ast

    ofensivos = []
    for py in (RAIZ_ADDONS / MODULO).rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        arbol = ast.parse(py.read_text(encoding="utf-8"), filename=str(py))
        for nodo in ast.walk(arbol):
            if (isinstance(nodo, ast.Attribute) and nodo.attr == "path"
                    and isinstance(nodo.value, ast.Name) and nodo.value.id == "sys"):
                ofensivos.append(f"{py.relative_to(RAIZ_ADDONS)}:{nodo.lineno}")
    assert not ofensivos, f"manipulan sys.path: {ofensivos}"
