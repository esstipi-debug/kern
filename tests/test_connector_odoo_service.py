"""The Odoo connector's service side: what was promised, what shipped, and when.

``OdooConnector.orders()`` used to read three fields off ``sale.order.line``
(``product_id``, ``product_uom_qty``, ``price_unit``), which is the DEMAND side and
nothing else. Odoo already stores the service side in its standard models -
``sale.order.line.qty_delivered``, ``sale.order.commitment_date`` and the done
outgoing ``stock.move`` rows that served each line - so the gap between a client's
live ERP and ``src/order_fulfillment.py`` was a field list, not missing data.

Each of the three is OPTIONAL in a real database: ``commitment_date`` is routinely
left blank, and ``stock.move.sale_line_id`` only exists where the Sales/Inventory
bridge is installed. These tests pin that every absence travels as ``None`` all the
way to the deliverable instead of being filled in with a plausible substitute - the
same stance ``receipts()`` already takes when it refuses to stand in the supplier's
declared lead time for the measured one.
"""

import pytest

from src import order_fulfillment as of
from src.connectors import realized_service
from src.connectors.odoo import InMemoryOdoo, OdooConnector

_PRODUCTS = {
    1: {"default_code": "SKU-1", "name": "Widget", "list_price": 20.0, "standard_price": 12.0},
    2: {"default_code": "SKU-2", "name": "Gadget", "list_price": 50.0, "standard_price": 30.0},
}


def _odoo_served() -> InMemoryOdoo:
    """A confirmed order promised for the 10th, served short and late on the 12th."""
    return InMemoryOdoo(
        {
            "product.product": dict(_PRODUCTS),
            "sale.order": {
                200: {
                    "name": "S0001",
                    "date_order": "2026-01-05 10:00:00",
                    "commitment_date": "2026-01-10 00:00:00",
                    "state": "sale",
                    "order_line": [300, 301],
                },
                201: {
                    "name": "S0002",
                    "date_order": "2026-01-06 09:00:00",
                    "commitment_date": False,  # Odoo's empty date
                    "state": "sale",
                    "order_line": [302],
                },
            },
            "sale.order.line": {
                300: {"product_id": [1, "Widget"], "product_uom_qty": 10.0, "price_unit": 20.0, "qty_delivered": 8.0},
                301: {"product_id": [2, "Gadget"], "product_uom_qty": 4.0, "price_unit": 50.0, "qty_delivered": 4.0},
                302: {"product_id": [1, "Widget"], "product_uom_qty": 5.0, "price_unit": 20.0, "qty_delivered": 0.0},
            },
            "stock.picking.type": {
                50: {"code": "outgoing"},
                51: {"code": "incoming"},
            },
            "stock.move": {
                # line 300 went out in two shipments: it was served on the LAST one
                600: {
                    "sale_line_id": [300, "S0001"],
                    "state": "done",
                    "date": "2026-01-09 15:00:00",
                    "picking_type_id": [50, "Delivery"],
                },
                601: {
                    "sale_line_id": [300, "S0001"],
                    "state": "done",
                    "date": "2026-01-12 11:00:00",
                    "picking_type_id": [50, "Delivery"],
                },
                # a cancelled move never shipped anything
                602: {
                    "sale_line_id": [301, "S0001"],
                    "state": "cancel",
                    "date": "2026-01-08 09:00:00",
                    "picking_type_id": [50, "Delivery"],
                },
                603: {
                    "sale_line_id": [301, "S0001"],
                    "state": "done",
                    "date": "2026-01-09 09:00:00",
                    "picking_type_id": [50, "Delivery"],
                },
                # an inbound receipt is not a delivery to a customer
                604: {
                    "sale_line_id": [302, "S0002"],
                    "state": "done",
                    "date": "2026-01-20 09:00:00",
                    "picking_type_id": [51, "Receipt"],
                },
            },
            "stock.location": {},
            "stock.quant": {},
            "product.supplierinfo": {},
            "stock.warehouse.orderpoint": {},
        }
    )


# -- the three fields ---------------------------------------------------------


def test_delivered_quantity_comes_from_qty_delivered():
    orders = {o.order_id: o for o in OdooConnector(_odoo_served()).orders()}

    lines = {ln.sku: ln for ln in orders["S0001"].lines}
    assert lines["SKU-1"].quantity == 10.0
    assert lines["SKU-1"].delivered_qty == 8.0
    assert lines["SKU-2"].delivered_qty == 4.0


def test_promised_date_comes_from_the_orders_commitment_date():
    orders = {o.order_id: o for o in OdooConnector(_odoo_served()).orders()}

    # the promise is made per order, so every line of it carries the same one
    assert {ln.promised_date for ln in orders["S0001"].lines} == {"2026-01-10"}


def test_an_order_with_no_commitment_date_promises_nothing():
    """Odoo returns ``False`` for a blank date; that is an absence, not a date."""
    orders = {o.order_id: o for o in OdooConnector(_odoo_served()).orders()}

    (line,) = orders["S0002"].lines
    assert line.promised_date is None


def test_delivered_date_is_the_last_done_outgoing_move_for_that_line():
    orders = {o.order_id: o for o in OdooConnector(_odoo_served()).orders()}
    lines = {ln.sku: ln for ln in orders["S0001"].lines}

    # two shipments on the 9th and the 12th: the line was not served until the 12th
    assert lines["SKU-1"].delivered_date == "2026-01-12"


def test_a_cancelled_move_never_delivered_anything():
    orders = {o.order_id: o for o in OdooConnector(_odoo_served()).orders()}
    lines = {ln.sku: ln for ln in orders["S0001"].lines}

    # SKU-2 has a cancelled move on the 8th and a done one on the 9th
    assert lines["SKU-2"].delivered_date == "2026-01-09"


def test_an_inbound_receipt_is_not_a_delivery_to_the_customer():
    orders = {o.order_id: o for o in OdooConnector(_odoo_served()).orders()}

    (line,) = orders["S0002"].lines
    assert line.delivered_date is None


# -- absences stay absences ---------------------------------------------------


def _odoo_bare() -> InMemoryOdoo:
    """An Odoo with no delivery bridge: no ``qty_delivered``, no ``sale_line_id``."""
    return InMemoryOdoo(
        {
            "product.product": dict(_PRODUCTS),
            "sale.order": {
                200: {
                    "name": "S0001",
                    "date_order": "2026-01-05 10:00:00",
                    "state": "sale",
                    "order_line": [300],
                }
            },
            "sale.order.line": {
                300: {"product_id": [1, "Widget"], "product_uom_qty": 10.0, "price_unit": 20.0}
            },
            "stock.location": {},
            "stock.quant": {},
            "product.supplierinfo": {},
            "stock.warehouse.orderpoint": {},
        }
    )


def test_an_odoo_without_the_delivery_fields_still_reads_its_orders():
    (order,) = OdooConnector(_odoo_bare()).orders()

    (line,) = order.lines
    assert (line.sku, line.quantity, line.price) == ("SKU-1", 10.0, 20.0)


def test_a_missing_qty_delivered_is_none_not_zero():
    """Zero delivered is a total stockout; unknown is not. They must not collapse."""
    (order,) = OdooConnector(_odoo_bare()).orders()

    (line,) = order.lines
    assert line.delivered_qty is None


def test_without_the_sales_inventory_bridge_no_delivery_date_is_invented():
    (order,) = OdooConnector(_odoo_bare()).orders()

    (line,) = order.lines
    assert line.delivered_date is None


def test_a_bare_odoo_reports_service_as_not_measured():
    records = realized_service(OdooConnector(_odoo_bare()).orders())

    assert [r.provenance for r in records] == [of.PROVENANCE_NOT_MEASURED]
    assert records[0].fill_rate is None


# -- end to end ---------------------------------------------------------------


def test_a_live_odoo_yields_observed_service_per_sku():
    records = {r.product_id: r for r in realized_service(OdooConnector(_odoo_served()).orders())}

    widget = records["SKU-1"]
    assert widget.provenance == of.PROVENANCE_OBSERVED
    # 8 of 10 on S0001 plus 0 of 5 on S0002
    assert widget.fill_rate == pytest.approx(8.0 / 15.0)
    assert widget.lost_sales_units == pytest.approx(7.0)
    assert widget.lost_sales_value == pytest.approx(140.0)
    # only S0001's line carried both dates, and it was short AND late
    assert widget.otif == pytest.approx(0.0)

    gadget = records["SKU-2"]
    assert gadget.fill_rate == pytest.approx(1.0)
    assert gadget.otif == pytest.approx(1.0)  # complete, and out on the 9th vs the 10th promised


def test_the_delivery_demo_odoo_measures_service_offline():
    """The outbound mirror of ``demo_odoo_receipts``: a runnable OTIF with no live ERP."""
    from src.connectors.odoo import demo_odoo_deliveries

    records = {r.product_id: r for r in realized_service(OdooConnector(demo_odoo_deliveries()).orders())}

    assert set(records) == {"SKU-1", "SKU-3"}
    assert all(r.provenance == of.PROVENANCE_OBSERVED for r in records.values())
    # Widget goes short and late in the back half; Gizmo never does
    assert records["SKU-1"].fill_rate < 1.0
    assert records["SKU-1"].otif == pytest.approx(0.5)
    assert records["SKU-3"].fill_rate == pytest.approx(1.0)
    assert records["SKU-3"].otif == pytest.approx(1.0)


def test_the_plain_demo_odoo_reports_no_service_rather_than_a_zero():
    """``demo_odoo()`` has no delivery data at all: that must read as unmeasured."""
    from src.connectors.odoo import demo_odoo

    records = realized_service(OdooConnector(demo_odoo()).orders())

    assert records and all(r.provenance == of.PROVENANCE_NOT_MEASURED for r in records)
