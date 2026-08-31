"""El archivo del propio cliente como fuente de servicio realizado.

``jobs/intake.py`` normaliza la demanda de un CSV/Excel de cliente, y su esquema canonico
no tenia ninguna columna de servicio: un export que SI trae cantidad entregada o fecha de
entrega las perdia en silencio. O sea que el dato para medir fill rate y OTIF podia estar
en el archivo que el cliente ya mando, y se descartaba antes de llegar al motor.

Eso deja al orquestador sin productor: ``scm_agent/tools.py::_inventory_run`` nunca pasaba
``order_lines``, y no habia de donde sacarlo sin escribir Python a mano.

Lo que se fija aca: que las columnas se reconozcan, que NO se agreguen por periodo (el
servicio es por linea, no por semana), y -- sobre todo -- que el informe diga DE QUE
COLUMNA salio cada numero. Un archivo cuyo header dice ``fecha_despacho`` y otro que dice
``fecha_entrega`` producen el mismo campo con significados opuestos, y esa diferencia vale
6,2 puntos de OTIF medidos (ver ``examples/run_service_report.py``).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from jobs.intake import service_lines


def _csv(tmp: Path, texto: str, nombre: str = "ventas.csv") -> Path:
    ruta = tmp / nombre
    ruta.write_text(texto, encoding="utf-8")
    return ruta


def test_un_archivo_sin_columnas_de_servicio_no_produce_nada(tmp_path: Path):
    """Y ``None``, no un frame vacio: "este cliente no manda servicio" y "manda servicio y
    esta todo en cero" son cosas distintas, y el job las trata distinto."""
    ruta = _csv(tmp_path, "fecha,sku,cantidad\n2026-01-05,A-1,10\n")

    assert service_lines(ruta) is None


def test_reconoce_las_tres_columnas_de_servicio_en_ingles(tmp_path: Path):
    ruta = _csv(
        tmp_path,
        "date,sku,quantity,delivered_qty,promised_date,delivered_date,price\n"
        "2026-01-05,A-1,10,8,2026-01-10,2026-01-12,20\n",
    )

    frame = service_lines(ruta)

    assert list(frame.columns) == [
        "product_id", "ordered_qty", "delivered_qty",
        "promised_date", "delivered_date", "unit_price",
    ]
    fila = frame.iloc[0]
    assert fila["product_id"] == "A-1"
    assert fila["ordered_qty"] == 10.0
    assert fila["delivered_qty"] == 8.0
    assert fila["promised_date"] == "2026-01-10"
    assert fila["delivered_date"] == "2026-01-12"


def test_reconoce_los_encabezados_en_espanol_que_exporta_un_erp_de_latam(tmp_path: Path):
    """La tabla de alias de intake ya es rica en espanol para demanda, y no tenia una sola
    entrada de servicio. Un ERP de LatAm exporta exactamente esto."""
    ruta = _csv(
        tmp_path,
        "fecha,codigo,cantidad,cantidad_entregada,fecha_comprometida,fecha_entrega,precio\n"
        "2026-01-05,A-1,10,8,2026-01-10,2026-01-12,20\n",
    )

    frame = service_lines(ruta)

    assert frame is not None
    assert frame.iloc[0]["delivered_qty"] == 8.0
    assert frame.iloc[0]["promised_date"] == "2026-01-10"


def test_un_archivo_con_solo_una_columna_de_servicio_ya_sirve(tmp_path: Path):
    """La promesa sola habilita OTIF sobre las lineas que ademas tengan entrega. Exigir las
    tres tiraria archivos que si aportan algo."""
    ruta = _csv(
        tmp_path,
        "date,sku,quantity,delivered_qty\n2026-01-05,A-1,10,8\n",
    )

    frame = service_lines(ruta)

    assert frame is not None
    assert frame.iloc[0]["delivered_qty"] == 8.0
    assert frame.iloc[0]["promised_date"] is None


def test_las_lineas_NO_se_agregan_por_periodo(tmp_path: Path):
    """La demanda se agrega a semanas; el servicio NO. Dos pedidos del mismo SKU en la
    misma semana son dos resultados de servicio distintos -- uno pudo servirse entero y el
    otro no -- y sumarlos borra justamente el que fallo."""
    ruta = _csv(
        tmp_path,
        "date,sku,quantity,delivered_qty\n"
        "2026-01-05,A-1,10,10\n"
        "2026-01-07,A-1,10,2\n",
    )

    frame = service_lines(ruta)

    assert len(frame) == 2
    assert sorted(frame["delivered_qty"]) == [2.0, 10.0]


def test_una_celda_vacia_no_viaja_como_cero_ni_puntua(tmp_path: Path):
    """La regla de todo el paquete: una ausencia no es un quiebre.

    Se afirma el SIGNIFICADO y no la identidad, por la misma razon que en
    ``tests/test_connector_service_scan.py``: pandas convierte ``None`` en ``NaN`` al
    meterlo en una columna de floats, asi que ``v is None`` da False sobre el frame aunque
    la ausencia se haya reportado bien. Lo que sostiene el producto es que NO sea 0.0 y que
    el motor la deje afuera -- ``order_fulfillment._number`` filtra por ``math.isfinite``,
    y ese es el unico motivo por el que la conversion de pandas no rompe nada.
    """
    import math as _math

    ruta = _csv(
        tmp_path,
        "date,sku,quantity,delivered_qty\n"
        "2026-01-05,A-1,10,8\n"
        "2026-01-06,A-1,5,\n",
    )

    frame = service_lines(ruta)
    servidas = list(frame["delivered_qty"])

    assert 8.0 in servidas
    ausentes = [v for v in servidas if v is None or (isinstance(v, float) and _math.isnan(v))]
    assert len(ausentes) == 1, "la fila sin cantidad entregada tiene que viajar ausente"
    assert 0.0 not in servidas

    # Y el motor la deja fuera del denominador: 8 de 10, no 8 de 15.
    from src.order_fulfillment import measure_from_order_lines

    medido = measure_from_order_lines("A-1", frame.to_dict("records"))
    assert medido.fill_rate == pytest.approx(0.8)
    assert medido.lines_scored == 1


def test_dice_de_que_columna_del_cliente_salio_cada_campo(tmp_path: Path):
    """Lo que hace auditable al numero, y lo que un CSV necesita mas que un ERP.

    De un adaptador sabemos que significa ``delivered_date`` porque leimos la doc del
    vendor. De un archivo no: un header ``fecha_despacho`` y uno ``fecha_entrega``
    producen el mismo campo con significados opuestos, y entre esos dos hay 6,2 puntos de
    OTIF medidos. Lo unico honesto es decir cual matcheo y dejar que el cliente confirme.
    """
    ruta = _csv(
        tmp_path,
        "fecha,codigo,cantidad,cantidad_entregada,fecha_despacho\n"
        "2026-01-05,A-1,10,8,2026-01-12\n",
    )

    frame = service_lines(ruta)

    assert frame.attrs["service_headers"]["delivered_date"] == "fecha_despacho"
    assert frame.attrs["service_headers"]["delivered_qty"] == "cantidad_entregada"
    assert "fecha_despacho" in frame.attrs["service_note"]


def test_la_nota_advierte_que_no_se_sabe_si_es_despacho_o_llegada(tmp_path: Path):
    """El caveat que un ERP no necesita y un archivo si. Ninguno de los tres backends del
    canal registra la llegada, asi que lo mas probable es que la columna del cliente sea un
    despacho -- pero probable no es sabido, y el entregable no puede afirmarlo."""
    ruta = _csv(
        tmp_path,
        "date,sku,quantity,delivered_qty,delivered_date\n2026-01-05,A-1,10,8,2026-01-12\n",
    )

    nota = service_lines(ruta).attrs["service_note"]

    assert "despacho" in nota.lower() and "llegada" in nota.lower()


def test_un_archivo_sin_las_columnas_obligatorias_de_demanda_no_produce_servicio(tmp_path: Path):
    """Sin ``product_id`` no hay a que SKU atribuirle el servicio. Devolver filas sueltas
    seria peor que no devolver nada."""
    ruta = _csv(tmp_path, "fecha,cantidad,delivered_qty\n2026-01-05,10,8\n")

    assert service_lines(ruta) is None


def test_el_frame_entra_directo_al_job_de_inventario(tmp_path: Path):
    """La prueba de que los eslabones encajan: el archivo del cliente -> el job -> servicio
    OBSERVADO en el reporte. Antes de esto, order_lines era None en cada corrida."""
    from jobs.intake import prepare
    from jobs.inventory_optimization import run as run_inventory

    ruta = _csv(
        tmp_path,
        "date,sku,quantity,delivered_qty,promised_date,delivered_date,price\n"
        + "".join(
            f"2026-01-{d:02d},A-1,10,{8 if d == 1 else 10},2026-01-{d + 3:02d},"
            f"2026-01-{d + 3:02d},20\n"
            for d in range(1, 11)
        ),
    )
    demanda = prepare(ruta, period="D")

    reporte = run_inventory(demanda, order_lines=service_lines(ruta), period_freq="D")

    rec = next(r for r in reporte.recommendations if r.product_id == "A-1")
    assert rec.realized_service is not None
    assert rec.realized_service.provenance == "observed"
    assert rec.realized_service.fill_rate == pytest.approx(0.98)
