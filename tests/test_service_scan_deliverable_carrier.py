"""El entregable cuando la fecha de entrega salio de un transportista.

Dos afirmaciones del deck dejan de ser ciertas al empalmar, y las dos son del tipo que nadie
revisa porque estaban bien escritas cuando se escribieron:

* el KPI *Lines scored* se describe como "la evidencia sobre la que descansan TODOS los
  numeros de arriba", y con cobertura parcial deja de serlo para el OTIF;
* la recomendacion #1 le pide al cliente confirmar que significa su fecha de entrega --
  exactamente lo que el empalme ya contesto.
"""

from __future__ import annotations

from jobs import service_scan_deliverable as deck
from jobs.service_scan_job import ServiceScanReport
from src import order_fulfillment as of


def _reporte(*, arrival_coverage=None, lines_scored=100, lines_otif_scored=100):
    return ServiceScanReport(
        source_name="ERP de prueba" + (" + Transportista" if arrival_coverage is not None else ""),
        records=(),
        portfolio=of.RealizedService(
            product_id="portfolio", fill_rate=0.95, otif=0.9, days_out_of_stock=3,
            lost_sales_units=12.0, lost_sales_value=240.0,
            provenance="observed", method="order_lines",
            order_lines=lines_scored, lines_scored=lines_scored,
            lines_otif_scored=lines_otif_scored,
        ),
        arrival_coverage=arrival_coverage,
    )


def _kpi(entregable, nombre):
    return next(k for k in entregable.kpis if k.name == nombre)


def test_sin_empalme_el_deck_pide_confirmar_que_significa_la_fecha():
    """El regresor: lo que el deck dice hoy tiene que seguir diciendolo cuando no hay
    transportista, porque ahi la pregunta sigue abierta."""
    entregable = deck.build(_reporte())

    recomendaciones = " ".join(entregable.recommendations).lower()
    assert "dispatch" in recomendaciones and "arrival" in recomendaciones


def test_con_empalme_ya_no_pide_confirmar_lo_que_el_transportista_contesto():
    """Gastar la recomendacion #1 en una pregunta resuelta empuja hacia abajo la unica que
    el cliente tenia que accionar."""
    entregable = deck.build(_reporte(arrival_coverage=0.8))

    primera = entregable.recommendations[0].lower()
    assert "confirm with the operator what the backend's delivery date records" not in primera


def test_con_empalme_el_deck_dice_que_el_otif_es_de_llegada_y_sobre_cuanto():
    """El numero que diferencia a este producto tiene que estar dicho, no deducible."""
    entregable = deck.build(_reporte(arrival_coverage=0.8))

    texto = " ".join(entregable.recommendations) + entregable.summary
    assert "80%" in texto
    assert "arrival" in texto.lower()


def test_el_kpi_de_evidencia_separa_la_del_otif_cuando_no_coincide():
    """``Lines scored`` es el denominador del fill rate. Con cobertura parcial el OTIF
    descansa en menos lineas, y el deck no tenia forma de decirlo: un lector veia 100 y
    creia que los dos numeros pesaban lo mismo."""
    entregable = deck.build(
        _reporte(arrival_coverage=0.2, lines_scored=100, lines_otif_scored=20)
    )

    etiquetas = {k.name for k in entregable.kpis}
    assert "Lines scored" in etiquetas
    evidencia_otif = [
        k for k in entregable.kpis
        if k.name != "Lines scored" and "scored" in k.name.lower()
    ]
    assert evidencia_otif, etiquetas
    assert evidencia_otif[0].value == "20"


def test_sin_divergencia_no_se_agrega_un_kpi_de_mas():
    """Cuando los dos denominadores coinciden -- que es todo scan sin transportista -- un
    KPI extra es ruido que compite por la atencion con los cuatro que importan."""
    entregable = deck.build(_reporte(lines_scored=100, lines_otif_scored=100))

    assert len(entregable.kpis) == 4
    assert "evidence every number above rests on" in _kpi(entregable, "Lines scored").rationale


def test_cuando_divergen_el_kpi_de_lines_scored_deja_de_prometer_todos_los_numeros():
    """La afirmacion vieja se vuelve falsa, y dejarla es peor que no tener el KPI."""
    entregable = deck.build(
        _reporte(arrival_coverage=0.2, lines_scored=100, lines_otif_scored=20)
    )

    assert "every number above" not in _kpi(entregable, "Lines scored").rationale


def test_la_cobertura_de_llegada_viaja_a_la_hoja_de_fuentes():
    """Va en ``data_sources`` y no en ``findings``: un finding es un problema del negocio
    del cliente, y esto es un limite de la medicion."""
    entregable = deck.build(_reporte(arrival_coverage=0.8))

    fuentes = " ".join(f"{d.field} {d.source}" for d in entregable.data_sources)
    assert "80%" in fuentes
