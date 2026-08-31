"""One declaration, every adapter: which part of customer service each backend can measure.

Why this exists. ``src/order_fulfillment.py`` measures fill rate, outbound OTIF (SCOR
RL.1.1) and lost sales from three OPTIONAL fields on ``OrderLine`` -- ``delivered_qty``,
``promised_date``, ``delivered_date``. Which of the three a backend can actually fill is
the whole competitive question this codebase is built around: the handoff of 2026-08-29
verified that StockTrim's sales-order schema carries none of them and that Netstock
measures only the inbound mirror. That finding is worth exactly as much as its shelf life,
and its shelf life is one adapter written by someone who did not read it.

So the adapters declare it, in code, and this file EXECUTES the declaration. A
``SERVICE_COVERAGE`` constant that says ``delivered_qty`` is unavailable, sitting beside an
adapter that returns a number for it, fails here. So does the reverse -- a connector
claiming to measure something its own fixture never produces. A declaration nobody runs is
a comment, and a comment is what the previous version of this argument was.

The drift guard at the end is what keeps that true past today: a NEW module in
``src/connectors/`` that defines ``orders()`` OR ``arrivals()`` and does not declare its
coverage fails, at the moment it lands, rather than the first time a client asks what their
OTIF was computed from. Same stance as ``tests/test_connector_contract.py`` takes on the
write side.

That second verb was added the day the first carrier landed, and the reason is worth
keeping: an ``ArrivalSource`` reads no orders, but it WRITES ``delivered_date`` -- it feeds
a service field like any adapter, and can turn a dispatch OTIF into an arrival one or ruin
it. Guarding "implements this Protocol" would have let it through; guarding "fills these
three fields" does not.

Scope, stated rather than implied: the fixtures here exercise the six offline-runnable
sources (simulator, HTTP client, MCP source, Unleashed, Cin7 Core, Olist) plus Odoo through
its ``InMemoryOdoo`` stand-in. ``ledger.CanonicalLedger`` is a MERGE of sources rather than
a backend, and ``emulator`` is a FastAPI app that serves one -- both are named in
``_NOT_A_BACKEND`` rather than left to be forgotten.

Olist is where the declaration stops being bookkeeping and starts being the finding. Three
of these sources -- Unleashed, Cin7 Core, Odoo -- report a ``delivered_date`` that is a
DEPARTURE, and Olist reports one that is an ARRIVAL. Same field name, same type, opposite
meaning; only the ``means`` sentence separates them. That is the difference the whole
outbound-service argument rests on, and it lives here because here it is executed.

Cin7 Core is what turned this file from a claim into evidence. It reaches the same three
service fields by a completely different route -- a two-hop join through box labels instead
of a second endpoint -- and needs no ``orders_with_shipments()`` at all, because its
fulfilments arrive inline. It passes the same six assertions with no special case, which is
the cheapest available answer to "does this abstraction describe backends, or did it
describe the first one someone wrote".
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.connectors import (  # noqa: E402
    COVERAGE_DERIVED,
    COVERAGE_MEASURED,
    COVERAGE_UNAVAILABLE,
    FieldCoverage,
    ServiceCoverage,
)


def test_a_field_that_reports_is_distinguishable_from_one_that_cannot():
    """``reports`` is the single bit the guard branches on. Measured and derived both
    produce a number; unavailable never does, and no fourth status may sneak past by
    being truthy."""
    measured = FieldCoverage(COVERAGE_MEASURED, "SalesOrder.RequiredDate", "the promise")
    derived = FieldCoverage(COVERAGE_DERIVED, "qty - unfulfilled", "deduced")
    absent = FieldCoverage(COVERAGE_UNAVAILABLE, "", "the API has no such field")
    assert measured.reports is True
    assert derived.reports is True
    assert absent.reports is False


def test_an_unavailable_field_must_still_say_why():
    """The whole point is the sentence. ``UNAVAILABLE`` with an empty ``means`` is a
    shrug, and a shrug is what a competitor's spec sheet also says."""
    with pytest.raises(ValueError, match="means"):
        FieldCoverage(COVERAGE_UNAVAILABLE, "", "")


def test_a_reporting_field_must_name_its_source_field():
    """A number with no named backend field cannot be reconciled by a client, and cannot
    be re-verified when the vendor changes its schema."""
    with pytest.raises(ValueError, match="source"):
        FieldCoverage(COVERAGE_MEASURED, "", "the promised date")


def test_an_unknown_status_is_refused_rather_than_treated_as_absent():
    with pytest.raises(ValueError, match="status"):
        FieldCoverage("probably", "X.y", "who knows")


def test_coverage_exposes_exactly_the_three_service_fields_by_name():
    """Keyed by the ``OrderLine`` attribute name, so the guard can compare a declaration
    against an observed line without a translation table that could itself drift."""
    coverage = ServiceCoverage(
        backend="demo",
        delivered_qty=FieldCoverage(COVERAGE_MEASURED, "A.b", "units that left"),
        promised_date=FieldCoverage(COVERAGE_MEASURED, "A.c", "the promise"),
        delivered_date=FieldCoverage(COVERAGE_UNAVAILABLE, "", "no arrival is recorded"),
    )
    assert set(coverage.fields()) == {"delivered_qty", "promised_date", "delivered_date"}
    assert coverage.fields()["delivered_date"].reports is False


# -- the adapters under test ----------------------------------------------------

import importlib  # noqa: E402
from dataclasses import dataclass  # noqa: E402
from typing import Callable  # noqa: E402

from fastapi.testclient import TestClient  # noqa: E402

from src.connectors import Order, OrderLine  # noqa: E402
from src.connectors.cin7 import Cin7Connector, Cin7Credentials  # noqa: E402
from src.connectors.emulator import create_app  # noqa: E402
from src.connectors.http_client import StoreApiClient  # noqa: E402
from src.connectors.mcp_source import McpInventorySource  # noqa: E402
from src.connectors.odoo import InMemoryOdoo, OdooConnector  # noqa: E402
from src.connectors.olist import OlistSource  # noqa: E402
from src.connectors.simulator import demo_store  # noqa: E402
from src.connectors.unleashed import UnleashedConnector, UnleashedCredentials  # noqa: E402


@dataclass(frozen=True)
class AdapterCase:
    """An adapter, the module that declares its coverage, and a fixture rich enough to
    exercise every field the declaration claims.

    A thin fixture is how a TRUE declaration fails here: the guard cannot tell "this
    backend cannot report it" from "this fixture forgot to". Each ``build`` below
    therefore produces at least one line carrying every field its adapter claims.
    """

    name: str
    module: str
    build: Callable[[], object]


def _simulator():
    return demo_store()


def _http_client():
    return StoreApiClient(TestClient(create_app(demo_store())))


#: One order the way an MCP server publishes it, with all three service keys present so
#: the fixture reaches the ceiling the declaration claims.
_MCP_ORDERS = [
    {
        "order_id": "SO-1",
        "created_at": "2026-01-05",
        "lines": [
            {"sku": "SKU-1", "quantity": 10.0, "price": 20.0, "delivered_qty": 8.0,
             "promised_date": "2026-01-10", "delivered_date": "2026-01-12"},
        ],
    }
]


def _mcp():
    return McpInventorySource(lambda name, args: _MCP_ORDERS)


def _odoo():
    """A confirmed order promised for the 10th, served short, shipped on the 12th.

    Same shape as ``tests/test_connector_odoo_service.py`` and kept local on purpose: a
    fixture edited over there must not be able to silently weaken this guard.
    """
    return OdooConnector(
        InMemoryOdoo(
            {
                "product.product": {
                    1: {"default_code": "SKU-1", "name": "Widget",
                        "list_price": 20.0, "standard_price": 12.0},
                },
                "sale.order": {
                    200: {"name": "S0001", "date_order": "2026-01-05 10:00:00",
                          "commitment_date": "2026-01-10 00:00:00", "state": "sale",
                          "order_line": [300]},
                },
                "sale.order.line": {
                    300: {"product_id": [1, "Widget"], "product_uom_qty": 10.0,
                          "price_unit": 20.0, "qty_delivered": 8.0},
                },
                "stock.picking.type": {50: {"code": "outgoing"}},
                "stock.move": {
                    600: {"sale_line_id": [300, "S0001"], "state": "done",
                          "date": "2026-01-12 11:00:00", "picking_type_id": [50, "Delivery"]},
                },
            }
        )
    )


class _UnleashedHttp:
    """El transporte de mentira de Unleashed: sirve un payload por recurso pedido.

    Local a este archivo, igual que el fixture de Odoo y por la misma razon: un fixture
    editado en ``tests/test_connector_unleashed.py`` no debe poder debilitar este guard en
    silencio. Los dos archivos describen la misma cuenta a proposito; si divergen, es este
    el que manda, porque es el que verifica la afirmacion que se le hace a un cliente.
    """

    class _Response:
        def __init__(self, payload: dict) -> None:
            self._payload = payload
            self.status_code = 200

        def json(self) -> dict:
            return self._payload

    def __init__(self, by_resource: dict[str, list[dict]]) -> None:
        self._by_resource = by_resource

    def get(self, path, *, params=None, headers=None):
        items = self._by_resource.get(path.strip("/").split("/")[0], [])
        return _UnleashedHttp._Response(
            {"Pagination": {"NumberOfPages": 1, "PageNumber": 1}, "Items": items}
        )


#: Una orden pedida el 5, prometida para el 10, servida corta (8 de 10) en dos despachos,
#: el ultimo el 12. Alcanza el techo que la declaracion de Unleashed reclama: las tres
#: columnas llegan con valor en al menos una linea.
_UNLEASHED_ORDERS = [
    {
        "OrderNumber": "SO-1", "OrderDate": "2026-01-05T00:00:00",
        "RequiredDate": "2026-01-10T00:00:00", "CompletedDate": "2026-01-12T00:00:00",
        "OrderStatus": "Completed",
        "SalesOrderLines": [
            {"LineNumber": 1, "Product": {"ProductCode": "SKU-1"},
             "OrderQuantity": 10.0, "UnitPrice": 20.0},
        ],
    },
]

_UNLEASHED_SHIPMENTS = [
    {
        "ShipmentNumber": "SH-1", "OrderNumber": "SO-1", "ShipmentStatus": "Completed",
        "DispatchDate": "2026-01-12T00:00:00",
        "SalesShipmentLines": [
            {"LineNumber": 1, "SalesOrderLineNumber": 1,
             "Product": {"ProductCode": "SKU-1"}, "ShipmentQty": 8.0},
        ],
    },
]


class _UnleashedAdapter:
    """Unleashed leido ENTERO: ordenes mas despachos.

    ``orders()`` sola deja ``delivered_qty`` en ``None`` a proposito -- ``/SalesOrders`` no
    la trae -- y la declaracion describe el techo del ADAPTADOR, no el de su lectura mas
    barata. Envolver ``orders_with_shipments`` es lo que hace que el guard verifique la
    afirmacion que efectivamente se le hace a un cliente.
    """

    def __init__(self) -> None:
        self._conector = UnleashedConnector(
            _UnleashedHttp({"SalesOrders": _UNLEASHED_ORDERS,
                            "SalesShipments": _UNLEASHED_SHIPMENTS}),
            UnleashedCredentials(api_id="guard", api_key="guard"),
        )

    def orders(self, *, since: str | None = None):
        return self._conector.orders_with_shipments(since=since)


def _unleashed():
    return _UnleashedAdapter()


class _Cin7Http:
    """Transporte de mentira de Cin7: la lista de ventas, y la venta entera por ID.

    Local a este archivo, como el de Odoo y el de Unleashed y por la misma razon: un
    fixture editado en ``tests/test_connector_cin7.py`` no debe poder debilitar este guard
    en silencio.
    """

    class _Response:
        def __init__(self, payload: dict) -> None:
            self._payload = payload
            self.status_code = 200

        def json(self) -> dict:
            return self._payload

    def get(self, path, *, params=None, headers=None):
        params = dict(params or {})
        if path.strip("/") == "sale":
            return _Cin7Http._Response(_CIN7_SALE)
        return _Cin7Http._Response({"Total": 1, "Page": 1, "SaleList": [_CIN7_SALE_ROW]})


#: Una venta cerrada, prometida para el 10, empacada en una caja y despachada el 12.
#: Alcanza el techo que la declaracion de Cin7 reclama: las tres columnas con valor.
_CIN7_SALE_ROW = {
    "SaleID": "s1", "OrderNumber": "SO-1", "OrderDate": "2026-01-05T00:00:00",
    "ShipBy": "2026-01-10T00:00:00", "Status": "COMPLETED", "OrderStatus": "AUTHORISED",
    "CombinedShippingStatus": "SHIPPED",
}

_CIN7_SALE = {
    "ID": "s1", "SaleOrderNumber": "SO-1", "SaleOrderDate": "2026-01-05T00:00:00",
    "ShipBy": "2026-01-10T00:00:00", "CombinedShippingStatus": "SHIPPED",
    "Order": {"Status": "AUTHORISED",
              "Lines": [{"SKU": "SKU-1", "Quantity": 10, "Price": 20.0}]},
    "Fulfilments": [{
        "TaskID": "t1",
        "Pack": {"Status": "AUTHORISED",
                 "Lines": [{"SKU": "SKU-1", "Box": "Box 1", "Quantity": 8}]},
        "Ship": {"Status": "AUTHORISED",
                 "Lines": [{"Boxes": "Box 1", "ShipmentDate": "2026-01-12T00:00:00",
                            "IsShipped": True}]},
    }],
}


#: Olist en dos archivos minimos. El fixture vive aca, como el de Odoo, Unleashed y Cin7:
#: un fixture editado en tests/test_connector_olist.py no debe poder debilitar este guard.
_OLIST_ORDERS = "\n".join(
    [
        '"order_id","customer_id","order_status","order_purchase_timestamp",'
        '"order_approved_at","order_delivered_carrier_date",'
        '"order_delivered_customer_date","order_estimated_delivery_date"',
        "o1,c1,delivered,2017-10-02 10:56:33,2017-10-02 11:07:15,"
        "2017-10-04 19:55:00,2017-10-20 21:25:13,2017-10-18 00:00:00",
        "",
    ]
)
_OLIST_ITEMS = "\n".join(
    [
        '"order_id","order_item_id","product_id","seller_id","shipping_limit_date",'
        '"price","freight_value"',
        '"o1",1,"P-1","s1",2017-09-19 09:45:35,58.90,13.29',
        "",
    ]
)


def _olist():
    """Olist es la unica fuente del repo cuya fecha de entrega es una LLEGADA.

    Que pase el mismo guard que tres ERP que solo ven la salida es el punto: la
    declaracion no dice "mide la entrega", dice DE DONDE sale y QUE significa, y por eso
    puede distinguir dos cosas que se llaman igual.
    """
    import tempfile

    carpeta = Path(tempfile.mkdtemp())
    (carpeta / "orders.csv").write_text(_OLIST_ORDERS, encoding="utf-8")
    (carpeta / "items.csv").write_text(_OLIST_ITEMS, encoding="utf-8")
    return OlistSource(carpeta / "orders.csv", carpeta / "items.csv")


def _cin7():
    """Cin7 se pasa TAL CUAL, sin envoltorio.

    Es la diferencia estructural con Unleashed y por eso vale la pena decirla: Cin7 devuelve
    el fulfilment inline dentro de la venta, asi que su ``orders()`` sola ya llena las tres
    columnas y no hay una lectura mas completa que envolver. Que el mismo guard acepte los
    dos, sin caso especial, es la prueba de que describe backends y no describia al primero.
    """
    return Cin7Connector(_Cin7Http(), Cin7Credentials(account_id="g", application_key="g"))


ADAPTERS: tuple[AdapterCase, ...] = (
    AdapterCase("simulator", "src.connectors.simulator", _simulator),
    AdapterCase("http_client", "src.connectors.http_client", _http_client),
    AdapterCase("mcp_source", "src.connectors.mcp_source", _mcp),
    AdapterCase("odoo", "src.connectors.odoo", _odoo),
    AdapterCase("unleashed", "src.connectors.unleashed", _unleashed),
    AdapterCase("cin7", "src.connectors.cin7", _cin7),
    AdapterCase("olist", "src.connectors.olist", _olist),
)


@pytest.fixture(params=ADAPTERS, ids=lambda case: case.name)
def adapter(request) -> AdapterCase:
    return request.param


def _coverage(module_name: str) -> ServiceCoverage:
    module = importlib.import_module(module_name)
    declared = getattr(module, "SERVICE_COVERAGE", None)
    assert isinstance(declared, ServiceCoverage), (
        f"{module_name} implements orders() but declares no SERVICE_COVERAGE. Every "
        "adapter says field by field what service it can measure; silence reads as a "
        "capability nobody checked."
    )
    return declared


def _observed(orders: list[Order]) -> dict[str, bool]:
    """Per service field: did ANY line come back carrying a value?

    ``any``, not ``all``: a real backend leaves the promise blank on the orders nobody
    promised a date for, and that is correct behaviour rather than a broken claim. The
    declaration describes the adapter's ceiling, so one line reaching it is proof.
    """
    lines = [line for order in orders for line in order.lines]
    return {
        field: any(getattr(line, field) is not None for line in lines)
        for field in ("delivered_qty", "promised_date", "delivered_date")
    }


# -- the contract ---------------------------------------------------------------


def test_every_adapter_declares_all_three_service_fields(adapter: AdapterCase):
    coverage = _coverage(adapter.module)
    assert set(coverage.fields()) == {"delivered_qty", "promised_date", "delivered_date"}
    assert coverage.backend.strip(), "a declaration must name the backend it describes"


def test_a_field_declared_unavailable_never_comes_back_with_a_value(adapter: AdapterCase):
    """The direction that protects the client. An adapter that quietly starts filling a
    field it declared absent is filling it with something, and the something is a guess."""
    coverage = _coverage(adapter.module)
    observed = _observed(adapter.build().orders())
    for name, field in coverage.fields().items():
        if not field.reports:
            assert observed[name] is False, (
                f"{adapter.name} declares {name} {field.status} ({field.means}) but a line "
                "came back carrying one. Either the backend gained the field - update the "
                "declaration - or the adapter is inventing it."
            )


def test_a_field_declared_reportable_actually_arrives(adapter: AdapterCase):
    """The direction that protects the differentiator. A declaration is a sales claim, and
    this is the only thing standing between it and a slide."""
    coverage = _coverage(adapter.module)
    observed = _observed(adapter.build().orders())
    for name, field in coverage.fields().items():
        if field.reports:
            assert observed[name] is True, (
                f"{adapter.name} declares {name} {field.status} from {field.source!r}, but "
                "no line in its own fixture carried one."
            )


# -- proof that the contract has teeth ------------------------------------------

#: Every adapter in ADAPTERS today declares all three fields reportable, so the
#: "declared unavailable" branch above never executes against a real one. A branch that
#: never runs is a green test that proves nothing. These two run the SAME assertions
#: against deliberately-lying adapters, so the guard is watched failing before it is
#: trusted passing.


class _LyingAdapter:
    """An adapter whose lines carry exactly what ``lines`` says, honestly returned.
    The lie is in the declaration handed alongside it, not in the data."""

    def __init__(self, line: OrderLine) -> None:
        self._line = line

    def orders(self, *, since: str | None = None) -> list[Order]:
        return [Order("SO-1", "2026-01-05", (self._line,))]


def _assert_declaration_holds(coverage: ServiceCoverage, source) -> None:
    """The two directional checks above, factored so the lying adapters run the real
    thing rather than a paraphrase of it."""
    observed = _observed(source.orders())
    for name, field in coverage.fields().items():
        if field.reports:
            assert observed[name] is True, f"{name} declared {field.status}, never arrived"
        else:
            assert observed[name] is False, f"{name} declared {field.status}, arrived anyway"


def test_the_guard_catches_an_adapter_filling_a_field_it_declared_absent():
    """The direction that protects the client: a connector that starts inventing a
    delivered quantity must not pass while still claiming it cannot measure one."""
    lying = _LyingAdapter(
        OrderLine("SKU-1", 10.0, 20.0, delivered_qty=10.0,
                  promised_date="2026-01-10", delivered_date="2026-01-12")
    )
    coverage = ServiceCoverage(
        backend="a backend that cannot report deliveries",
        delivered_qty=FieldCoverage(COVERAGE_UNAVAILABLE, "", "no shipped quantity exists"),
        promised_date=FieldCoverage(COVERAGE_MEASURED, "X.promised", "the promise"),
        delivered_date=FieldCoverage(COVERAGE_MEASURED, "X.shipped", "the dispatch"),
    )
    with pytest.raises(AssertionError, match="arrived anyway"):
        _assert_declaration_holds(coverage, lying)


def test_the_guard_catches_an_adapter_claiming_a_field_it_never_produces():
    """The direction that protects the differentiator: 'we measure outbound OTIF' is a
    sales claim, and this is the only thing between it and a slide."""
    lying = _LyingAdapter(OrderLine("SKU-1", 10.0, 20.0, delivered_qty=8.0))
    coverage = ServiceCoverage(
        backend="a backend that promises more than it has",
        delivered_qty=FieldCoverage(COVERAGE_MEASURED, "X.shipped_qty", "units that left"),
        promised_date=FieldCoverage(COVERAGE_MEASURED, "X.promised", "the promise"),
        delivered_date=FieldCoverage(COVERAGE_MEASURED, "X.delivered", "the arrival"),
    )
    with pytest.raises(AssertionError, match="never arrived"):
        _assert_declaration_holds(coverage, lying)


# -- the drift guard ------------------------------------------------------------

#: Modules in ``src/connectors/`` that define ``orders()`` and are deliberately NOT held
#: to a declaration. Each exclusion is a decision on the record, not an oversight.
_NOT_A_BACKEND = {
    # A MERGE of other sources, not a system of record: its coverage is the union of its
    # channels' and cannot be declared statically. Its orders() also takes no ``since``,
    # so it does not satisfy InventorySource in the first place.
    "ledger",
    # A FastAPI app that SERVES a SimulatedStore over HTTP; its orders() is a route
    # handler, not an adapter method. What it serves is declared by simulator.py, and
    # what reads it back is declared by http_client.py.
    "emulator",
    # El Protocol de las fuentes de llegada y el helper que las empalma, no una fuente:
    # define ``arrivals()`` en un ``Protocol``, no en un adaptador. Lo que empalma se
    # declara en el modulo del transportista, y lo compuesto en ``coverage_of_arrivals``.
    "arrivals",
}


def test_every_connector_that_reads_orders_declares_its_service_coverage():
    """Drift guard: a new adapter is one entry in ADAPTERS away from being held to the
    same assertions. Landing one without a declaration fails HERE, at the moment it
    lands, rather than the first time a client asks what their OTIF was computed from."""
    import re

    connectors_dir = _REPO_ROOT / "src" / "connectors"
    # ``orders()`` O ``arrivals()``. La segunda se agrego cuando aterrizo el primer
    # transportista: un ArrivalSource no lee ordenes, pero ESCRIBE ``delivered_date`` -- o
    # sea que alimenta un campo de servicio igual que cualquier adaptador, y sin esta
    # alternancia se habria colado sin declarar nada. El guard cubre lo que llena los tres
    # campos, no lo que implementa un Protocol en particular.
    readers = {
        path.stem
        for path in sorted(connectors_dir.glob("*.py"))
        if path.stem != "__init__"
        and re.search(r"def (orders|arrivals)\(", path.read_text(encoding="utf-8"))
    }
    covered = {case.module.rsplit(".", 1)[-1] for case in ADAPTERS}
    covered |= {modulo.rsplit(".", 1)[-1] for _, modulo, _ in ARRIVAL_SOURCES}
    assert readers == covered | _NOT_A_BACKEND, (
        "connector(s) reading orders but absent from both ADAPTERS and _NOT_A_BACKEND: "
        f"{sorted(readers - covered - _NOT_A_BACKEND)}. Add a SERVICE_COVERAGE and an "
        "AdapterCase, or record here why this one is not a backend."
    )


# -- el hallazgo de Unleashed, fijado ------------------------------------------


def test_unleashed_declares_its_delivery_date_as_a_dispatch_and_not_an_arrival():
    """El hallazgo que sostiene el argumento de venta, fijado en un test para que no se
    pierda cuando alguien "mejore" el docstring.

    Unleashed no registra ninguna fecha de llegada: ni ``CompletedDate`` ni
    ``DispatchDate`` son la recepcion del cliente, y el indice de endpoints no tiene un
    recurso de entrega. Entonces el OTIF que sale de aca mide DESPACHAR antes de la fecha
    pedida. Un adaptador que lo declare como llegada convierte cada demora de transporte
    en puntualidad, y se lo dice a un cliente que puede verificarlo.
    """
    from src.connectors.unleashed import SERVICE_COVERAGE

    entrega = SERVICE_COVERAGE.delivered_date
    assert entrega.reports is True
    assert "despacho" in entrega.means.lower()
    assert "llegada" in entrega.means.lower()


def test_unleashed_derives_the_delivered_quantity_from_a_second_endpoint():
    """No es MEASURED: ``/SalesOrders`` no la trae. Que sea DERIVED y que la fuente nombre
    ``/SalesShipments`` es lo que le explica al que lea esto por que hay dos lecturas."""
    from src.connectors.unleashed import SERVICE_COVERAGE

    assert SERVICE_COVERAGE.delivered_qty.status == COVERAGE_DERIVED
    assert "SalesShipment" in SERVICE_COVERAGE.delivered_qty.source


# -- Cin7 Core: la segunda prueba de que el guard describe backends ---------------


def test_cin7_declara_su_fecha_de_entrega_como_despacho_y_no_como_llegada():
    """Tercer backend seguido sin fecha de llegada, y el tercero que tiene que decirlo.

    Cin7 Core tiene TrackingNumber y TrackingURL, pero ningun campo de entrega confirmada:
    ``ShipmentDate`` es cuando la caja SALIO. Que Unleashed, Odoo y Cin7 compartan este
    limite no lo vuelve inocuo -- lo vuelve la norma del segmento, y el unico OTIF de
    llegada verificado en este repo sigue siendo el de Shopify.
    """
    from src.connectors.cin7 import SERVICE_COVERAGE

    entrega = SERVICE_COVERAGE.delivered_date
    assert entrega.reports is True
    assert "despacho" in entrega.means.lower()
    assert "llegada" in entrega.means.lower()


def test_cin7_declara_la_cantidad_servida_como_empalme_y_no_como_un_campo():
    """El error que este PR corrige respecto de la nota previa: no hay ningun endpoint que
    devuelva la cantidad despachada. La fuente declarada tiene que nombrar los DOS lados del
    empalme, o el proximo que lea esto va a buscar un campo que no existe."""
    from src.connectors import COVERAGE_DERIVED
    from src.connectors.cin7 import SERVICE_COVERAGE

    fuente = SERVICE_COVERAGE.delivered_qty
    assert fuente.status == COVERAGE_DERIVED
    assert "Pack" in fuente.source and "Ship" in fuente.source


def test_cin7_es_el_unico_que_declara_poder_medir_un_cero():
    """La diferencia real con Unleashed, y la unica ventaja que el handoff le atribuia que
    resulto cierta. El ``means`` tiene que decirlo, porque es lo que le explica a un cliente
    por que su informe de Cin7 muestra quiebres totales y el de Unleashed no."""
    from src.connectors.cin7 import SERVICE_COVERAGE

    assert "cero" in SERVICE_COVERAGE.delivered_qty.means.lower()


# -- las fuentes de LLEGADA, que no leen ordenes pero llenan un campo de servicio ---

#: Un transportista no tiene ``orders()``: no sabe que se pidio. Pero SI escribe
#: ``delivered_date``, asi que esta tan sujeto a declarar su cobertura como cualquier
#: adaptador -- y mas, porque es el unico que puede convertir un OTIF de despacho en uno de
#: llegada, o arruinarlo. Por eso el drift guard de arriba busca ``orders`` O ``arrivals``.
#:
#: Se prueban con sus propias aserciones y no con ``AdapterCase``: ese caso corre
#: ``.orders()`` y compara los tres campos, y aca dos de los tres los tiene que poner el
#: ERP. Meterlo a la fuerza en la misma maquinaria exigiria un caso especial, que es
#: justamente lo que este archivo dice no tener.


def _starshipit():
    """Un envio entregado el 22, con un evento posterior que NO debe correr la fecha."""
    from src.connectors.starshipit import StarshipitArrivals, StarshipitCredentials

    class _Http:
        class _R:
            def __init__(self, p, s=200):
                self._p, self.status_code = p, s

            def json(self):
                return self._p

        def get(self, path, *, params=None, headers=None):
            return _Http._R({
                "results": {
                    "order_number": "SO-1",
                    "tracking_status": "Delivered",
                    "last_updated_date": "2024-04-30T08:00:00",
                    "tracking_events": [
                        {"event_datetime": "2024-04-19T09:15:00", "status": "InTransit"},
                        {"event_datetime": "2024-04-22T14:03:11", "status": "Delivered"},
                        {"event_datetime": "2024-04-30T08:00:00", "status": "Exception"},
                    ],
                },
                "success": True,
            })

    return StarshipitArrivals(
        _Http(), StarshipitCredentials(api_key="g", subscription_key="g"), ["SO-1"]
    )


ARRIVAL_SOURCES: tuple[tuple[str, str, Callable[[], object]], ...] = (
    ("starshipit", "src.connectors.starshipit", _starshipit),
)


@pytest.fixture(params=ARRIVAL_SOURCES, ids=lambda caso: caso[0])
def arrival_source(request):
    return request.param


def test_toda_fuente_de_llegada_declara_los_tres_campos(arrival_source):
    nombre, modulo, _ = arrival_source
    coverage = _coverage(modulo)
    assert set(coverage.fields()) == {"delivered_qty", "promised_date", "delivered_date"}


def test_una_fuente_de_llegada_declara_ausente_lo_que_un_transportista_no_sabe(arrival_source):
    """La rama que hasta este adaptador solo se probaba contra dobles de test.

    Ninguna de las fuentes anteriores declaraba un campo UNAVAILABLE -- todas podian medir
    los tres -- asi que la mitad del guard corria en vacio y hubo que escribir mutantes
    para probarla. Un transportista es el primer caso REAL: mueve bultos, no lineas de
    pedido, y no puede saber ni cuanto se pidio ni que fecha se prometio.
    """
    _, modulo, _ = arrival_source
    coverage = _coverage(modulo)
    assert coverage.delivered_qty.reports is False
    assert coverage.promised_date.reports is False
    # Y la que si reporta, reporta. Un transportista que no sepa la fecha de entrega no
    # tiene ningun motivo para existir en este repo.
    assert coverage.delivered_date.reports is True


def test_la_fecha_que_declara_medir_efectivamente_llega(arrival_source):
    """Misma direccion que ``test_a_field_declared_reportable_actually_arrives``: una
    declaracion es una afirmacion de venta, y esto es lo unico entre ella y una slide."""
    nombre, _, build = arrival_source
    llegadas = build().arrivals()
    assert llegadas, f"{nombre} no reporto ninguna llegada sobre su propio fixture"
    assert all(a.arrived_on for a in llegadas)


def test_empalmar_un_transportista_cambia_de_donde_sale_la_fecha_de_entrega(arrival_source):
    """La composicion, verificada: el ERP pone lo pedido y la promesa, el transportista
    pone la llegada, y la declaracion resultante lo dice. Publicar la del ERP a secas
    despues del empalme afirmaria una llegada que el ERP no puede dar."""
    from src.connectors.arrivals import coverage_of_arrivals
    from src.connectors.unleashed import SERVICE_COVERAGE as ERP

    _, modulo, _ = arrival_source
    compuesta = coverage_of_arrivals(ERP, _coverage(modulo))

    assert compuesta.delivered_qty == ERP.delivered_qty
    assert compuesta.promised_date == ERP.promised_date
    assert compuesta.delivered_date.reports is True
    assert "despacho" in ERP.delivered_date.means.lower()
    assert "llego al cliente" in compuesta.delivered_date.means
