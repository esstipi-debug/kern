"""El caveat de la fecha de entrega tiene que llegar al ENTREGABLE, no solo al resumen.

#315 puso la nota en el ``summary`` que lee el OPERADOR. El cliente que abre el Excel o el
informe no la ve -- y es justamente el numero mas delicado del entregable: un OTIF cuya
fecha, cuando sale de un archivo, puede ser el despacho del deposito o la llegada al
cliente. Entre las dos lecturas hay 6,2 puntos medidos sobre 98.666 pedidos reales
(``examples/run_service_report.py``).

Un numero que el cliente no puede reconciliar contra su propio sistema es un numero que no
usa. La nota va donde va la procedencia: ``Deliverable.data_sources``, que se renderiza en
el markdown Y en una hoja del Excel.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from jobs import inventory_deliverable
from jobs.inventory_optimization import run as run_inventory

_NOTA = (
    "Service measured from the file's own columns: delivered_date <- 'fecha_despacho'. "
    "IMPORTANT: from a file there is no way to know whether that date is the DISPATCH from "
    "the warehouse or the ARRIVAL at the customer."
)


def _demanda() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"date": f"2026-01-{d:02d}", "product_id": "A-1", "quantity": 10.0,
             "unit_cost": 12.0}
            for d in range(1, 15)
        ]
    )


def _lineas(*, con_nota: bool) -> pd.DataFrame:
    frame = pd.DataFrame(
        [
            {"product_id": "A-1", "ordered_qty": 10.0,
             "delivered_qty": 8.0 if d == 1 else 10.0,
             "promised_date": f"2026-01-{d:02d}", "delivered_date": f"2026-01-{d:02d}",
             "unit_price": 20.0}
            for d in range(1, 15)
        ]
    )
    if con_nota:
        frame.attrs["service_note"] = _NOTA
    return frame


def test_el_reporte_lleva_la_nota_de_servicio():
    """Primer eslabon: la nota vive en ``.attrs`` del frame de entrada, y el deck no ve el
    frame -- ve el ``JobReport``. Sin este paso no hay forma de que llegue."""
    reporte = run_inventory(_demanda(), order_lines=_lineas(con_nota=True), period_freq="D")

    assert reporte.service_note == _NOTA


def test_sin_nota_el_reporte_no_inventa_una():
    reporte = run_inventory(_demanda(), order_lines=_lineas(con_nota=False), period_freq="D")

    assert reporte.service_note == ""


def test_sin_order_lines_tampoco():
    reporte = run_inventory(_demanda(), period_freq="D")

    assert reporte.service_note == ""


def test_el_deck_publica_la_nota_como_procedencia_y_no_como_hallazgo():
    """``findings`` son problemas del negocio del cliente; esto es de donde salio un numero.
    Meterlo entre los hallazgos lo hace leer como un defecto de su operacion en vez de como
    un limite de la medicion."""
    reporte = run_inventory(_demanda(), order_lines=_lineas(con_nota=True), period_freq="D")

    deck = inventory_deliverable.build(reporte, client="ACME")

    fuentes = {d.field: d.source for d in deck.data_sources}
    assert any("despacho" in s or "DISPATCH" in s for s in fuentes.values())
    assert not any("DISPATCH" in f.detail for f in deck.findings)


def test_la_nota_nombra_la_columna_del_cliente():
    """Lo que la hace auditable: el cliente tiene que poder mirar SU archivo y confirmar."""
    reporte = run_inventory(_demanda(), order_lines=_lineas(con_nota=True), period_freq="D")

    deck = inventory_deliverable.build(reporte, client="ACME")

    assert any("fecha_despacho" in d.source for d in deck.data_sources)


def test_sin_nota_no_se_agrega_una_fila_vacia_al_mapa_de_fuentes():
    """Ruido en cada corrida es como se deja de leer el mapa de fuentes."""
    reporte = run_inventory(_demanda(), order_lines=_lineas(con_nota=False), period_freq="D")

    deck = inventory_deliverable.build(reporte, client="ACME")

    assert all(d.source.strip() for d in deck.data_sources)
    assert not any("IMPORTANT" in d.source for d in deck.data_sources)


def test_la_nota_llega_al_ARCHIVO_que_abre_el_cliente(tmp_path: Path):
    """La unica prueba que importa de verdad: no que este en un objeto, que este en el
    entregable escrito. ``data_sources`` se renderiza en el markdown y en una hoja del
    Excel, asi que el caveat viaja por los dos."""
    reporte = run_inventory(_demanda(), order_lines=_lineas(con_nota=True), period_freq="D")
    deck = inventory_deliverable.build(reporte, client="ACME")

    escritos = deck.write_all(tmp_path)

    markdown = Path(escritos["report"]).read_text(encoding="utf-8")
    assert "fecha_despacho" in markdown
    assert "DISPATCH" in markdown or "ARRIVAL" in markdown


def test_la_nota_del_archivo_del_cliente_esta_en_ingles_como_el_resto_del_deck():
    """Correccion de #315, que la escribio en espanol y la inyectaba en un resumen y un deck
    que son en ingles por defecto. El deck tiene i18n (``src/i18n.py``) para producir una
    version en espanol entera; mezclar los dos idiomas dentro de un mismo entregable no es
    eso, es un descuido."""
    from jobs.intake import _NOTA_SERVICIO

    assert "IMPORTANT" in _NOTA_SERVICIO
    assert "DISPATCH" in _NOTA_SERVICIO and "ARRIVAL" in _NOTA_SERVICIO


def test_el_deck_sigue_diciendo_de_donde_sale_el_servicio_medido():
    """La fila que ya existia no se pisa: sigue estando el origen y el metodo."""
    reporte = run_inventory(_demanda(), order_lines=_lineas(con_nota=True), period_freq="D")

    deck = inventory_deliverable.build(reporte, client="ACME")

    campos = [d.field for d in deck.data_sources]
    assert "Realized fill rate / OTIF / lost sales" in campos
    assert len(campos) == len(set(campos)), "cada fila del mapa de fuentes es un campo distinto"


@pytest.mark.parametrize("con_nota", [True, False])
def test_el_deck_se_construye_igual_con_y_sin_nota(con_nota: bool):
    """La nota es un extra. Si falta, el entregable sale igual."""
    reporte = run_inventory(_demanda(), order_lines=_lineas(con_nota=con_nota), period_freq="D")

    deck = inventory_deliverable.build(reporte, client="ACME")

    assert deck.summary
    assert deck.data_sources
