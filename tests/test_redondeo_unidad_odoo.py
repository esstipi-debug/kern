"""F4 de la prueba del recorrido del 2026-09-19: Kern escribia decimales en Odoo.

187,6 rollos, 1,4 escaleras y 2,8 extintores en ordenes de compra borrador, y un punto
de reorden de 227,6. El plan redondeaba a UN decimal (``src/connectors/replenish.py``) y
el conector solo aplicaba el minimo del proveedor.

La unidad de Odoo sola no lo arregla: "Units" viene de fabrica con ``rounding=0.01``
(default del campo en uom/models/uom_uom.py de Odoo 17), asi que para Odoo 187,6 es una
cantidad legal. La regla: una unidad de la categoria "Unit" no se parte (paso 1); el
resto va a la precision de su unidad. Siempre hacia arriba, y lo que pidio el plan
queda visible al lado. Un producto que se compra en otra unidad (docena, caja) va a la
OC en esa unidad, que es en la que Odoo lee el minimo y el precio del proveedor.
"""

from __future__ import annotations

import pytest

from src.constraints import purchasable_quantity

# -- la regla: src/constraints.py ----------------------------------------------


def test_una_pieza_sube_al_entero_y_conserva_lo_que_pidio_el_plan():
    lote = purchasable_quantity(187.6, unit_step=1.0)

    assert lote.quantity == 188.0
    assert lote.asked == 187.6
    assert lote.adjusted is True
    assert lote.rule == "multiple 1"


def test_el_ruido_de_coma_flotante_no_sube_un_paso_entero():
    """``round_up_to_multiple(0.1 * 3, 0.1)`` da 0.4: el cociente sale
    3.0000000000000004 y ``ceil`` lo sube. La regla mira primero si ya es legal."""
    assert purchasable_quantity(0.1 * 3, unit_step=0.1).quantity == 0.3
    assert purchasable_quantity(187.6, unit_step=0.01).quantity == 187.6
    assert purchasable_quantity(2.3456, unit_step=0.001).quantity == 2.346


def test_el_minimo_del_proveedor_manda_y_queda_sobre_el_paso_de_la_unidad():
    assert purchasable_quantity(12.8, unit_step=1.0, minimum_order_quantity=50.0).quantity == 50.0
    lote = purchasable_quantity(12.8, unit_step=1.0, minimum_order_quantity=49.5)

    assert lote.quantity == 50.0
    assert lote.rule == "moq 49.5, multiple 1"


def test_nada_que_comprar_no_se_sube_al_minimo():
    lote = purchasable_quantity(0.0, unit_step=1.0, minimum_order_quantity=50.0)

    assert lote.quantity == 0.0
    assert lote.adjusted is False


def test_sin_paso_ni_minimo_la_cantidad_pasa_intacta():
    lote = purchasable_quantity(187.6)

    assert lote.quantity == 187.6
    assert lote.adjusted is False
    assert lote.rule == ""


def test_una_cantidad_negativa_es_un_error_no_un_lote():
    with pytest.raises(ValueError):
        purchasable_quantity(-1.0, unit_step=1.0)
