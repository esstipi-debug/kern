"""Realized service straight off a connector - the last mile that was missing.

``src/order_fulfillment.py`` measures fill rate, OTIF (SCOR RL.1.1), stockout days
and lost sales from order lines carrying five keys: ``ordered_qty``,
``delivered_qty``, ``promised_date``, ``delivered_date`` and ``unit_price``. The
canonical ``OrderLine`` every connector emits carried three of them and no date at
all, so for every live backend the engine had nothing to score and answered
``not_measured`` no matter how much order history the client had. The motor was
built and unreachable.

These tests pin the widened DTO, the bridge that feeds the engine, and - the whole
point of the change - that a delivery figure nobody reported stays an absence
instead of becoming a confident zero. Squashing an unreported ``delivered_qty`` to
0.0 would turn every un-instrumented line into a total stockout and hand a client a
0% fill rate; defaulting it to the ordered quantity would hand them a perfect 100%.
Both are the failure mode ``order_fulfillment``'s provenance discipline exists to
prevent, and both look identical to a green test suite that never asserts on it.
"""

import pytest

from src import order_fulfillment as of
from src.connectors import Order, OrderLine, realized_service, service_frame, service_lines


def _order(order_id: str, created_at: str, *lines: OrderLine) -> Order:
    return Order(order_id, created_at, tuple(lines))


# -- the canonical DTO --------------------------------------------------------


def test_order_line_still_builds_from_three_positional_fields():
    """Every existing caller passes (sku, quantity, price) positionally."""
    line = OrderLine("SKU-1", 3.0, 20.0)

    assert (line.sku, line.quantity, line.price) == ("SKU-1", 3.0, 20.0)
    assert line.delivered_qty is None
    assert line.promised_date is None
    assert line.delivered_date is None
    # and an unwidened line still equals a widened one that reported nothing
    assert line == OrderLine("SKU-1", 3.0, 20.0, None, None, None)


def test_order_line_carries_the_service_side_when_the_backend_reports_it():
    line = OrderLine("SKU-1", 10.0, 20.0, 8.0, "2026-01-10", "2026-01-12")

    assert line.delivered_qty == 8.0
    assert (line.promised_date, line.delivered_date) == ("2026-01-10", "2026-01-12")


# -- the bridge ---------------------------------------------------------------


def test_service_lines_groups_by_sku_in_the_engines_key_names():
    orders = [
        _order("o1", "2026-01-05", OrderLine("SKU-1", 3.0, 20.0, 3.0, "2026-01-07", "2026-01-07")),
        _order("o2", "2026-01-06", OrderLine("SKU-2", 1.0, 50.0, 0.0, "2026-01-08", "2026-01-09")),
        _order("o3", "2026-01-07", OrderLine("SKU-1", 5.0, 20.0, 4.0, "2026-01-10", "2026-01-11")),
    ]

    grouped = service_lines(orders)

    assert set(grouped) == {"SKU-1", "SKU-2"}
    assert len(grouped["SKU-1"]) == 2
    assert grouped["SKU-2"] == [
        {
            "ordered_qty": 1.0,
            "delivered_qty": 0.0,
            "promised_date": "2026-01-08",
            "delivered_date": "2026-01-09",
            "unit_price": 50.0,
        }
    ]


def test_service_lines_keys_are_exactly_what_the_engine_reads():
    """A renamed key here is a silent ``not_measured``, not a failure."""
    grouped = service_lines([_order("o1", "2026-01-05", OrderLine("SKU-1", 3.0, 20.0, 3.0))])

    assert set(grouped["SKU-1"][0]) == {
        "ordered_qty",
        "delivered_qty",
        "promised_date",
        "delivered_date",
        "unit_price",
    }


def test_service_lines_of_no_orders_is_empty_not_an_error():
    assert service_lines([]) == {}


# -- the measured result ------------------------------------------------------


def test_realized_service_measures_fill_rate_from_a_connectors_orders():
    orders = [
        _order("o1", "2026-01-05", OrderLine("SKU-1", 10.0, 20.0, 10.0, "2026-01-07", "2026-01-07")),
        _order("o2", "2026-01-09", OrderLine("SKU-1", 10.0, 20.0, 6.0, "2026-01-11", "2026-01-11")),
    ]

    (record,) = realized_service(orders)

    assert record.product_id == "SKU-1"
    assert record.provenance == of.PROVENANCE_OBSERVED
    assert record.fill_rate == pytest.approx(16.0 / 20.0)
    assert record.lost_sales_units == pytest.approx(4.0)
    assert record.lost_sales_value == pytest.approx(80.0)


def test_realized_service_measures_otif_against_the_promised_date():
    orders = [
        # complete and on the promised day -> a hit
        _order("o1", "2026-01-05", OrderLine("SKU-1", 5.0, 20.0, 5.0, "2026-01-07", "2026-01-07")),
        # complete but two days late -> a miss
        _order("o2", "2026-01-06", OrderLine("SKU-1", 5.0, 20.0, 5.0, "2026-01-08", "2026-01-10")),
        # on time but short -> a miss (in full is half of on-time-in-full)
        _order("o3", "2026-01-07", OrderLine("SKU-1", 5.0, 20.0, 4.0, "2026-01-09", "2026-01-09")),
        # early -> still a hit
        _order("o4", "2026-01-08", OrderLine("SKU-1", 5.0, 20.0, 5.0, "2026-01-12", "2026-01-10")),
    ]

    (record,) = realized_service(orders)

    assert record.otif == pytest.approx(2.0 / 4.0)


def test_otif_scores_only_the_lines_that_carried_both_dates():
    orders = [
        _order("o1", "2026-01-05", OrderLine("SKU-1", 5.0, 20.0, 5.0, "2026-01-07", "2026-01-07")),
        # delivered, but nobody ever promised a date: it cannot be late or on time
        _order("o2", "2026-01-06", OrderLine("SKU-1", 5.0, 20.0, 5.0, None, "2026-01-30")),
    ]

    (record,) = realized_service(orders)

    assert record.otif == pytest.approx(1.0)  # one line scored, one hit - not 1/2
    assert record.fill_rate == pytest.approx(1.0)  # both lines still count for fill rate


def test_a_line_without_a_delivered_quantity_is_not_measured_never_a_stockout():
    """The whole reason the three fields are ``None``-by-default and not 0.0."""
    orders = [
        _order("o1", "2026-01-05", OrderLine("SKU-1", 10.0, 20.0)),
        _order("o2", "2026-01-06", OrderLine("SKU-1", 4.0, 20.0)),
    ]

    (record,) = realized_service(orders)

    assert record.provenance == of.PROVENANCE_NOT_MEASURED
    assert record.fill_rate is None
    assert record.otif is None
    assert record.lost_sales_units is None
    assert record.order_lines == 2  # the lines were seen, they just carried nothing


def test_an_unreported_line_does_not_drag_down_a_measured_one():
    orders = [
        _order("o1", "2026-01-05", OrderLine("SKU-1", 10.0, 20.0, 10.0, "2026-01-07", "2026-01-07")),
        _order("o2", "2026-01-06", OrderLine("SKU-1", 90.0, 20.0)),  # nobody said
    ]

    (record,) = realized_service(orders)

    assert record.provenance == of.PROVENANCE_OBSERVED
    assert record.fill_rate == pytest.approx(1.0)  # not 10/100
    assert record.lines_scored == 1
    assert record.order_lines == 2


def test_a_delivered_quantity_of_zero_is_a_measured_total_stockout():
    """``0.0`` reported and ``None`` unreported must not collapse into each other."""
    orders = [_order("o1", "2026-01-05", OrderLine("SKU-1", 10.0, 20.0, 0.0, "2026-01-07", "2026-01-09"))]

    (record,) = realized_service(orders)

    assert record.provenance == of.PROVENANCE_OBSERVED
    assert record.fill_rate == pytest.approx(0.0)
    assert record.lost_sales_units == pytest.approx(10.0)
    assert record.lost_sales_value == pytest.approx(200.0)


def test_realized_service_returns_one_record_per_sku_sorted():
    orders = [
        _order(
            "o1",
            "2026-01-05",
            OrderLine("SKU-2", 4.0, 50.0, 4.0, "2026-01-07", "2026-01-07"),
            OrderLine("SKU-1", 2.0, 20.0, 1.0, "2026-01-07", "2026-01-07"),
        )
    ]

    records = realized_service(orders)

    assert [r.product_id for r in records] == ["SKU-1", "SKU-2"]
    assert records[0].fill_rate == pytest.approx(0.5)
    assert records[1].fill_rate == pytest.approx(1.0)


def test_realized_service_takes_the_clients_own_stockout_day_count_per_sku():
    orders = [_order("o1", "2026-01-05", OrderLine("SKU-1", 10.0, 20.0, 6.0, "2026-01-07", "2026-01-09"))]

    (fallback,) = realized_service(orders)
    (measured,) = realized_service(orders, stockout_days={"SKU-1": 12})

    assert fallback.days_out_of_stock == 1  # lower bound: the one short date
    assert measured.days_out_of_stock == 12
    assert measured.method != fallback.method


# -- the seam with the inventory job ------------------------------------------


def test_service_frame_carries_the_columns_the_inventory_job_groups_by():
    """``jobs.inventory_optimization`` has accepted this frame all along; nothing built one."""
    from jobs.inventory_optimization import _lines_by_sku

    frame = service_frame(
        [
            _order("o1", "2026-01-05", OrderLine("SKU-1", 10.0, 20.0, 7.0, "2026-01-08", "2026-01-11")),
            _order("o2", "2026-01-06", OrderLine("SKU-2", 4.0, 50.0, 4.0, "2026-01-09", "2026-01-09")),
        ]
    )

    assert list(frame.columns) == [
        "product_id",
        "ordered_qty",
        "delivered_qty",
        "promised_date",
        "delivered_date",
        "unit_price",
    ]
    grouped = _lines_by_sku(frame)
    assert set(grouped) == {"SKU-1", "SKU-2"}

    record = of.measure_from_order_lines("SKU-1", grouped["SKU-1"])
    assert record.provenance == of.PROVENANCE_OBSERVED
    assert record.fill_rate == pytest.approx(0.7)
    assert record.otif == pytest.approx(0.0)  # short and three days late


def test_service_frame_of_no_orders_is_empty_but_still_shaped():
    frame = service_frame([])

    assert frame.empty
    assert "product_id" in frame.columns


def test_the_demo_store_reaches_the_inventory_job_with_real_service():
    from jobs.inventory_optimization import _lines_by_sku
    from src.connectors.simulator import demo_store

    grouped = _lines_by_sku(service_frame(demo_store().orders()))
    records = [of.measure_from_order_lines(sku, lines) for sku, lines in grouped.items()]

    assert records and all(r.provenance == of.PROVENANCE_OBSERVED for r in records)
    assert any(r.otif is not None and r.otif < 1.0 for r in records)
