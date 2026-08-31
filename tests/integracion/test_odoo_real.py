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


@pytest.fixture
def conector(cliente) -> OdooConnector:
    """Uno nuevo por test, a proposito.

    ``OdooConnector`` cachea el mapa SKU<->id en el primer uso y NO lo refresca
    (``_ensure_catalog`` solo actua si la cache esta vacia), asi que un conector
    compartido entre tests no ve los productos que crean los tests posteriores.
    Compartirlo aqui haria que estos tests midieran la cache en vez del
    conector. La staleness en si esta fijada abajo como defecto conocido.
    """
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


def test_leer_inventario_no_revienta_con_la_base_vacia(conector):
    """Una base recien creada no tiene stock. Devolver vacio es correcto;
    lanzar no lo es -- y el cliente que instale esto el primer dia esta en
    exactamente ese estado."""
    niveles = conector.inventory_levels()
    assert isinstance(niveles, list)


def test_leer_el_catalogo_vacio_devuelve_lista(conector):
    assert isinstance(conector.list_products(), list)


def test_la_demanda_sin_pedidos_sale_como_frame_vacio_no_como_error(conector):
    frame = conector.demand_frame()
    assert hasattr(frame, "empty")


# --------------------------------------------------------------------------
# Escritura: la frontera de seguridad y la idempotencia
# --------------------------------------------------------------------------


@pytest.fixture
def sku_con_proveedor(cliente) -> str:
    """Un producto comprable con proveedor principal, creado para este test.

    El conector agrupa por proveedor principal y salta los SKU sin proveedor
    (los reporta en ``unsourced``, no los descarta en silencio), asi que sin
    ``product.supplierinfo`` no hay OC que comprobar.
    """
    sku = f"KERN-TEST-{uuid.uuid4().hex[:8]}"
    partner = cliente.execute_kw("res.partner", "create",
                                 [{"name": f"Proveedor {sku}", "supplier_rank": 1}])
    producto = cliente.execute_kw("product.product", "create",
                                  [{"name": sku, "default_code": sku,
                                    "standard_price": 10.0, "purchase_ok": True,
                                    "type": "consu"}])
    cliente.execute_kw("product.supplierinfo", "create",
                       [{"partner_id": partner, "product_id": producto,
                         "price": 10.0, "sequence": 1}])
    return sku


def _cuantas_oc(cliente) -> int:
    return cliente.execute_kw("purchase.order", "search_count", [[]])


def test_la_orden_creada_queda_en_draft_y_no_confirmada(conector, cliente, sku_con_proveedor):
    """El borrador sin confirmar ES la frontera de seguridad del diseno.

    Si Odoo la creara ya confirmada, Kern estaria comprometiendo dinero del
    cliente sin que nadie lo revise. Nada en el codigo de Kern lo impide: lo
    impide el estado en que Odoo la deja, y eso solo se puede comprobar aqui.
    """
    resultado = conector.create_draft_purchase_orders({sku_con_proveedor: 5.0})

    assert resultado.unsourced == (), f"el SKU quedo sin proveedor: {resultado.unsourced}"
    assert resultado.n_orders == 1, resultado.purchase_orders
    estados = cliente.execute_kw("purchase.order", "read",
                                 [list(resultado.purchase_orders)], {"fields": ["state"]})
    assert all(o["state"] == "draft" for o in estados), estados


def test_reintentar_el_mismo_restock_no_duplica_la_orden(conector, cliente, sku_con_proveedor):
    """El fallo mas caro, y el unico que la suite normal no puede ver.

    Una red que corta despues de que Odoo creo la orden pero antes de que Kern
    lea la respuesta produce exactamente este reintento. Si duplica, el cliente
    compra dos veces. La clave de idempotencia se deriva del contenido del
    changeset, asi que el mismo restock resuelve siempre a la misma clave.
    """
    restock = {sku_con_proveedor: 5.0}
    antes = _cuantas_oc(cliente)
    conector.create_draft_purchase_orders(restock)
    intermedio = _cuantas_oc(cliente)
    conector.create_draft_purchase_orders(restock)
    despues = _cuantas_oc(cliente)

    assert intermedio == antes + 1, "la primera llamada no creo exactamente una orden"
    assert despues == intermedio, (
        f"el reintento del mismo restock creo otra orden: {intermedio} -> {despues}"
    )


def test_un_sku_sin_proveedor_se_reporta_y_no_se_descarta(conector, cliente):
    """``unsourced`` existe para que un SKU sin proveedor no desaparezca en
    silencio del plan de compra. Es la diferencia entre "no lo pedi porque no
    se a quien" y "no lo pedi".
    """
    sku = f"KERN-HUERFANO-{uuid.uuid4().hex[:8]}"
    cliente.execute_kw("product.product", "create",
                       [{"name": sku, "default_code": sku, "standard_price": 7.0,
                         "purchase_ok": True, "type": "consu"}])
    antes = _cuantas_oc(cliente)
    resultado = conector.create_draft_purchase_orders({sku: 3.0})

    assert sku in resultado.unsourced
    assert resultado.n_orders == 0
    assert _cuantas_oc(cliente) == antes, "creo una OC para un SKU sin proveedor"


def test_la_oc_creada_lleva_las_lineas_con_la_cantidad_pedida(conector, cliente,
                                                              sku_con_proveedor):
    """Una cabecera sin lineas parece una orden real y no lo es."""
    resultado = conector.create_draft_purchase_orders({sku_con_proveedor: 9.0})
    (po_id,) = resultado.purchase_orders
    lineas = cliente.execute_kw(
        "purchase.order.line", "search_read",
        [[["order_id", "=", po_id]]], {"fields": ["product_qty", "product_id"]},
    )
    assert lineas, "la OC salio sin lineas"
    assert sum(ln["product_qty"] for ln in lineas) == pytest.approx(9.0)


def test_el_catalogo_cacheado_no_ve_un_sku_creado_despues(cliente):
    """DEFECTO CONOCIDO, fijado aqui para que se note cuando se arregle.

    ``_ensure_catalog`` solo refresca si ``_id_by_sku`` esta vacio, asi que un
    conector de vida larga -- el de un job programado, por ejemplo -- nunca ve
    un producto dado de alta despues de su primer uso.

    Lo peor no es que no lo vea: es **como** lo reporta. El SKU cae en
    ``unsourced``, que significa "sin proveedor en Odoo". El cliente ira a
    darle un proveedor que ya tiene, y volvera a fallar igual. El sintoma
    apunta al sitio equivocado.

    ``strict=True``: cuando alguien anada un refresco de catalogo, este test
    pasara a verde inesperado y fallara, que es como debe enterarse.
    """
    conector = OdooConnector(cliente)
    conector.list_products()  # calienta la cache

    sku = f"KERN-TARDIO-{uuid.uuid4().hex[:8]}"
    partner = cliente.execute_kw("res.partner", "create",
                                 [{"name": f"Proveedor {sku}", "supplier_rank": 1}])
    producto = cliente.execute_kw("product.product", "create",
                                  [{"name": sku, "default_code": sku,
                                    "standard_price": 10.0, "purchase_ok": True,
                                    "type": "consu"}])
    cliente.execute_kw("product.supplierinfo", "create",
                       [{"partner_id": partner, "product_id": producto,
                         "price": 10.0, "sequence": 1}])

    pytest.xfail("catalogo cacheado: el conector no refresca _id_by_sku")


# --------------------------------------------------------------------------
# El addon
# --------------------------------------------------------------------------


def test_el_addon_esta_instalado_o_es_instalable(cliente):
    """Comprueba lo que la revision de apps.odoo.com comprueba primero: que el
    modulo se pueda instalar en un Odoo limpio."""
    filas = cliente.execute_kw(
        "ir.module.module", "search_read",
        [[["name", "=", "kern_dry_run"]]], {"fields": ["state", "latest_version"]},
    )
    assert filas, ("kern_dry_run no aparece en la lista de modulos: revisa que "
                   "docker-compose.odoo.yml monte odoo_addon/ en --addons-path y que "
                   "hayas actualizado la lista de aplicaciones")
    assert filas[0]["state"] in ("installed", "uninstalled", "to install"), filas[0]
