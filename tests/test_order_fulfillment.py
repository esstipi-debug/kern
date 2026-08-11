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
