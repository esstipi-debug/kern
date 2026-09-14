"""La lectura de servicio de un conector, y lo que arrastra consigo.

``src/connectors/__init__.py`` ya trae ``service_frame()``, cuyo propio docstring se
describe como "the missing constructor" del argumento ``order_lines`` que
``jobs.inventory_optimization.run`` acepta desde que aterrizo el trabajo de servicio
realizado. Tenia razon en la mitad: el constructor existia, y no lo llamaba nadie. Un
motor que puede medir OTIF saliente y una lectura que puede llenarlo nunca se habian
tocado, asi que cada corrida pasaba ``None`` y cada SKU volvia ``not_measured``.

``scan_service`` es ese llamador. Y arrastra una segunda cosa que el motor solo no puede
saber: el ``SERVICE_COVERAGE`` del adaptador, convertido en notas. Un cliente cuyo ERP no
registra fecha de llegada tiene que LEER eso en su informe, no deducirlo de un OTIF que
salio mas alto de lo que su gente sabe que es.
"""

from __future__ import annotations

import pytest

from src.connectors import COVERAGE_MEASURED, COVERAGE_UNAVAILABLE, FieldCoverage, ServiceCoverage
from src.connectors.service_scan import ServiceScan, scan_service
from src.connectors.simulator import demo_store
from src.connectors.unleashed import UnleashedConnector, UnleashedCredentials

_CREDS = UnleashedCredentials(api_id="the-id", api_key="the-key")


class _Http:
    """Transporte de mentira: un payload por recurso."""

    class _Response:
        def __init__(self, payload: dict) -> None:
            self._payload = payload
            self.status_code = 200

        def json(self) -> dict:
            return self._payload

    def __init__(self, by_resource: dict[str, list[dict]]) -> None:
        self._by_resource = by_resource
        self.pedidos: list[str] = []

    def get(self, path, *, params=None, headers=None):
        self.pedidos.append(path)
        items = self._by_resource.get(path.strip("/").split("/")[0], [])
        return _Http._Response({"Pagination": {"NumberOfPages": 1, "PageNumber": 1}, "Items": items})


_PRODUCTS = [
    {"ProductCode": "SKU-1", "ProductDescription": "Widget",
     "DefaultSellPrice": 20.0, "LastCost": 12.0},
]

#: Dos ordenes del mismo SKU: una servida corta y tarde, otra sin despachar todavia.
_ORDERS = [
    {
        "OrderNumber": "SO-1", "OrderDate": "2026-01-05T00:00:00",
        "RequiredDate": "2026-01-10T00:00:00", "CompletedDate": "2026-01-12T00:00:00",
        "OrderStatus": "Completed",
        "SalesOrderLines": [
            {"LineNumber": 1, "Product": {"ProductCode": "SKU-1"},
             "OrderQuantity": 10.0, "UnitPrice": 20.0},
        ],
    },
    {
        "OrderNumber": "SO-2", "OrderDate": "2026-01-20T00:00:00",
        "RequiredDate": "2026-01-25T00:00:00", "OrderStatus": "Backordered",
        "SalesOrderLines": [
            {"LineNumber": 1, "Product": {"ProductCode": "SKU-1"},
             "OrderQuantity": 5.0, "UnitPrice": 20.0},
        ],
    },
]

_SHIPMENTS = [
    {
        "ShipmentNumber": "SH-1", "OrderNumber": "SO-1", "ShipmentStatus": "Completed",
        "DispatchDate": "2026-01-12T00:00:00",
        "SalesShipmentLines": [
            {"LineNumber": 1, "SalesOrderLineNumber": 1,
             "Product": {"ProductCode": "SKU-1"}, "ShipmentQty": 8.0},
        ],
    },
]


def _unleashed() -> UnleashedConnector:
    return UnleashedConnector(
        _Http({"Products": _PRODUCTS, "SalesOrders": _ORDERS, "SalesShipments": _SHIPMENTS}),
        _CREDS,
    )


class _SoloOrdenes:
    """Un conector minimo SIN ``orders_with_shipments`` y SIN declaracion de cobertura."""

    def __init__(self, orders) -> None:
        self._orders = list(orders)

    def list_products(self):
        return []

    def inventory_levels(self):
        return []

    def orders(self, *, since: str | None = None):
        return list(self._orders)


# -- que lectura elige ---------------------------------------------------------


def test_prefiere_la_lectura_con_despachos_cuando_el_adaptador_la_tiene():
    """El adaptador de Unleashed expone las dos. ``orders()`` sola deja la cantidad
    servida en None y produce un informe sin fill rate; elegirla seria tirar a la basura
    el unico numero que la competencia del anillo 2 no puede dar."""
    conector = _unleashed()
    scan = scan_service(conector)
    assert any("SalesShipments" in p for p in conector._http.pedidos)
    registro = {r.product_id: r for r in scan.records}["SKU-1"]
    assert registro.fill_rate == pytest.approx(0.8)
    assert registro.provenance == "observed"


def test_cae_a_orders_cuando_el_adaptador_no_tiene_la_lectura_completa():
    """El simulador no tiene ``orders_with_shipments`` y no tiene por que tenerla. La
    ausencia del metodo no puede ser un error: es la mayoria de los backends."""
    scan = scan_service(demo_store())
    assert scan.records
    assert scan.order_lines.empty is False


# -- la declaracion viaja ------------------------------------------------------


def test_un_campo_no_reportable_produce_una_nota_con_su_motivo():
    """La nota lleva el ``means`` de la declaracion, no un 'no disponible' generico. Ese
    texto es el hallazgo, y es lo que el cliente puede verificar contra su propio ERP."""
    conector = _SoloOrdenes([])
    conector.SERVICE_COVERAGE = ServiceCoverage(
        backend="un ERP sin despachos",
        delivered_qty=FieldCoverage(
            COVERAGE_UNAVAILABLE, "", "esta API no registra ninguna cantidad despachada"
        ),
        promised_date=FieldCoverage(COVERAGE_MEASURED, "X.promised", "la promesa"),
        delivered_date=FieldCoverage(COVERAGE_MEASURED, "X.shipped", "el despacho"),
    )
    scan = scan_service(conector)
    assert any("delivered_qty" in n and "no registra" in n for n in scan.notes)
    assert not any("promised_date" in n for n in scan.notes)


def test_un_adaptador_sin_declaracion_lo_dice_en_vez_de_callarse():
    """Silencio y 'lo medimos todo' se leen igual en un informe. Uno de los dos es falso."""
    scan = scan_service(_SoloOrdenes([]))
    assert scan.coverage is None
    assert any("no declara" in n.lower() for n in scan.notes)


def test_la_nota_de_unleashed_dice_que_la_fecha_es_de_despacho():
    """El hallazgo del hilo, ahora en la salida y no solo en el codigo: Unleashed no
    registra llegada, asi que su OTIF mide salir a tiempo del deposito."""
    scan = scan_service(_unleashed())
    assert any("despacho" in n.lower() and "llegada" in n.lower() for n in scan.notes)


# -- lo que sale ---------------------------------------------------------------


def test_el_frame_trae_las_seis_columnas_que_lee_el_job():
    """Una columna renombrada no rompe: hace que el job descarte la linea en silencio y
    reporte no medido sobre un historial entero. Por eso se afirman por nombre."""
    scan = scan_service(_unleashed())
    assert list(scan.order_lines.columns) == [
        "product_id", "ordered_qty", "delivered_qty",
        "promised_date", "delivered_date", "unit_price",
    ]
    assert len(scan.order_lines) == 2


def test_la_linea_sin_despacho_no_viaja_como_cero_ni_puntua():
    """SO-2 no salio todavia. Un 0.0 aca inventa un quiebre total sobre una orden que
    quiza se sirva manana, y lo mete en el denominador del fill rate.

    Se afirma el SIGNIFICADO y no la identidad: pandas convierte ``None`` en ``NaN`` al
    meterlo en una columna de floats, asi que ``v is None`` es False sobre el frame aunque
    el conector haya reportado la ausencia correctamente. Lo que sostiene el producto es
    que ese valor NO sea 0.0 y que el motor lo deje afuera -- ``order_fulfillment._number``
    filtra por ``math.isfinite``, y ese es el unico motivo por el que la conversion de
    pandas no rompe nada. Si alguien cambia ese chequeo, este test cae.
    """
    import math

    scan = scan_service(_unleashed())
    servidas = list(scan.order_lines["delivered_qty"])
    assert 8.0 in servidas
    ausentes = [v for v in servidas if v is None or (isinstance(v, float) and math.isnan(v))]
    assert len(ausentes) == 1, "la linea sin despacho tiene que viajar ausente, no en 0.0"
    assert 0.0 not in servidas

    # Y el motor la deja fuera del denominador: 8 de 10, no 8 de 15.
    registro = {r.product_id: r for r in scan.records}["SKU-1"]
    assert registro.fill_rate == pytest.approx(0.8)
    assert registro.lines_scored == 1
    assert registro.order_lines == 2


def test_el_rollup_pondera_por_evidencia_y_no_por_sku():
    scan = scan_service(_unleashed())
    assert scan.portfolio.fill_rate == pytest.approx(0.8)
    assert scan.portfolio.units_ordered == 10.0


def test_la_demanda_sale_en_la_forma_que_comen_los_motores():
    """``(date, product_id, quantity, unit_cost)`` es lo que consume
    ``DataFrameDemandSource``. Sale de las MISMAS ordenes que el servicio, a proposito:
    dos lecturas distintas darian una demanda sobre un universo y un fill rate sobre otro."""
    scan = scan_service(_unleashed())
    assert list(scan.demand.columns) == ["date", "product_id", "quantity", "unit_cost"]
    assert len(scan.demand) == 2
    assert set(scan.demand["date"]) == {"2026-01-05", "2026-01-20"}
    # El costo sale del catalogo, no del precio de venta.
    assert set(scan.demand["unit_cost"]) == {12.0}


def test_un_conector_sin_ordenes_devuelve_un_scan_vacio_y_no_revienta():
    """Una cuenta recien conectada no tiene historia. Eso es un informe que dice 'no hay
    datos todavia', no una excepcion en la primera pantalla que ve un prospecto."""
    scan = scan_service(_SoloOrdenes([]))
    assert isinstance(scan, ServiceScan)
    assert scan.records == ()
    assert scan.order_lines.empty
    assert scan.portfolio.provenance == "not_measured"


# -- la cadena entera: conector -> job -> reporte -------------------------------

from jobs.inventory_optimization import run as run_inventory  # noqa: E402

#: Suficientes ordenes para que el job tenga historia con la que trabajar. Es el mismo SKU
#: servido corto una vez y completo el resto, para que el fill rate no sea ni 0 ni 1 -- un
#: extremo se puede producir por accidente, un 90% no.
_ORDERS_LARGAS = [
    {
        "OrderNumber": f"SO-{i}", "OrderDate": f"2026-01-{i:02d}T00:00:00",
        "RequiredDate": f"2026-01-{i + 3:02d}T00:00:00",
        "CompletedDate": f"2026-01-{i + 3:02d}T00:00:00",
        "OrderStatus": "Completed",
        "SalesOrderLines": [
            {"LineNumber": 1, "Product": {"ProductCode": "SKU-1"},
             "OrderQuantity": 10.0, "UnitPrice": 20.0},
        ],
    }
    for i in range(1, 11)
]

#: Nueve despachos completos y uno corto: 98 de 100.
_SHIPMENTS_LARGOS = [
    {
        "ShipmentNumber": f"SH-{i}", "OrderNumber": f"SO-{i}", "ShipmentStatus": "Completed",
        "DispatchDate": f"2026-01-{i + 3:02d}T00:00:00",
        "SalesShipmentLines": [
            {"LineNumber": 1, "SalesOrderLineNumber": 1,
             "Product": {"ProductCode": "SKU-1"}, "ShipmentQty": 8.0 if i == 1 else 10.0},
        ],
    }
    for i in range(1, 11)
]


def _unleashed_largo() -> UnleashedConnector:
    return UnleashedConnector(
        _Http({"Products": _PRODUCTS, "SalesOrders": _ORDERS_LARGAS,
               "SalesShipments": _SHIPMENTS_LARGOS}),
        _CREDS,
    )


def _servicio_del_reporte(report, sku: str):
    rec = next(r for r in report.recommendations if r.product_id == sku)
    return rec.realized_service


def test_el_servicio_medido_llega_al_reporte_del_job():
    """La prueba de que los eslabones encajan. Sin esto el adaptador mide bien y nadie lo
    llama, que es exactamente como quedo ``bwm_weights``: seis tests y cero llamadores."""
    scan = scan_service(_unleashed_largo())
    report = run_inventory(scan.demand, order_lines=scan.order_lines, period_freq="D")
    servicio = _servicio_del_reporte(report, "SKU-1")
    assert servicio is not None
    assert servicio.provenance == "observed"
    assert servicio.fill_rate == pytest.approx(0.98)
    # OTIF 0.9 y no 1.0 aunque las diez ordenes salieron el dia prometido: SCOR RL.1.1
    # cuenta una linea solo si llego COMPLETA **Y** a tiempo, y la primera salio 8 de 10.
    # Las dos condiciones se ven distinto en los dos numeros -- 98% de unidades servidas,
    # 90% de pedidos perfectos -- y esa brecha es justamente lo que un cliente que solo
    # mira fill rate no ve.
    assert servicio.otif == pytest.approx(0.9)
    assert servicio.lost_sales_units == pytest.approx(2.0)
    assert servicio.lost_sales_value == pytest.approx(40.0)


def test_el_mismo_reporte_sin_order_lines_no_mide_nada():
    """El control negativo, y es lo que prueba que el test de arriba no es casualidad: la
    MISMA demanda sin el frame de lineas es la corrida que este proyecto venia haciendo en
    cada llamada -- ``order_lines=None`` y todos los SKU en ``not_measured``."""
    scan = scan_service(_unleashed_largo())
    report = run_inventory(scan.demand, period_freq="D")
    servicio = _servicio_del_reporte(report, "SKU-1")
    assert servicio is None or servicio.provenance == "not_measured"
    assert servicio is None or servicio.fill_rate is None


# -- generalidad: el mismo scan sobre un adaptador de forma distinta -------------


def test_scan_service_mide_cin7_sin_orders_with_shipments():
    """La prueba de que la eleccion de lectura de ``scan_service`` no se hizo a la medida
    de Unleashed.

    Cin7 devuelve el fulfilment INLINE dentro de la venta, asi que no tiene -- ni necesita
    -- un ``orders_with_shipments()``. ``scan_service`` cae a ``orders()`` y aun asi obtiene
    las tres columnas. Si alguien "simplifica" el getattr y pasa a exigir el metodo
    completo, este test cae y Cin7 deja de medir nada.
    """
    from src.connectors.cin7 import Cin7Connector, Cin7Credentials

    venta = {
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
    fila = {"SaleID": "s1", "OrderNumber": "SO-1", "OrderDate": "2026-01-05T00:00:00",
            "ShipBy": "2026-01-10T00:00:00", "Status": "COMPLETED",
            "OrderStatus": "AUTHORISED", "CombinedShippingStatus": "SHIPPED"}

    class _Http:
        class _R:
            def __init__(self, p):
                self._p, self.status_code = p, 200

            def json(self):
                return self._p

        def get(self, path, *, params=None, headers=None):
            if path.strip("/") == "sale":
                return _Http._R(venta)
            return _Http._R({"Total": 1, "Page": 1, "SaleList": [fila]})

    conector = Cin7Connector(_Http(), Cin7Credentials(account_id="a", application_key="k"))
    assert not hasattr(conector, "orders_with_shipments")

    scan = scan_service(conector)
    registro = {r.product_id: r for r in scan.records}["SKU-1"]
    assert registro.fill_rate == pytest.approx(0.8)
    assert registro.otif == 0.0
    assert registro.provenance == "observed"
    # Y su nota de despacho llega igual que la de Unleashed, por el mismo camino.
    assert any("despacho" in n.lower() and "llegada" in n.lower() for n in scan.notes)
