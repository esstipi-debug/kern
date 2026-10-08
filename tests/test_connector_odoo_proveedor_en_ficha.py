"""El proveedor cargado en la ficha del producto tambien es un proveedor.

En Odoo 17 el formulario de la ficha deja ``product.supplierinfo.product_id`` VACIO:
la lista de proveedores de la plantilla solo pasa ``default_product_tmpl_id``
(``purchase/views/product_views.xml``) y la columna de variante ni se muestra. Al
confirmar una OC, Odoo da de alta al proveedor tambien sin variante
(``purchase_order._prepare_supplier_info``). Odoo aplica esa fila a todas las
variantes de la plantilla (``product_supplierinfo._get_filtered_supplier``:
``not s.product_id or s.product_id == product_id``, y solo si
``s.partner_id.active``) y recorre los proveedores en el orden
``(sequence, -min_qty, price, id)`` (``product_product._prepare_sellers``).

El conector leia solo por ``product_id``. En el laboratorio del 2026-09-19 (20
productos cargados desde la ficha) eso dio 0 plazos declarados, 0 ordenes de compra
y 9 SKU reportados "sin proveedor en Odoo" que el cliente desmiente abriendo el
producto. El aviso de plazos ausentes tampoco salia: una fila saltada no contaba como
"SKU con linea de proveedor", asi que la pantalla mostraba n/a sin explicar nada.

Estos tests siembran el stand-in como lo deja el formulario -- ``product_id`` en
``False``, que es como XML-RPC devuelve un many2one vacio -- y fijan la regla de
Odoo, no una aproximacion: una fila de variante NO le gana a una de plantilla por
serlo, compite con la misma clave; y un proveedor archivado no compite.
"""
from __future__ import annotations

from src.connectors.odoo import _DECLARED_DELAY_FLOOR, InMemoryOdoo, OdooConnector

#: La plantilla de los productos. Su id (11) es distinto del de la variante (1) a
#: proposito: un conector que confunda los dos ids pasaria un test donde coinciden.
_PLANTILLA = (11, "Rodamiento")


def _odoo(proveedores: dict[int, dict], *, variantes: int = 1,
          socios: dict[int, dict] | None = None) -> InMemoryOdoo:
    """``variantes`` productos (SKU-1, SKU-2, ...) de la misma plantilla 11.

    ``socios`` son los ``res.partner`` que el test necesita describir. Uno que no
    esta sembrado cuenta como activo, que es el default de Odoo (``active=True``).
    """
    return InMemoryOdoo(
        {
            "product.product": {
                pid: {"default_code": f"SKU-{pid}", "name": "Rodamiento", "list_price": 20.0,
                      "standard_price": 12.0, "product_tmpl_id": list(_PLANTILLA)}
                for pid in range(1, variantes + 1)
            },
            "product.supplierinfo": proveedores,
            "res.partner": socios or {},
            "purchase.order": {},
        }
    )


def _de_la_ficha(partner: int, *, delay: float = 21.0, sequence: int = 1,
                 min_qty: float = 0.0) -> dict:
    """Una fila como la deja el formulario: con plantilla y SIN variante."""
    return {"product_id": False, "product_tmpl_id": list(_PLANTILLA),
            "partner_id": [partner, f"Proveedor {partner}"], "sequence": sequence,
            "delay": delay, "min_qty": min_qty, "price": 10.0}


def _de_la_variante(pid: int, partner: int, *, delay: float = 7.0, sequence: int = 1,
                    min_qty: float = 0.0) -> dict:
    """Una fila fijada a una variante, como la deja la API o una importacion."""
    return {**_de_la_ficha(partner, delay=delay, sequence=sequence, min_qty=min_qty),
            "product_id": [pid, f"SKU-{pid}"]}


def test_el_plazo_cargado_en_la_ficha_sin_variante_se_lee():
    plazos = OdooConnector(_odoo({400: _de_la_ficha(70, delay=21.0)})).declared_lead_times()

    assert plazos.declared == {"SKU-1": 21.0}
    assert plazos.missing == () and plazos.qa_issues == ()


def test_la_po_borrador_encuentra_al_proveedor_cargado_en_la_ficha():
    """El sintoma del laboratorio: 'Unsourced (no supplier in Odoo)' sobre un SKU que
    tiene proveedor."""
    conector = OdooConnector(_odoo({400: _de_la_ficha(70)}))

    cs, unsourced = conector.stage_draft_purchase_orders({"SKU-1": 5.0})

    assert unsourced == ()
    assert [c.entity_id for c in cs.changes] == ["supplier:70"]


def test_el_minimo_de_la_fila_de_la_ficha_tambien_se_respeta():
    conector = OdooConnector(_odoo({400: _de_la_ficha(70, min_qty=50.0)}))

    cs, _ = conector.stage_draft_purchase_orders({"SKU-1": 13.0})

    (cambio,) = cs.changes
    assert cambio.after == (("SKU-1", 50.0, 0.0),)
    assert conector.last_moq_adjustments == {"SKU-1": (13.0, 50.0)}


def test_la_fila_de_la_ficha_vale_para_toda_variante_y_la_de_otra_variante_no():
    """Plantilla con dos variantes. La fila fijada a SKU-2 tiene mejor sequence, pero
    no es de SKU-1: Odoo la salta para SKU-1 y la elige para SKU-2."""
    conector = OdooConnector(_odoo(
        {400: _de_la_ficha(70, sequence=2), 401: _de_la_variante(2, 71, sequence=1)},
        variantes=2,
    ))

    assert conector.primary_supplier_by_sku(["SKU-1", "SKU-2"]) == {"SKU-1": 70, "SKU-2": 71}


def test_variante_y_ficha_compiten_por_sequence_como_en_odoo():
    """Una fila de variante NO gana por serlo: ``_prepare_sellers`` ordena por
    ``(sequence, -min_qty, price, id)`` sin mirar si la fila tiene variante."""
    conector = OdooConnector(_odoo({
        400: _de_la_variante(1, 71, sequence=5, delay=7.0),
        401: _de_la_ficha(70, sequence=1, delay=21.0),
    }))

    assert conector.primary_supplier_by_sku(["SKU-1"]) == {"SKU-1": 70}
    assert conector.lead_times() == {"SKU-1": 21.0}


def test_con_la_misma_sequence_gana_el_minimo_mayor_como_en_odoo():
    """Segundo criterio de Odoo: ``min_qty`` descendente. El stand-in devuelve en orden
    de insercion (400 antes que 401), que es el contrario: un conector que confie en el
    orden del ``search_read`` en vez de ordenar elige mal aca."""
    conector = OdooConnector(_odoo({
        400: _de_la_ficha(70, sequence=1, min_qty=0.0),
        401: _de_la_ficha(71, sequence=1, min_qty=24.0),
    }))

    assert conector.primary_supplier_by_sku(["SKU-1"]) == {"SKU-1": 71}


def test_una_ficha_con_el_plazo_sin_configurar_dispara_el_aviso():
    """``delay`` = 1 es el default de Odoo, no una promesa. Antes la fila de plantilla ni
    contaba como linea de proveedor, asi que el SKU salia n/a SIN aviso."""
    plazos = OdooConnector(
        _odoo({400: _de_la_ficha(70, delay=float(_DECLARED_DELAY_FLOOR))})
    ).declared_lead_times()

    assert plazos.declared == {}
    assert plazos.missing == ("SKU-1",)
    assert any("SKU-1" in aviso for aviso in plazos.qa_issues)


def test_un_proveedor_archivado_no_se_elige():
    """``_get_filtered_supplier`` exige ``s.partner_id.active``: con el primero de la
    ficha archivado, Odoo repone con el siguiente. Sin este filtro la OC borrador iria
    a un proveedor que en Odoo ya no se puede elegir, y su plazo seria el declarado.
    El 71 no esta sembrado en ``res.partner``: cuenta como activo, como en Odoo."""
    conector = OdooConnector(_odoo(
        {400: _de_la_ficha(70, sequence=1, delay=21.0), 401: _de_la_ficha(71, sequence=2, delay=9.0)},
        socios={70: {"name": "Proveedor 70", "active": False}},
    ))

    assert conector.primary_supplier_by_sku(["SKU-1"]) == {"SKU-1": 71}
    assert conector.lead_times() == {"SKU-1": 9.0}
