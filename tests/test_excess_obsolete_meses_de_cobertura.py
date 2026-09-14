"""T11 pide "meses de cobertura por SKU" y el job solo emitia dias.

La clausula es literal y la unidad no es un detalle de presentacion: quien decide cortar
compras razona en meses, y "1.278 dias" es un numero que nadie convierte de cabeza antes
de aceptarlo. La conversion es derivable de lo que el job ya calcula -- no hace falta
ningun dato nuevo, que es justo por que la ausencia era un bug y no una carencia de dato.

La conversion vive en jobs/, no en ``SkuEO``: days_of_cover es lo que consumen
src/alerting.py, src/liquidation.py y jobs/seo_priority.py, y meterle una unidad de
reporte al modelo mueve un contrato que no es de esta capa.

El caso de borde que hay que cuidar es inf. Un SKU sin demanda tiene cobertura infinita,
y redondearla a un numero grande lo pondria en un plan de reposicion: "nunca se acaba" no
es "se acaba en mucho tiempo".
"""

from __future__ import annotations

import csv

import pandas as pd

from jobs.excess_obsolete_job import build_deck, prepare_records, run, write_operational

# 900 unidades a 1/dia = 900 dias de cobertura = 29,6 meses (365,25/12 por mes).
# SIN-DEMANDA queda con demanda 0 -> dead -> cobertura infinita.
_DF = pd.DataFrame({
    "product_id": ["LENTO", "SANO", "SIN-DEMANDA"],
    "on_hand": [900.0, 30.0, 40.0],
    "daily_demand": [1.0, 1.0, 0.0],
    "unit_cost": [10.0, 10.0, 25.0],
})


def _report():
    return run(prepare_records(_DF))


def _rows(tmp_path) -> list[dict]:
    paths = write_operational(_report(), tmp_path)
    with open(paths["csv"], newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def test_el_csv_operativo_declara_los_meses_de_cobertura_por_sku(tmp_path):
    por_sku = {r["product_id"]: r for r in _rows(tmp_path)}

    assert float(por_sku["LENTO"]["months_of_cover"]) == 29.6


def test_un_sku_sin_demanda_sale_inf_y_no_cero(tmp_path):
    """Cobertura infinita no es cobertura nula, y tampoco es una excepcion."""
    por_sku = {r["product_id"]: r for r in _rows(tmp_path)}

    assert por_sku["SIN-DEMANDA"]["months_of_cover"] == "inf"
    assert por_sku["SIN-DEMANDA"]["days_of_cover"] == "inf"


def test_la_columna_de_dias_sigue_estando(tmp_path):
    """El cambio es aditivo: nadie que leyera days_of_cover se queda sin su columna."""
    por_sku = {r["product_id"]: r for r in _rows(tmp_path)}

    assert float(por_sku["LENTO"]["days_of_cover"]) == 900.0
    assert {"product_id", "classification", "on_hand", "excess_units",
            "at_risk_value", "recommended_action"} <= set(por_sku["LENTO"])


def test_cada_sku_en_riesgo_trae_accion_y_cobertura(tmp_path):
    """Las dos clausulas de T11 que se verifican por linea, sobre el mismo entregable."""
    en_riesgo = [r for r in _rows(tmp_path) if r["classification"] != "healthy"]

    assert en_riesgo
    assert all(r["recommended_action"].strip() for r in en_riesgo)
    assert all(r["months_of_cover"] for r in en_riesgo)


def test_el_deck_dice_la_cobertura_en_meses_del_peor_sku():
    md = build_deck(_report(), client="Acme").to_markdown()

    assert "months of cover" in md
    assert md.isascii()                      # cp1252-safe


def test_el_deck_deletrea_la_cobertura_infinita_en_vez_de_imprimir_inf():
    """La mayor exposicion aca es el SKU sin demanda: 'inf months' seria un glitch."""
    solo_muerto = pd.DataFrame({
        "product_id": ["SIN-DEMANDA"],
        "on_hand": [40.0],
        "daily_demand": [0.0],
        "unit_cost": [25.0],
    })

    md = build_deck(run(prepare_records(solo_muerto))).to_markdown()

    assert "cover unbounded" in md
    assert "inf months" not in md
