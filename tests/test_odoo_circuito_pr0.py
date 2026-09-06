"""PR-0 del plan del 2026-09-05: lo que tiene que estar cerrado ANTES de tocar la Odoo de un cliente.

Cuatro piezas, cada una con el error que evita delante de un partner:

1. Los reorder points ya no se aplican sin un humano con nombre (``examples/run_odoo.py``
   aplicaba sin ``--approved-by``; las PO borrador si lo exigian). Y cada aplicacion deja
   una fila en un ledger en disco, no en memoria de proceso.
2. Lo que ya viene en camino se descuenta (probado en ``tests/test_neteo_en_transito.py``).
3. La PO borrador respeta el minimo de pedido del proveedor (``product.supplierinfo.min_qty``):
   37 unidades a un proveedor que vende de a 100 es una orden que nadie acepta.
4. Un producto dado de alta despues de la primera lectura no cae en ``unsourced``: el
   catalogo se refresca cuando le piden un SKU que no conoce.
"""

from __future__ import annotations

import sys

import pytest

from src.connectors.odoo import InMemoryOdoo, OdooConnector, demo_odoo


def _odoo_con_proveedor(min_qty: float = 0.0) -> InMemoryOdoo:
    return InMemoryOdoo(
        {
            "product.product": {
                1: {"default_code": "SKU-1", "name": "Widget", "list_price": 20.0, "standard_price": 12.0},
            },
            "res.partner": {70: {"name": "Acme", "commercial_partner_id": [70, "Acme"]}},
            "product.supplierinfo": {
                500: {"product_id": [1, "Widget"], "partner_id": [70, "Acme"], "sequence": 1,
                      "delay": 7, "min_qty": min_qty, "price": 10.0},
            },
            "purchase.order": {},
            "purchase.order.line": {},
            "stock.location": {},
            "stock.quant": {},
            "stock.warehouse.orderpoint": {},
        }
    )


# -- 3. minimo de pedido del proveedor -------------------------------------------


def test_la_po_borrador_sube_al_minimo_del_proveedor_y_lo_dice():
    con = OdooConnector(_odoo_con_proveedor(min_qty=100.0))
    cs, unsourced = con.stage_draft_purchase_orders({"SKU-1": 37.0})

    assert unsourced == ()
    (change,) = cs.changes
    assert change.after == (("SKU-1", 100.0, 0.0),)
    assert con.last_moq_adjustments == {"SKU-1": (37.0, 100.0)}


def test_sin_minimo_la_po_borrador_pide_lo_que_el_plan_pide():
    con = OdooConnector(_odoo_con_proveedor(min_qty=0.0))
    cs, _ = con.stage_draft_purchase_orders({"SKU-1": 37.0})

    (change,) = cs.changes
    assert change.after == (("SKU-1", 37.0, 0.0),)
    assert con.last_moq_adjustments == {}


def test_el_resultado_de_crear_la_po_trae_lo_que_subio_al_minimo():
    con = OdooConnector(_odoo_con_proveedor(min_qty=100.0))
    po = con.create_draft_purchase_orders({"SKU-1": 37.0}, approved_by="ana")

    assert po.raised_to_min == {"SKU-1": (37.0, 100.0)}
    # El fake guarda las lineas anidadas en la orden, como el create de Odoo:
    # order_line = [(0, 0, {...}), ...]
    ordenes = con._rpc.records("purchase.order").values()
    cantidades = [ln[2]["product_qty"] for o in ordenes for ln in o["order_line"]]
    assert cantidades == [100.0]


# -- 4. refresco del catalogo --------------------------------------------------


def test_un_sku_dado_de_alta_despues_no_cae_en_unsourced():
    """El sintoma viejo apuntaba al lugar equivocado: "sin proveedor", cuando el
    proveedor existia. Un refresco por SKU desconocido cuesta una lectura."""
    rpc = _odoo_con_proveedor()
    con = OdooConnector(rpc)
    con.list_products()  # calienta la cache con SKU-1 solo

    rpc.records("product.product")[2] = {
        "default_code": "SKU-TARDIO", "name": "Nuevo", "list_price": 5.0, "standard_price": 2.0,
    }
    rpc.records("product.supplierinfo")[501] = {
        "product_id": [2, "Nuevo"], "partner_id": [70, "Acme"], "sequence": 1, "delay": 3, "min_qty": 0.0,
    }

    _cs, unsourced = con.stage_draft_purchase_orders({"SKU-TARDIO": 5.0})
    assert unsourced == ()


def test_un_sku_que_de_verdad_no_tiene_proveedor_sigue_en_unsourced():
    rpc = _odoo_con_proveedor()
    con = OdooConnector(rpc)
    rpc.records("product.product")[3] = {
        "default_code": "SKU-HUERFANO", "name": "Sin proveedor", "list_price": 5.0, "standard_price": 2.0,
    }
    _cs, unsourced = con.stage_draft_purchase_orders({"SKU-HUERFANO": 5.0})
    assert unsourced == ("SKU-HUERFANO",)


# -- 1. aprobacion con nombre y ledger en disco para los reorder points ----------


def _orderpoints(rpc: InMemoryOdoo) -> dict:
    return {i: dict(r) for i, r in rpc.records("stock.warehouse.orderpoint").items()}


def _correr(monkeypatch, tmp_path, argv: list[str]) -> InMemoryOdoo:
    from examples import run_odoo

    rpc = demo_odoo()
    monkeypatch.setattr(run_odoo, "_rpc", lambda: rpc)
    monkeypatch.setenv("KERN_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["run_odoo.py", *argv])
    run_odoo.main()
    return rpc


def test_sin_approved_by_los_reorder_points_quedan_en_dry_run(monkeypatch, tmp_path, capsys):
    rpc = _correr(monkeypatch, tmp_path, [])

    assert _orderpoints(rpc) == {}, "no se escribio ningun reorder point"
    salida = capsys.readouterr().out
    assert "Staged (dry-run)" in salida
    assert "Nothing applied" in salida


def test_con_approved_by_se_aplica_y_queda_una_fila_en_el_ledger_en_disco(monkeypatch, tmp_path, capsys):
    rpc = _correr(monkeypatch, tmp_path, ["--approved-by", "ana"])

    assert _orderpoints(rpc), "los reorder points se escribieron"
    salida = capsys.readouterr().out
    assert "approved by ana" in salida
    assert "idempotent_skip=True" in salida
    ledger = tmp_path / "odoo_writeback.sqlite"
    assert ledger.exists() and ledger.stat().st_size > 0


@pytest.mark.parametrize("argv", [["--draft-po"]])
def test_la_po_borrador_sigue_exigiendo_approved_by(monkeypatch, tmp_path, argv):
    from examples import run_odoo

    monkeypatch.setattr(run_odoo, "_rpc", lambda: demo_odoo())
    monkeypatch.setenv("KERN_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["run_odoo.py", *argv])
    with pytest.raises(SystemExit):
        run_odoo.main()
