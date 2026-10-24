"""El modelo canonico de datos (``src/cdm.py``) y su puente con los conectores.

``src/cdm.py`` se copia byte a byte a ``mytra-fde/src/fdekit/cdm.py``. El primer
test fija su sha256: si falla, alguien edito una sola copia. Ver el encabezado
del modulo para el procedimiento.
"""

from __future__ import annotations

import ast
import hashlib
import json
import math
import sys
from pathlib import Path

import pytest

from nosilo_core import writeback
from src import cdm
from src.cdm import (
    CdmError,
    CycleCount,
    GateArrival,
    InventorySnapshot,
    OrderLine,
    Provenance,
    ScheduledReceipt,
    ScrapDisposal,
    from_record,
    to_record,
)
from src.cdm_bridge import inventory_to_cdm, on_hand_from_cdm, orders_to_cdm, receipts_to_cdm
from src.connectors import InventoryLevel, Order
from src.connectors import OrderLine as KernLine
from src.connectors import ScheduledReceipt as KernReceipt

CDM_PATH = Path(__file__).resolve().parents[1] / "src" / "cdm.py"

#: sha256 de src/cdm.py. El MISMO valor esta fijado en mytra-fde/tests/test_cdm.py.
CDM_SHA256 = "dc290a58057f68df4e516fa1482bc4b97e3375e4646cbf629aa2e3e7b2eb7f5f"

PROV = Provenance(source_system="odoo", source_id="42", observed_at="2026-10-01T12:00:00+00:00")


def _one_of_each() -> list:
    return [
        InventorySnapshot(sku="SKU-1", location="WH1", on_hand=12.0, as_of="2026-10-01"),
        OrderLine(
            order_id="SO-1",
            sku="SKU-1",
            ordered_on="2026-09-30",
            quantity=3,
            price=9.5,
            delivered_qty=3,
            promised_date="2026-10-02",
            delivered_date="2026-10-03",
            delivered_date_means="departure",
        ),
        ScheduledReceipt(sku="SKU-1", quantity=40, order_id="PO-7", promised_date="2026-10-12"),
        CycleCount(sku="SKU-1", location="A-01-02", counted_qty=11, counted_at="2026-10-01T09:30:00-03:00", expected_qty=12),
        ScrapDisposal(sku="SKU-1", location="BIN-9", units=1, reason="expired_lot", disposed_at="2026-10-01T10:00:00Z"),
        GateArrival(delivery_id="DLV-0001", carrier_id="CARR1", location="GATE-1", arrived_at="2026-10-01T08:00:00+00:00"),
    ]


# ---- la copia vendorizada ------------------------------------------------------

def test_la_copia_vendorizada_no_diverge() -> None:
    digest = hashlib.sha256(CDM_PATH.read_bytes()).hexdigest()
    assert digest == CDM_SHA256, (
        "src/cdm.py cambio. Esta vendorizado en mytra-fde/src/fdekit/cdm.py: subi CDM_VERSION, "
        "copia el archivo byte a byte al otro repo y actualiza CDM_SHA256 en los dos tests/test_cdm.py. "
        f"Hash actual: {digest}"
    )


def test_solo_usa_la_biblioteca_estandar() -> None:
    tree = ast.parse(CDM_PATH.read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported |= {alias.name.split(".")[0] for alias in node.names}
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0, "una importacion relativa no sobrevive la copia a fdekit"
            imported.add(node.module.split(".")[0])
    ajenos = {name for name in imported if name not in sys.stdlib_module_names and name != "__future__"}
    assert not ajenos, f"src/cdm.py tiene que correr sin instalar nada: importa {sorted(ajenos)}"


def test_compila_con_la_gramatica_de_python_3_10() -> None:
    # fdekit declara requires-python >= 3.10.
    ast.parse(CDM_PATH.read_text(encoding="utf-8"), feature_version=(3, 10))


def test_los_niveles_de_riesgo_son_los_de_writeback() -> None:
    assert cdm.RISK_TIERS == (writeback.TIER_READ, writeback.TIER_REVERSIBLE, writeback.TIER_IRREVERSIBLE)


# ---- el sobre ------------------------------------------------------------------

@pytest.mark.parametrize("item", _one_of_each(), ids=lambda i: i.KIND)
def test_ida_y_vuelta_por_json(item) -> None:
    record = json.loads(json.dumps(to_record(item, PROV)))
    assert record["cdm"] == cdm.CDM_VERSION
    assert from_record(record) == (item, PROV)


def test_cada_tipo_tiene_su_kind() -> None:
    assert {type(i).KIND for i in _one_of_each()} == set(cdm.KINDS)


def test_el_none_viaja_como_null_y_no_como_cero() -> None:
    record = to_record(InventorySnapshot(sku="S", location="L", on_hand=5, as_of="2026-10-01"), PROV)
    assert "on_order" in record["data"] and record["data"]["on_order"] is None
    item, _ = from_record(record)
    assert item.on_order is None


def test_una_clave_que_falta_no_es_un_none() -> None:
    record = to_record(InventorySnapshot(sku="S", location="L", on_hand=5, as_of="2026-10-01"), PROV)
    del record["data"]["on_order"]
    with pytest.raises(CdmError, match="faltan"):
        from_record(record)


def test_un_campo_desconocido_se_rechaza() -> None:
    record = to_record(InventorySnapshot(sku="S", location="L", on_hand=5, as_of="2026-10-01"), PROV)
    record["data"]["onhand"] = 5
    with pytest.raises(CdmError, match="desconocidos"):
        from_record(record)


def test_otra_version_mayor_no_se_lee_y_otra_menor_si() -> None:
    record = to_record(InventorySnapshot(sku="S", location="L", on_hand=5, as_of="2026-10-01"), PROV)
    major = int(cdm.CDM_VERSION.split(".")[0])
    with pytest.raises(CdmError, match="version"):
        from_record({**record, "cdm": f"{major + 1}.0.0"})
    assert from_record({**record, "cdm": f"{major}.99.0"})[0].on_hand == 5


def test_un_kind_desconocido_se_rechaza() -> None:
    record = to_record(InventorySnapshot(sku="S", location="L", on_hand=5, as_of="2026-10-01"), PROV)
    with pytest.raises(CdmError, match="kind"):
        from_record({**record, "kind": "invoice"})


def test_sin_procedencia_no_hay_registro() -> None:
    with pytest.raises(CdmError, match="provenance"):
        to_record(InventorySnapshot(sku="S", location="L", on_hand=5, as_of="2026-10-01"), None)


# ---- las reglas del contrato ---------------------------------------------------

def test_una_fecha_de_entrega_sin_significado_se_rechaza() -> None:
    with pytest.raises(CdmError, match="delivered_date_means"):
        OrderLine(order_id="SO", sku="S", ordered_on="2026-10-01", quantity=1, delivered_date="2026-10-03")


def test_el_significado_de_la_fecha_es_un_vocabulario_cerrado() -> None:
    with pytest.raises(CdmError, match="delivered_date_means"):
        OrderLine(
            order_id="SO", sku="S", ordered_on="2026-10-01", quantity=1,
            delivered_date="2026-10-03", delivered_date_means="shipped",
        )


def test_un_instante_sin_zona_horaria_se_rechaza() -> None:
    with pytest.raises(CdmError, match="zona horaria"):
        CycleCount(sku="S", location="L", counted_qty=1, counted_at="2026-10-01T09:30:00")


@pytest.mark.parametrize("bad", [True, math.nan, math.inf, -1, "3"])
def test_una_cantidad_tiene_que_ser_un_numero_finito_no_negativo(bad) -> None:
    with pytest.raises(CdmError, match="on_hand"):
        InventorySnapshot(sku="S", location="L", on_hand=bad, as_of="2026-10-01")


@pytest.mark.parametrize("bad", ["2026-13-01", "01/10/2026", "2026-10-01T00:00:00"])
def test_una_fecha_es_un_dia_iso(bad) -> None:
    with pytest.raises(CdmError, match="as_of"):
        InventorySnapshot(sku="S", location="L", on_hand=1, as_of=bad)


def test_la_diferencia_de_conteo_sin_expectativa_es_none() -> None:
    assert CycleCount(sku="S", location="L", counted_qty=4, counted_at="2026-10-01T00:00:00Z").variance is None
    assert CycleCount(sku="S", location="L", counted_qty=4, counted_at="2026-10-01T00:00:00Z", expected_qty=6).variance == -2


# ---- el puente con los conectores ------------------------------------------------

def _order(delivered_date=None) -> Order:
    return Order(
        order_id="SO-9",
        created_at="2026-09-30T14:00:00",
        lines=(KernLine(sku="SKU-1", quantity=2, price=5.0, delivered_qty=2, delivered_date=delivered_date),),
    )


def test_las_lineas_llevan_el_id_y_el_dia_de_su_pedido() -> None:
    (record,) = orders_to_cdm([_order()], provenance=PROV)
    assert record["data"]["order_id"] == "SO-9"
    assert record["data"]["ordered_on"] == "2026-09-30"
    assert record["data"]["delivered_date_means"] is None


def test_el_puente_no_deduce_que_significa_la_fecha() -> None:
    with pytest.raises(CdmError, match="delivered_date_means"):
        orders_to_cdm([_order("2026-10-02")], provenance=PROV)
    (record,) = orders_to_cdm([_order("2026-10-02")], provenance=PROV, delivered_date_means="departure")
    assert record["data"]["delivered_date_means"] == "departure"


def test_en_transito_sin_leer_es_none_y_leido_es_cero_si_no_aparece() -> None:
    levels = [InventoryLevel(sku="A", available=3), InventoryLevel(sku="B", available=1)]
    sin_leer = inventory_to_cdm(levels, as_of="2026-10-01", provenance=PROV)
    assert [r["data"]["on_order"] for r in sin_leer] == [None, None]
    leido = inventory_to_cdm(levels, as_of="2026-10-01", provenance=PROV, on_order_by_sku={"A": 7})
    assert [r["data"]["on_order"] for r in leido] == [7.0, 0.0]


def test_una_recepcion_sin_orden_no_inventa_un_id() -> None:
    (record,) = receipts_to_cdm([KernReceipt(sku="A", quantity=5, promised_date="2026-10-09T00:00:00")], provenance=PROV)
    assert record["data"]["order_id"] is None
    assert record["data"]["promised_date"] == "2026-10-09"
    assert record["data"]["expected_date"] is None


def test_el_conteo_del_piso_le_gana_a_la_foto_del_mismo_dia() -> None:
    records = [
        to_record(InventorySnapshot(sku="A", location="L1", on_hand=10, as_of="2026-10-01"), PROV),
        to_record(CycleCount(sku="A", location="L1", counted_qty=7, counted_at="2026-10-01T18:00:00Z"), PROV),
    ]
    assert on_hand_from_cdm(records) == {"A": 7}
    assert on_hand_from_cdm(list(reversed(records))) == {"A": 7}


def test_una_foto_posterior_le_gana_a_un_conteo_viejo_y_las_ubicaciones_suman() -> None:
    records = [
        to_record(CycleCount(sku="A", location="L1", counted_qty=7, counted_at="2026-09-28T18:00:00Z"), PROV),
        to_record(InventorySnapshot(sku="A", location="L1", on_hand=9, as_of="2026-10-01"), PROV),
        to_record(InventorySnapshot(sku="A", location="L2", on_hand=2, as_of="2026-10-01"), PROV),
        to_record(GateArrival(delivery_id="D-000001", carrier_id="C1", location="G", arrived_at="2026-10-01T08:00:00Z"), PROV),
    ]
    assert on_hand_from_cdm(records) == {"A": 11}


def test_un_sku_sin_observacion_no_sale_con_cero() -> None:
    assert on_hand_from_cdm([]) == {}
