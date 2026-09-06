"""El orquestador reporta servicio medido cuando el archivo del cliente lo trae.

El limite que declaro el PR #308 y que este cierra: ``scm_agent/tools.py::_inventory_run``
nunca pasaba ``order_lines`` al job, asi que la unica entrada capaz de producir un OTIF
saliente llegaba en ``None`` en cada corrida y todos los SKU volvian ``not_measured``.

No hacia falta una tool nueva. Hacia falta un productor, y estaba en el archivo que el
cliente ya manda: ``jobs.intake.service_lines``.

Lo que se fija aca es el camino COMPLETO del agente -- ``prepare -> run`` -- porque el
motor y el productor ya tienen sus propios tests y lo que fallaba era el cable entre ellos.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from nosilo_core.registry import JobRequest

from scm_agent.tools import inventory_tool

_CON_SERVICIO = "date,sku,quantity,delivered_qty,promised_date,delivered_date,price\n" + "".join(
    f"2026-01-{d:02d},A-1,10,{8 if d == 1 else 10},2026-01-{d:02d},2026-01-{d:02d},20\n"
    for d in range(1, 13)
)
_SIN_SERVICIO = "date,sku,quantity,price\n" + "".join(
    f"2026-01-{d:02d},A-1,10,20\n" for d in range(1, 13)
)


def _correr(tmp: Path, contenido: str, **params):
    ruta = tmp / "ventas.csv"
    ruta.write_text(contenido, encoding="utf-8")
    tool = inventory_tool()
    preparado = tool.prepare(
        JobRequest(brief="politica de inventario", data_path=str(ruta),
                   params={"period": "D", **params}),
        None,
    )
    assert preparado.status == "ok", preparado.messages
    return tool.run(preparado.payload, {"period": "D", **params})


def test_el_agente_reporta_servicio_OBSERVADO_si_el_archivo_lo_trae(tmp_path: Path):
    """La corrida que antes era imposible sin escribir Python a mano."""
    producido = _correr(tmp_path, _CON_SERVICIO)

    rec = next(r for r in producido.report.recommendations if r.product_id == "A-1")
    assert rec.realized_service is not None
    assert rec.realized_service.provenance == "observed"
    assert rec.realized_service.fill_rate == pytest.approx(118 / 120)


def test_el_mismo_archivo_SIN_columnas_de_servicio_sigue_sin_medir(tmp_path: Path):
    """El control negativo, y lo que prueba que el de arriba no es casualidad: es
    exactamente la corrida que este proyecto venia haciendo en cada llamada."""
    producido = _correr(tmp_path, _SIN_SERVICIO)

    rec = next(r for r in producido.report.recommendations if r.product_id == "A-1")
    assert rec.realized_service is None or rec.realized_service.provenance == "not_measured"


def test_el_resumen_del_agente_dice_que_midio_servicio_y_de_que_columnas(tmp_path: Path):
    """El resumen es lo que el operador lee antes de abrir el entregable. Si el numero
    aparece sin decir de donde salio, no lo puede reconciliar contra el ERP del cliente."""
    producido = _correr(tmp_path, _CON_SERVICIO)

    assert "service measured" in producido.summary.lower()
    assert "delivered_qty" in producido.summary


def test_el_resumen_advierte_que_la_fecha_puede_ser_despacho_o_llegada(tmp_path: Path):
    """El caveat que un archivo necesita y un adaptador no: de un ERP sabemos que significa
    su columna porque leimos la doc del vendor, y de un CSV no."""
    producido = _correr(tmp_path, _CON_SERVICIO)

    resumen = producido.summary.lower()
    assert "dispatch" in resumen and "arrival" in resumen


def test_sin_servicio_el_resumen_no_habla_de_servicio(tmp_path: Path):
    """Ruido en el resumen de cada corrida es como se deja de leer el resumen."""
    producido = _correr(tmp_path, _SIN_SERVICIO)

    assert "service measured" not in producido.summary.lower()


def test_dos_columnas_de_servicio_de_tres_ya_alcanzan(tmp_path: Path):
    """Exigir las tres tiraria archivos que si aportan algo. Sin cantidad entregada no hay
    fill rate, pero con las dos fechas y una cantidad servida en otra corrida si hay OTIF.

    Este test empezo siendo otro -- pretendia romper la deteccion renombrando UNA columna
    -- y la corrida mostro que el servicio se detectaba igual, correctamente. La conducta
    era la buena; la fixture era la equivocada.
    """
    contenido = _CON_SERVICIO.replace("delivered_qty", "delivered_qty_x")
    producido = _correr(tmp_path, contenido)

    assert producido.report.n_skus == 1
    assert "promised_date" in producido.summary
    assert "delivered_qty <-" not in producido.summary


def test_si_la_deteccion_de_servicio_revienta_la_corrida_sigue(tmp_path: Path, monkeypatch):
    """El servicio es un extra sobre lo que el cliente pidio. Si su deteccion falla, la
    politica de inventario tiene que salir igual: un adorno que rompe el entregable es peor
    que no tenerlo."""
    from scm_agent import tools as _tools

    def _explota(*a, **kw):
        raise ValueError("archivo raro")

    monkeypatch.setattr(_tools.intake, "service_lines", _explota)
    producido = _correr(tmp_path, _CON_SERVICIO)

    assert producido.report.n_skus == 1
    assert "service measured" not in producido.summary.lower()
