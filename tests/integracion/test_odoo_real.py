"""Los tests que un simulador no puede sustituir, contra un Odoo de verdad.

**Que cubren estos y no cubren los 81 de la suite normal.** Los 81 prueban el
*contrato de llamadas*: que el conector pida el modelo correcto, con el metodo
correcto, y que distinga un ``xmlrpc.client.Fault`` (Odoo proceso y rechazo: no
reintentar) de un error de red (reintentar). Todos corren contra un proxy
falso, asi que ninguno puede detectar:

- que un campo se llame distinto en Odoo 17 (``default_code``, ``standard_price``,
  ``seller_ids``...). Un rename entre versiones pasa los 81 y falla en el cliente.
- que el usuario de API no tenga permiso sobre ``purchase.order``.
- que Odoo **rechace** un ``create`` bien formado por una regla de negocio.
- que reintentar con la misma clave de idempotencia duplique la orden.

Ese ultimo es el que importa de verdad: el fallo no es un numero peor, es una
orden de compra duplicada en el ERP de un cliente.

**Como correrlos**

    docker compose -f docker-compose.odoo.yml up -d
    uv run --no-sync pytest -m integracion
    docker compose -f docker-compose.odoo.yml down -v

Estan fuera de la suite por defecto (``addopts = "-m 'not integracion'"``) para
que no haga falta infraestructura para trabajar. Eso NO los hace opcionales: la
publicacion del addon en apps.odoo.com los exige verdes, y el checklist de
``odoo_addon/PUBLICACION.md`` lo dice.

> **Estos tests no se han ejecutado nunca todavia.** Se escribieron con el
> daemon de Docker parado, asi que estan verificados como codigo pero no contra
> un servidor. La primera corrida es parte del trabajo, no una formalidad:
> espera que alguno falle, porque su razon de existir es justo lo que el
> simulador no podia decir.
"""

from __future__ import annotations

import os
import uuid

import pytest

from src.connectors.odoo import OdooClient, OdooConnector, OdooError

pytestmark = pytest.mark.integracion

URL = os.environ.get("ODOO_URL", "http://localhost:8069")
DB = os.environ.get("ODOO_DB", "kern_test")
USER = os.environ.get("ODOO_USERNAME", "admin")
KEY = os.environ.get("ODOO_API_KEY", "admin")


@pytest.fixture(scope="module")
def cliente() -> OdooClient:
    """Conexion real. Si no hay servidor, falla en vez de saltarse.

    Un ``skipif`` aqui convertiria "no levante el Odoo" en "todo verde", que es
    exactamente como se pierde una verificacion. Quien corre ``-m integracion``
    esta pidiendo explicitamente hablar con un servidor.
    """
    try:
        return OdooClient(URL, DB, USER, KEY)
    except OdooError as exc:
        pytest.fail(
            f"no hay Odoo en {URL} (db={DB}): {exc}. "
            "Levantalo con: docker compose -f docker-compose.odoo.yml up -d"
        )


@pytest.fixture(scope="module")
def conector(cliente) -> OdooConnector:
    return OdooConnector(cliente)


# --------------------------------------------------------------------------
# Lectura: los nombres de campo existen de verdad en Odoo 17
# --------------------------------------------------------------------------


def test_autentica_y_devuelve_un_uid(cliente):
    assert isinstance(cliente.uid, int) and cliente.uid > 0


@pytest.mark.parametrize("modelo,campos", [
    ("product.product", ["default_code", "standard_price", "seller_ids"]),
    ("purchase.order", ["partner_id", "state", "date_order", "order_line"]),
    ("purchase.order.line", ["product_id", "product_qty", "price_unit"]),
    ("stock.quant", ["product_id", "location_id", "quantity"]),
    ("stock.location", ["usage"]),
    ("sale.order.line", ["product_id", "product_uom_qty", "order_id"]),
])
def test_los_campos_que_el_conector_lee_existen_en_este_odoo(cliente, modelo, campos):
    """El fallo que ningun mock puede dar: el campo se llama distinto.

    ``fields_get`` es la fuente de verdad del propio servidor, asi que esto
    detecta un rename de version sin depender de que haya datos cargados.
    """
    disponibles = cliente.execute_kw(modelo, "fields_get", [], {"attributes": ["type"]})
    faltantes = [c for c in campos if c not in disponibles]
    assert not faltantes, f"{modelo} no tiene {faltantes} en este Odoo"


def test_leer_stock_no_revienta_con_la_base_vacia(conector):
    """Una base recien creada no tiene stock. Devolver vacio es correcto;
    lanzar no lo es -- y el cliente que instale esto el primer dia esta en
    exactamente ese estado."""
    posiciones = conector.stock_on_hand()
    assert isinstance(posiciones, dict)


# --------------------------------------------------------------------------
# Escritura: la frontera de seguridad y la idempotencia
# --------------------------------------------------------------------------


@pytest.fixture
def proveedor(cliente) -> int:
    ids = cliente.execute_kw("res.partner", "search",
                             [[["supplier_rank", ">", 0]]], {"limit": 1})
    if not ids:
        ids = [cliente.execute_kw("res.partner", "create",
                                  [{"name": "Proveedor de prueba", "supplier_rank": 1}])]
    return ids[0]


@pytest.fixture
def producto(cliente) -> int:
    sku = f"KERN-TEST-{uuid.uuid4().hex[:8]}"
    return cliente.execute_kw("product.product", "create",
                              [{"name": sku, "default_code": sku,
                                "standard_price": 10.0, "purchase_ok": True}])


def test_la_orden_creada_queda_en_draft_y_no_confirmada(conector, cliente,
                                                        proveedor, producto):
    """El borrador sin confirmar ES la frontera de seguridad del diseno.

    Si Odoo la creara ya confirmada, Kern estaria comprometiendo dinero del
    cliente sin que nadie lo revise. Nada en el codigo de Kern lo impide: lo
    impide el estado en que Odoo la deja, y eso solo se puede comprobar aqui.
    """
    resultado = conector.create_draft_purchase_orders(
        {producto: 5.0}, supplier_id=proveedor,
        idempotency_key=f"test-{uuid.uuid4().hex}",
    )
    ids = getattr(resultado, "order_ids", None) or getattr(resultado, "ids", None)
    assert ids, f"el conector no devolvio ids de orden: {resultado!r}"
    estados = cliente.execute_kw("purchase.order", "read", [list(ids)], {"fields": ["state"]})
    assert all(o["state"] == "draft" for o in estados), estados


def test_reintentar_con_la_misma_clave_no_duplica_la_orden(conector, cliente,
                                                           proveedor, producto):
    """El fallo mas caro de todos, y el unico que la suite normal no puede ver.

    Una red que corta despues de que Odoo creo la orden pero antes de que Kern
    lea la respuesta produce exactamente este reintento. Si duplica, el cliente
    compra dos veces.
    """
    clave = f"test-idem-{uuid.uuid4().hex}"
    antes = cliente.execute_kw("purchase.order", "search_count",
                               [[["partner_id", "=", proveedor]]])
    conector.create_draft_purchase_orders({producto: 5.0}, supplier_id=proveedor,
                                          idempotency_key=clave)
    intermedio = cliente.execute_kw("purchase.order", "search_count",
                                    [[["partner_id", "=", proveedor]]])
    conector.create_draft_purchase_orders({producto: 5.0}, supplier_id=proveedor,
                                          idempotency_key=clave)
    despues = cliente.execute_kw("purchase.order", "search_count",
                                 [[["partner_id", "=", proveedor]]])

    assert intermedio == antes + 1, "la primera llamada no creo exactamente una orden"
    assert despues == intermedio, (
        f"el reintento con la misma clave creo otra orden: {intermedio} -> {despues}"
    )


def test_un_producto_inexistente_falla_fuerte_y_no_crea_nada(conector, cliente, proveedor):
    """Fallar a medias -- crear la cabecera y no las lineas -- deja al cliente
    con una OC vacia que parece real."""
    antes = cliente.execute_kw("purchase.order", "search_count",
                               [[["partner_id", "=", proveedor]]])
    with pytest.raises(Exception):
        conector.create_draft_purchase_orders(
            {999_999_999: 5.0}, supplier_id=proveedor,
            idempotency_key=f"test-{uuid.uuid4().hex}",
        )
    despues = cliente.execute_kw("purchase.order", "search_count",
                                 [[["partner_id", "=", proveedor]]])
    assert despues == antes, "quedo una orden a medias"


# --------------------------------------------------------------------------
# El addon
# --------------------------------------------------------------------------


def test_el_addon_esta_instalado_o_es_instalable(cliente):
    """Comprueba lo que la revision de apps.odoo.com comprueba primero: que el
    modulo se pueda instalar en un Odoo limpio."""
    filas = cliente.execute_kw(
        "ir.module.module", "search_read",
        [[["name", "=", "linchpin_dry_run"]]], {"fields": ["state", "latest_version"]},
    )
    assert filas, ("linchpin_dry_run no aparece en la lista de modulos: revisa que "
                   "docker-compose.odoo.yml monte odoo_addon/ en --addons-path y que "
                   "hayas actualizado la lista de aplicaciones")
    assert filas[0]["state"] in ("installed", "uninstalled", "to install"), filas[0]
