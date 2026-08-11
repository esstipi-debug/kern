"""La plata perdida se rotula segun COMO fue valuada, no siempre como ingreso.

``lost_sales_value`` no tiene una sola base de valuacion. ``measure_from_order_lines``
valora el faltante al ``unit_price`` de la linea -- eso SI es ingreso no capturado.
``from_reported_fill_rate`` lo valora al ``unit_cost``, y su propio
``METHOD_REPORTED_FILL_RATE`` lo dice con todas las letras ("valued at mean unit
cost"). Y ``aggregate`` puede sumar registros de las dos procedencias en un mismo
total (``mixed``).

El deck imprimia "of revenue not captured" para las tres. Con procedencia
``client_reported`` eso presenta un COSTO al cliente rotulado como INGRESO: dos
numeros distintos, con margen de por medio, bajo el mismo nombre. Es el mismo tipo
de defecto que ``src/order_fulfillment.py`` fue escrito para impedir -- dos numeros
que no son lo mismo llegando al cliente bajo un nombre unico -- solo que en la
etiqueta del dinero en vez de en la del servicio.

La regla que estos tests fijan: la etiqueta sigue a la procedencia.
"""
from __future__ import annotations

from jobs.inventory_deliverable import build
from jobs.inventory_optimization import JobReport, SkuRecommendation
from src import order_fulfillment as of

_PARAMS = {"service_level": 0.95, "holding_rate": 0.25, "order_cost": 75.0,
           "periods_per_year": 52.0}


def _rec(pid: str, servicio: of.RealizedService) -> SkuRecommendation:
    return SkuRecommendation(
        product_id=pid, method="ses", intermittent=False, forecast=10.0, error_std=2.0,
        bias=0.0, mae=1.5, policy_kind="(s, Q)", order_quantity=50.0, order_up_to=None,
        reorder_point=30.0, safety_stock=12.0, z_factor=1.64, service_level=0.95,
        unit_cost=8.0, lead_periods=2.0, cycle_investment=6000.0, ss_investment=4000.0,
        investment=10000.0, status="ok", realized_service=servicio,
    )


def _reporte(*servicios: of.RealizedService) -> JobReport:
    recs = [_rec(f"SKU-{i}", s) for i, s in enumerate(servicios)]
    return JobReport(
        recommendations=recs, params=_PARAMS, requested_investment=10000.0,
        cycle_floor=6000.0, final_investment=10000.0, safety_stock_scale=1.0,
        feasible=True, budget=None, n_skus=len(recs), n_at_risk=0, n_intermittent=0,
    )


def _brecha(report: JobReport):
    return next(f for f in build(report).findings if "did not serve" in f.title)


_POR_LINEAS = of.measure_from_order_lines(
    "SKU-0", [{"ordered_qty": 100.0, "delivered_qty": 80.0, "unit_price": 9.0}]
)
_REPORTADO = of.from_reported_fill_rate("SKU-0", 0.80, shipped_units=800.0, unit_cost=5.0)


def test_una_tasa_reportada_por_el_cliente_no_se_rotula_como_ingreso():
    """El caso que llegaba mal al deck: 200 unidades a costo 5.00 son 1000.00 de
    COSTO. Presentarlos como ingreso no capturado infla la perdida por el margen."""
    brecha = _brecha(_reporte(_REPORTADO))

    assert "$1,000.00" in brecha.impact          # el numero no cambia
    # Lo que se prohibe es la AFIRMACION, no la palabra: la etiqueta puede (y
    # conviene que) nombre a "revenue" para negarlo explicitamente. Un
    # "revenue" not in ... castigaria justo la version mas clara.
    assert "of revenue not captured" not in brecha.impact.lower(), brecha.impact
    assert "valued at unit cost" in brecha.impact.lower(), brecha.impact


def test_medido_desde_lineas_de_pedido_si_es_ingreso_no_capturado():
    """El control: valuado al precio de la linea, "revenue" es la palabra correcta y
    tiene que seguir estando. El fix no puede borrar la etiqueta, solo hacerla seguir
    a la procedencia."""
    brecha = _brecha(_reporte(_POR_LINEAS))

    assert "$180.00" in brecha.impact           # 20 unidades a 9.00
    assert "revenue" in brecha.impact.lower(), brecha.impact


def test_un_total_mixto_declara_que_mezcla_precio_y_costo():
    """``aggregate`` suma las dos bases en un solo total. El deck no puede elegir una
    de las dos y callarse la otra: dice que el total es mixto."""
    reporte = _reporte(_POR_LINEAS, _REPORTADO)
    servicio = of.aggregate([_POR_LINEAS, _REPORTADO])
    assert servicio.provenance == of.PROVENANCE_MIXED   # el escenario es el que creo

    brecha = _brecha(reporte)
    assert "$1,180.00" in brecha.impact                 # 180 a precio + 1000 a costo
    texto = brecha.impact.lower()
    assert "of revenue not captured" not in texto, brecha.impact
    assert "selling price" in texto and "unit cost" in texto, brecha.impact


def test_toda_procedencia_tiene_etiqueta_y_ninguna_revienta():
    """Sin KeyError y sin etiqueta vacia para ninguna procedencia declarada.

    Las CINCO: el bucle listaba cuatro y se salteaba ``model_implied``, que el
    docstring de ``src/order_fulfillment.py`` declara como una de las procedencias
    que un ``RealizedService`` lleva. Justo la que mas importa rotular.
    """
    from jobs import inventory_deliverable as inv

    for procedencia in (of.PROVENANCE_OBSERVED, of.PROVENANCE_CLIENT_REPORTED,
                        of.PROVENANCE_MIXED, of.PROVENANCE_NOT_MEASURED,
                        of.PROVENANCE_MODEL_IMPLIED):
        assert inv._LOST_VALUE_BASIS[procedencia].strip()
        assert inv._ORIGIN_TEXT[procedencia].strip()


# -- una procedencia por test, y el deck armado de verdad ---------------------
#
# El bucle de arriba mira las tablas. Estos arman el Deliverable ENTERO, que es
# donde estaba el KeyError: ``build()`` toca ``_ORIGIN_TEXT`` una segunda vez
# fuera del hallazgo (para los KPI y las fuentes de dato), y una tabla completa no
# prueba que las dos lecturas la usen.


def _servicio_a_mano(procedencia: str, metodo: str) -> of.RealizedService:
    """Un registro con procedencia elegida a mano. Igual que los que arma un
    llamador viejo o un puente desde ``src/fill_rate.py``: el dataclass es publico
    y su ``provenance`` es un campo, no una deduccion."""
    return of.RealizedService(
        product_id="SKU-0", fill_rate=0.90, otif=0.88, days_out_of_stock=None,
        lost_sales_units=20.0, lost_sales_value=180.0,
        provenance=procedencia, method=metodo,
        units_ordered=200.0, units_delivered=180.0,
    )


def test_procedencia_observed_arma_el_deck():
    entregable = build(_reporte(_POR_LINEAS))

    assert entregable.to_markdown().isascii()
    assert "measured here from customer order lines" in entregable.to_markdown()


def test_procedencia_client_reported_arma_el_deck():
    entregable = build(_reporte(_REPORTADO))

    assert entregable.to_markdown().isascii()
    assert "reported by the client" in entregable.to_markdown()


def test_procedencia_not_measured_arma_el_deck():
    """Sin ningun ``realized_service`` el portfolio cae en ``not_measured``."""
    sin_servicio = JobReport(
        recommendations=[_rec("SKU-0", None)], params=_PARAMS, requested_investment=10000.0,
        cycle_floor=6000.0, final_investment=10000.0, safety_stock_scale=1.0,
        feasible=True, budget=None, n_skus=1, n_at_risk=0, n_intermittent=0,
    )

    md = build(sin_servicio).to_markdown()

    assert md.isascii()
    assert "no order lines and no fill rate reported by the client" in md


def test_procedencia_model_implied_arma_el_deck_y_se_declara_como_modelo():
    """El bug: ``build()`` moria con ``KeyError: 'model_implied'``.

    Y el arreglo no puede ser una etiqueta cualquiera. ``model_implied`` es la beta
    que una politica dimensionada IMPLICA (``src/fill_rate.py``); es lo unico que
    el deck no puede presentar como servicio recibido, asi que la etiqueta tiene
    que negarlo explicitamente y la plata no puede salir rotulada como ingreso.
    """
    servicio = _servicio_a_mano(
        of.PROVENANCE_MODEL_IMPLIED, "beta implied by the sized policy (src/fill_rate.py)"
    )

    entregable = build(_reporte(servicio))       # antes: KeyError
    md = entregable.to_markdown()

    assert md.isascii()
    texto = _brecha(_reporte(servicio)).impact.lower()
    assert "$180.00" in _brecha(_reporte(servicio)).impact
    assert "of revenue not captured" not in texto, texto
    assert "policy" in texto and "not a loss anyone observed" in texto, texto
    # Y la misma negacion tiene que llegar a los KPI, que leen la tabla aparte.
    assert "not service any customer received" in md


def test_una_procedencia_que_el_deck_no_conoce_no_suprime_el_entregable():
    """Una quinta procedencia futura no puede voltear el deck con un ``KeyError``,
    y tampoco puede inventarle una base de valuacion: nombra que no la conoce."""
    servicio = _servicio_a_mano("procedencia_que_no_existe_todavia", "metodo nuevo")

    md = build(_reporte(servicio)).to_markdown()

    assert md.isascii()
    assert "no wording for" in md or "unstated basis" in md
