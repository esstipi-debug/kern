"""Olist como ``InventorySource``: el unico dato de este repo con fecha de LLEGADA.

Olist es el dataset publico de un marketplace brasileno real -- 99.441 ordenes con las
tres fechas que importan: la prometida al comprador, la de despacho al transportista, y la
de ENTREGA al cliente. Esa ultima es la que ningun ERP del canal AU/NZ registra: ni
Unleashed, ni Cin7 Core, ni Odoo. Los tres pueden decir "salio del deposito" y ninguno
puede decir "llego".

Por eso este lector existe y por eso vive en ``connectors/``: no es un backend de cliente,
es la unica fuente con la que se puede MEDIR cuanto se equivoca un OTIF de despacho contra
uno de llegada, en vez de afirmarlo.

Los tests corren sobre CSVs chiquitos escritos en ``tmp_path``. ``data/`` esta gitignoreado
y CI no lo tiene, asi que un test que dependa del dataset real seria verde en esta maquina
y rojo en todas las demas.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from src.connectors import InventorySource, realized_service
from src.connectors.olist import OlistSource

_ORDERS_HEADER = (
    '"order_id","customer_id","order_status","order_purchase_timestamp",'
    '"order_approved_at","order_delivered_carrier_date",'
    '"order_delivered_customer_date","order_estimated_delivery_date"'
)
_ITEMS_HEADER = (
    '"order_id","order_item_id","product_id","seller_id","shipping_limit_date",'
    '"price","freight_value"'
)


def _dataset(tmp: Path, orders: list[str], items: list[str]) -> tuple[Path, Path]:
    o = tmp / "orders.csv"
    i = tmp / "items.csv"
    o.write_text("\n".join([_ORDERS_HEADER, *orders]) + "\n", encoding="utf-8")
    i.write_text("\n".join([_ITEMS_HEADER, *items]) + "\n", encoding="utf-8")
    return o, i


#: Cuatro ordenes que cubren los cuatro destinos posibles de una linea:
#: entregada tarde, entregada a tiempo, cancelada (cero MEDIDO), y despachada
#: pero todavia sin llegar (nadie lo midio).
_ORDERS = [
    'o1,c1,delivered,2017-10-02 10:56:33,2017-10-02 11:07:15,'
    '2017-10-04 19:55:00,2017-10-20 21:25:13,2017-10-18 00:00:00',
    'o2,c2,delivered,2017-10-03 10:00:00,2017-10-03 11:00:00,'
    '2017-10-05 10:00:00,2017-10-10 10:00:00,2017-10-18 00:00:00',
    'o3,c3,canceled,2017-10-04 10:00:00,,,,2017-10-18 00:00:00',
    'o4,c4,shipped,2017-10-05 10:00:00,2017-10-05 11:00:00,'
    '2017-10-07 10:00:00,,2017-10-30 00:00:00',
]
_ITEMS = [
    '"o1",1,"P-1","s1",2017-09-19 09:45:35,58.90,13.29',
    '"o2",1,"P-1","s1",2017-09-19 09:45:35,58.90,13.29',
    '"o3",1,"P-1","s1",2017-09-19 09:45:35,58.90,13.29',
    '"o4",1,"P-2","s1",2017-09-19 09:45:35,20.00,5.00',
]


def _source(tmp: Path, **kw) -> OlistSource:
    o, i = _dataset(tmp, _ORDERS, _ITEMS)
    return OlistSource(o, i, **kw)


def test_satisface_el_protocolo_inventory_source(tmp_path: Path):
    assert isinstance(_source(tmp_path), InventorySource)


def test_no_hay_stock_y_se_dice_vacio_en_vez_de_inventarlo(tmp_path: Path):
    """Olist publica ordenes, no inventario. Devolver una lista vacia es la respuesta
    correcta; fabricar niveles a partir de lo vendido seria inventar un almacen."""
    assert _source(tmp_path).inventory_levels() == []


def test_cada_item_es_una_linea_con_su_precio(tmp_path: Path):
    pedidos = {o.order_id: o for o in _source(tmp_path).orders()}
    linea = pedidos["o1"].lines[0]
    assert linea.sku == "P-1"
    assert linea.quantity == 1.0
    assert linea.price == pytest.approx(58.90)


def test_la_fecha_de_entrega_por_defecto_es_la_LLEGADA_al_cliente(tmp_path: Path):
    """El dato que hace valer a este lector. ``order_delivered_customer_date`` es cuando
    el pedido llego a la puerta del comprador, no cuando salio del deposito."""
    pedidos = {o.order_id: o for o in _source(tmp_path).orders()}
    assert pedidos["o1"].lines[0].delivered_date == "2017-10-20"  # llegada
    assert pedidos["o1"].lines[0].promised_date == "2017-10-18"


def test_en_modo_transportista_se_ve_lo_MISMO_que_veria_un_erp_del_canal(tmp_path: Path):
    """El modo que existe para medir la brecha, no para usarse en produccion.

    Unleashed, Cin7 y Odoo solo registran la salida. Poder releer las MISMAS ordenes con
    la fecha de despacho es lo unico que permite cuantificar cuanto se equivoca un OTIF de
    deposito, en vez de afirmar que se equivoca.
    """
    pedidos = {o.order_id: o for o in _source(tmp_path, delivery="carrier").orders()}
    assert pedidos["o1"].lines[0].delivered_date == "2017-10-04"  # despacho, 16 dias antes


def test_una_orden_cancelada_es_un_cero_MEDIDO(tmp_path: Path):
    """``canceled`` es terminal: esa orden ya no se va a servir. Es el mismo caso que el
    estado SHIPPED de Cin7 -- evidencia de que el resultado se cerro sin entregar."""
    pedidos = {o.order_id: o for o in _source(tmp_path).orders()}
    assert pedidos["o3"].lines[0].delivered_qty == 0.0


def test_una_orden_todavia_en_transito_no_se_mide(tmp_path: Path):
    """``shipped`` salio y no llego. No es un cero: puede llegar manana. Un 0.0 aca
    fabricaria un quiebre sobre una orden en curso."""
    pedidos = {o.order_id: o for o in _source(tmp_path).orders()}
    assert pedidos["o4"].lines[0].delivered_qty is None
    assert pedidos["o4"].lines[0].delivered_date is None


def test_since_filtra_por_fecha_de_compra(tmp_path: Path):
    pedidos = _source(tmp_path).orders(since="2017-10-04")
    assert sorted(o.order_id for o in pedidos) == ["o3", "o4"]


def test_la_cadena_entera_mide_el_servicio_real(tmp_path: Path):
    """P-1 tiene tres ordenes: una llego tarde, una a tiempo, una cancelada.

    Fill rate 2 de 3 (la cancelada es un cero medido). OTIF 1 de 2: solo las dos entregadas
    tienen las dos fechas, y una llego dos dias tarde. La cancelada no puntua OTIF porque
    nunca hubo despacho y su promesa -- el 18 de octubre -- vencio DENTRO de la ventana.
    """
    registros = {r.product_id: r for r in realized_service(_source(tmp_path).orders())}
    p1 = registros["P-1"]
    assert p1.fill_rate == pytest.approx(2 / 3)
    assert p1.provenance == "observed"
    assert p1.otif == pytest.approx(1 / 3)


def test_el_mismo_dato_da_un_otif_MEJOR_medido_por_despacho(tmp_path: Path):
    """La comprobacion que sostiene toda la pieza de venta, sobre una fixture minima.

    Los mismos pedidos, la misma promesa. Medido por llegada, o1 fallo (llego el 20 contra
    una promesa del 18). Medido por despacho, o1 "cumplio" (salio el 4). El numero mejora
    sin que nadie sirviera mejor: es el ERP que no puede ver el ultimo tramo.

    ``as_of`` FIJO en las dos lecturas, y esto no es un detalle: sin el, cada una deriva su
    propia ventana de la ultima fecha que ve, y la de despacho termina 15 dias antes. Con
    ventanas distintas, la orden cancelada queda "en curso" en una y "vencida" en la otra,
    o sea que se estarian comparando dos cohortes distintas y parte de la brecha seria un
    artefacto de la medicion. La primera version de este test cayo justo ahi.
    """
    cierre = "2017-10-31"
    por_llegada = {
        r.product_id: r for r in realized_service(_source(tmp_path).orders(), as_of=cierre)
    }
    por_despacho = {
        r.product_id: r
        for r in realized_service(_source(tmp_path, delivery="carrier").orders(), as_of=cierre)
    }

    # Mismo denominador, misma cohorte: las 3 ordenes de P-1 en las dos lecturas.
    assert por_llegada["P-1"].lines_scored == por_despacho["P-1"].lines_scored == 3
    assert por_llegada["P-1"].fill_rate == por_despacho["P-1"].fill_rate

    # Y la unica diferencia es el tramo que el ERP no ve.
    assert por_llegada["P-1"].otif == pytest.approx(1 / 3)
    assert por_despacho["P-1"].otif == pytest.approx(2 / 3)
