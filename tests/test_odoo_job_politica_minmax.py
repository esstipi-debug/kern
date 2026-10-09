"""El job Odoo corre la politica min/max: el plazo y el nivel de servicio llegan al entregable.

Sin este cableado, F2 y F3 (prueba de recorrido del 2026-09-19) quedaban arreglados
en ``plan_replenishment`` y el camino que vende seguia en cobertura: ``odoo_job.run``
no pasaba ``lead_time_days_by_sku`` y la transpaleta seguia comprando 7.

Todo sobre ``demo_odoo()`` (sin red): SKU-1 vende 10 por semana con plazo 7,
SKU-3 vende 5 con plazo 21, SKU-2 no vende. Ventana: 7 semanas calendario.
"""

from __future__ import annotations

import sys

import pytest

from jobs import odoo_job
from nosilo_core import writeback
from nosilo_core.registry import JobRequest
from scm_agent import tools
from src.connectors.odoo import InMemoryOdoo, OdooConnector, demo_odoo
from src.connectors.replenish import plan_replenishment


def _reporte(rpc: InMemoryOdoo | None = None, **kw):
    return odoo_job.run(odoo_job.prepare(None, {"odoo_rpc": rpc or demo_odoo()}), **kw)


def _linea(reporte, sku):
    return next(ln for ln in reporte.lines if ln.sku == sku)


def test_el_job_dimensiona_con_la_politica_min_max():
    reporte = _reporte()
    assert reporte.policy == "min_max"
    uno = _linea(reporte, "SKU-1")
    assert uno.policy == "min_max" and uno.lead_time_days == 7.0
    assert uno.order_up_to == 20.0 and uno.restock_qty == 8.0      # 10 x (7+7)/7 - 12
    tres = _linea(reporte, "SKU-3")
    assert tres.order_up_to == 20.0 and tres.restock_qty == 0.0    # 5 x (21+7)/7 = 20 = estante
    assert _linea(reporte, "SKU-2").status == "insufficient_history"


def test_el_default_de_nivel_de_servicio_se_declara_en_el_reporte():
    reporte = _reporte()
    assert reporte.service_level == 0.95 and reporte.review_days == 7.0
    assert reporte.service_level_assumed is True
    assert any(a.startswith("service_level=0.95") for a in reporte.assumptions)
    assert any("qty_to_order" in a for a in reporte.assumptions)


def test_un_nivel_de_servicio_dado_no_se_declara_como_default():
    reporte = _reporte(service_level=0.90, review_days=14.0)
    assert reporte.service_level == 0.90 and reporte.review_days == 14.0
    assert reporte.service_level_assumed is False
    assert not any(a.startswith("service_level") for a in reporte.assumptions)
    assert _linea(reporte, "SKU-1").order_up_to == 30.0            # 10 x (7+14)/7


def test_el_tool_pasa_nivel_de_servicio_y_revision_desde_los_params():
    tool = tools.build_default_registry().get("odoo_replenishment")
    assert tool.required_client_params == ("service_level",)
    params = {"odoo_rpc": demo_odoo(), "service_level": 0.90, "review_days": 14.0}
    prep = tool.prepare(JobRequest(brief="x", data_path=None, params=params), None)
    reporte = tool.run(prep.payload, params).report
    assert reporte.service_level == 0.90 and reporte.service_level_assumed is False


def test_el_minimo_del_proveedor_llega_al_plan():
    rpc = demo_odoo()
    rpc.records("product.supplierinfo")[400]["min_qty"] = 50.0
    uno = _linea(_reporte(rpc), "SKU-1")
    assert uno.restock_qty == 50.0 and uno.lot_rule == "moq 50"


def test_sin_plazos_nada_se_dimensiona_y_no_se_dice_hold():
    rpc = demo_odoo()
    rpc.records("product.supplierinfo").clear()
    reporte = _reporte(rpc)
    assert reporte.restock == {}
    assert reporte.unsized_no_lead_time == ("SKU-1", "SKU-2", "SKU-3")
    assert reporte.outcome.options[0].label == "Load the missing lead times"
    assert not any(o.label.startswith("Hold") for o in reporte.outcome.options)


def test_la_regla_se_escribe_min_igual_max_y_trigger_manual():
    rpc = demo_odoo()
    conector = OdooConnector(rpc)
    conector.list_products()
    cs = conector.stage_reorder_policy({"SKU-1": (20.0, 20.0)}, idempotency_key="k-1")
    conector.apply_restock(cs, approval=writeback.approve(cs, "ana"), auto_apply_reversible=False)
    [regla] = rpc.records("stock.warehouse.orderpoint").values()
    assert regla["product_min_qty"] == 20.0 and regla["product_max_qty"] == 20.0
    assert regla["trigger"] == "manual"


def test_un_maximo_menor_que_el_minimo_no_se_escribe():
    conector = OdooConnector(demo_odoo())
    with pytest.raises(ValueError, match="SKU-1"):
        conector.stage_reorder_policy({"SKU-1": (20.0, 10.0)}, idempotency_key="k-2")


def test_run_odoo_muestra_supuestos_y_minimo_en_ascii(monkeypatch, tmp_path, capsys):
    from examples import run_odoo

    monkeypatch.setattr(run_odoo, "_rpc", lambda: demo_odoo())
    monkeypatch.setenv("KERN_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(sys, "argv", ["run_odoo.py", "--service-level", "0.9"])
    run_odoo.main()
    salida = capsys.readouterr().out
    assert salida.isascii()
    assert "Supuestos:" in salida and "review_days=7" in salida
    assert "service_level=0.95" not in salida
    assert "min=max" in salida


_DOCENA = 1 / 12  # uom.uom.factor de "Dozens" en Odoo 17: 1 unidad = 1/12 docena


def _odoo_con_unidades() -> InMemoryOdoo:
    """Tres productos con las unidades de F4: PIEZA en "Units" (paso 1), GRANEL por kilo
    (paso 0,001) y GUANTE, que se guarda por unidad y se compra por docena con un minimo
    de 3 en la ficha del proveedor (``supplierinfo.product_uom`` es un related a
    ``uom_po_id``: son 3 docenas)."""
    return InMemoryOdoo({
        "product.product": {
            1: {"default_code": "PIEZA", "name": "Pieza", "list_price": 10.0, "standard_price": 5.0,
                "uom_id": [1, "Units"], "uom_po_id": [1, "Units"]},
            2: {"default_code": "GRANEL", "name": "Granel", "list_price": 3.0, "standard_price": 1.0,
                "uom_id": [2, "kg"], "uom_po_id": [2, "kg"]},
            4: {"default_code": "GUANTE", "name": "Guante", "list_price": 15.0, "standard_price": 10.0,
                "uom_id": [1, "Units"], "uom_po_id": [3, "Dozens"]},
        },
        "uom.uom": {
            1: {"name": "Units", "rounding": 0.01, "category_id": [1, "Unit"], "factor": 1.0},
            2: {"name": "kg", "rounding": 0.001, "category_id": [2, "Weight"], "factor": 1.0},
            3: {"name": "Dozens", "rounding": 0.01, "category_id": [1, "Unit"], "factor": _DOCENA},
        },
        "uom.category": {1: {"name": "Unit"}, 2: {"name": "Weight"}},
        "stock.location": {10: {"usage": "internal"}},
        "stock.quant": {},
        "product.supplierinfo": {
            400: {"product_id": [1, "Pieza"], "partner_id": [70, "Acme"], "sequence": 1, "min_qty": 0.0},
            401: {"product_id": [2, "Granel"], "partner_id": [70, "Acme"], "sequence": 1, "min_qty": 0.0},
            403: {"product_id": [4, "Guante"], "partner_id": [70, "Acme"], "sequence": 1, "min_qty": 3.0},
        },
        "stock.warehouse.orderpoint": {},
    })


def test_el_minimo_por_docena_llega_al_plan_en_unidades_de_stock():
    """3 docenas en la ficha del proveedor son un MOQ de 36 guantes, no de 3 (F4 R5)."""
    politica = odoo_job.politica_min_max(OdooConnector(_odoo_con_unidades()), {},
                                         service_level=None, review_days=None)
    assert politica["min_qty_by_sku"]["GUANTE"] == 36.0


@pytest.mark.parametrize(("sku", "escrito"), [("GRANEL", 2.346), ("PIEZA", 3.0)])
def test_la_regla_escrita_cae_en_el_paso_de_stock(sku, escrito):
    """Quien llame con valores crudos no escribe decimales: la proteccion de F4 en stage_restock."""
    rpc = _odoo_con_unidades()
    conector = OdooConnector(rpc)
    conector.list_products()
    cs = conector.stage_reorder_policy({sku: (2.3456, 2.3456)}, idempotency_key=f"k-{sku}")
    conector.apply_restock(cs, approval=writeback.approve(cs, "ana"), auto_apply_reversible=False)
    [regla] = rpc.records("stock.warehouse.orderpoint").values()
    assert regla["product_min_qty"] == escrito and regla["product_max_qty"] == escrito
    assert regla["trigger"] == "manual"


# -- F8 (D04=b): sin clave del llamador, la clave sale del contenido -------------------


def test_sin_clave_la_regla_usa_el_hash_del_contenido():
    """Sin clave, o con el default replenish-1 de plan_replenishment, la clave es
    content_hash[:32], como en stage_draft_purchase_orders. Una clave explicita se respeta."""
    conector = OdooConnector(demo_odoo())
    conector.list_products()
    sin_clave = conector.stage_reorder_policy({"SKU-1": (20.0, 20.0)})
    por_defecto = conector.stage_reorder_policy({"SKU-1": (20.0, 20.0)}, idempotency_key="replenish-1")
    explicita = conector.stage_reorder_policy({"SKU-1": (20.0, 20.0)}, idempotency_key="k-3")
    assert sin_clave.idempotency_key == sin_clave.content_hash[:32]
    assert por_defecto.idempotency_key == sin_clave.idempotency_key    # mismo contenido, misma clave
    assert explicita.idempotency_key == "k-3"


def test_dos_politicas_distintas_no_comparten_clave_y_la_segunda_escribe():
    """F8: con la clave fija replenish-1, la segunda aprobacion era un idempotent-skip
    y Odoo se quedaba con los minimos de la primera semana sin avisar."""
    rpc = demo_odoo()
    conector = OdooConnector(rpc)
    conector.list_products()
    uno = conector.stage_reorder_policy({"SKU-1": (20.0, 20.0)}, idempotency_key="replenish-1")
    conector.apply_restock(uno, approval=writeback.approve(uno, "ana"), auto_apply_reversible=False)
    dos = conector.stage_reorder_policy({"SKU-1": (30.0, 30.0)}, idempotency_key="replenish-1")
    assert uno.idempotency_key != dos.idempotency_key and len(dos.idempotency_key) == 32
    segunda = conector.apply_restock(dos, approval=writeback.approve(dos, "ana"),
                                     auto_apply_reversible=False)
    assert segunda.applied and not segunda.idempotent_skip
    [regla] = rpc.records("stock.warehouse.orderpoint").values()
    assert regla["product_min_qty"] == 30.0 and regla["product_max_qty"] == 30.0


def test_el_plan_min_max_hereda_la_clave_del_contenido():
    """_plan_min_max pasa su idempotency_key (default replenish-1) a stage_reorder_policy."""
    conector = OdooConnector(demo_odoo())
    plan = plan_replenishment(conector, store=conector, on_order_by_sku={},
                              lead_time_days_by_sku={"SKU-1": 7.0, "SKU-3": 21.0})
    assert plan.changeset is not None
    assert plan.changeset.idempotency_key == plan.changeset.content_hash[:32]


# -- revision 2026-10-08: la regla que ya existia vuelve ENTERA, trigger incluido ------


def test_una_regla_existente_en_auto_se_ve_pasar_a_manual_y_el_rollback_la_devuelve():
    """El trigger se lee como min y max. Sin leerlo, el dry-run que firma el aprobador
    mostraba ``trigger: None -> manual`` sobre una regla que estaba en ``auto`` -- el
    cambio que apaga la reposicion automatica de Odoo quedaba invisible -- y el rollback
    dejaba la regla en manual: la marcha atras no devolvia la configuracion del cliente.
    Importa porque el nivel T1 de ``scm_agent/autonomy.py`` aplica solo los cambios
    reversibles, sin una persona delante."""
    rpc = demo_odoo()
    conector = OdooConnector(rpc)
    conector.list_products()
    pid = conector._id_by_sku["SKU-1"]
    rpc.records("stock.warehouse.orderpoint")[900] = {
        "product_id": [pid, "SKU-1"], "product_min_qty": 5.0, "product_max_qty": 10.0, "trigger": "auto",
    }

    cs = conector.stage_reorder_policy({"SKU-1": (20.0, 20.0)}, idempotency_key="k-auto")
    antes = {c.field: c.before for c in cs.changes}
    assert antes == {"trigger": "auto", "product_min_qty": 5.0, "product_max_qty": 10.0}

    conector.apply_restock(cs, approval=writeback.approve(cs, "ana"), auto_apply_reversible=False)
    assert rpc.records("stock.warehouse.orderpoint")[900]["trigger"] == "manual"

    conector.rollback("k-auto")
    regla = rpc.records("stock.warehouse.orderpoint")[900]
    assert (regla["trigger"], regla["product_min_qty"], regla["product_max_qty"]) == ("auto", 5.0, 10.0)
