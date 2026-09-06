"""Logica de protocolo sin ninguna dependencia de Odoo.

Este subpaquete existe para que el cliente del servicio se pueda probar fuera
de un runtime de Odoo (``tests/test_odoo_addon_mcp_client.py``). Que sea un
paquete de verdad -- con este ``__init__.py`` -- es lo que permite importarlo
con ``from ..lib.mcp_client import ...`` desde el wizard, en vez de insertar
su carpeta en ``sys.path``.

**Por que importa que sea un import relativo.** ``sys.path.insert(0, lib_dir)``
mete un modulo llamado ``mcp_client`` al principio de la ruta de busqueda
GLOBAL del proceso. En una instancia de Odoo con varios addons instalados eso
significa que cualquier otro modulo llamado igual queda ensombrecido por este,
y al reves si otro addon hace lo mismo primero. Ademas es de las primeras
cosas que marca una revision de apps.odoo.com.
"""
