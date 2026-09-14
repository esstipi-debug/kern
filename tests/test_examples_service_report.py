"""El informe de servicio sigue corriendo. Guard de bitrot, no de diseno.

El comportamiento vive en tests/test_connector_olist.py y en el guard de cobertura, y se
escribio antes que el codigo. Esto cubre lo otro: que el script no se pudra cuando alguien
renombre un campo de RealizedService o de ServiceCoverage. Es la pieza que se le muestra a
un prospecto, asi que romperla en silencio es peor que romper un test.

Los CSV son minimos y viven en tmp_path: ``data/`` esta gitignoreado y CI no lo tiene.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from examples import run_service_report

_ORDERS = "\n".join([
    '"order_id","customer_id","order_status","order_purchase_timestamp",'
    '"order_approved_at","order_delivered_carrier_date",'
    '"order_delivered_customer_date","order_estimated_delivery_date"',
    "o1,c1,delivered,2017-10-02 10:56:33,2017-10-02 11:07:15,"
    "2017-10-04 19:55:00,2017-10-20 21:25:13,2017-10-18 00:00:00",
    "o2,c2,delivered,2017-10-03 10:00:00,2017-10-03 11:00:00,"
    "2017-10-05 10:00:00,2017-10-10 10:00:00,2017-10-18 00:00:00",
    "",
])
_ITEMS = "\n".join([
    '"order_id","order_item_id","product_id","seller_id","shipping_limit_date",'
    '"price","freight_value"',
    '"o1",1,"P-1","s1",2017-09-19 09:45:35,58.90,13.29',
    '"o2",1,"P-1","s1",2017-09-19 09:45:35,58.90,13.29',
    "",
])


def _datos(tmp: Path) -> Path:
    (tmp / "olist_orders_dataset.csv").write_text(_ORDERS, encoding="utf-8")
    (tmp / "olist_order_items_dataset.csv").write_text(_ITEMS, encoding="utf-8")
    return tmp


def test_el_informe_corre_y_muestra_las_dos_mediciones(tmp_path: Path, capsys, monkeypatch):
    monkeypatch.setattr(
        "sys.argv", ["run_service_report.py", "--data", str(_datos(tmp_path))]
    )
    assert run_service_report.main() == 0
    salida = capsys.readouterr().out
    assert "EL MISMO PERIODO, MEDIDO DE DOS FORMAS" in salida
    assert "llegada al cliente" in salida
    assert "salida del deposito" in salida
    # o1 llego tarde y o2 a tiempo: la mitad por llegada, las dos por despacho.
    assert "BRECHA" in salida


def test_sin_los_csv_explica_donde_faltan_en_vez_de_reventar(tmp_path: Path, monkeypatch):
    """``data/`` esta gitignoreado, asi que "no estan los archivos" es el primer error que
    va a ver cualquiera que clone el repo. Tiene que decir que falta y donde."""
    monkeypatch.setattr("sys.argv", ["run_service_report.py", "--data", str(tmp_path)])
    with pytest.raises(SystemExit, match="Olist"):
        run_service_report.main()


def test_un_valor_no_medido_no_se_imprime_como_cero_por_ciento():
    """La ausencia no se maquilla en la pantalla que ve el cliente, despues de que todo el
    motor se cuido de no hacerlo."""
    assert run_service_report._pct(None) == "no medido"
    assert run_service_report._pct(0.0) == "0.0%"
    assert run_service_report._plata(None) == "no medida"
