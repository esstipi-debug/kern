"""Tests for src/order_fulfillment.py - the service the client ACTUALLY got.

The defect under test is that Kern reported the service its model targeted and
called it service. So beyond the arithmetic, these check the two things that keep
the two numbers apart: every record says where it came from (``provenance``), and
a criterion nobody could measure is ``None``, never a confident zero.
"""
from __future__ import annotations

from datetime import date

import pytest

from src import order_fulfillment as of


def _line(ordered, delivered, *, promised=None, shipped=None, price=None):
    line = {"ordered_qty": ordered, "delivered_qty": delivered}
    if promised is not None:
        line["promised_date"] = promised
    if shipped is not None:
        line["delivered_date"] = shipped
    if price is not None:
        line["unit_price"] = price
    return line


def test_fill_rate_is_units_delivered_over_units_ordered():
    # Arrange
    lines = [_line(100, 80), _line(100, 100), _line(50, 20)]

    # Act
    got = of.measure_from_order_lines("SKU-A", lines)

    # Assert
    assert got.units_ordered == 250.0
    assert got.units_delivered == 200.0
    assert got.fill_rate == pytest.approx(0.80)
    assert got.provenance == of.PROVENANCE_OBSERVED


def test_over_delivery_on_one_line_does_not_repay_a_short_line():
    """Shipping 120 against an order of 100 does not make the next line's shortfall
    disappear - an uncapped sum would report a fill rate above 1."""
    got = of.measure_from_order_lines("SKU-A", [_line(100, 120), _line(100, 50)])

    assert got.fill_rate == pytest.approx(0.75)
    assert got.lost_sales_units == pytest.approx(50.0)


def test_otif_needs_both_dates_and_a_complete_line():
    """Complete AND on time. A line that is complete but late does not count."""
    lines = [
        _line(10, 10, promised=date(2026, 1, 5), shipped=date(2026, 1, 5)),   # OTIF
        _line(10, 10, promised=date(2026, 1, 5), shipped=date(2026, 1, 9)),   # late
        _line(10, 4, promised=date(2026, 1, 6), shipped=date(2026, 1, 6)),    # short
        _line(10, 10),                                                        # no dates
    ]

    got = of.measure_from_order_lines("SKU-A", lines)

    assert got.otif == pytest.approx(1 / 3)  # 3 dated lines, not 4
    assert got.lines_scored == 4


def test_lines_without_quantities_stay_out_of_the_denominator():
    """The whole doctrine of src/supplier_scorecard.py: never divide by len(lines)."""
    lines = [_line(100, 90), {"ordered_qty": None, "delivered_qty": None},
             {"ordered_qty": "n/a", "delivered_qty": 5}]

    got = of.measure_from_order_lines("SKU-A", lines)

    assert got.order_lines == 3
    assert got.lines_scored == 1
    assert got.fill_rate == pytest.approx(0.90)


def test_nothing_evaluable_is_not_measured_rather_than_zero():
    got = of.measure_from_order_lines("SKU-A", [{"ordered_qty": None}])

    assert got.provenance == of.PROVENANCE_NOT_MEASURED
    assert got.fill_rate is None
    assert got.otif is None
    assert got.days_out_of_stock is None
    assert got.lost_sales_units is None
    assert got.order_lines == 1


def test_stockout_days_count_distinct_dates_with_a_short_line():
    lines = [
        _line(10, 4, promised=date(2026, 2, 1)),
        _line(10, 0, promised=date(2026, 2, 1)),   # same day, counted once
        _line(10, 3, promised=date(2026, 2, 4)),
        _line(10, 10, promised=date(2026, 2, 5)),  # served, not a stockout day
    ]

    got = of.measure_from_order_lines("SKU-A", lines)

    assert got.days_out_of_stock == 2
    assert "lower bound" in got.method


def test_lines_without_any_date_do_not_report_zero_stockout_days():
    """Regresion: sin una sola fecha se reportaba 0 dias de quiebre con faltante real.

    ``short_dates`` queda vacio cuando ninguna linea trae fecha, y ``len()`` de
    eso es 0: exactamente el error que el docstring del modulo nombra, convertir
    "nunca lo medi" en un 0 confiado. Sin fechas no hay evidencia de dias, y la
    respuesta honesta es ``None``.
    """
    got = of.measure_from_order_lines("SKU-A", [_line(100, 40, price=10.0)])

    assert got.lost_sales_units == pytest.approx(60.0)   # el faltante SI se midio
    assert got.days_out_of_stock is None                 # los dias, no
    assert got.method == of.METHOD_ORDER_LINES_UNDATED
    assert "lower bound" not in got.method


def test_zero_stockout_days_is_a_real_measurement_when_the_lines_carried_dates():
    """El otro lado: con fechas y sin faltante, 0 dias es un hecho observado."""
    got = of.measure_from_order_lines(
        "SKU-A", [_line(10, 10, promised=date(2026, 2, 1), shipped=date(2026, 2, 1))]
    )

    assert got.days_out_of_stock == 0
    assert "lower bound" in got.method


def test_explicit_stock_zero_history_wins_over_the_lower_bound():
    """The client's own stock-zero count sees the days nobody ordered on."""
    lines = [_line(10, 4, promised=date(2026, 2, 1))]

    got = of.measure_from_order_lines("SKU-A", lines, stockout_days=9)

    assert got.days_out_of_stock == 9
    assert got.method == of.METHOD_ORDER_LINES_STOCK_HISTORY


def test_iso_text_dates_are_accepted_and_junk_dates_are_refused():
    ok = of.measure_from_order_lines(
        "SKU-A", [_line(10, 10, promised="2026-03-02", shipped="2026-03-01")]
    )
    junk = of.measure_from_order_lines(
        "SKU-A", [_line(10, 10, promised="whenever", shipped="2026-03-01")]
    )

    assert ok.otif == pytest.approx(1.0)
    assert junk.otif is None  # refused, not scored as on time


def test_lost_sales_are_valued_at_the_price_of_the_line_that_went_short():
    lines = [_line(100, 60, price=5.0), _line(100, 90, price=2.0)]

    got = of.measure_from_order_lines("SKU-A", lines)

    assert got.lost_sales_units == pytest.approx(50.0)
    assert got.lost_sales_value == pytest.approx(40 * 5.0 + 10 * 2.0)
    assert got.lines_priced == 2


def test_lost_sales_value_is_none_when_no_line_carried_a_price():
    got = of.measure_from_order_lines("SKU-A", [_line(100, 60)])

    assert got.lost_sales_units == pytest.approx(40.0)
    assert got.lost_sales_value is None


def test_reported_fill_rate_is_carried_through_exactly():
    """It is the client's measurement. Re-deriving it would replace it with ours."""
    got = of.from_reported_fill_rate("SKU-A", 0.6, shipped_units=600.0, unit_cost=12.0)

    assert got.fill_rate == 0.6
    assert got.provenance == of.PROVENANCE_CLIENT_REPORTED


def test_reported_fill_rate_reconstructs_the_censored_demand():
    """beta = served / asked, so asked = served / beta and the gap is the lost sale."""
    got = of.from_reported_fill_rate("SKU-A", 0.8, shipped_units=800.0, unit_cost=10.0)

    assert got.units_ordered == pytest.approx(1000.0)
    assert got.units_delivered == pytest.approx(800.0)
    assert got.lost_sales_units == pytest.approx(200.0)
    assert got.lost_sales_value == pytest.approx(2000.0)


def test_a_zero_unit_cost_values_the_lost_sale_at_zero_instead_of_not_measured():
    """Regresion: ``and unit_cost`` trataba un costo 0.0 como costo ausente.

    ``_check_reported`` solo rechaza costos negativos, asi que 0.0 es un valor
    legal (muestra gratis, promocion, insumo regalado). Con truthiness caia en la
    rama ``None`` y la venta perdida salia "no medida" cuando en realidad se midio
    y vale cero.
    """
    got = of.from_reported_fill_rate("SKU-A", 0.8, shipped_units=800.0, unit_cost=0.0)

    assert got.lost_sales_units == pytest.approx(200.0)
    assert got.lost_sales_value == pytest.approx(0.0)
    assert got.lost_sales_value is not None


def test_an_absent_unit_cost_still_leaves_the_lost_sale_unvalued():
    """El contrapeso: sin costo no hay valor, y eso sigue siendo ``None``."""
    got = of.from_reported_fill_rate("SKU-A", 0.8, shipped_units=800.0)

    assert got.lost_sales_units == pytest.approx(200.0)
    assert got.lost_sales_value is None


def test_reported_fill_rate_cannot_produce_otif_or_stockout_days():
    """One long outage and many short ones give the same beta. Say so, don't guess."""
    got = of.from_reported_fill_rate("SKU-A", 0.6, shipped_units=600.0, unit_cost=12.0)

    assert got.otif is None
    assert got.days_out_of_stock is None


def test_reported_fill_rate_takes_a_measured_stock_zero_history():
    got = of.from_reported_fill_rate(
        "SKU-A", 0.6, shipped_units=600.0, unit_cost=12.0, stockout_days=4
    )

    assert got.days_out_of_stock == 4
    assert "stock-zero history" in got.method


def test_a_zero_fill_rate_does_not_reconstruct_demand_by_dividing_by_zero():
    got = of.from_reported_fill_rate("SKU-A", 0.0, shipped_units=0.0, unit_cost=12.0)

    assert got.fill_rate == 0.0
    assert got.units_ordered is None
    assert got.lost_sales_units is None
    assert got.method == of.METHOD_REPORTED_FILL_RATE_ZERO


@pytest.mark.parametrize("bad", [1.3, -0.1])
def test_a_fill_rate_outside_zero_one_is_refused(bad):
    with pytest.raises(ValueError, match="fill rate lives in"):
        of.from_reported_fill_rate("SKU-A", bad, shipped_units=100.0)


def test_not_measured_says_so_in_every_field():
    got = of.not_measured("SKU-A")

    assert got.provenance == of.PROVENANCE_NOT_MEASURED
    assert (got.fill_rate, got.otif, got.days_out_of_stock) == (None, None, None)
    assert (got.lost_sales_units, got.lost_sales_value) == (None, None)


def test_aggregate_weights_the_fill_rate_by_units_not_by_sku():
    """A perfectly served four-unit SKU must not offset a badly served big one."""
    small = of.measure_from_order_lines("SMALL", [_line(4, 4)])
    big = of.measure_from_order_lines("BIG", [_line(40000, 16000)])

    got = of.aggregate([small, big])

    assert got.fill_rate == pytest.approx(16004 / 40004)
    assert got.provenance == of.PROVENANCE_OBSERVED


def test_aggregate_sums_the_damage_and_names_the_broken_skus():
    a = of.measure_from_order_lines("A", [_line(10, 4, promised=date(2026, 2, 1), price=3.0)])
    b = of.measure_from_order_lines("B", [_line(10, 10, promised=date(2026, 2, 1), price=3.0)])

    got = of.aggregate([a, b])

    assert got.days_out_of_stock == 1
    assert got.lost_sales_units == pytest.approx(6.0)
    assert got.lost_sales_value == pytest.approx(18.0)
    assert of.skus_out_of_stock([a, b]) == ("A",)


def test_aggregate_of_nothing_measured_is_not_measured():
    got = of.aggregate([of.not_measured("A"), of.not_measured("B")])

    assert got.provenance == of.PROVENANCE_NOT_MEASURED
    assert got.fill_rate is None
    assert got.days_out_of_stock is None
    assert got.lost_sales_value is None


def test_aggregate_flags_a_mixed_roll_up():
    observed = of.measure_from_order_lines("A", [_line(10, 8)])
    reported = of.from_reported_fill_rate("B", 0.9, shipped_units=90.0)

    assert of.aggregate([observed, reported]).provenance == of.PROVENANCE_MIXED


def test_aggregate_of_measured_records_does_not_call_itself_not_measured():
    """Regresion: el caso feliz reportaba "no lo medi".

    ``provenance`` se resolvia con ``origins.pop()``, que VACIA el conjunto, y
    Python evalua los kwargs en orden: cuando le tocaba a ``method`` el conjunto
    ya estaba vacio, asi que un roll-up de SKUs todos observados salia con
    ``METHOD_NOT_MEASURED``. Justo la capacidad que este modulo existe para dar.
    """
    a = of.measure_from_order_lines("A", [_line(10, 8, price=5.0)])
    b = of.measure_from_order_lines("B", [_line(20, 20, price=5.0)])

    got = of.aggregate([a, b])

    assert got.provenance == of.PROVENANCE_OBSERVED
    assert got.method == of.METHOD_AGGREGATE
    assert got.fill_rate == pytest.approx(28 / 30)


def test_aggregate_of_one_record_keeps_both_its_provenance_and_the_roll_up_method():
    """El borde exacto del bug: un solo origen es el unico caso que hacia .pop()."""
    got = of.aggregate([of.from_reported_fill_rate("A", 0.9, shipped_units=90.0)])

    assert got.provenance == of.PROVENANCE_CLIENT_REPORTED
    assert got.method == of.METHOD_AGGREGATE


def test_the_method_field_is_not_named_after_any_metric():
    """A sentence explaining how a number was derived must never be readable as the
    number itself. Guards the field naming this module's honesty rests on."""
    metric_words = ("fill_rate", "otif", "days_out", "stockout_day", "lost_sale", "quiebre")
    assert not any(w in "method" for w in metric_words)
    assert isinstance(of.not_measured("A").days_out_of_stock, type(None))


# -- la promesa que vencio sin que saliera nada ---------------------------------


def test_una_promesa_vencida_sin_nada_despachado_es_un_fallo_de_otif():
    """El peor caso posible tiene que puntuar, y hasta ahora no puntuaba.

    Una linea prometida para el 5 que nunca despacho nada no tiene fecha de entrega -- no
    hubo entrega -- asi que caia fuera del denominador de OTIF por falta de la segunda
    fecha. Resultado: el escenario mas grave (prometimos y no entregamos NADA) se reportaba
    como "no medido" y el OTIF salia mejor de lo que fue.

    La incoherencia es visible dentro del mismo registro: esa linea YA cuenta como faltante
    total en fill rate y en venta perdida. Excluirla solo de OTIF no era prudencia, era una
    inconsistencia.
    """
    lines = [
        _line(10, 10, promised=date(2026, 1, 5), shipped=date(2026, 1, 5)),  # OTIF
        _line(10, 0, promised=date(2026, 1, 5)),                             # nunca salio
    ]

    got = of.measure_from_order_lines("SKU-A", lines, as_of=date(2026, 2, 1))

    assert got.otif == pytest.approx(0.5)
    assert got.fill_rate == pytest.approx(0.5)
    assert got.lost_sales_units == pytest.approx(10.0)


def test_una_promesa_que_todavia_no_vencio_no_es_un_fallo():
    """La trampa que hace falta el ``as_of``. Odoo reporta ``qty_delivered = 0.0`` en toda
    orden CONFIRMADA que todavia no salio, con su fecha comprometida a futuro. Sin este
    corte, cada orden abierta de cada cliente de Odoo pasaria a contar como incumplimiento
    el dia que se mergea este cambio."""
    lines = [_line(10, 0, promised=date(2026, 3, 20))]

    got = of.measure_from_order_lines("SKU-A", lines, as_of=date(2026, 2, 1))

    assert got.otif is None
    # Y tampoco cuenta para fill rate. Esta linea afirmaba lo contrario cuando se escribio
    # el arreglo del OTIF ("sigue contando para fill rate, que es lo correcto"): era una
    # media verdad. Sacarla de OTIF y dejarla como faltante total en fill rate deja al
    # registro contradiciendose -- la misma orden, no vencida para un criterio y fallada
    # para el otro. Una orden en curso no es ni un acierto ni un faltante, en ningun lado.
    assert got.lines_scored == 0
    assert got.lines_in_flight == 1


def test_sin_as_of_no_se_puede_saber_si_vencio_y_la_linea_queda_afuera():
    """Sin fecha de corte no hay forma de saber si la promesa esta vencida o pendiente, y
    adivinar en cualquiera de las dos direcciones inventa un numero. El comportamiento por
    defecto no cambia."""
    got = of.measure_from_order_lines("SKU-A", [_line(10, 0, promised=date(2026, 1, 5))])

    assert got.otif is None


def test_una_promesa_que_vence_EL_ULTIMO_DIA_de_la_ventana_si_se_juzga():
    """La ventana es INCLUSIVA: ``as_of`` es el ultimo dia cubierto, no el primero que
    falta. Una promesa que cae exactamente ahi vencio DENTRO de lo observado y hay que
    juzgarla.

    Una version previa de este test afirmaba lo contrario ("recibe el beneficio de la
    duda"), y era una moneda al aire tirada antes de que la frontera estuviera definida.
    Una vez que ``as_of`` significa "ultimo dia cubierto", diferir esa linea la deja en un
    limbo del que no sale nunca: la proxima lectura tendra una ventana mas larga y la
    juzgara igual, un dia tarde y con el mismo dato.
    """
    got = of.measure_from_order_lines(
        "SKU-A", [_line(10, 0, promised=date(2026, 2, 1))], as_of=date(2026, 2, 1)
    )

    assert got.otif == 0.0
    assert got.lines_in_flight == 0


def test_una_cantidad_servida_NO_MEDIDA_no_puntua_aunque_la_promesa_haya_vencido():
    """El discriminante entero. ``delivered_qty=0.0`` es una medicion -- no salio nada --
    y ``None`` es una ausencia: nadie lo reporto. Un backend que no publica cantidades
    servidas no puede producir fallos de OTIF de la nada solo porque sus promesas sean
    viejas."""
    got = of.measure_from_order_lines(
        "SKU-A",
        [{"ordered_qty": 10, "delivered_qty": None, "promised_date": date(2026, 1, 5)}],
        as_of=date(2026, 2, 1),
    )

    assert got.otif is None


def test_una_entrega_parcial_sin_fecha_sigue_afuera_de_otif():
    """Salio algo pero no se sabe cuando. Puntuarlo como fallo seria correcto por lo
    incompleto y arbitrario por lo temporal; puntuarlo como acierto seria falso. La unica
    lectura honesta de ``salio algo, no se cuando`` es que el tiempo no se midio.

    Solo el CERO es inequivoco: no hay envio, entonces no hay fecha que buscar."""
    got = of.measure_from_order_lines(
        "SKU-A", [_line(10, 4, promised=date(2026, 1, 5))], as_of=date(2026, 2, 1)
    )

    assert got.otif is None
    assert got.fill_rate == pytest.approx(0.4)


# -- la orden que todavia no vencio no es un faltante ----------------------------


def test_una_linea_prometida_a_futuro_no_cuenta_como_faltante():
    """Una orden confirmada que todavia no vencio no es una falla de servicio: es trabajo
    en curso. Contarla castiga al cliente por pedidos que puede servir manana.

    Odoo pone ``qty_delivered = 0.0`` en toda orden confirmada sin despachar, asi que sin
    este corte el fill rate de cualquier cliente de Odoo arrastra su libro abierto entero
    como si fuera quiebre -- y la venta perdida cuenta plata que nadie perdio.
    """
    lines = [
        _line(10, 8, promised=date(2026, 1, 5), shipped=date(2026, 1, 5)),  # vencida, corta
        _line(4, 0, promised=date(2026, 6, 30)),                            # abierta
    ]

    got = of.measure_from_order_lines("SKU-A", lines, as_of=date(2026, 2, 1))

    assert got.fill_rate == pytest.approx(0.8)   # 8 de 10, no 8 de 14
    assert got.lost_sales_units == pytest.approx(2.0)
    assert got.lines_scored == 1
    assert got.lines_in_flight == 1


def test_una_linea_no_vencida_PERO_YA_ENTREGADA_si_cuenta():
    """La evidencia le gana a la simetria, y esto es una correccion de mi propio diseno.

    La primera version de esta regla excluia TODA linea prometida despues de la ventana,
    entregada o no, buscando simetria: sacar solo las pendientes que estan cortas y dejar
    los aciertos anticipados sube el fill rate por construccion, que es sesgo de
    supervivencia de manual.

    Correrla la refuto. Una linea entregada el 20 de enero contra una promesa para junio
    es un HECHO que ya ocurrio; descartar una entrega observada para protegerse de un
    sesgo es el error mas grande, y tiraba servicio real medido a la basura. Rompio seis
    tests legitimos donde las entregas habian pasado de verdad.

    Asi que una linea con despacho -- o ya servida entera -- esta RESUELTA y cuenta. La
    asimetria que queda es real y es el precio de no descartar hechos; se publica en
    ``lines_in_flight`` para que se pueda ver su tamano en vez de discutirla a ciegas.
    """
    lines = [
        _line(10, 8, promised=date(2026, 1, 5), shipped=date(2026, 1, 5)),
        _line(10, 10, promised=date(2026, 6, 30), shipped=date(2026, 1, 20)),  # servida temprano
    ]

    got = of.measure_from_order_lines("SKU-A", lines, as_of=date(2026, 2, 1))

    assert got.fill_rate == pytest.approx(0.9)  # 18 de 20: la entrega temprana es un hecho
    assert got.lines_scored == 2
    assert got.lines_in_flight == 0


def test_sin_promesa_no_hay_forma_de_saber_si_esta_abierta_y_se_sigue_contando():
    """El limite honesto de este arreglo. ``commitment_date`` en Odoo esta en blanco de
    rutina, y sin fecha prometida una linea corta y una orden en curso son indistinguibles.
    Excluirlas todas tiraria la mayor parte de los datos reales; contarlas mantiene el
    comportamiento anterior, que al menos es conocido."""
    got = of.measure_from_order_lines(
        "SKU-A", [_line(10, 0)], as_of=date(2026, 2, 1)
    )

    assert got.fill_rate == pytest.approx(0.0)
    assert got.lines_scored == 1
    assert got.lines_in_flight == 0


def test_sin_ventana_nada_se_considera_en_curso():
    """Sin fecha de corte no se puede saber que vencio. El comportamiento por defecto no
    cambia: se cuenta todo, como antes."""
    got = of.measure_from_order_lines("SKU-A", [_line(4, 0, promised=date(2026, 6, 30))])

    assert got.fill_rate == pytest.approx(0.0)
    assert got.lines_in_flight == 0


def test_una_linea_en_curso_no_aporta_dias_sin_servir_ni_venta_perdida():
    """Los tres numeros se mueven juntos o el registro se contradice a si mismo: una linea
    que no cuenta como faltante tampoco puede aportar un dia de quiebre ni plata perdida."""
    lines = [
        _line(10, 10, promised=date(2026, 1, 5), shipped=date(2026, 1, 5), price=25.0),
        _line(4, 0, promised=date(2026, 6, 30), price=25.0),
    ]

    got = of.measure_from_order_lines("SKU-A", lines, as_of=date(2026, 2, 1))

    assert got.fill_rate == pytest.approx(1.0)
    assert got.lost_sales_units == pytest.approx(0.0)
    assert got.lost_sales_value == pytest.approx(0.0)  # y no 4 x 25 = 100 de plata inventada
    assert got.days_out_of_stock == 0


def test_el_metodo_declara_que_se_dejaron_lineas_en_curso():
    """Un denominador mas chico sin explicacion es un numero que un cliente no puede
    reconciliar contra su propio ERP. El ``method`` tiene que decir que se excluyo."""
    lines = [
        _line(10, 8, promised=date(2026, 1, 5), shipped=date(2026, 1, 5)),
        _line(4, 0, promised=date(2026, 6, 30)),
    ]

    got = of.measure_from_order_lines("SKU-A", lines, as_of=date(2026, 2, 1))

    assert "en curso" in got.method


def test_un_sku_cuyas_ordenes_estan_TODAS_en_curso_dice_eso_y_no_no_medido():
    """"Nada vencio todavia" y "nadie lo midio" son estados distintos, y se veian igual.

    Un SKU cuyo libro entero esta abierto cae con cero lineas puntuadas, que es la misma
    condicion que dispara ``not_measured``. Pero no es lo mismo: en un caso el backend no
    publica servicio, y en el otro lo publica y todavia no hay nada que juzgar. Un cliente
    que acaba de conectar su ERP y ve "no medido" sobre su cartera nueva merece leer por
    que.
    """
    got = of.measure_from_order_lines(
        "SKU-A", [_line(4, 0, promised=date(2026, 6, 30))], as_of=date(2026, 2, 1)
    )

    assert got.provenance == of.PROVENANCE_NOT_MEASURED
    assert got.fill_rate is None
    assert got.lines_in_flight == 1
    assert "en curso" in got.method
