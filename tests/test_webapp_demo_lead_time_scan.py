"""Salida #1 del embudo: el plazo observado se mide, no se cree."""
from __future__ import annotations

import pandas as pd
import pytest

from webapp import demo_lead_time_scan as scan


def _receipts(rows: list[tuple[str, str, str, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "supplier": s,
                "order_date": o,
                "receipt_date": r,
                "declared_lead_time_days": d,
                "product_id": f"SKU-{i}",
            }
            for i, (s, o, r, d) in enumerate(rows)
        ]
    )


def test_gap_entre_lo_declarado_y_lo_observado_sale_en_el_titular():
    df = _receipts(
        [
            ("ACME", "2026-01-01", "2026-02-04", 21.0),  # 34 d
            ("ACME", "2026-02-01", "2026-03-10", 21.0),  # 37 d
            ("ACME", "2026-03-01", "2026-03-29", 21.0),  # 28 d
        ]
    )
    result = scan.run_lead_time_scan(df)

    assert result.ok, result.qa_issues
    score = result.scores[0]
    assert score.declared_lead_time == 21.0
    assert score.avg_lead_time == pytest.approx(33.0)
    assert score.lead_time_gap == pytest.approx(12.0)
    assert score.lead_time_std is not None  # la desviacion que el ERP no tiene
    # Un gap de 57% pasa la banda de materialidad: los SKU quedan marcados.
    assert score.skus_to_recalculate
    assert "21" in result.findings[0] and "33" in result.findings[0]


def test_sin_fechas_no_se_mide_aunque_venga_el_campo_del_erp():
    """El campo `lead_time_days` del ERP es la promesa, no la observacion.

    Tomarlo como medicion compara la promesa consigo misma y devuelve un gap de
    cero sobre el defecto que el scan viene a mostrar.
    """
    df = pd.DataFrame(
        [{"supplier": "ACME", "lead_time_days": 21.0} for _ in range(5)]
    )
    with pytest.raises(ValueError, match="fecha de orden"):
        scan.run_lead_time_scan(df)


def test_recepcion_anterior_a_su_orden_se_descarta_y_se_reporta():
    df = _receipts(
        [
            ("ACME", "2026-01-01", "2026-02-04", 21.0),
            ("ACME", "2026-02-01", "2026-03-10", 21.0),
            ("ACME", "2026-03-01", "2026-03-29", 21.0),
            ("ACME", "2026-04-10", "2026-04-01", 21.0),  # rota
        ]
    )
    result = scan.run_lead_time_scan(df)

    assert result.dropped["plazo_negativo"] == 1
    assert result.scores[0].deliveries == 3
    assert result.scores[0].avg_lead_time == pytest.approx(33.0)
    assert any("Se descarto 1 fila" in f for f in result.findings)


def test_sin_plazo_declarado_el_qa_dice_que_falta_y_donde_esta():
    df = _receipts(
        [
            ("ACME", "2026-01-01", "2026-02-04", 21.0),
            ("ACME", "2026-02-01", "2026-03-10", 21.0),
            ("ACME", "2026-03-01", "2026-03-29", 21.0),
        ]
    ).drop(columns=["declared_lead_time_days"])
    result = scan.run_lead_time_scan(df)

    assert not result.ok
    assert not result.findings  # QA falla => no hay entregable
    assert any("maestro de articulos" in i for i in result.qa_issues)


def test_menos_de_tres_recepciones_no_es_un_proveedor_medido():
    df = _receipts(
        [
            ("ACME", "2026-01-01", "2026-02-04", 21.0),
            ("ACME", "2026-02-01", "2026-03-10", 21.0),
        ]
    )
    result = scan.run_lead_time_scan(df)

    assert not result.ok
    assert not result.scores
    assert any("recepciones" in i for i in result.qa_issues)


def test_un_export_en_espaniol_con_acentos_se_mide_sin_configurar_nada():
    """El caso real del mercado objetivo: Defontana, Softland, Contpaqi."""
    df = pd.DataFrame(
        [
            {
                "Proveedor": "Rodamientos Andes",
                "Fecha Orden": o,
                "Fecha Recepción": r,
                "Plazo": 21,
                "Código": "RD-6205",
            }
            for o, r in [
                ("2026-01-01", "2026-02-04"),
                ("2026-02-01", "2026-03-10"),
                ("2026-03-01", "2026-03-29"),
            ]
        ]
    )
    result = scan.run_lead_time_scan(df)

    assert result.ok, result.qa_issues
    assert result.scores[0].supplier == "Rodamientos Andes"
    assert result.scores[0].declared_lead_time == 21.0
    assert result.scores[0].avg_lead_time == pytest.approx(33.0)


def test_fechas_en_formatos_mezclados_no_pierden_recepciones_en_silencio():
    """Un export que mezcla ISO con dd/mm/yyyy es normal, no un caso borde.

    `pd.to_datetime` sin `format="mixed"` se queda con el formato que infiere de
    las primeras filas y manda a NaT todo lo demas: la recepcion desaparece del
    promedio sin que nadie se entere.
    """
    df = _receipts(
        [
            ("ACME", "2026-01-01", "2026-02-04", 21.0),
            ("ACME", "2026-02-01", "2026-03-10", 21.0),
            ("ACME", "2026-03-01", "2026-03-29", 21.0),
        ]
    )
    df.loc[2, "order_date"] = "03/01/2026"  # mismo dia, otro formato
    df.loc[2, "receipt_date"] = "03/29/2026"
    result = scan.run_lead_time_scan(df)

    assert result.ok, result.qa_issues
    assert result.dropped["sin_fecha"] == 0
    assert result.scores[0].deliveries == 3


def test_overrides_ganan_cuando_el_automapeo_no_encuentra_la_columna():
    df = _receipts(
        [
            ("ACME", "2026-01-01", "2026-02-04", 21.0),
            ("ACME", "2026-02-01", "2026-03-10", 21.0),
            ("ACME", "2026-03-01", "2026-03-29", 21.0),
        ]
    ).rename(columns={"receipt_date": "Fecha 2"})
    with pytest.raises(ValueError, match="fecha de recepcion"):
        scan.run_lead_time_scan(df)

    result = scan.run_lead_time_scan(df, {"receipt_date": "Fecha 2"})
    assert result.ok, result.qa_issues
    assert result.scores[0].avg_lead_time == pytest.approx(33.0)


def test_el_mini_reporte_dice_no_medido_en_vez_de_cero():
    df = _receipts(
        [
            ("ACME", "2026-01-01", "2026-02-04", 21.0),
            ("ACME", "2026-02-01", "2026-03-10", 21.0),
            ("ACME", "2026-03-01", "2026-03-29", 21.0),
            ("BETA", "2026-01-01", "2026-01-15", 14.0),
            ("BETA", "2026-02-01", "2026-02-15", 14.0),
            ("BETA", "2026-03-01", "2026-03-15", 14.0),
        ]
    )
    result = scan.run_lead_time_scan(df)
    report = scan.render_mini_report(result, dataset_label="recepciones.csv", ts="2026-08-06")

    assert "ACME" in report and "BETA" in report
    # BETA cumple exacto: desviacion 0, brecha 0 -- y eso se imprime como 0, no como vacio.
    assert "| BETA | 3 | 14 | 14 | 0 | 14 | 0 d |" in report
