"""Odoo ERP connector — read a live Odoo backend, write back safely (Gap #5).

Implements the ``InventorySource`` read side (products, inventory levels, sales orders)
straight from Odoo's standard models, plus a write side that routes restock decisions
through the safe-staging ``nosilo_core.writeback`` plane (dry-run -> approval -> idempotent apply
-> audit/rollback) as Odoo *reorder rules* -- Kern never mutates the system of record
blindly.

Two layers, mirroring ``http_client.StoreApiClient`` (which takes any httpx-style object):

- ``OdooClient`` is the real transport: ``xmlrpc.client`` (Python stdlib, no new deps)
  against ``/xmlrpc/2/common`` (authenticate) and ``/xmlrpc/2/object`` (``execute_kw``).
- ``OdooConnector`` holds all the model<->DTO mapping and speaks only an ``execute_kw``
  duck type, so the whole connector runs and is tested offline against ``InMemoryOdoo`` --
  a real Odoo instance (URL + db + API key) is needed only at deploy time.

Field mapping (Odoo standard models -> canonical connector DTOs):

===================== =========================================================
Odoo model            mapped to
===================== =========================================================
product.product       default_code -> sku, name, list_price -> price,
                      standard_price -> cost
stock.quant           internal-location quantity summed per product -> level
sale.order(.line)     date_order + product_uom_qty + price_unit -> Order/line;
                      qty_delivered -> lo servido y commitment_date -> lo
                      prometido, el par que vuelve medible el servicio al
                      cliente (ver ``orders()``)
stock.move +          date (state=done, tipo de salida) de la ULTIMA salida que
sale.order.line       sirvio la linea -> cuando la linea quedo servida
product.supplierinfo  delay (days) -> el plazo DECLARADO, la promesa de la ficha
                      (NO el observado: ver ``receipts()``)
stock.move +          date (state=done) menos purchase.order.date_order -> el
purchase.order        plazo OBSERVADO, una fila por recepcion
stock.warehouse.      product_min_qty (reorder point) <- restock target write
  orderpoint          (reversible)
===================== =========================================================

Las dos ultimas filas son el par que da sentido al producto: sin la segunda, el
motor dimensiona contra la promesa del ERP y la brecha da cero por construccion.
"""

from __future__ import annotations

import math
import time
import xmlrpc.client
from dataclasses import dataclass
from dataclasses import field as _dc_field
from dataclasses import replace as _dc_replace
from datetime import datetime, timedelta
from typing import Any, Protocol, runtime_checkable

import pandas as pd

from nosilo_core import writeback
from src.connectors import (
    COVERAGE_DERIVED,
    COVERAGE_MEASURED,
    WRITE_APPROVAL_GATED,
    FieldCoverage,
    InventoryLevel,
    Order,
    OrderLine,
    Product,
    ScheduledReceipt,
    ServiceCoverage,
    WriteSurface,
)
from src.constraints import apply_order_rules
from src.supplier_scorecard import LEAD_TIME_MEASURED

# Odoo ORM methods that only read: safe to retry on a transient transport error,
# since retrying can never duplicate a write. `create`/`write`/`unlink` are never
# retried automatically here - a lost response after the request reached the
# server would otherwise risk a duplicate PO/record on retry.
_READ_ONLY_METHODS = frozenset({"search", "search_read", "read", "search_count", "fields_get"})
_DEFAULT_TIMEOUT_SECONDS = 30.0
_DEFAULT_MAX_ATTEMPTS = 3
_DEFAULT_BACKOFF_SECONDS = 0.5

# Odoo standard model names.
_M_PRODUCT = "product.product"
_M_LOCATION = "stock.location"
_M_QUANT = "stock.quant"
_M_SALE = "sale.order"
_M_SALE_LINE = "sale.order.line"
_M_SUPPLIERINFO = "product.supplierinfo"
_M_ORDERPOINT = "stock.warehouse.orderpoint"
_M_PO = "purchase.order"
_M_PO_LINE = "purchase.order.line"
_M_MOVE = "stock.move"
_M_PICKING_TYPE = "stock.picking.type"
_M_PARTNER = "res.partner"

_SALE_STATES = ("sale", "done")  # confirmed/locked sales count as realized demand
# Ordenes de compra COMPROMETIDAS. `draft`/`sent` son cotizaciones: contarlas
# como suministro asume mercaderia que ningun proveedor prometio todavia.
_PO_OPEN_STATES = ("purchase", "done")

#: Tipos de operacion que son una recepcion de compra. `dropship` va incluido a
#: proposito: filtrar solo por `incoming` descarta cada envio directo, lo que
#: puede empujar a un proveedor real bajo el minimo de recepciones y devolver un
#: entregable confiadamente vacio sobre un Odoo lleno de recepciones. Un leaf
#: `in` que nombra un valor de seleccion que la base no tiene no matchea nada,
#: asi que no hace falta sondear si el modulo esta instalado.
_RECEIPT_TYPE_CODES = ("incoming", "dropship")

#: El espejo de salida de ``_RECEIPT_TYPE_CODES``, y por el mismo motivo incluye
#: `dropship`: una linea servida por envio directo del proveedor al cliente se
#: entrego igual, y dejarla fuera la deja sin fecha -- o sea fuera de OTIF -- en
#: toda cuenta que despacha asi.
_DELIVERY_TYPE_CODES = ("outgoing", "dropship")
_MOVE_DONE = "done"
_PO_REALIZED_STATES = ("purchase", "done")

#: Lo que cuenta como OC ABIERTA (lo comprado y no recibido) es _PO_OPEN_STATES:
#: `purchase` y `done`. `done` SI: con "Bloquear ordenes confirmadas" activado,
#: Odoo pasa la OC a `done` apenas se confirma, sin relacion con si ya llego; lo
#: que ya se recibio lo descarta el pendiente <= 0, no el estado.
#: `draft`/`sent`/`to approve` no: nadie las confirmo, y contarlas es prometerle
#: a un cliente stock que no se compro.

#: `product.supplierinfo.delay` viene con default=1 en Odoo. Un 1 no es una
#: promesa, es el campo sin configurar -- y `relative_gap` solo se protege del
#: cero. Sin este piso, observado 34 contra declarado 1 da 3.300% de brecha
#: relativa, gana `worst`, y el titular del entregable pasa a ser un artefacto.
_DECLARED_DELAY_FLOOR = 1
_WB_TARGET = "odoo"


class OdooError(RuntimeError):
    """Odoo transport, authentication, or mapping failure."""


class OdooTransportError(OdooError):
    """La conexion se cayo: ni llego, o no volvio la respuesta.

    Subclase para que todo ``except OdooError`` existente la siga atrapando, y
    separada para que quien tenga que distinguirla no lo haga mirando el texto.
    La diferencia importa: ``OdooError`` a secas desde ``receipts()`` significa
    "este Odoo no se puede medir" (falta el modulo de compras) y se le reporta al
    cliente como dato faltante; esto otro significa "se corto la red" y reportarlo
    como dato faltante manda a revisar el ERP por un problema que no esta ahi.
    """


@runtime_checkable
class OdooRPC(Protocol):
    """The single method the connector needs: Odoo's ``execute_kw`` dispatch."""

    def execute_kw(self, model: str, method: str, args: list, kwargs: dict | None = None) -> Any: ...


# -- real transport -----------------------------------------------------------


class _TimeoutTransport(xmlrpc.client.Transport):
    """xmlrpc Transport that bounds the socket timeout on every connection."""

    def __init__(self, timeout: float, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._timeout = timeout

    def make_connection(self, host: Any) -> Any:
        conn = super().make_connection(host)
        conn.timeout = self._timeout
        return conn


class _TimeoutSafeTransport(xmlrpc.client.SafeTransport):
    """HTTPS variant of ``_TimeoutTransport`` (Odoo Online is always https)."""

    def __init__(self, timeout: float, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._timeout = timeout

    def make_connection(self, host: Any) -> Any:
        conn = super().make_connection(host)
        conn.timeout = self._timeout
        return conn


def server_proxies(url: str, *, timeout: float = _DEFAULT_TIMEOUT_SECONDS) -> tuple[Any, Any]:
    """Los dos ``ServerProxy`` de Odoo para ``url``, con el timeout de socket acotado.

    Publica porque el validador de conexion de ``odoo_connect.py`` necesita hablar
    con ``/xmlrpc/2/common`` ANTES de que exista un ``OdooClient`` -- distinguir
    "el host no contesta" de "la credencial no sirve" exige preguntar la version
    del servidor sin autenticar. Sin este corte, ese modulo copiaria la eleccion
    de transporte y el dia que aqui cambie el timeout quedaria una copia atras.
    """
    base = url.rstrip("/")
    transport_cls = _TimeoutSafeTransport if base.startswith("https") else _TimeoutTransport
    return (
        xmlrpc.client.ServerProxy(f"{base}/xmlrpc/2/common", transport=transport_cls(timeout)),
        xmlrpc.client.ServerProxy(f"{base}/xmlrpc/2/object", transport=transport_cls(timeout)),
    )


class OdooClient:
    """Real XML-RPC transport to an Odoo server (stdlib ``xmlrpc.client``).

    Pass ``common``/``models`` proxies to inject a transport in tests; otherwise they are
    built from ``url`` with a bounded socket ``timeout`` (a hung Odoo instance must never
    hang a job indefinitely).

    Read-only ORM methods (``search`` / ``search_read`` / ``read`` / ...) are retried up
    to ``max_attempts`` times with exponential backoff on a transient transport error
    (a dropped connection, DNS hiccup, timeout). Writes (``create`` / ``write`` /
    ``unlink``) are never auto-retried: if the response is lost after the request
    reached the server, blindly retrying a ``create`` could duplicate a record (e.g. a
    purchase order) - that ambiguity has to surface as an error, not a silent retry.
    """

    def __init__(
        self,
        url: str,
        db: str,
        username: str,
        api_key: str,
        *,
        common: Any = None,
        models: Any = None,
        timeout: float = _DEFAULT_TIMEOUT_SECONDS,
        max_attempts: int = _DEFAULT_MAX_ATTEMPTS,
        backoff_seconds: float = _DEFAULT_BACKOFF_SECONDS,
    ) -> None:
        if common is None or models is None:
            hechos = server_proxies(url, timeout=timeout)
            common = common or hechos[0]
            models = models or hechos[1]
        uid = common.authenticate(db, username, api_key, {})
        if not uid:
            raise OdooError(f"Odoo authentication failed for user {username!r} on db {db!r}")
        self._db = db
        self._uid = int(uid)
        self._api_key = api_key
        self._models = models
        self._max_attempts = max(1, int(max_attempts))
        self._backoff_seconds = backoff_seconds

    @property
    def uid(self) -> int:
        return self._uid

    def execute_kw(self, model: str, method: str, args: list, kwargs: dict | None = None) -> Any:
        attempts = self._max_attempts if method in _READ_ONLY_METHODS else 1
        last_exc: Exception | None = None
        for attempt in range(attempts):
            try:
                return self._models.execute_kw(self._db, self._uid, self._api_key, model, method, args, kwargs or {})
            except xmlrpc.client.Fault as exc:
                # An application-level error response from Odoo (bad data, permission
                # denied, ...): the request was processed. Retrying will not help.
                raise OdooError(f"Odoo rejected {model}.{method}: {exc.faultString}") from exc
            except (OSError, TimeoutError) as exc:  # transport-level: connection never confirmed
                last_exc = exc
                if attempt + 1 >= attempts:
                    break
                time.sleep(self._backoff_seconds * (2**attempt))
        raise OdooTransportError(
            f"Odoo transport failed for {model}.{method} after {attempts} attempt(s): {last_exc}"
        ) from last_exc


# -- connector ----------------------------------------------------------------


def _as_date(value: Any) -> str:
    """Odoo datetimes arrive as 'YYYY-MM-DD HH:MM:SS'; keep the ISO date so it sorts."""
    return str(value)[:10] if value else ""


def _as_date_or_none(value: Any) -> str | None:
    """``_as_date``, pero separando "sin fecha" de la cadena vacia.

    Odoo devuelve ``False`` por una fecha en blanco, y ``commitment_date`` viene en
    blanco en la mayoria de las cuentas: nadie prometio nada. Un ``""`` viajando
    hasta ``src.order_fulfillment`` no se parsea y da lo mismo que ``None``, pero
    dice otra cosa a quien lee la linea; el campo es opcional, asi que se declara.
    """
    dia = _as_date(value)
    return dia or None


def _as_qty_or_none(value: Any) -> float | None:
    """Una cantidad reportada, o ``None`` cuando el ERP no reporto ninguna.

    Odoo manda ``False`` por un campo sin valor y ``0.0`` por uno que si midio y
    dio cero. La diferencia es la de "nadie lo midio" contra "no salio ni una
    unidad", y ``src.order_fulfillment`` las cuenta en denominadores distintos.
    Como ``False == 0`` en Python, aplastar el booleano con ``float(...)`` convierte
    cada linea sin dato en un quiebre total y el fill rate del cliente en cero: de
    ahi el ``isinstance(value, bool)`` antes de cualquier conversion.
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        numero = float(value)
    except (TypeError, ValueError):
        return None
    return numero if math.isfinite(numero) else None


def _sumar_dias(dia: str | None, plazo: float | None) -> str | None:
    """``dia`` corrido ``plazo`` dias, o ``None`` si falta cualquiera de los dos.

    La suma se hace sobre la fecha de la ORDEN y con el plazo medido, que es la
    unica combinacion que no reconstruye la promesa del proveedor: partir de
    ``date_planned`` y sumarle algo daria ``date_order + seller.delay + algo``,
    o sea la ficha otra vez, disfrazada de calculo.
    """
    if not dia or plazo is None:
        return None
    try:
        base = datetime.strptime(dia[:10], "%Y-%m-%d")
    except ValueError:
        return None
    return f"{base + timedelta(days=float(plazo)):%Y-%m-%d}"


def _m2o_name(value: Any) -> str | None:
    """La mitad del nombre de un many2one, que ``_m2o_id`` descarta."""
    if isinstance(value, (list, tuple)) and len(value) > 1 and value[1]:
        return str(value[1])
    return None


def _as_datetime(value: Any) -> datetime | None:
    """'YYYY-MM-DD HH:MM:SS' (o solo la fecha) -> datetime naive en UTC.

    Y NO ``_as_date``: su truncado a diez caracteres es un error de un dia entero
    sobre una validacion de las 21:00 en Santiago, que en UTC cae al dia
    siguiente. Sobre un plazo declarado de 7 dias eso es 14%, por encima del
    umbral de materialidad, o sea suficiente para meter o sacar a un proveedor de
    la lista de SKU a recalcular por un artefacto de huso horario.
    """
    if not value:
        return None
    texto = str(value)
    for formato in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d"):
        try:
            return datetime.strptime(texto, formato)
        except ValueError:
            continue
    return None


def _m2o_id(value: Any) -> int | None:
    """Odoo many2one fields read back as ``[id, display_name]`` (or ``False`` when unset)."""
    if isinstance(value, (list, tuple)) and value:
        return int(value[0])
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    return None


@dataclass(frozen=True)
class OdooReceipts:
    """Lo que `OdooConnector.receipts()` devuelve: filas listas para el scorecard."""

    #: Nombre del proveedor comercial -> filas que `score_supplier` come.
    grouped: dict[str, list[dict]]
    #: Movimientos descartados y por que. Las tres primeras claves son identicas a
    #: las del camino del CSV para que un mismo renderer diga lo mismo.
    dropped: dict[str, int]
    #: Lo que el lector tiene que saber para no sobre-leer el numero.
    qa_issues: tuple[str, ...]


@dataclass(frozen=True)
class OdooLeadTimes:
    """Lo que `OdooConnector.declared_lead_times()` devuelve: la promesa del ERP,
    y los SKU sobre los que el ERP no promete nada.

    Misma forma que :class:`OdooReceipts` a proposito -- un hueco que no viaja
    junto al dato no llega a ninguna pantalla, y ese fue el defecto original.
    """

    #: SKU -> plazo declarado en dias. Solo los que traen uno utilizable.
    declared: dict[str, float]
    #: SKU con linea de proveedor pero sin plazo cargado, ordenados para que el
    #: aviso no cambie entre corridas.
    missing: tuple[str, ...]
    #: Lo que el lector tiene que saber para no sobre-leer el numero.
    qa_issues: tuple[str, ...]


#: Cuantos SKU se nombran en el aviso antes de resumir el resto. Un aviso con
#: cinco mil codigos no se lee, y uno sin ninguno no es accionable.
_MAX_SKU_EN_AVISO = 10


@dataclass(frozen=True)
class OdooOpenPurchases:
    """Lo que `OdooConnector.open_purchase_qty_by_sku()` devuelve.

    Se devuelve un objeto y no un `dict` pelado a proposito: un mapa sin las
    bajas se lee como "esto es todo lo que hay en camino", y una linea descartada
    por unidad de medida distinta es exactamente lo que el lector necesita saber
    antes de creerle al numero.
    """

    #: SKU -> unidades pendientes de recibir, sumadas sobre sus lineas de OC.
    #: Un SKU AUSENTE significa "sin OC abiertas"; el consumidor no debe
    #: convertirlo en 0.0 sin decirlo.
    by_sku: dict[str, float]
    #: Lineas descartadas y por que. `uom_distinta` es la que importa: son
    #: compromisos reales que no se pudieron contar sin inventar una conversion.
    dropped: dict[str, int]
    #: True cuando se alcanzo `max_lines` y la lectura quedo corta: el mapa
    #: sub-estima el en-transito, o sea que el plan compra de mas, no de menos.
    truncated: bool = False


@dataclass(frozen=True)
class _OpenPoLine:
    """Una linea de OC confirmada con algo pendiente de recibir, ya en unidades del producto."""

    sku: str
    pending: float
    promised_date: str | None
    ordered_date: str | None
    supplier: str | None
    order_name: str


@dataclass(frozen=True)
class _OpenPoScan:
    """Lo que devuelve el lector unico: las lineas que cuentan y las que no, con motivo."""

    lines: tuple[_OpenPoLine, ...]
    dropped: dict[str, int]
    truncated: bool


@dataclass(frozen=True)
class DraftPOResult:
    """Outcome of staging draft purchase orders: what was created and what couldn't be sourced."""

    purchase_orders: dict[int, tuple[str, ...]]   # new PO id -> the SKUs on it
    unsourced: tuple[str, ...]                     # SKUs with no supplier in Odoo (skipped, not dropped)
    #: sku -> (lo que pedia el plan, lo que se pidio al proveedor) cuando el minimo
    #: de pedido del proveedor (product.supplierinfo.min_qty) obligo a subir.
    #: Solo el minimo: el multiplo de caja no se lee de Odoo (no es campo estandar
    #: de supplierinfo); esa correccion la hace el comprador al confirmar.
    raised_to_min: dict[str, tuple[float, float]] = _dc_field(default_factory=dict)

    @property
    def n_orders(self) -> int:
        return len(self.purchase_orders)


def _cuenta(valores) -> dict:
    salida: dict = {}
    for v in valores:
        salida[v] = salida.get(v, 0) + 1
    return salida


def _aviso_plazos(missing: tuple[str, ...], n_con_linea: int) -> tuple[str, ...]:
    """El aviso de plazos declarados ausentes, o nada si no falta ninguno.

    Vacio cuando no hay SKU sin plazo -- incluido el caso de un Odoo sin ninguna
    linea de proveedor, que no tiene plazos ausentes sino que no tiene plazos. Un
    aviso que aparece siempre deja de leerse, que es como se pierde el unico
    que importaba.
    """
    if not missing:
        return ()
    nombrados = ", ".join(missing[:_MAX_SKU_EN_AVISO])
    resto = len(missing) - _MAX_SKU_EN_AVISO
    if resto > 0:
        nombrados += f" y {resto} mas"
    return (
        f"plazo declarado ausente en {len(missing)} de {n_con_linea} SKU con linea de "
        f"proveedor ({nombrados}): el ERP no registra cuanto promete el proveedor, asi "
        "que no se reporta plazo para esos SKU en vez de reportar cero. El plazo "
        "observado se mide aparte, con receipts().",
    )


def _collapse_shipments(filas: list[dict]) -> list[dict]:
    """Los movimientos de un mismo envio se vuelven UNA observacion.

    Un camion de 40 lineas emitido como 40 valores identicos no tiene dispersion
    interna, y `score_supplier` calcula desviacion y p90 sobre una lista plana:
    el resultado es un `lead_time_std` falsamente apretado que despues viaja al
    stock de seguridad. Una recepcion es un evento, no cuarenta.
    """
    por_envio: dict[tuple, list[dict]] = {}
    for fila in filas:
        por_envio.setdefault((fila["supplier"], fila["po_id"], fila["receipt_date"]), []).append(fila)
    salida: list[dict] = []
    for grupo in por_envio.values():
        fila = dict(grupo[0])
        skus = tuple(sorted({g["product_id"] for g in grupo if g.get("product_id")}))
        if len(skus) == 1:
            fila["product_id"] = skus[0]
        else:
            # Una fila lleva un solo SKU; inventar un representante fabricaria la
            # lista de SKU a recalcular. Se omite y viajan todos aparte.
            fila.pop("product_id", None)
            fila["product_ids"] = skus
        salida.append(fila)
    return salida


class OdooConnector:
    """``InventorySource`` over Odoo, with safe-staging restock as reorder-point writes.

    ``ledger``, when given (a ``nosilo_core.writeback_store.SqliteAuditLedger``), persists the
    audit/idempotency bookkeeping for BOTH write paths (reorder points and draft POs)
    so a process restart cannot re-apply an idempotency key or lose rollback data.
    Without one, bookkeeping lives in process memory - unchanged from before.
    """

    def __init__(self, rpc: OdooRPC, *, ledger: object | None = None) -> None:
        self._rpc = rpc
        self._sku_by_id: dict[int, str] = {}
        self._id_by_sku: dict[str, int] = {}
        # product_id -> uom_id del producto, para descartar lineas de OC que
        # vienen en otra unidad en vez de sumarlas como si fueran la misma.
        self._uom_by_product: dict[int, int | None] = {}
        self._field_cache: dict[tuple[str, str], bool] = {}
        # sku -> minimo de pedido del proveedor primario (product.supplierinfo.min_qty).
        # Se llena en primary_supplier_by_sku(); lo usa el borrador de PO para no
        # pedirle 37 unidades a un proveedor que vende de a 100.
        self._min_qty_by_sku: dict[str, float] = {}
        #: sku -> (pedido por el plan, pedido al proveedor) de la ultima puesta en
        #: escena de PO, solo para los SKU que subieron al minimo. Lo lee el
        #: entregable para decirlo; el numero del plan no se toca.
        self.last_moq_adjustments: dict[str, tuple[float, float]] = {}
        self._rules = _ReorderRuleStore(rpc, self._id_by_sku, ledger=ledger)
        self._po_store = _DraftPoStore(rpc, self._id_by_sku, ledger=ledger)

    def _has_field(self, model: str, field: str) -> bool:
        """Si esta base expone ``field`` en ``model``. Se sondea una vez por conector.

        Pedir en un ``read`` un campo que el modelo no tiene revuelve un error del
        servidor, y que un campo opcional exista depende de los modulos instalados
        (``qty_delivered`` y ``sale_line_id`` llegan con el puente de Ventas e
        Inventario). Mismo sondeo que ``receipts()`` hace con ``purchase_line_id``,
        con la diferencia de que aqui la ausencia no es un error: el servicio se
        reporta como no medido, que es lo que efectivamente pasa.
        """
        clave = (model, field)
        if clave not in self._field_cache:
            campos = self._rpc.execute_kw(model, "fields_get", [[field]], {"attributes": ["type"]})
            self._field_cache[clave] = field in (campos or {})
        return self._field_cache[clave]

    # -- read side (InventorySource) ------------------------------------------

    def list_products(self) -> list[Product]:
        rows = self._rpc.execute_kw(
            _M_PRODUCT,
            "search_read",
            [[["default_code", "!=", False]]],
            {"fields": ["default_code", "name", "list_price", "standard_price"]},
        )
        products: list[Product] = []
        for r in rows:
            sku = str(r["default_code"])
            self._sku_by_id[r["id"]] = sku
            self._id_by_sku[sku] = r["id"]
            products.append(
                Product(
                    sku,
                    str(r.get("name") or sku),
                    float(r.get("list_price") or 0.0),
                    float(r.get("standard_price") or 0.0),
                )
            )
        return products

    def inventory_levels(self) -> list[InventoryLevel]:
        internal = self._rpc.execute_kw(_M_LOCATION, "search", [[["usage", "=", "internal"]]])
        domain = [["location_id", "in", internal]] if internal else []
        rows = self._rpc.execute_kw(
            _M_QUANT, "search_read", [domain], {"fields": ["product_id", "quantity"]}
        )
        totals: dict[int, float] = {}
        for r in rows:
            pid = _m2o_id(r.get("product_id"))
            if pid is not None:
                totals[pid] = totals.get(pid, 0.0) + float(r.get("quantity") or 0.0)
        levels: list[InventoryLevel] = []
        for pid, qty in totals.items():
            sku = self._sku(pid)
            if sku is not None:
                levels.append(InventoryLevel(sku, qty))
        return levels

    def orders(self, *, since: str | None = None) -> list[Order]:
        """Las ordenes confirmadas, con lo pedido Y lo servido en cada linea.

        Las tres columnas de servicio (``delivered_qty``, ``promised_date``,
        ``delivered_date``) son las que ``src.order_fulfillment`` necesita para
        medir fill rate, OTIF y venta perdida reales. Las tres son opcionales en
        una base viva, y cada ausencia viaja como ``None`` hasta el entregable:

        * ``commitment_date`` es la promesa, y es de la ORDEN, no de la linea -
          por eso todas las lineas de una orden llevan la misma. En blanco (lo
          habitual) significa que no se prometio fecha, y la linea queda fuera de
          OTIF en vez de medirse contra ``date_order``, que es cuando el cliente
          compro y no cuando le dijimos que llegaba.
        * la fecha de entrega sale del movimiento de stock, no de
          ``sale.order.effective_date``: ese campo es la validacion del PRIMER
          despacho de la orden entera, asi que en una orden servida en dos
          camiones le regala a las lineas del segundo la fecha del primero y las
          declara a tiempo. Se mide por linea y con el ULTIMO movimiento hecho
          (ver ``_delivery_dates``).
        """
        domain: list = [["state", "in", list(_SALE_STATES)]]
        if since is not None:
            domain.append(["date_order", ">=", since])
        head_fields = ["name", "date_order", "order_line"]
        if self._has_field(_M_SALE, "commitment_date"):
            head_fields.append("commitment_date")
        heads = self._rpc.execute_kw(_M_SALE, "search_read", [domain], {"fields": head_fields})
        line_ids = [lid for h in heads for lid in h.get("order_line", [])]
        lines_by_id = self._read_sale_lines(line_ids)
        shipped = self._delivery_dates(line_ids)
        out: list[Order] = []
        for h in heads:
            promised = _as_date_or_none(h.get("commitment_date"))
            lines = tuple(
                _dc_replace(
                    lines_by_id[lid], promised_date=promised, delivered_date=shipped.get(lid)
                )
                for lid in h.get("order_line", [])
                if lid in lines_by_id
            )
            out.append(Order(str(h["name"]), _as_date(h["date_order"]), lines))
        return sorted(out, key=lambda o: o.created_at)

    def _open_po_lines(self, *, max_lines: int = 20_000) -> _OpenPoScan | None:
        """El UNICO lector de lineas de OC abiertas: lo comprado y todavia no recibido.

        ``scheduled_receipts()`` (fechas por linea, para prometer) y
        ``open_purchase_qty_by_sku()`` (unidades por SKU, para no comprar dos veces
        lo mismo) son dos vistas de ESTA lectura. Antes eran dos lectores con dos
        filtros de estado y dos reglas de unidad de medida: un cliente con "Bloquear
        ordenes confirmadas" activado (toda OC confirmada pasa a ``done``) veia su
        en-transito en una vista y no en la otra.

        Devuelve ``None`` cuando la base no tiene modulo de Compras: no es un error,
        es una tienda que compra por fuera del ERP, y cada vista decide que hacer con
        esa ausencia. Levanta ``OdooError`` cuando el modulo existe pero no expone
        ``qty_received``: sin eso no se sabe cuanto llego, y devolver lo ordenado
        como pendiente sobre-estima el suministro y deja al cliente sin stock.

        Reglas compartidas, y por que cada una:

        * Solo ``state in ('purchase', 'done')``: una cotizacion (``draft``/``sent``)
          no es suministro, nadie la prometio todavia.
        * Pendiente = ``product_qty - qty_received`` con piso en cero. Una OC de 500
          con 480 recibidas aporta 20, no 500.
        * Una linea en otra unidad que la del producto se DESCARTA y se cuenta en
          ``dropped['uom_distinta']`` en vez de sumar cajas como unidades. Si la
          base no expone ``product_uom`` en la linea no hay con que comparar y
          se aplica la misma doctrina (en la duda se descarta): las lineas se
          cuentan en ``dropped['uom_no_verificable']`` y no suman.
        * Se filtra por estado de la LINEA, no por ``date_planned``: filtrar por la
          promesa dejaria fuera justo las OC atrasadas, que son las que mas importan.
        """
        if not self._has_field(_M_PO_LINE, "product_qty"):
            return None
        if not self._has_field(_M_PO_LINE, "qty_received"):
            raise OdooError(
                "este Odoo no expone purchase.order.line.qty_received, asi que no hay "
                "forma de saber cuanto de cada OC ya llego. Devolver lo ordenado como "
                "si estuviera pendiente sobre-estimaria el suministro y dejaria al "
                "cliente sin stock: se levanta en vez de adivinar."
            )
        self._ensure_catalog()
        uom_verificable = self._has_field(_M_PO_LINE, "product_uom")
        fields = ["product_id", "product_qty", "qty_received", "date_planned", "order_id"]
        if uom_verificable:
            fields.append("product_uom")
        filas = self._rpc.execute_kw(
            _M_PO_LINE,
            "search_read",
            [[["state", "in", list(_PO_OPEN_STATES)]]],
            {"fields": fields, "limit": max_lines},
        )
        truncated = len(filas) >= max_lines
        dropped = {"sin_sku": 0, "uom_distinta": 0, "sin_pendiente": 0, "uom_no_verificable": 0}

        order_ids = sorted({oid for oid in (_m2o_id(r.get("order_id")) for r in filas) if oid is not None})
        cabecera: dict[int, tuple[str, str | None, str | None]] = {}
        if order_ids:
            pedidos = self._rpc.execute_kw(
                _M_PO, "read", [order_ids], {"fields": ["name", "date_order", "partner_id"]}
            )
            for p in pedidos or []:
                cabecera[int(p["id"])] = (
                    str(p.get("name") or ""),
                    _as_date_or_none(p.get("date_order")),
                    _m2o_name(p.get("partner_id")),
                )

        lines: list[_OpenPoLine] = []
        for r in filas:
            pid = _m2o_id(r.get("product_id"))
            if pid is None:
                dropped["sin_sku"] += 1
                continue
            pendiente = (_as_qty_or_none(r.get("product_qty")) or 0.0) - (
                _as_qty_or_none(r.get("qty_received")) or 0.0
            )
            if pendiente <= 0:
                dropped["sin_pendiente"] += 1
                continue
            sku = self._sku(pid)
            if sku is None:
                dropped["sin_sku"] += 1
                continue
            if not uom_verificable:
                # Misma doctrina que _uom_matches_product: en la duda se descarta.
                # Sub-estimar el en-transito compra de mas; sobre-estimarlo deja al
                # cliente sin stock y el numero sigue pareciendo razonable. El
                # conteo viaja en dropped para que read_on_order lo diga en palabras.
                dropped["uom_no_verificable"] += 1
                continue
            if not self._uom_matches_product(pid, _m2o_id(r.get("product_uom"))):
                dropped["uom_distinta"] += 1
                continue
            oid = _m2o_id(r.get("order_id"))
            orden, ordenada, proveedor = cabecera.get(oid if oid is not None else -1, ("", None, None))
            lines.append(
                _OpenPoLine(
                    sku=sku,
                    pending=pendiente,
                    promised_date=_as_date_or_none(r.get("date_planned")),
                    ordered_date=ordenada,
                    supplier=proveedor,
                    order_name=orden,
                )
            )
        return _OpenPoScan(tuple(lines), dropped, truncated)

    def scheduled_receipts(
        self,
        *,
        until: str | None = None,
        measured_lead_times: Any = None,
    ) -> list[ScheduledReceipt]:
        """Lo comprado y todavia no recibido: la mirada hacia ADELANTE.

        Vista con fechas del lector unico ``_open_po_lines()`` (ahi viven el filtro
        de estado, la regla de pendiente y la de unidad de medida, compartidas con
        ``open_purchase_qty_by_sku()``). ``receipts()`` mide el plazo que un
        proveedor CORRIO, restando fechas de movimientos ya hechos. Esta lee lo que
        falta llegar, que es lo unico que deja prometerle a un cliente una fecha
        apoyada en algo mas que el stock que ya esta en el estante.

        Cada recepcion lleva DOS fechas y no se colapsan nunca:

        * ``promised_date`` es ``purchase.order.line.date_planned``. El docstring
          de ``receipts()`` ya explica que en Odoo eso vale
          ``date_order + seller.delay``: la ficha del proveedor, no su conducta.
        * ``expected_date`` es ``date_order`` mas el plazo MEDIDO de ese
          proveedor, que entra por ``measured_lead_times`` (lo produce
          ``supplier_scorecard`` sobre las filas de ``receipts()``). Sin plazo
          medido queda en ``None`` -- jamas la prometida con otro nombre.

        Con ``until``, se descarta lo que llega despues del horizonte pedido. Una
        recepcion a noventa dias no le sirve a un pedido de esta semana, y
        arrastrarla engorda el disponible con unidades que no van a estar. Sin
        modulo de Compras devuelve vacio: una tienda que compra por fuera del ERP
        no tiene nada en camino que leer, y eso no es un error.
        """
        scan = self._open_po_lines()
        if scan is None:
            return []
        plazos = dict(measured_lead_times or {})
        salida: list[ScheduledReceipt] = []
        for ln in scan.lines:
            plazo = plazos.get(ln.supplier) if ln.supplier is not None else None
            esperada = _sumar_dias(ln.ordered_date, plazo)
            horizonte = esperada or ln.promised_date
            if until is not None and horizonte is not None and horizonte > until:
                continue
            salida.append(
                ScheduledReceipt(
                    sku=ln.sku,
                    quantity=ln.pending,
                    promised_date=ln.promised_date,
                    expected_date=esperada,
                    expected_source=LEAD_TIME_MEASURED if esperada else None,
                    supplier=ln.supplier,
                    order_id=ln.order_name,
                )
            )
        return salida

    def _delivery_dates(self, line_ids: list[int]) -> dict[int, str]:
        """Cuando quedo servida cada linea: su ULTIMA salida hecha.

        El ultimo movimiento y no el primero: una linea despachada en dos partes no
        esta entregada hasta que sale la segunda, y quedarse con la primera fecha es
        exactamente lo que vuelve puntual a un despacho parcial que llego tarde -
        el sesgo va en la direccion que le conviene al que reporta.

        Sin ``stock.move.sale_line_id`` (el puente Ventas-Inventario no instalado)
        no hay con que anclar el movimiento a la linea: se devuelve vacio y las
        lineas quedan sin fecha, o sea fuera de OTIF. No se sustituye por la fecha
        de la orden ni por la del picking: seria inventar la medicion que faltaba.
        """
        if not line_ids or not self._has_field(_M_MOVE, "sale_line_id"):
            return {}
        type_ids = self._rpc.execute_kw(
            _M_PICKING_TYPE, "search", [[["code", "in", list(_DELIVERY_TYPE_CODES)]]]
        )
        if not type_ids:
            return {}
        rows = self._rpc.execute_kw(
            _M_MOVE,
            "search_read",
            [
                [
                    ["state", "=", _MOVE_DONE],
                    ["sale_line_id", "in", list(line_ids)],
                    ["picking_type_id", "in", list(type_ids)],
                ]
            ],
            {"fields": ["sale_line_id", "date"]},
        )
        ultima: dict[int, str] = {}
        for r in rows:
            lid = _m2o_id(r.get("sale_line_id"))
            dia = _as_date(r.get("date"))
            if lid is None or not dia:
                continue
            if dia > ultima.get(lid, ""):
                ultima[lid] = dia
        return ultima

    # -- demand + lead-time bridges into the existing engines ------------------

    def demand_frame(self) -> pd.DataFrame:
        """Sales lines as a ``(date, product_id, quantity, unit_cost)`` demand history.

        Same shape ``src.sources.DataFrameDemandSource`` consumes, so an Odoo backend
        drops straight into the forecasting / inventory engines.
        """
        costs = {p.sku: p.cost for p in self.list_products()}
        rows = [
            {"date": o.created_at, "product_id": ln.sku, "quantity": ln.quantity, "unit_cost": costs.get(ln.sku, 0.0)}
            for o in self.orders()
            for ln in o.lines
        ]
        frame = pd.DataFrame(rows, columns=["date", "product_id", "quantity", "unit_cost"])
        if frame.empty:
            return frame
        return frame.groupby(["date", "product_id"], as_index=False).agg(
            quantity=("quantity", "sum"), unit_cost=("unit_cost", "first")
        )

    def declared_lead_times(self) -> OdooLeadTimes:
        """El plazo DECLARADO por SKU, con los que no lo traen nombrados aparte.

        ``product.supplierinfo.delay`` es la **promesa** del proveedor, no una
        observacion; la contraparte medida es :meth:`receipts`. Alimenta el
        ``lead_time_days`` canonico que consumen los motores de inventario y la
        diferenciacion del periodo de riesgo. Solo se mapean las lineas con
        variante.

        **Un ``delay`` que no supera ``_DECLARED_DELAY_FLOOR`` no se convierte en
        un plazo.** La conversion original era ``float(r.get("delay") or 0.0)``, y
        es el mismo defecto que ``src/order_fulfillment.py`` y
        ``src/supplier_scorecard.py`` ya nombran: una ausencia renderizada como
        medicion. Duele mas aca que en una tasa, porque un plazo de compra corto
        no es una lectura neutra sino la mas favorable que existe -- dice que el
        proveedor entrega casi en el acto, y de ahi sale un buffer que no cubre
        nada.

        El piso es el mismo que aplica :meth:`receipts` sobre el mismo campo, y
        por la misma razon: ``delay`` viene con ``default=1`` en Odoo, asi que un
        1 no es una promesa sino el campo sin configurar, y desde XML-RPC no se
        distingue del 1 que alguien escribio a mano. Ante dos lecturas posibles
        se toma la que no baja la proteccion recomendada. Que las dos lecturas
        del campo compartan la constante es deliberado: dos umbrales distintos
        para el mismo hecho, en el mismo archivo, es como empiezan a divergir.

        Una segunda linea del mismo SKU con plazo utilizable le gana a una
        primera sin plazo: una linea que no promete nada no tiene por que tapar
        a una que si. Antes ganaba siempre la primera que llegara.
        """
        rows = self._rpc.execute_kw(
            _M_SUPPLIERINFO, "search_read", [[]], {"fields": ["product_id", "delay"]}
        )
        declared: dict[str, float] = {}
        con_linea: list[str] = []          # orden de aparicion, sin repetir
        for r in rows:
            pid = _m2o_id(r.get("product_id"))
            if pid is None:
                continue
            sku = self._sku(pid)
            if sku is None:
                continue
            if sku not in con_linea:
                con_linea.append(sku)
            if sku in declared:
                continue
            dias = float(r.get("delay") or 0.0)
            if dias > _DECLARED_DELAY_FLOOR:
                declared[sku] = dias
        missing = tuple(sorted(s for s in con_linea if s not in declared))
        return OdooLeadTimes(
            declared=declared, missing=missing, qa_issues=_aviso_plazos(missing, len(con_linea))
        )

    def lead_times(self) -> dict[str, float]:
        """Plazo declarado por SKU, solo para los que el ERP lo trae.

        Accesorio de :meth:`declared_lead_times` para el llamador que solo quiere
        el diccionario. Un SKU sin plazo cargado **no aparece** en vez de aparecer
        con cero; el porque, y el hueco que hay que mostrar en su lugar, estan en
        ese metodo.
        """
        return self.declared_lead_times().declared

    def open_purchase_qty_by_sku(self, *, max_lines: int = 20_000) -> OdooOpenPurchases:
        """Unidades ya compradas y aun no recibidas, por SKU.

        Vista agregada del lector unico ``_open_po_lines()``: mismas lineas, mismo
        filtro de estado y misma regla de unidad que ``scheduled_receipts()``, sumadas
        por SKU. Es la entrada que le falta a la reposicion para no comprar dos veces
        lo mismo: ``src.connectors.replenish.plan_replenishment(on_order_by_sku=...)``
        consume este mapa. Sin el, la cantidad se dimensiona contra el stock en
        estante y una OC en camino se vuelve a pedir entera.

        Sin modulo de Compras LEVANTA ``OdooError`` en vez de devolver un mapa vacio:
        ``{}`` se leeria aguas abajo como "medido, y no viene nada", y lo cierto es que
        no se pudo medir. El que llama decide (``jobs.odoo_job.read_on_order`` pasa
        ``None`` y lo dice en palabras).

        Un SKU AUSENTE del mapa significa "sin OC abiertas"; el consumidor no debe
        convertirlo en 0.0 sin decirlo. Las lineas descartadas viajan en
        ``dropped`` (``uom_distinta`` es la que importa: compromisos reales que no
        se pudieron contar sin inventar una conversion) y ``truncated`` avisa que la
        lectura quedo corta y el mapa sub-estima el en-transito.
        """
        scan = self._open_po_lines(max_lines=max_lines)
        if scan is None:
            raise OdooError(
                "este Odoo no tiene modulo de Compras (purchase.order.line sin "
                "product_qty): no hay OC abiertas que leer. Un mapa vacio se leeria "
                "como 'medido: nada en camino', asi que se levanta para que el plan "
                "diga que no pudo netear."
            )
        out: dict[str, float] = {}
        for ln in scan.lines:
            out[ln.sku] = out.get(ln.sku, 0.0) + ln.pending
        return OdooOpenPurchases(out, scan.dropped, scan.truncated)

    def _uom_matches_product(self, product_id: int, line_uom_id: int | None) -> bool:
        """True cuando la linea esta en la unidad de stock del producto.

        Una linea sin ``product_uom`` legible se trata como NO coincidente: en la
        duda se descarta, porque el costo de sumar cajas como unidades lo paga el
        cliente en quiebre y no se ve en ningun lado.
        """
        if line_uom_id is None:
            return False
        if product_id not in self._uom_by_product:
            rows = self._rpc.execute_kw(
                _M_PRODUCT, "read", [[product_id]], {"fields": ["uom_id"]}
            )
            self._uom_by_product[product_id] = _m2o_id(rows[0].get("uom_id")) if rows else None
        return self._uom_by_product[product_id] == line_uom_id

    def receipts(
        self, *, since: str | None = None, unit: str = "shipment", max_moves: int = 20_000
    ) -> OdooReceipts:
        """Recepciones de compra REALES, agrupadas por proveedor comercial.

        Es la contraparte observada de :meth:`lead_times`. Esa lee
        ``product.supplierinfo.delay``, que es la **promesa** del ERP; esta mide
        ``stock.move.date`` menos ``purchase.order.date_order`` y de ningun otro
        lado. Sin las dos, la brecha que el producto existe para mostrar da cero
        por construccion sobre una conexion viva.

        Las filas que emite son las que ``supplier_scorecard.score_supplier``
        come, con las mismas claves que arma el camino del CSV.

        Lo que NO se lee, y por que cada uno seria una respuesta atractiva y mal:

        * ``purchase.order.effective_date`` -- es el ``min()`` de las recepciones
          hechas, asi que un proveedor que manda 10% al dia 12 y 90% al dia 47 se
          lee como 12 dias. Sesga *achicando* la brecha: esconde el defecto.
        * ``scheduled_date`` / ``date_planned`` / ``date_deadline`` -- son
          promesas, y ``date_planned`` es ``date_order + seller.delay``, o sea que
          medir contra ella reconstruye el propio ``delay`` y devuelve brecha ~0.
        * ``stock.picking.date`` -- ``purchase_stock`` la siembra desde el
          ``date_order`` de la OC, asi que la resta da cero para toda recepcion:
          un plazo uniforme y confiado de cero dias que colapsa el periodo de
          riesgo y borra el stock de seguridad.

        Declarado como NO resuelto, no como resuelto a medias:

        * ``date_done`` es el clic de validar, no la llegada del camion. Odoo no
          tiene un campo para lo segundo. Se reporta como aviso, no se corrige.
        * No se emiten unidades, defectos, ni cumplimiento de fecha: la cantidad
          del movimiento es demanda convertida de unidad de medida, no lo
          recibido, y no hay booleano defendible que ofrecer.
        * En una recepcion de dos o tres pasos esto mide la llegada al **anden**,
          no la puesta en estante: el filtro va por tipo de operacion y no por
          "el destino es una ubicacion interna", que le cobraria al proveedor el
          tiempo de control de calidad y guardado del propio cliente.
        """
        if unit not in ("shipment", "line"):
            raise OdooError(f"unit debe ser 'shipment' o 'line', no {unit!r}")

        # Sin el modulo de compras no hay con que anclar la orden. Se levanta
        # nombrandolo en vez de caer a `supplierinfo.delay`: sustituir la medicion
        # por la promesa en silencio ES el defecto.
        campos = self._rpc.execute_kw(
            _M_MOVE, "fields_get", [["purchase_line_id"]], {"attributes": ["type"]}
        )
        if "purchase_line_id" not in (campos or {}):
            raise OdooError(
                "este Odoo no tiene el modulo de compras instalado (stock.move no "
                "expone purchase_line_id), asi que no hay fecha de orden contra la "
                "cual medir. El plazo declarado de la ficha del proveedor no sirve "
                "de reemplazo: es la promesa que veniamos a contrastar."
            )

        self._ensure_catalog()
        qa: list[str] = []
        dropped = {"sin_fecha": 0, "plazo_negativo": 0, "sin_proveedor": 0, "po_no_confirmada": 0}

        type_ids = self._rpc.execute_kw(
            _M_PICKING_TYPE, "search", [[["code", "in", list(_RECEIPT_TYPE_CODES)]]]
        )
        if not type_ids:
            return OdooReceipts({}, dropped, ("este Odoo no tiene tipos de operacion de entrada",))

        # `purchase_line_id != False` es el unico leaf que hace falta para excluir
        # devoluciones de cliente, ajustes de inventario, componentes de produccion
        # y traslados entre bodegas: ninguno de esos movimientos tiene linea de OC.
        # Y el estado se filtra en el MOVIMIENTO, no en el picking: un picking
        # hecho puede contener movimientos cancelados que igual traen fecha.
        domain = [
            ["state", "=", _MOVE_DONE],
            ["purchase_line_id", "!=", False],
            ["picking_type_id", "in", list(type_ids)],
        ]
        if since:
            domain.append(["date", ">=", since])
        moves = self._rpc.execute_kw(
            _M_MOVE, "search_read", [domain],
            {"fields": ["date", "product_id", "purchase_line_id", "picking_id"], "limit": max_moves},
        )
        if len(moves) >= max_moves:
            qa.append(
                f"se leyeron {max_moves} movimientos, que es el tope de esta consulta: "
                "hay mas recepciones sin mirar. Acota con `since`."
            )
        if not moves:
            return OdooReceipts({}, dropped, tuple(qa) or ("no hay recepciones de compra hechas",))

        pol_ids = sorted({_m2o_id(m.get("purchase_line_id")) for m in moves} - {None})
        lineas = self._rpc.execute_kw(_M_PO_LINE, "read", [pol_ids], {"fields": ["order_id"]})
        po_por_linea = {r["id"]: _m2o_id(r.get("order_id")) for r in lineas}

        po_ids = sorted(set(po_por_linea.values()) - {None})
        ordenes = self._rpc.execute_kw(
            _M_PO, "read", [po_ids],
            {"fields": ["name", "date_order", "date_approve", "partner_id", "state"]},
        )
        po_por_id = {r["id"]: r for r in ordenes}

        # El proveedor sale SIEMPRE de la OC, nunca del picking: Odoo blanquea
        # `picking.partner_id` y `picking.origin` cuando se fusionan movimientos de
        # origenes distintos, y `origin` es texto libre que el asistente de
        # devolucion sobrescribe.
        partner_ids = sorted({_m2o_id(r.get("partner_id")) for r in ordenes} - {None})
        socios = self._rpc.execute_kw(
            _M_PARTNER, "read", [partner_ids], {"fields": ["name", "commercial_partner_id"]}
        )
        # Se agrupa por la empresa comercial: sin eso "Acme - Sucursal Santiago" y
        # "Acme - Sucursal Norte" son dos proveedores distintos, cada uno capaz de
        # quedar bajo el minimo de recepciones y desaparecer del entregable.
        comercial: dict[int, str] = {}
        for r in socios:
            nombre = _m2o_name(r.get("commercial_partner_id")) or r.get("name")
            comercial[r["id"]] = str(nombre) if nombre else ""

        declarado = self._declared_by_partner(set(comercial) | set(partner_ids))

        filas: list[dict] = []
        for m in moves:
            pol = _m2o_id(m.get("purchase_line_id"))
            po = po_por_id.get(po_por_linea.get(pol))
            if po is None or po.get("state") not in _PO_REALIZED_STATES:
                dropped["po_no_confirmada"] += 1
                continue
            recibido, ordenado = _as_datetime(m.get("date")), _as_datetime(po.get("date_order"))
            if recibido is None or ordenado is None:
                dropped["sin_fecha"] += 1
                continue
            partner = _m2o_id(po.get("partner_id"))
            proveedor = comercial.get(partner, "")
            if not proveedor:
                dropped["sin_proveedor"] += 1
                continue
            dias = (recibido - ordenado).total_seconds() / 86400.0
            if dias < 0:
                # Un plazo negativo es un dato roto, no un plazo de cero dias:
                # promediarlo baja el observado y disimula justo la brecha.
                dropped["plazo_negativo"] += 1
                continue

            pid = _m2o_id(m.get("product_id"))
            fila = {
                "lead_time_days": dias,
                "lead_time_source": LEAD_TIME_MEASURED,
                "supplier": proveedor,
                "po_id": str(po.get("name") or ""),
                "order_date": ordenado,
                "receipt_date": recibido,
                "picking_id": _m2o_id(m.get("picking_id")),
                "partner_id": partner,
                # Sin `default_code` se cae al id en vez de desaparecer: un SKU que
                # se evapora rompe la reconciliacion contra el total del ERP.
                "product_id": (self._sku(pid) or str(pid)) if pid is not None else None,
            }
            promesa = declarado.get(partner) or declarado.get(
                next((k for k, v in comercial.items() if v == proveedor), None)
            )
            # Ausente y no cero: una clave que falta se lee como "no medido".
            if promesa is not None and promesa > _DECLARED_DELAY_FLOOR:
                fila["declared_lead_time_days"] = float(promesa)
            aprobada = _as_datetime(po.get("date_approve"))
            if aprobada is not None:
                # Aparte, para que una OC que espero tres semanas la aprobacion no
                # se le cobre al proveedor.
                fila["approval_latency_days"] = (aprobada - ordenado).total_seconds() / 86400.0
            filas.append(fila)

        if unit == "shipment":
            filas = _collapse_shipments(filas)
            qa.append(
                "una recepcion cuenta una vez aunque traiga varias lineas (unit='shipment'). "
                "Con unit='line' se emite una fila por movimiento, como el camino del CSV."
            )

        grouped: dict[str, list[dict]] = {}
        for fila in filas:
            grouped.setdefault(fila.pop("supplier"), []).append(fila)

        n_backorder = sum(
            1 for rows in grouped.values()
            for po, veces in _cuenta(r.get("po_id") for r in rows).items()
            if po and veces > 1
        )
        if n_backorder:
            qa.append(
                f"{n_backorder} orden(es) llegaron en mas de un envio. Cada envio cuenta como "
                "una observacion propia: no se promedia por orden, porque un proveedor que "
                "parte la entrega tarda de verdad lo que tarda el ultimo bulto."
            )
        qa.append(
            "el plazo se mide contra la validacion de la recepcion en Odoo, que es cuando "
            "alguien apreto el boton, no cuando llego el camion."
        )
        return OdooReceipts(grouped, dropped, tuple(qa))

    def _declared_by_partner(self, partner_ids: set[int | None]) -> dict[int, float]:
        """El plazo DECLARADO por proveedor, de ``product.supplierinfo.delay``.

        No reusa :meth:`lead_times`: esa se queda con la primera fila por producto
        sin mirar el proveedor, o sea que puede aparear la promesa del proveedor A
        con el comportamiento del proveedor B.
        """
        ids = sorted(i for i in partner_ids if i is not None)
        if not ids:
            return {}
        rows = self._rpc.execute_kw(
            _M_SUPPLIERINFO, "search_read", [[["partner_id", "in", ids]]],
            {"fields": ["partner_id", "delay", "sequence"]},
        )
        mejor: dict[int, tuple[int, float]] = {}
        for r in rows:
            pid = _m2o_id(r.get("partner_id"))
            delay = r.get("delay")
            if pid is None or delay is None or delay is False:
                continue
            seq = int(r.get("sequence") or 0)
            if pid not in mejor or seq < mejor[pid][0]:
                mejor[pid] = (seq, float(delay))
        return {pid: valor for pid, (_, valor) in mejor.items()}

    # -- write side (safe-staging restock -> reorder point) -------------------

    def stage_restock(self, restock: dict[str, float], *, idempotency_key: str, reason: str = "") -> writeback.Changeset:
        """Stage a dry-run reorder-point update (min qty = on-hand + restock). Does NOT write.

        The restock delta from the engines is interpreted as the *target* cover level, so
        Odoo's own replenishment generates the POs. Reversible: applying only edits a field.
        """
        self._ensure_catalog()
        on_hand = {lvl.sku: lvl.available for lvl in self.inventory_levels()}
        edits = {
            sku: {"product_min_qty": round(on_hand.get(sku, 0.0) + float(qty), 4)}
            for sku, qty in restock.items()
        }
        return writeback.stage(
            self._rules,
            _WB_TARGET,
            edits,
            risk_tier=writeback.TIER_REVERSIBLE,
            idempotency_key=idempotency_key,
            reason=reason,
        )

    def apply_restock(
        self,
        changeset: writeback.Changeset,
        *,
        approval: writeback.Approval | None = None,
        now: float | None = None,
        auto_apply_reversible: bool = True,
    ) -> writeback.ApplyResult:
        """Apply a staged reorder-point change. Reversible edits auto-apply by default; pass an
        ``approval`` (and ``auto_apply_reversible=False``) to require a human in the loop.
        ``now`` defaults to the real clock; pass an explicit value only in tests."""
        return writeback.apply(
            self._rules, changeset, approval=approval, now=now, auto_apply_reversible=auto_apply_reversible
        )

    def rollback(self, idempotency_key: str) -> None:
        """Undo an applied reorder-point OR draft-PO change (whichever holds the key)."""
        if idempotency_key in self._rules.applied_keys():
            self._rules.rollback(idempotency_key)
        elif idempotency_key in self._po_store.applied_keys():
            self._po_store.rollback(idempotency_key)
        else:
            raise KeyError(idempotency_key)

    # -- write side (draft purchase orders) -----------------------------------

    def primary_supplier_by_sku(self, skus: list[str]) -> dict[str, int]:
        """Map each SKU to its primary supplier's partner id (lowest-sequence product.supplierinfo).

        De paso guarda el minimo de pedido de ese proveedor (``min_qty``) en
        ``_min_qty_by_sku``: es la regla del proveedor que la PO borrador tiene que
        respetar, y vive en la misma fila que el proveedor, asi que se lee una vez.
        """
        self._ensure_catalog(skus)
        out: dict[str, int] = {}
        for sku in skus:
            pid = self._id_by_sku.get(sku)
            if pid is None:
                continue
            rows = self._rpc.execute_kw(
                _M_SUPPLIERINFO, "search_read", [[["product_id", "=", pid]]],
                {"fields": ["partner_id", "sequence", "min_qty"]},
            )
            if not rows:
                continue
            best = min(rows, key=lambda r: r.get("sequence") or 0)
            partner = _m2o_id(best.get("partner_id"))
            if partner is not None:
                out[sku] = partner
                self._min_qty_by_sku[sku] = float(best.get("min_qty") or 0.0)
        return out

    def stage_draft_purchase_orders(
        self, restock: dict[str, float], *, prices: dict[str, float] | None = None, reason: str = ""
    ) -> tuple[writeback.Changeset, tuple[str, ...]]:
        """Stage (dry-run) draft POs for the restock, grouped by each SKU's primary supplier.
        Does NOT write. Returns the changeset plus SKUs with no supplier (unsourced, not dropped).

        The idempotency key is derived from the changeset's own content hash, so staging
        the SAME restock+prices twice (e.g. a client retry) always resolves to the same
        key - a retry can idempotent-skip instead of raising a duplicate RFQ.
        """
        prices = prices or {}
        # primary_supplier_by_sku refresca el catalogo si le piden un SKU que no
        # conoce; llamarlo aca tambien releia el catalogo entero dos veces.
        suppliers = self.primary_supplier_by_sku(list(restock))
        by_supplier: dict[int, list[str]] = {}
        unsourced: list[str] = []
        for sku in restock:
            partner = suppliers.get(sku)
            if partner is None:
                unsourced.append(sku)
            else:
                by_supplier.setdefault(partner, []).append(sku)

        # La regla del proveedor manda sobre el numero del plan: 37 unidades a un
        # proveedor con minimo 100 es una PO que nadie va a aceptar. Se sube al
        # minimo y se deja anotado en last_moq_adjustments para que el entregable
        # lo diga; el plan sigue diciendo 37, que es lo que la demanda pide.
        # Solo el MINIMO: el multiplo de caja/pack no se lee de Odoo porque
        # product.supplierinfo no lo tiene como campo estandar (vive en
        # product.packaging, que no todos usan). Si un proveedor vende por cajas,
        # hoy el comprador lo redondea en Odoo al confirmar la PO borrador.
        self.last_moq_adjustments = {}
        ordered: dict[str, float] = {}
        for sku in restock:
            asked = float(restock[sku])
            qty = apply_order_rules(asked, minimum_order_quantity=self._min_qty_by_sku.get(sku, 0.0))
            ordered[sku] = qty
            if qty > asked:
                self.last_moq_adjustments[sku] = (asked, qty)

        changes = tuple(
            writeback.Change(
                f"supplier:{partner}",
                "draft_po_lines",
                None,  # nothing existed before (a create, not an edit) - matches stage()'s convention
                tuple(sorted((sku, ordered[sku], float(prices.get(sku, 0.0))) for sku in skus)),
            )
            for partner, skus in sorted(by_supplier.items())
        )
        # IRREVERSIBLE, and the "but a draft PO can be deleted" argument is exactly the
        # trap. Two reasons it does not hold:
        #
        #   1. Business, not database. `nosilo_core.writeback`'s own tier docstring names
        #      "send a PO" as THE example of an irreversible write. A draft PO landing in
        #      a buyer's queue is a purchase proposal from a system they trust; the
        #      undo window closes the moment somebody confirms it, and nothing here
        #      controls when that happens.
        #   2. The rollback does not survive a restart. _DraftPoStore tracks the created
        #      PO ids in `self._created`, process memory, and jobs/odoo_job.py builds the
        #      connector with no persistent ledger at all - so a rollback in a later
        #      process has nothing to unlink. A "reversible" tier is a PROMISE that undo
        #      works; that promise is not currently backed here.
        #
        # Reorder-point writes (stage_restock above) stay reversible: those set a field,
        # the prior value is captured in the changeset, and restoring it is exact.
        cs = writeback.Changeset(_WB_TARGET, changes, writeback.TIER_IRREVERSIBLE, idempotency_key="pending", reason=reason)
        cs = _dc_replace(cs, idempotency_key=cs.content_hash[:32])
        return cs, tuple(unsourced)

    def apply_draft_purchase_orders(
        self,
        changeset: writeback.Changeset,
        *,
        approval: writeback.Approval | None = None,
        now: float | None = None,
        auto_apply_reversible: bool = False,
    ) -> writeback.ApplyResult:
        """Apply staged draft POs. Requires a signed ``approval``: raises
        ``writeback.WritebackRefused`` without one.

        This used to default to ``auto_apply_reversible=True``, which - combined with the
        changeset being labelled reversible - meant a caller could put purchase orders
        into a client's ERP with `approval=None` and no human anywhere in the path. The
        tier is now IRREVERSIBLE (see stage_draft_purchase_orders), and
        ``writeback.requires_approval`` returns True for that tier regardless of this
        flag, so the parameter is kept only for symmetry with the reversible write paths;
        setting it True does NOT bypass approval here.

        ``now`` defaults to the real clock; pass an explicit value only in tests.
        """
        return writeback.apply(
            self._po_store, changeset, approval=approval, now=now, auto_apply_reversible=auto_apply_reversible
        )

    def create_draft_purchase_orders(
        self, restock: dict[str, float], *, approved_by: str, prices: dict[str, float] | None = None
    ) -> DraftPOResult:
        """Create DRAFT (RFQ) purchase orders for the restock, grouped by each SKU's primary supplier.

        Convenience one-shot: stage + apply through the safe-staging plane (idempotent on
        content, audited, rollback-able via ``rollback()``). SKUs with no supplier are
        skipped and reported in ``unsourced`` rather than silently dropped.

        ``approved_by`` is REQUIRED and has no default. It names the human accountable for
        this purchase and is what the audit entry records. This method used to apply with
        no approval at all, on the reasoning that "the unconfirmed draft IS the safety
        boundary; a buyer reviews and confirms it in Odoo". That reasoning is half right
        and the missing half matters: Odoo's draft state is a real second net, but moving
        the ONLY gate into Odoo means Kern keeps no record of who authorised the spend,
        and a buyer confronted with a purchase order generated by a system they trust is
        rubber-stamping, not reviewing.

        So the human is still required, just named. The caller asserting "I am the human"
        is a legitimate, auditable answer; the caller asserting nothing is not. Where the
        approval must come from someone OTHER than the caller - which is the normal case
        for an unattended run - use ``stage_draft_purchase_orders`` to produce the
        changeset, route it to that person, and apply with the approval they sign.
        """
        changeset, unsourced = self.stage_draft_purchase_orders(restock, prices=prices)
        if changeset.changes:
            approval = writeback.approve(changeset, approved_by)
            self.apply_draft_purchase_orders(changeset, approval=approval)
        created_by_entity = self._po_store.created_pos(changeset.idempotency_key)
        purchase_orders: dict[int, tuple[str, ...]] = {}
        for c in changeset.changes:
            po_id = created_by_entity.get(c.entity_id)
            if po_id is not None:
                purchase_orders[po_id] = tuple(sku for sku, _qty, _price in c.after)
        return DraftPOResult(
            purchase_orders=purchase_orders,
            unsourced=unsourced,
            raised_to_min=dict(self.last_moq_adjustments),
        )

    # -- internals ------------------------------------------------------------

    def _ensure_catalog(self, skus: list[str] | None = None) -> None:
        """Carga el catalogo si esta vacio, y lo REFRESCA si le piden un SKU que no conoce.

        Antes solo cargaba cuando el mapa estaba vacio, asi que un conector de vida
        larga (el de un job programado) nunca veia un producto dado de alta despues
        de su primer uso. Lo peor era como lo reportaba: el SKU caia en ``unsourced``
        ("sin proveedor en Odoo"), el cliente iba a darle un proveedor que ya tenia,
        y volvia a fallar igual. Un refresco por SKU desconocido cuesta una lectura
        y evita mandar a un humano a arreglar lo que no estaba roto.
        """
        if not self._id_by_sku or (skus and any(sku not in self._id_by_sku for sku in skus)):
            self.list_products()

    def _sku(self, product_id: int) -> str | None:
        if product_id not in self._sku_by_id:
            rows = self._rpc.execute_kw(_M_PRODUCT, "read", [[product_id]], {"fields": ["default_code"]})
            code = rows[0]["default_code"] if rows else None
            if not code:
                return None
            self._sku_by_id[product_id] = str(code)
            self._id_by_sku[str(code)] = product_id
        return self._sku_by_id[product_id]

    def _read_sale_lines(self, line_ids: list[int]) -> dict[int, OrderLine]:
        """Lo pedido y lo servido en cada linea; las fechas las estampa ``orders()``."""
        if not line_ids:
            return {}
        fields = ["product_id", "product_uom_qty", "price_unit"]
        if self._has_field(_M_SALE_LINE, "qty_delivered"):
            fields.append("qty_delivered")
        rows = self._rpc.execute_kw(_M_SALE_LINE, "read", [line_ids], {"fields": fields})
        out: dict[int, OrderLine] = {}
        for r in rows:
            pid = _m2o_id(r.get("product_id"))
            if pid is None:
                continue
            sku = self._sku(pid)
            if sku is not None:
                out[r["id"]] = OrderLine(
                    sku,
                    float(r.get("product_uom_qty") or 0.0),
                    float(r.get("price_unit") or 0.0),
                    delivered_qty=_as_qty_or_none(r.get("qty_delivered")),
                )
        return out


class _ReorderRuleStore:
    """writeback system-of-record surface (read/applied_keys/claim/release/commit/
    rollback) over Odoo reorder rules (``stock.warehouse.orderpoint``). Lets the
    connector reuse the entire safe-staging policy (tiers, approval, idempotency,
    audit) unchanged, against Odoo."""

    def __init__(self, rpc: OdooRPC, id_by_sku: dict[str, int], *, ledger: object | None = None) -> None:
        self._rpc = rpc
        self._id_by_sku = id_by_sku  # shared with the connector; filled by list_products()
        self._audit = writeback.AuditBookkeeping(ledger)

    def read(self, entity_id: str) -> dict:
        pid = self._id_by_sku.get(entity_id)
        if pid is None:
            return {}
        rows = self._rpc.execute_kw(
            _M_ORDERPOINT,
            "search_read",
            [[["product_id", "=", pid]]],
            {"fields": ["product_min_qty", "product_max_qty"], "limit": 1},
        )
        if not rows:
            return {}
        return {
            "product_min_qty": float(rows[0].get("product_min_qty") or 0.0),
            "product_max_qty": float(rows[0].get("product_max_qty") or 0.0),
        }

    def applied_keys(self) -> set[str]:
        return self._audit.applied_keys()

    def claim(self, idempotency_key: str, *, now: float | None = None) -> bool:
        return self._audit.claim(idempotency_key, now=now)

    def release(self, idempotency_key: str) -> None:
        self._audit.release(idempotency_key)

    def commit(self, changeset: writeback.Changeset, approved_by: str) -> writeback.AuditEntry:
        restore: list[tuple[str, str, object]] = []
        try:
            for c in changeset.changes:
                current = self.read(c.entity_id)
                restore.append((c.entity_id, c.field, current.get(c.field, writeback.ABSENT)))
                self._write_field(c.entity_id, c.field, c.after)
        except Exception:
            # A write partway through raised: everything written so far in THIS call is
            # already live in Odoo but not yet audited. Undo it with the same restore
            # values `rollback()` would use, before letting the failure propagate - a
            # local compensating transaction, since Odoo has no cross-call transaction
            # to lean on here. If a compensating write itself raises (e.g. Odoo drops
            # mid-undo), that's a compounding failure outside what a local compensation
            # can recover from - it surfaces (chained via __context__, nothing swallowed)
            # but leaves no audit entry, since record() below is unreached either way;
            # that residual needs manual reconciliation, same as a bare `rollback()`
            # failing partway today.
            self._apply_restore(restore)
            raise
        entry = writeback.AuditEntry(changeset.idempotency_key, changeset.target, approved_by, tuple(restore))
        self._audit.record(entry)
        return entry

    def rollback(self, idempotency_key: str) -> None:
        entry = self._audit.get(idempotency_key)
        if entry is None:
            raise KeyError(idempotency_key)
        self._apply_restore(entry.restore)
        self._audit.forget(idempotency_key)

    def _apply_restore(self, restore: list[tuple[str, str, object]] | tuple[tuple[str, str, object], ...]) -> None:
        for entity_id, fld, original in restore:
            if original is not writeback.ABSENT:  # a freshly-created rule is left in place (still reversible)
                self._write_field(entity_id, fld, original)

    def _write_field(self, sku: str, field: str, value: object) -> None:
        pid = self._id_by_sku.get(sku)
        if pid is None:
            raise OdooError(f"unknown SKU {sku!r}; call list_products() before staging a restock")
        existing = self._rpc.execute_kw(_M_ORDERPOINT, "search", [[["product_id", "=", pid]]], {"limit": 1})
        if existing:
            self._rpc.execute_kw(_M_ORDERPOINT, "write", [existing, {field: value}])
        else:
            self._rpc.execute_kw(_M_ORDERPOINT, "create", [{"product_id": pid, field: value}])


class _DraftPoStore:
    """writeback system-of-record surface over Odoo draft purchase orders.

    Lets draft-PO creation reuse the same safe-staging policy (idempotency, audit,
    rollback) as reorder-point writes, instead of calling ``execute_kw(... "create")``
    directly: previously a retry after a lost response could raise a duplicate RFQ,
    with no record of what was created and no way to undo it.

    One ``Change`` per supplier group: ``entity_id`` is a synthetic
    ``"supplier:<partner_id>"`` key (there is no pre-existing entity to diff against -
    a PO is created, not edited), ``before`` is always ``writeback.ABSENT``, and
    ``after`` is the sorted tuple of ``(sku, qty, price)`` lines for that supplier.
    """

    def __init__(self, rpc: OdooRPC, id_by_sku: dict[str, int], *, ledger: object | None = None) -> None:
        self._rpc = rpc
        self._id_by_sku = id_by_sku
        self._audit = writeback.AuditBookkeeping(ledger)
        # idempotency_key -> {entity_id: created po_id}, for the caller to map lines
        # back to the PO id and for rollback to know what to unlink.
        self._created: dict[str, dict[str, int]] = {}

    def read(self, entity_id: str) -> dict:
        return {}  # nothing to diff against: a draft PO is created, never edited here

    def applied_keys(self) -> set[str]:
        return self._audit.applied_keys()

    def claim(self, idempotency_key: str, *, now: float | None = None) -> bool:
        return self._audit.claim(idempotency_key, now=now)

    def release(self, idempotency_key: str) -> None:
        self._audit.release(idempotency_key)

    def created_pos(self, idempotency_key: str) -> dict[str, int]:
        """``{entity_id: po_id}`` created under ``idempotency_key`` (empty if unknown/rolled back)."""
        return dict(self._created.get(idempotency_key, {}))

    def commit(self, changeset: writeback.Changeset, approved_by: str) -> writeback.AuditEntry:
        restore: list[tuple[str, str, object]] = []
        created: dict[str, int] = {}
        try:
            for c in changeset.changes:
                if c.is_noop:
                    continue
                partner = int(c.entity_id.split(":", 1)[1])
                order_lines = [
                    (0, 0, {"product_id": self._id_by_sku[sku], "product_qty": qty, "price_unit": price, "name": sku})
                    for sku, qty, price in c.after
                ]
                po_id = self._rpc.execute_kw(_M_PO, "create", [{"partner_id": partner, "order_line": order_lines}])
                created[c.entity_id] = int(po_id)
                restore.append((c.entity_id, c.field, writeback.ABSENT))  # nothing existed before
        except Exception:
            # A create partway through raised: POs created so far in THIS call are
            # already live in Odoo but not yet audited, and `self._created` (the only
            # record of their ids) is still a local variable that would be discarded on
            # re-raise. Delete them before propagating - a local compensating
            # transaction, since Odoo has no cross-call transaction to lean on here. If
            # an unlink itself raises (compounding failure), that surfaces too (chained
            # via __context__, nothing swallowed) but leaves no audit entry describing
            # which POs are still orphaned - that residual needs manual reconciliation,
            # same as a bare `rollback()` failing partway today.
            self._unlink_all(created.values())
            raise
        entry = writeback.AuditEntry(changeset.idempotency_key, changeset.target, approved_by, tuple(restore))
        self._audit.record(entry)
        self._created[changeset.idempotency_key] = created
        return entry

    def rollback(self, idempotency_key: str) -> None:
        """Delete the POs created under ``idempotency_key``.

        Raises ``OdooError`` when the audit ledger knows the key but this process does
        not know which POs it created. That happens whenever the ledger is persistent
        (``SqliteAuditLedger``) and the apply ran in an EARLIER process, because
        ``self._created`` is process memory. The previous behaviour in that case was to
        unlink nothing, forget the audit entry, and return normally - a rollback that
        reports success, reverses nothing, and destroys the only record of what is still
        live in the client's ERP. Failing loudly is the only honest option: the POs are
        real and somebody has to go delete them by hand.
        """
        entry = self._audit.get(idempotency_key)
        if entry is None:
            raise KeyError(idempotency_key)
        created = self._created.get(idempotency_key)
        if created is None:
            raise OdooError(
                f"cannot roll back {idempotency_key!r}: this process has no record of the "
                "purchase orders it created (the apply ran in a different process). The "
                "POs are still live in Odoo and must be removed by hand; the audit entry "
                "has been left in place so the key is not silently reusable."
            )
        self._unlink_all(created.values())
        self._audit.forget(idempotency_key)
        self._created.pop(idempotency_key, None)

    def _unlink_all(self, po_ids) -> None:
        for po_id in po_ids:
            self._rpc.execute_kw(_M_PO, "unlink", [[po_id]])  # a draft PO can be deleted outright


# -- offline stand-in (the Odoo analogue of SimulatedStore / emulator) --------


class InMemoryOdoo:
    """Offline stand-in for an Odoo server: the slice of ``execute_kw`` the connector uses.

    Holds records per model as ``{id: {field: value}}`` and supports the handful of methods
    the connector calls (search / search_read / read / write / create) over simple domain
    leaves (``=``, ``!=``, ``in``, ``>=``). Lets the whole Odoo connector run and be tested
    end-to-end with no network or API key. Domains use flat fields only (the connector is
    written to avoid dotted relational leaves so this stand-in stays small).
    """

    def __init__(self, data: dict[str, dict[int, dict]] | None = None) -> None:
        self._data = {m: {i: dict(r) for i, r in recs.items()} for m, recs in (data or {}).items()}
        self._next = max([i for recs in self._data.values() for i in recs] + [0]) + 1

    def execute_kw(self, model: str, method: str, args: list, kwargs: dict | None = None) -> Any:
        handler = getattr(self, f"_op_{method}", None)
        if handler is None:
            raise OdooError(f"InMemoryOdoo does not implement execute_kw method {method!r}")
        return handler(model, list(args), dict(kwargs or {}))

    def records(self, model: str) -> dict[int, dict]:
        """Direct access to a model's records (read-only inspection in tests)."""
        return self._data.setdefault(model, {})

    def _match(self, rec: dict, domain: list) -> bool:
        for field, op, val in domain:
            cur = rec.get(field)
            if field.endswith("_id") and isinstance(cur, (list, tuple)) and cur:
                cur = cur[0]  # many2one stored as [id, name]
            if op == "=":
                if cur != val:
                    return False
            elif op == "!=":
                if cur == val:
                    return False
            elif op == "in":
                if cur not in val:
                    return False
            elif op == ">=":
                if not (cur is not None and cur >= val):
                    return False
            else:
                # Sin este else un operador no soportado no matcheaba ningun branch,
                # caia al final y el registro pasaba: una consulta con `<=` devolvia
                # TODAS las filas en vez de fallar. Suite verde sobre un numero malo.
                raise OdooError(f"InMemoryOdoo no soporta el operador de dominio {op!r}")
        return True

    def _project(self, rec_id: int, rec: dict, fields: list | None) -> dict:
        keys = fields if fields else list(rec.keys())
        row: dict = {"id": rec_id}
        for k in keys:
            row[k] = rec.get(k, False)
        return row

    def _op_fields_get(self, model: str, args: list, kwargs: dict) -> dict:
        """Que campos existen: lo que distingue "modulo no instalado" de "sin datos"."""
        pedidos = args[0] if args else []
        presentes = {k for r in self.records(model).values() for k in r}
        return {name: {"type": "many2one"} for name in pedidos if name in presentes}

    def _op_search(self, model: str, args: list, kwargs: dict) -> list[int]:
        domain = args[0] if args else []
        ids = [i for i, r in self.records(model).items() if self._match(r, domain)]
        limit = kwargs.get("limit")
        return ids[:limit] if limit else ids

    def _op_search_read(self, model: str, args: list, kwargs: dict) -> list[dict]:
        domain = args[0] if args else []
        fields = kwargs.get("fields")
        rows = [self._project(i, r, fields) for i, r in self.records(model).items() if self._match(r, domain)]
        limit = kwargs.get("limit")
        return rows[:limit] if limit else rows

    def _op_read(self, model: str, args: list, kwargs: dict) -> list[dict]:
        ids = args[0] if args else []
        fields = kwargs.get("fields")
        recs = self.records(model)
        return [self._project(i, recs[i], fields) for i in ids if i in recs]

    def _op_write(self, model: str, args: list, kwargs: dict) -> bool:
        ids, vals = args[0], args[1]
        recs = self.records(model)
        for i in ids:
            if i in recs:
                recs[i].update(vals)
        return True

    def _op_create(self, model: str, args: list, kwargs: dict) -> int:
        vals = args[0]
        new_id = self._next
        self._next += 1
        self.records(model)[new_id] = dict(vals)
        return new_id

    def _op_unlink(self, model: str, args: list, kwargs: dict) -> bool:
        ids = args[0] if args else []
        recs = self.records(model)
        for i in ids:
            recs.pop(i, None)
        return True


#: Eight weekly sale orders, not three. Widget stays at 10 units/order-day and Gizmo
#: at 5, so every forecast and restock figure downstream is unchanged -- SES on a
#: constant series returns that constant regardless of length. What changes is only
#: the DEPTH: three order-days is below plan_replenishment's min_observations gate
#: (src/forecasting.py's MIN_PERIODS_FOR_POLICY), so the old fixture exercised the
#: refusal branch instead of the replenishment mechanics these demos exist to show.
_DEMO_SALE_DATES = (
    "2026-01-05", "2026-01-12", "2026-01-19", "2026-01-26",
    "2026-02-02", "2026-02-09", "2026-02-16", "2026-02-23",
)
#: Alternating sale/done, preserving the original fixture's mix of order states.
_DEMO_SALES = {
    200 + i: {
        "name": f"S{i + 1:04d}",
        "date_order": f"{date} 10:00:00",
        "state": "done" if i % 3 == 2 else "sale",
        "order_line": [300 + 2 * i, 301 + 2 * i],
    }
    for i, date in enumerate(_DEMO_SALE_DATES)
}
_DEMO_SALE_LINES = {
    **{300 + 2 * i: {"product_id": [1, "Widget"], "product_uom_qty": 10.0, "price_unit": 20.0}
       for i in range(len(_DEMO_SALE_DATES))},
    **{301 + 2 * i: {"product_id": [3, "Gizmo"], "product_uom_qty": 5.0, "price_unit": 8.0}
       for i in range(len(_DEMO_SALE_DATES))},
}


def demo_odoo_receipts() -> InMemoryOdoo:
    """Un Odoo offline con recepciones de compra REALES, para medir el plazo.

    ``demo_odoo()`` no sirve para esto: no tiene ``purchase.order`` ni
    ``stock.move``, asi que ``receipts()`` no encuentra nada que restar. Y sin un
    fixture asi, el ejemplo de la salida #1 solo correria contra un Odoo vivo --
    o sea, contra ninguno, hasta que alguien tenga credenciales. Todos los demas
    ejemplos del repo corren offline; este tambien.

    Acme promete 7 dias en la ficha y entrega en ~20. Esa brecha es el punto: un
    fixture donde promesa y comportamiento coinciden no muestra nada.
    """
    data: dict = {
        _M_PRODUCT: {
            1: {"default_code": "SKU-1", "name": "Rodamiento", "list_price": 20.0, "standard_price": 12.0},
        },
        _M_PARTNER: {
            69: {"name": "Acme", "commercial_partner_id": [69, "Acme"]},
            70: {"name": "Acme Sucursal Santiago", "commercial_partner_id": [69, "Acme"]},
        },
        _M_PICKING_TYPE: {10: {"code": "incoming"}, 11: {"code": "outgoing"}},
        _M_SUPPLIERINFO: {
            400: {"partner_id": [70, "Acme Sucursal Santiago"], "product_id": [1, "Rodamiento"],
                  "delay": 7.0, "sequence": 1},
        },
        _M_PO: {}, _M_PO_LINE: {},
        # Un despacho a cliente: existe para que `fields_get` vea el campo aunque
        # se filtren todas las recepciones, y de paso comprueba que un movimiento
        # sin linea de OC no entra al calculo.
        _M_MOVE: {
            999: {"state": "done", "date": "2026-01-02 09:00:00", "product_id": [1, "Rodamiento"],
                  "purchase_line_id": False, "picking_id": [999, "WH/OUT/1"],
                  "picking_type_id": [11, "Entregas"]},
        },
    }
    for i, (orden, recepcion) in enumerate(
        [("2026-01-05", "2026-01-25"), ("2026-02-02", "2026-02-23"), ("2026-03-02", "2026-03-21")]
    ):
        po, pol = 200 + i, 300 + i
        data[_M_PO][po] = {
            "name": f"PO-{i:03d}", "date_order": f"{orden} 09:00:00",
            "date_approve": f"{orden} 11:00:00",
            "partner_id": [70, "Acme Sucursal Santiago"], "state": "purchase",
        }
        data[_M_PO_LINE][pol] = {"order_id": [po, f"PO-{i:03d}"]}
        data[_M_MOVE][400 + i] = {
            "state": "done", "date": f"{recepcion} 15:00:00", "product_id": [1, "Rodamiento"],
            "purchase_line_id": [pol, "linea"], "picking_id": [500 + i, f"WH/IN/{i}"],
            "picking_type_id": [10, "Recepciones"],
        }
    return InMemoryOdoo(data)


def demo_odoo_deliveries() -> InMemoryOdoo:
    """Un Odoo offline con el lado de SERVICIO poblado, para medir OTIF y fill rate.

    El espejo de salida de ``demo_odoo_receipts()``, y existe por el mismo motivo:
    ``demo_odoo()`` no tiene ``commitment_date``, ni ``qty_delivered``, ni
    ``stock.move``, asi que ``src.connectors.realized_service`` sobre el devuelve
    ``not_measured`` -- correcto, y no muestra nada. Sin este fixture el unico modo
    de ver un OTIF real seria contra un Odoo vivo, o sea contra ninguno hasta que
    haya credenciales.

    Widget promete a 5 dias y se queda corto y tarde en la segunda mitad del
    periodo; Gizmo se sirve completo y a tiempo. La mezcla es el punto: un fixture
    donde todo llega perfecto prueba la plomeria y esconde el numero.
    """
    ventas: dict = {}
    lineas: dict = {}
    movimientos: dict = {}
    for i, dia in enumerate(_DEMO_SALE_DATES):
        pedido = datetime.strptime(dia, "%Y-%m-%d")
        prometido = pedido + timedelta(days=5)
        tarde = prometido + timedelta(days=3 if i >= 4 else 0)
        ventas[200 + i] = {
            "name": f"S{i + 1:04d}",
            "date_order": f"{dia} 10:00:00",
            "commitment_date": f"{prometido:%Y-%m-%d} 00:00:00",
            "state": "done" if i % 3 == 2 else "sale",
            "order_line": [300 + 2 * i, 301 + 2 * i],
        }
        lineas[300 + 2 * i] = {
            "product_id": [1, "Widget"],
            "product_uom_qty": 10.0,
            "price_unit": 20.0,
            "qty_delivered": 7.0 if i >= 4 else 10.0,
        }
        lineas[301 + 2 * i] = {
            "product_id": [3, "Gizmo"],
            "product_uom_qty": 5.0,
            "price_unit": 8.0,
            "qty_delivered": 5.0,
        }
        movimientos[500 + 2 * i] = {
            "sale_line_id": [300 + 2 * i, f"S{i + 1:04d}"],
            "state": _MOVE_DONE,
            "date": f"{tarde:%Y-%m-%d} 14:00:00",
            "picking_type_id": [11, "Entregas"],
        }
        movimientos[501 + 2 * i] = {
            "sale_line_id": [301 + 2 * i, f"S{i + 1:04d}"],
            "state": _MOVE_DONE,
            "date": f"{prometido:%Y-%m-%d} 09:00:00",
            "picking_type_id": [11, "Entregas"],
        }
    return InMemoryOdoo(
        {
            _M_PRODUCT: {
                1: {"default_code": "SKU-1", "name": "Widget", "list_price": 20.0, "standard_price": 12.0},
                3: {"default_code": "SKU-3", "name": "Gizmo", "list_price": 8.0, "standard_price": 5.0},
            },
            _M_LOCATION: {10: {"usage": "internal"}},
            _M_QUANT: {
                100: {"product_id": [1, "Widget"], "location_id": [10, "WH/Stock"], "quantity": 12.0},
            },
            _M_PICKING_TYPE: {10: {"code": "incoming"}, 11: {"code": "outgoing"}},
            _M_SALE: ventas,
            _M_SALE_LINE: lineas,
            _M_MOVE: movimientos,
            _M_SUPPLIERINFO: {},
            _M_ORDERPOINT: {},
        }
    )


def demo_odoo() -> InMemoryOdoo:
    """A small, deterministic in-memory Odoo for demos and tests (no randomness)."""
    return InMemoryOdoo(
        {
            _M_PRODUCT: {
                1: {"default_code": "SKU-1", "name": "Widget", "list_price": 20.0, "standard_price": 12.0},
                2: {"default_code": "SKU-2", "name": "Gadget", "list_price": 50.0, "standard_price": 30.0},
                3: {"default_code": "SKU-3", "name": "Gizmo", "list_price": 8.0, "standard_price": 5.0},
            },
            _M_LOCATION: {10: {"usage": "internal"}, 11: {"usage": "customer"}},
            _M_QUANT: {
                100: {"product_id": [1, "Widget"], "location_id": [10, "WH/Stock"], "quantity": 12.0},
                101: {"product_id": [2, "Gadget"], "location_id": [10, "WH/Stock"], "quantity": 300.0},
                102: {"product_id": [3, "Gizmo"], "location_id": [10, "WH/Stock"], "quantity": 20.0},
            },
            _M_SALE: dict(_DEMO_SALES),
            _M_SALE_LINE: dict(_DEMO_SALE_LINES),
            _M_SUPPLIERINFO: {
                400: {"product_id": [1, "Widget"], "partner_id": [70, "Acme Supply"], "sequence": 1, "delay": 7.0},
                401: {"product_id": [2, "Gadget"], "partner_id": [71, "Globex"], "sequence": 1, "delay": 14.0},
                402: {"product_id": [3, "Gizmo"], "partner_id": [70, "Acme Supply"], "sequence": 1, "delay": 21.0},
            },
            _M_ORDERPOINT: {},
        }
    )


#: Lo que un Odoo estandar puede medir del servicio AL CLIENTE. Las tres columnas
#: existen y las tres son opcionales en una base viva -- por eso ``OdooConnector`` sondea
#: cada campo antes de pedirlo (``_has_field``) y cada ausencia viaja como ``None``.
SERVICE_COVERAGE = ServiceCoverage(
    backend="Odoo (XML-RPC, modelos estandar de Ventas e Inventario)",
    delivered_qty=FieldCoverage(
        COVERAGE_MEASURED,
        "sale.order.line.qty_delivered",
        "unidades entregadas de la linea; el campo llega con el puente Ventas-Inventario, "
        "y sin ese puente el servicio sale no medido en vez de cero",
    ),
    promised_date=FieldCoverage(
        COVERAGE_MEASURED,
        "sale.order.commitment_date",
        "la fecha comprometida de la ORDEN, replicada a sus lineas. En blanco es lo "
        "habitual, y saca la linea de OTIF en vez de medirla contra date_order",
    ),
    delivered_date=FieldCoverage(
        COVERAGE_DERIVED,
        "stock.move (state=done, picking_type=outgoing) -> max(date) por linea",
        "la fecha del ULTIMO despacho que sirvio la linea. Es DESPACHO, no llegada: Odoo "
        "no registra la recepcion del cliente, asi que este OTIF mide salir a tiempo del "
        "deposito y no llegar a tiempo",
    ),
)


#: Que escribe este conector en el sistema del cliente (ver ``WriteSurface``).
WRITE_SURFACE = WriteSurface(
    WRITE_APPROVAL_GATED,
    objects=(
        'stock.warehouse.orderpoint.product_min_qty',
        'purchase.order (estado borrador, con sus purchase.order.line)',
    ),
    means=(
        'los reorder points se escriben con aprobacion firmada, idempotencia y rollback (reversibles); las ordenes de compra quedan EN BORRADOR y las confirma un comprador en Odoo (irreversibles: aprobacion con nombre obligatoria). Cada escritura deja fila en el ledger.'
    ),
)
