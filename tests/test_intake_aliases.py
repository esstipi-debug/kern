"""El mapeo por alias tiene que sobrevivir a un export en espanol.

El primer mercado declarado es LatAm y los ERP locales (Defontana, Softland,
Contpaqi, Siigo, Tango) exportan cabeceras acentuadas. Antes del plegado NFKD,
`[^a-z0-9]` BORRABA la vocal acentuada -- "Descripcion" con tilde caia en
"descripcin" -- y la columna se trataba como ausente sin decir nada.
"""
from __future__ import annotations

import pandas as pd

from jobs.intake import (
    RECEIPT_OPTIONAL,
    RECEIPT_REQUIRED,
    _norm,
    detect_columns,
)


def test_norm_pliega_acentos_en_vez_de_borrar_la_vocal():
    assert _norm("Descripción") == _norm("descripcion") == "descripcion"
    assert _norm("Código") == _norm("codigo") == "codigo"
    assert _norm("Días") == _norm("dias") == "dias"
    assert _norm("Fecha Recepción") == _norm("fecha_recepcion") == "fecharecepcion"


def test_un_export_con_cabeceras_en_espaniol_mapea_entero():
    df = pd.DataFrame(
        {"Código": ["A"], "Descripción": ["x"], "Cantidad": [2], "Fecha": ["2026-01-01"]}
    )
    m = detect_columns(df)

    assert m.ok, m.unmatched_required
    assert m.mapping["product_id"] == "Código"
    assert m.mapping["quantity"] == "Cantidad"
    assert m.mapping["date"] == "Fecha"


def test_los_campos_de_recepcion_se_piden_aparte_del_juego_de_demanda():
    df = pd.DataFrame(
        {
            "Proveedor": ["ACME"],
            "Fecha Orden": ["2026-01-01"],
            "Fecha Recepción": ["2026-02-04"],
            "Plazo": [21],
        }
    )
    m = detect_columns(df, required=RECEIPT_REQUIRED, optional=RECEIPT_OPTIONAL)

    assert m.ok, m.unmatched_required
    assert m.mapping["supplier"] == "Proveedor"
    assert m.mapping["order_date"] == "Fecha Orden"
    assert m.mapping["receipt_date"] == "Fecha Recepción"
    assert m.mapping["lead_time_days"] == "Plazo"


def test_una_columna_fecha_pelada_no_se_toma_por_ninguna_de_las_dos():
    """La ambiguedad que invertiria el signo del plazo medido.

    `date` acepta un "fecha" pelado porque en un export de demanda no hay otra;
    en uno de compras esa columna tanto puede ser la de la orden como la de la
    recepcion, y elegir mal da plazos negativos o un gap invertido. Ninguno de
    los dos campos de recepcion la acepta: o el archivo las nombra, o no se mide.
    """
    df = pd.DataFrame({"Proveedor": ["ACME"], "Fecha": ["2026-01-01"]})
    m = detect_columns(df, required=RECEIPT_REQUIRED, optional=RECEIPT_OPTIONAL)

    assert not m.ok
    assert set(m.unmatched_required) == {"order_date", "receipt_date"}


def test_el_juego_por_defecto_sigue_siendo_el_de_demanda():
    """Los llamadores previos no pasan `required`/`optional`: no deben cambiar."""
    df = pd.DataFrame({"date": ["2026-01-01"], "sku": ["A"], "qty": [1]})
    m = detect_columns(df)

    assert m.ok
    assert set(m.mapping) >= {"date", "product_id", "quantity"}
    assert "supplier" not in m.mapping  # no se cuela el juego de recepciones


def test_los_alias_del_normalizador_borrado_siguen_mapeando():
    """`dia`/`producto`/`demanda`/`plazo de entrega` vivian solo en jobs/column_mapping.py.

    Ese modulo era un segundo normalizador sin ningun importador fuera de su
    propio test, y se borro. Era el unico lugar del repo que aceptaba estos
    cuatro encabezados en espanol pelado -- exactamente la forma en que los
    nombra un export de ERP de LatAm -- asi que se portaron aca. Sin este test
    el borrado se lleva la cobertura en silencio.
    """
    df = pd.DataFrame(
        {
            "Día": ["2026-01-01"],
            "Producto": ["A"],
            "Demanda": [7],
            "Plazo de entrega": [21],
        }
    )
    m = detect_columns(df)

    assert m.ok, m.unmatched_required
    assert m.mapping["date"] == "Día"
    assert m.mapping["product_id"] == "Producto"
    assert m.mapping["quantity"] == "Demanda"
    assert m.mapping["lead_time_days"] == "Plazo de entrega"


def test_entrega_pelado_no_se_toma_por_un_plazo_en_dias():
    """El quinto alias del modulo borrado NO se porto, y es a proposito.

    `entrega` tanto puede ser una fecha de entrega como un plazo en dias, y
    confundirlas es el mismo error que RECEIPT_REQUIRED evita mas arriba. Que
    quede sin mapear es la respuesta correcta, no una omision.
    """
    df = pd.DataFrame(
        {"Fecha": ["2026-01-01"], "SKU": ["A"], "Cantidad": [1], "Entrega": ["2026-01-05"]}
    )
    m = detect_columns(df)

    assert m.ok, m.unmatched_required
    assert "lead_time_days" not in m.mapping
