"""Lo que comparten los tests que hablan con un Odoo real: la conexion.

Las credenciales salen de las mismas variables que lee el conector
(``ODOO_URL``, ``ODOO_DB``, ``ODOO_USERNAME``, ``ODOO_API_KEY``) y, sin ellas,
apuntan al laboratorio que levanta ``scripts/odoo_test_setup.py``. Como correr
todo esta en el docstring de ``test_recorrido_cliente.py``.
"""

from __future__ import annotations

import os
import xmlrpc.client

import pytest

from src.connectors.odoo import OdooClient, OdooError


@pytest.fixture(scope="session")
def credenciales_odoo() -> dict[str, str]:
    """Con los nombres de ``params`` que lee ``jobs/odoo_job.py::_make_rpc``."""
    return {
        "odoo_url": os.environ.get("ODOO_URL", "http://localhost:8069"),
        "odoo_db": os.environ.get("ODOO_DB", "kern_test"),
        "odoo_username": os.environ.get("ODOO_USERNAME", "admin"),
        "odoo_api_key": os.environ.get("ODOO_API_KEY", "admin"),
    }


@pytest.fixture(scope="session")
def cliente(credenciales_odoo) -> OdooClient:
    """Conexion real. Si no hay servidor, falla en vez de saltarse.

    Un ``skipif`` aqui convertiria "no levante el Odoo" en "todo verde", que es
    exactamente como se pierde una verificacion. Quien corre ``-m integracion``
    esta pidiendo explicitamente hablar con un servidor.

    Atrapa tambien ``OSError`` (puerto cerrado) y ``xmlrpc.client.Error`` (base
    inexistente): ``OdooClient`` solo convierte en ``OdooError`` la clave
    rechazada, y los otros dos llegaban como traceback crudo.
    """
    c = credenciales_odoo
    try:
        return OdooClient(c["odoo_url"], c["odoo_db"], c["odoo_username"], c["odoo_api_key"])
    except (OdooError, OSError, xmlrpc.client.Error) as exc:
        pytest.fail(
            f"no hay Odoo en {c['odoo_url']} (db={c['odoo_db']}): {exc}. "
            "Levantalo con: python scripts/odoo_test_setup.py"
        )
