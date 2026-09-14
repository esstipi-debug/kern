"""Conexion autoservicio a una tienda Shopify via APP CUSTOM, en SOLO LECTURA.

La app custom es la que el comerciante crea en SU PROPIO admin (Configuracion ->
Apps y canales de venta -> Desarrollar apps) y de la que saca un Admin API access
token. NO es una app del App Store: eso exige OAuth, app embebida, los tres
webhooks obligatorios de GDPR y Billing API, y nada de eso esta en esta ruta.

**Lo que Shopify NO tiene, y define el alcance.** Shopify no tiene ordenes de
compra ni recepciones de proveedor: el objeto no existe en la Admin API. Por lo
tanto el plazo real de proveedor -- la salida #1 del embudo, la que
``src/connectors/odoo_connect.py`` si mide -- ES IMPOSIBLE desde datos de
Shopify. Este modulo se niega a derivarlo por ninguna via y lo declara ausente
con motivo (:data:`PLAZO_AUSENTE`). El contrato de la casa es que la ausencia se
declara y nunca se rellena.

**Lo que Shopify si da, y es lo que se explota.** Tres piezas, y ninguna calcula
un plazo:

1. :func:`check_connection` -- valida y **clasifica la falla** en cuatro casos
   que mandan a cuatro pantallas distintas del admin del comerciante: dominio,
   token, scope faltante (diciendo CUAL), y version de API en drift. Un "no se
   pudo conectar" generico es inutil en un embudo sin humano al otro lado.
2. :func:`read_sales_history` -- las ordenes de venta con sus lineas, paginadas
   por CURSOR de GraphQL (nunca por offset: Shopify no lo ofrece en GraphQL y
   emularlo salteando paginas pierde filas en silencio cuando entran ordenes
   nuevas durante la lectura). Emite las filas con la MISMA forma que hoy sale
   del CSV de demanda, para que ``jobs/abc_xyz_job.aggregate_skus`` no se entere
   del origen.
3. :func:`read_stock_on_hand` -- el stock por location, EN LOTES: una consulta
   paginada por bodega en vez de una por (SKU, bodega). El roster de bodegas se
   reusa de ``src/connectors/shopify_inventory.py`` (``list_locations``), que ya
   habla esa consulta. La dimension location SE CONSERVA: sin ella no hay
   historia de pooling multi-bodega, que es la fuente de valor #2 de esta ruta.

Y :func:`preflight`, que reporta antes de correr que se encontro y que falta.
Es PURA sobre lo ya leido: cero llamadas extra, porque el presupuesto son 25
minutos por cuenta al mes y una segunda pasada a la API se paga dos veces.

**Solo lectura, y se hace cumplir por SCOPES, no por disciplina.** La app custom
pide unicamente los de :data:`REQUIRED_SCOPES`. Este modulo no importa
``nosilo_core.writeback``, no instancia ``ShopifyInventoryStore`` y no llama a
``adjust_inventory`` ni ``set_inventory`` -- que existen en el conector y que
esta ruta NO TOCA. Que el motion self-serve no escriba es el argumento de venta
entero: un comerciante aprueba un token de solo lectura en una reunion; uno que
escribe pasa por su comite. :func:`check_connection` ademas REPORTA si el
comerciante concedio scopes de escritura, para que la afirmacion sea medible en
vez de prometida.

**El secreto.** El ``access_token`` no se registra, no se persiste y no viaja en
ningun mensaje de error: :func:`_scrub` lo borra de todo texto que venga del
servidor antes de que salga de aqui, y :class:`ShopifyCredentials` lo excluye de
su propio ``repr``. Ver ``src/mcp_keys.py`` para el patron de la casa.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any
from urllib.parse import urlsplit

from src.connectors.shopify_api_version import PINNED_VERSION, served_version

# -- los motivos de falla ------------------------------------------------------

#: El dominio no contesta, o lo que contesta no es la Admin API de una tienda.
DOMINIO = "dominio_invalido"
#: La tienda existe y el Admin API access token no autentica (401).
TOKEN = "token_invalido"
#: El token autentica, pero a la app custom le falta algun scope de lectura.
SCOPE = "scope_faltante"
#: Shopify sirvio una version de API distinta de la pedida (fall forward).
DRIFT = "version_en_drift"

#: Los cuatro que el embudo tiene que saber distinguir, porque cada uno manda a
#: una pantalla distinta del admin del comerciante: el dominio a la barra de
#: direcciones, el token a "Revelar token una vez", el scope a la configuracion
#: de Admin API scopes de la app, y el drift a nadie -- ese lo arreglamos
#: nosotros bumpeando el pin.
FOUR_MODES = (DOMINIO, TOKEN, SCOPE, DRIFT)

#: La respuesta no se pudo clasificar. Se reporta con el texto (depurado del
#: token) en vez de forzarla dentro de los cuatro: un diagnostico inventado es
#: peor que uno que admite no saber.
SERVIDOR = "error_del_servidor"

# -- los scopes ----------------------------------------------------------------

#: Lo unico que la app custom pide. NUNCA ``write_inventory``.
REQUIRED_SCOPES = ("read_products", "read_inventory", "read_orders", "read_locations")

#: Sin este scope, ``orders`` devuelve SOLO los ultimos 60 dias -- y lo hace con
#: un 200 y una lista corta, no con un error. Dos cubos mensuales no sostienen
#: un coeficiente de variacion, asi que XYZ sale de adorno. No esta en
#: REQUIRED_SCOPES porque la conexion si funciona sin el; esta aqui porque el
#: preflight tiene que nombrarlo cuando la ventana pedida no cabe en 60 dias.
ALL_ORDERS_SCOPE = "read_all_orders"

#: Cuanto historial de ordenes devuelve Shopify sin ``read_all_orders``.
ORDERS_WINDOW_WITHOUT_ALL_ORDERS_DAYS = 60

#: Sin este, ``order.fulfillmentOrders`` viene VACIO -- con un 200, igual que
#: ``read_all_orders`` recorta a 60 dias sin fallar. Es de donde sale la fecha
#: prometida al comprador, o sea la mitad "on time" de OTIF. Fuera de
#: REQUIRED_SCOPES por lo mismo que el otro: todo lo demas funciona sin el, y
#: pedirlo obligaria a cada cliente ya instalado a reinstalar la app.
FULFILLMENT_ORDERS_SCOPE = "read_merchant_managed_fulfillment_orders"

#: Si el comerciante concedio alguno con este prefijo, esta ruta igual no lo usa
#: -- pero se reporta. "Solo lectura" es una afirmacion sobre permisos, y una
#: afirmacion sobre permisos que nadie mide es una promesa.
WRITE_SCOPE_PREFIX = "write_"

#: Camino paginado: barato para una tienda chica, no escala. Es el default para
#: no cambiarle el comportamiento a quien ya lo usa.
ORDERS_PAGED = "paged"
#: Lectura masiva: sin techo de costo ni de tamano, pero asincrona.
ORDERS_BULK = "bulk"

REDACTED = "<redactado>"

#: Por que no hay plazo de proveedor por esta ruta. Viaja en ``source_notes``
#: SIEMPRE, no solo cuando alguien pregunta: una salida que simplemente omite el
#: plazo es indistinguible de una que lo midio y le dio cero.
PLAZO_AUSENTE = (
    "plazo de proveedor: AUSENTE. Shopify no tiene ordenes de compra ni recepciones de "
    "proveedor -- el objeto no existe en su Admin API, asi que no hay fecha de pedido ni "
    "fecha de recepcion que restar. No se deriva por ninguna via (ni de la fecha de "
    "creacion del producto, ni del historial de stock, ni de la fecha de la orden de "
    "venta): cualquiera de esas seria un numero inventado con forma de medicion. Para "
    "medir plazo hace falta el ERP donde viven las compras."
)

#: El costo que se escribe cuando Shopify no reporta NINGUN unitCost. Es un
#: marcador plano y declarado, no una estimacion: con todos los costos iguales,
#: ``src.classification.value_basis`` detecta que el valor anual no discrimina y
#: ``jobs/abc_xyz_job.build_deck`` imprime "Ranked on units, not value" en la
#: pagina. O sea: el lector se entera del degradado por el motor mismo, no por
#: una nota al pie de este modulo. El caso MIXTO (algunos con costo y otros sin)
#: NO usa esto: se bloquea, porque ahi el marcador convierte un SKU en clase C
#: por falta de dato y eso si es un ranking fabricado.
PLACEHOLDER_UNIT_COST = 1.0

#: ``unit_cost`` salio de ``inventoryItem.unitCost`` de Shopify.
COSTO_REAL = "costo"
#: No habia ningun costo: el ABC de esta corrida es un ranking por unidades.
COSTO_UNIDADES = "unidades"


class ShopifyConnectError(RuntimeError):
    """Una lectura de onboarding fallo de forma que el llamador debe ver."""


def _scrub(text: Any, secret: str) -> str:
    """``text`` sin el token. Se aplica a TODO lo que venga del servidor.

    Que Shopify hoy no devuelva el token en un cuerpo de error no es una garantia
    que se pueda escribir en un contrato de seguridad, y el costo de asumirlo es
    el Admin API token de un comerciante en un log.
    """
    salida = str(text)
    if secret:
        salida = salida.replace(secret, REDACTED)
    return salida


# -- credenciales ---------------------------------------------------------------


@dataclass(frozen=True)
class ShopifyCredentials:
    """Los dos campos que escribe el comerciante. El token nunca entra al ``repr``.

    ``field(repr=False)`` no es cosmetico: un ``repr`` de esta clase termina en el
    mensaje de una excepcion no atrapada, en un breakpoint, o en el cuerpo de un
    reporte de error automatico. Excluirlo ahi es mas barato que auditar cada
    lugar donde alguien pueda imprimir el objeto.
    """

    shop_domain: str
    access_token: str = field(repr=False, default="")

    def __post_init__(self) -> None:
        object.__setattr__(self, "shop_domain", normalize_shop_domain(self.shop_domain))
        object.__setattr__(self, "access_token", self.access_token.strip())

    @property
    def base_url(self) -> str:
        return f"https://{self.shop_domain}" if self.shop_domain else ""

    @property
    def looks_custom_app(self) -> bool:
        """Los tokens de app custom empiezan con ``shpat_``.

        Se usa SOLO para mejorar el mensaje de una falla de token que YA ocurrio,
        jamas para rechazar antes de intentar: Shopify puede cambiar el prefijo, y
        rechazar localmente un token que el servidor habria aceptado es una falla
        que el comerciante no puede diagnosticar ni desde su lado ni desde el
        nuestro.
        """
        return self.access_token.startswith("shpat_")


def normalize_shop_domain(raw: str) -> str:
    """Lo que el comerciante pega -> ``mitienda.myshopify.com``.

    Un comerciante copia ``mitienda``, o su dominio publico, o la barra de
    direcciones entera del admin
    (``https://admin.shopify.com/store/mitienda/apps``), o deja un espacio al
    pegar. Todos tienen que funcionar: exigir la forma canonica es una pregunta
    de soporte por cuenta, y el presupuesto son 25 minutos al mes.

    El dominio publico personalizado (``www.mitienda.cl``) se deja pasar TAL
    CUAL, sin traducirlo: la Admin API solo responde en ``*.myshopify.com``, asi
    que adivinar el subdominio a partir del dominio publico daria otra tienda o
    ninguna. Falla nombrando el caso, que es mas barato que acertar a veces.
    """
    limpio = raw.strip().strip("<>").rstrip("/")
    if not limpio:
        return ""
    if "://" not in limpio:
        if "/" in limpio:
            # `admin.shopify.com/store/mitienda` pegado sin esquema.
            limpio = f"https://{limpio}"
        elif "." in limpio:
            return limpio.lower()
        else:
            # Un handle suelto: `mitienda`.
            return f"{limpio.lower()}.myshopify.com"
    partes = urlsplit(limpio)
    host = (partes.hostname or "").lower()
    if host == "admin.shopify.com":
        # /store/<handle>/... -- el handle es el subdominio myshopify.
        tramos = [t for t in partes.path.split("/") if t]
        if len(tramos) >= 2 and tramos[0] == "store":
            return f"{tramos[1].lower()}.myshopify.com"
        return ""
    return host


# -- resultados -----------------------------------------------------------------


@dataclass(frozen=True)
class ConnectionCheck:
    """Conecta o no, y cuando no: cual de los casos y que hacer al respecto."""

    ok: bool
    reason: str = ""
    #: En el idioma del comerciante y con la pantalla de SU admin donde se
    #: arregla. Nunca contiene el token (ver :func:`_scrub`).
    message: str = ""
    #: Los scopes que faltan, TODOS de una vez: un comerciante que vuelve a la
    #: pantalla de configuracion cuatro veces seguidas abandona.
    scopes_missing: tuple[str, ...] = ()
    #: Los scopes concedidos, tal como los reporta Shopify.
    scopes_granted: tuple[str, ...] = ()
    #: Scopes de escritura que el comerciante concedio. Esta ruta no los usa; se
    #: reportan para que "solo lectura" sea verificable y no una promesa.
    write_scopes: tuple[str, ...] = ()
    #: La version que Shopify dijo haber usado, si la respuesta traia cabecera.
    api_version_served: str = ""
    api_version_requested: str = PINNED_VERSION
    shop_name: str = ""
    #: ``read_all_orders`` concedido: sin el, el historial se corta a 60 dias.
    all_orders: bool = False
    #: ``read_merchant_managed_fulfillment_orders`` concedido: sin el, la fecha
    #: prometida al comprador no se puede leer y OTIF queda sin su mitad "on time".
    fulfillment_orders: bool = False

    @property
    def actionable(self) -> bool:
        """Falso solo si algo devolvio una falla sin mensaje: no deberia pasar."""
        return bool(self.message)


@dataclass(frozen=True)
class Catalog:
    """Las variantes de la tienda, que NO son lo mismo que las que vendieron.

    Existe porque el universo de SKU no puede salir del historial de venta: un
    SKU que no vendio nada no aparece en ninguna orden, y ese es exactamente el
    que T11 viene a encontrar. Sacar el catalogo de las ordenes haria que el
    stock muerto fuera invisible para el analisis de stock muerto -- y la salida
    se veria perfecta, porque lo que falta no deja hueco.

    Es tambien lo que consume el scope ``read_products``. Un permiso que se pide
    y no se usa es un permiso que no habria que pedir.
    """

    skus: tuple[str, ...] = ()
    #: SKU -> unitCost. La fuente preferida: cubre tambien lo que no vendio.
    cost_by_sku: dict = field(default_factory=dict)
    n_variants: int = 0
    dropped: dict = field(default_factory=dict)
    truncated: bool = False


@dataclass(frozen=True)
class StockRead:
    """El stock por (SKU, location), con el roster completo de bodegas.

    ``locations`` es el roster tal como lo declara la tienda, y NO el conjunto de
    bodegas que resultaron tener existencias: una bodega vacia sigue siendo una
    bodega, y desaparecerla del reporte le esconde al comerciante justamente el
    lugar donde no hay nada.
    """

    rows: tuple[dict, ...] = ()
    locations: tuple[dict, ...] = ()
    #: Niveles de inventario leidos (no filas emitidas: la grilla se completa
    #: con ceros). Es la medida del tamano real de la lectura.
    n_levels: int = 0
    #: Llamadas HTTP que costo. Se lleva contado porque el presupuesto de esta
    #: ruta son 25 minutos por cuenta al mes, y un costo que nadie mide es un
    #: costo que vuelve a crecer en el proximo cambio.
    n_requests: int = 0
    dropped: dict = field(default_factory=dict)


@dataclass(frozen=True)
class SalesHistory:
    """El historial de venta leido, ya bucketizado, con lo que se descarto.

    ``rows`` tiene la forma que come ``jobs/abc_xyz_job.aggregate_skus``: una fila
    por (SKU, periodo) con ``product_id`` / ``quantity`` / ``unit_cost``.
    """

    rows: tuple[dict, ...] = ()
    #: SKU -> unidades totales en la ventana. Insumo de ``daily_demand``.
    units_by_sku: dict = field(default_factory=dict)
    #: SKU -> fecha ISO de su ultima venta observada.
    last_sale_by_sku: dict = field(default_factory=dict)
    #: SKU -> unitCost de Shopify, SOLO para los que lo reportan.
    cost_by_sku: dict = field(default_factory=dict)
    n_orders: int = 0
    first_order: str = ""
    last_order: str = ""
    periods: tuple[str, ...] = ()
    cost_basis: str = COSTO_UNIDADES
    dropped: dict = field(default_factory=dict)
    notes: tuple[str, ...] = ()

    @property
    def n_skus(self) -> int:
        return len(self.units_by_sku)

    @property
    def n_never_sold(self) -> int:
        """SKU del catalogo sin una sola unidad vendida en la ventana."""
        return sum(1 for u in self.units_by_sku.values() if u <= 0)

    @property
    def span_days(self) -> int | None:
        if not (self.first_order and self.last_order):
            return None
        return (date.fromisoformat(self.last_order) - date.fromisoformat(self.first_order)).days


@dataclass(frozen=True)
class Preflight:
    """Que se encontro del otro lado, antes de correr el motor.

    ``missing`` son los BLOQUEANTES de esta capa: sin eso las tareas no se pueden
    correr en absoluto. ``warnings`` es lo que degrada la corrida sin impedirla,
    y lo que las compuertas de QA de cada job juzgan con su propio criterio --
    duplicar aqui el minimo de observaciones de ``src.forecasting`` crearia una
    SEGUNDA definicion de "suficiente historial", y la segunda copia de un
    guardia es como se reintroduce el defecto que el primero atajaba.
    """

    n_orders: int
    n_skus: int
    n_locations: int
    first_order: str
    last_order: str
    n_periods: int
    cost_basis: str
    dropped: dict = field(default_factory=dict)
    missing: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.missing

    @property
    def span_days(self) -> int | None:
        if not (self.first_order and self.last_order):
            return None
        return (date.fromisoformat(self.last_order) - date.fromisoformat(self.first_order)).days


@dataclass(frozen=True)
class ShopifyScanInput:
    """Lo que la ruta de conexion entrega a los motores T03 y T11.

    ``demand_rows`` va a ``jobs/abc_xyz_job.aggregate_skus``; ``eo_rows`` va a
    ``jobs/excess_obsolete_job.prepare_records``. ``stock_rows`` conserva la
    dimension location y es el insumo de la historia de pooling: no se pliega
    solo, porque plegarlo es lo unico que despues no se puede deshacer.
    """

    check: ConnectionCheck
    preflight: Preflight | None = None
    demand_rows: tuple[dict, ...] = ()
    stock_rows: tuple[dict, ...] = ()
    eo_rows: tuple[dict, ...] = ()
    dropped: dict = field(default_factory=dict)
    source_notes: tuple[str, ...] = ()
    #: Solo en el camino masivo. Cuando la operacion sigue corriendo, esto trae
    #: su id y ``preflight`` es None: el llamador vuelve mas tarde con ese id en
    #: vez de arrancar otra lectura.
    bulk: Any = None

    @property
    def ok(self) -> bool:
        return self.check.ok and self.preflight is not None and self.preflight.ok

    @property
    def in_progress(self) -> bool:
        """La lectura masiva arranco y todavia no termino. NO es una falla."""
        return self.preflight is None and self.bulk is not None and not self.bulk.done


# -- el copy del onboarding -------------------------------------------------------

#: Que desbloquea cada permiso, en el idioma del comerciante. Es la UNICA fuente:
#: de aqui salen el mensaje de la falla de scope, la advertencia del preflight y
#: la pantalla de setup. Tres listas de permisos escritas a mano en tres lugares
#: se desincronizan en el primer cambio, y la que queda vieja es justamente la
#: que el comerciante lee antes de configurar.
SCOPE_COPY: dict[str, str] = {
    "read_products": "el catalogo: que SKU existen. Sin esto no se puede saber que hay que "
                     "analizar, y lo que nunca vendio -- que es lo que hay que encontrar -- "
                     "seria invisible.",
    "read_inventory": "las existencias por bodega y el 'Costo por articulo'. Sin el costo la "
                      "clasificacion se hace por unidades y no por valor.",
    "read_orders": "el historial de venta, que es de donde sale la demanda.",
    "read_locations": "las bodegas. Sin esto no se puede decir en cual esta el stock parado.",
}

#: El permiso que se pasa por alto y que rompe el analisis en silencio.
ALL_ORDERS_COPY = (
    "el historial de mas de 60 dias. ESTE ES EL QUE SE PASA POR ALTO: sin el, Shopify entrega "
    "solo los ultimos 60 dias de ordenes, y lo hace con una respuesta EXITOSA y una lista corta "
    "-- no con un error. Sesenta dias son dos meses, y con dos observaciones no se distingue un "
    "SKU estable de uno erratico: la clasificacion sale con la forma correcta y las letras "
    "equivocadas. Marcalo junto con los demas."
)

FULFILLMENT_ORDERS_COPY = (
    "Deja leer la fecha de entrega que se le PROMETIO al comprador en el checkout "
    "(order.fulfillmentOrders -> deliveryMethod). Es la mitad 'on time' de OTIF: sin ella se "
    "puede medir cuanto se sirvio de cada pedido, pero no si llego cuando se dijo."
)

#: Los que esta ruta NUNCA pide. Va en el copy a proposito: que el motion no
#: escriba es el argumento de venta, y un argumento que solo aparece en un
#: docstring no lo lee el que decide.
NEVER_REQUESTED = ("write_products", "write_inventory", "write_orders")

SETUP_STEPS: tuple[dict[str, str], ...] = (
    {
        "n": "1",
        "titulo": "Crea una app custom en tu propio admin",
        "donde": "Configuracion -> Apps y canales de venta -> Desarrollar apps -> Crear una app",
        "detalle": "Es una app tuya, dentro de tu tienda. No se instala nada del App Store y no "
                   "hay que aprobar permisos de un tercero.",
    },
    {
        "n": "2",
        "titulo": "Marca los permisos de LECTURA",
        "donde": "tu app -> Configuracion -> Admin API access scopes",
        "detalle": "Son cuatro obligatorios mas uno que casi todos se saltan. Ninguno de "
                   "escritura: si ves marcado alguno de " + ", ".join(NEVER_REQUESTED) +
                   ", desmarcalo -- esta lectura no lo usa.",
    },
    {
        "n": "3",
        "titulo": "Instala la app y revela el token UNA vez",
        "donde": "tu app -> Claves de API -> Admin API access token -> Revelar token una vez",
        "detalle": "Empieza con 'shpat_'. Se muestra una sola vez: si se pierde hay que "
                   "desinstalar y reinstalar la app para generar otro. No es la clave ni el "
                   "secreto de la API, que estan en la misma pantalla y no sirven aqui.",
    },
    {
        "n": "4",
        "titulo": "Pega el dominio y el token",
        "donde": "Configuracion -> Dominios -> 'dominio de myshopify'",
        "detalle": "El dominio termina en .myshopify.com. El dominio publico de la tienda no "
                   "sirve: la Admin API solo responde en ese.",
    },
)


def setup_copy() -> dict:
    """El copy del onboarding, armado desde las mismas constantes que valida el codigo.

    Se entrega estructurado y no como un texto ya maquetado para que la pantalla
    que lo muestre decida como se ve, sin volver a escribir los permisos. El unico
    modo de que el copy y la validacion digan lo mismo es que salgan del mismo
    lado: si alguien agrega un scope a ``REQUIRED_SCOPES`` y el copy fuera una
    lista aparte, el comerciante configuraria de menos y se enteraria recien al
    fallar.
    """
    return {
        "tipo_de_app": "custom",
        "por_que_no_app_store": (
            "Una app del App Store exige OAuth, app embebida, los tres webhooks obligatorios de "
            "GDPR y Billing API. Una app custom la creas vos en tu propio admin en unos minutos, "
            "y el token no sale de tu tienda."
        ),
        "pasos": [dict(p) for p in SETUP_STEPS],
        "permisos": {
            "obligatorios": [
                {"handle": s, "para_que": SCOPE_COPY[s]} for s in REQUIRED_SCOPES
            ],
            "recomendado": [
                {
                    "handle": ALL_ORDERS_SCOPE,
                    "para_que": ALL_ORDERS_COPY,
                    "si_falta": (
                        f"el analisis corre igual, pero sobre "
                        f"{ORDERS_WINDOW_WITHOUT_ALL_ORDERS_DAYS} dias de historial, y se avisa."
                    ),
                },
                {
                    "handle": FULFILLMENT_ORDERS_SCOPE,
                    "para_que": FULFILLMENT_ORDERS_COPY,
                    "si_falta": (
                        "el fill rate y la venta perdida se miden igual, pero el OTIF queda "
                        "reportado como NO MEDIDO: sin la fecha que se le prometio al comprador "
                        "no hay contra que comparar la de entrega. La respuesta de Shopify es "
                        "EXITOSA y la lista viene vacia, asi que sin este aviso parece que la "
                        "tienda no promete fechas."
                    ),
                },
            ],
            "nunca_se_piden": list(NEVER_REQUESTED),
        },
        "lo_que_esto_no_puede_dar": PLAZO_AUSENTE,
    }


# -- transporte -----------------------------------------------------------------


def _fail(reason: str, message: str, **extra: Any) -> ConnectionCheck:
    return ConnectionCheck(ok=False, reason=reason, message=message, **extra)


def _graphql_path(api_version: str) -> str:
    return f"/admin/api/{api_version}/graphql.json"


def _post(http: Any, path: str, query: str, variables: dict) -> Any:
    return http.post(path, json={"query": query, "variables": variables})


def live_http(creds: ShopifyCredentials, *, timeout: float = 30.0) -> Any:
    """El cliente httpx apuntado a la tienda, con el token en la cabecera.

    El UNICO lugar del proyecto donde el Admin API access token se pone en un
    header. Esta aqui y no en el endpoint a proposito: un secreto que se arma en
    la capa HTTP es un secreto que hay que auditar en cada ruta que lo use, y ya
    van dos (la consola y el endpoint). Shopify autentica por
    ``X-Shopify-Access-Token``, no por ``Authorization: Bearer``.

    ``timeout`` acotado y no infinito: esta lectura corre dentro de un request
    web, y una tienda que no contesta tiene que soltar el hilo del threadpool en
    vez de quedarselo.
    """
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - depende del extra instalado
        raise ShopifyConnectError(
            "httpx es necesario para hablar con Shopify -- instala el extra 'repricing', o "
            "inyecta un transporte con http= (InMemoryShopifyAdmin en los tests)"
        ) from exc
    return httpx.Client(
        base_url=creds.base_url,
        headers={
            "X-Shopify-Access-Token": creds.access_token,
            "Content-Type": "application/json",
        },
        timeout=timeout,
    )


def _status(resp: Any) -> int:
    """El codigo HTTP, o 200 si el objeto no lo expone.

    Se lee del atributo en vez de via ``raise_for_status()`` a proposito: asi el
    clasificador separa 401 de 403 de 404 sin depender del tipo de excepcion de
    httpx, y el doble offline no tiene que emular la jerarquia de excepciones de
    httpx para ejercitar los cuatro modos.
    """
    try:
        return int(getattr(resp, "status_code", 200))
    except (TypeError, ValueError):
        return 200


SHOP_QUERY = """
query ShopInfo {
  shop {
    name
    myshopifyDomain
  }
}
"""

SCOPES_QUERY = """
query AppScopes {
  currentAppInstallation {
    accessScopes {
      handle
    }
  }
}
"""

CATALOG_QUERY = """
query Catalog($cursor: String) {
  productVariants(first: 250, after: $cursor) {
    pageInfo {
      hasNextPage
      endCursor
    }
    edges {
      node {
        sku
        inventoryItem {
          unitCost {
            amount
          }
        }
      }
    }
  }
}
"""

#: Ordenes por pagina x lineas por pagina. NO son numeros de gusto: Shopify
#: rechaza una consulta cuyo COSTO CALCULADO pase de 1.000 puntos, y una
#: conexion cuesta ``first`` x el costo de cada nodo. Aqui: 20 x (1 orden + 40
#: lineas x 1) = 820. Con los 100 x 250 que tenia antes, y bajando a
#: ``variant -> inventoryItem -> unitCost`` en cada linea, daba ~100.100 puntos:
#: cien veces el techo. Shopify lo habria rechazado ANTES de ejecutarlo, en toda
#: tienda real -- y no habia forma de verlo offline, porque el doble no cobra.
#: Quien toque estos numeros recalcula: first_ordenes x (1 + first_lineas) < 1000.
_ORDERS_PAGE = 20
_LINE_ITEMS_PAGE = 40

ORDERS_QUERY = f"""
query SalesHistory($q: String!, $cursor: String) {{
  orders(first: {_ORDERS_PAGE}, after: $cursor, query: $q, sortKey: CREATED_AT) {{
    pageInfo {{
      hasNextPage
      endCursor
    }}
    edges {{
      node {{
        id
        createdAt
        cancelledAt
        test
        lineItems(first: {_LINE_ITEMS_PAGE}) {{
          pageInfo {{
            hasNextPage
          }}
          edges {{
            node {{
              quantity
              sku
            }}
          }}
        }}
      }}
    }}
  }}
}}
"""


# -- 1. validacion de conexion ---------------------------------------------------


def _classify_http(status: int, creds: ShopifyCredentials, cuerpo: str) -> ConnectionCheck | None:
    """None si el status no es una falla clasificable de esta capa."""
    if status == 401:
        pista = (
            ""
            if creds.looks_custom_app
            else " El Admin API access token de una app custom empieza con 'shpat_'; si lo que "
            "pegaste empieza con 'shpca_', o es la clave o el secreto de la API, no es ese: el "
            "token esta en la app -> Claves de API -> Admin API access token."
        )
        return _fail(
            TOKEN,
            f"{creds.shop_domain} rechazo el token (401). El Admin API access token se revela UNA "
            "sola vez, al instalar la app: si se perdio hay que desinstalar y reinstalar la app "
            f"para generar uno nuevo. Tambien se invalida si alguien desinstalo la app.{pista}",
        )
    if status == 403:
        # 403 sin poder listar scopes: el token vive pero no alcanza. Se nombra
        # como scope y no como token, que es la pantalla correcta.
        return _fail(
            SCOPE,
            f"{creds.shop_domain} acepto el token pero nego el acceso (403). Revisa los Admin API "
            f"scopes de la app custom: hacen falta {', '.join(REQUIRED_SCOPES)}.",
            scopes_missing=REQUIRED_SCOPES,
        )
    if status == 402:
        return _fail(
            DOMINIO,
            f"la tienda {creds.shop_domain} esta congelada o impaga (402): Shopify bloquea su Admin "
            "API hasta que se regularice el plan.",
        )
    if status == 404:
        return _fail(
            DOMINIO,
            f"{creds.shop_domain} contesta, pero no tiene una Admin API en esa ruta (404). Verifica "
            "que el dominio sea el de tu tienda Shopify (termina en .myshopify.com).",
        )
    if status >= 400:
        return _fail(SERVIDOR, f"{creds.shop_domain} devolvio HTTP {status}: {cuerpo}")
    return None


def _errors_are_access_denied(errors: list) -> bool:
    for e in errors or ():
        try:
            code = (e.get("extensions") or {}).get("code", "")
        except AttributeError:
            continue
        if str(code).upper() == "ACCESS_DENIED":
            return True
    return False


def check_connection(
    creds: ShopifyCredentials, *, http: Any = None, api_version: str = PINNED_VERSION
) -> ConnectionCheck:
    """Valida los dos campos y, si falla, dice CUAL de los cuatro casos es.

    ``http`` inyecta el transporte (cualquier objeto con ``.post()`` estilo
    httpx) para poder ejercitar los cuatro modos sin red -- que es el unico lugar
    donde pueden quedar cubiertos, porque contra una tienda real solo se produce
    el modo que esa tienda tenga puesto.
    """
    if not creds.shop_domain:
        return _fail(
            DOMINIO,
            "falta el dominio de la tienda (por ejemplo mitienda.myshopify.com). Si tu tienda usa un "
            "dominio propio, ese NO sirve aqui: la Admin API solo responde en el .myshopify.com, que "
            "aparece en Configuracion -> Dominios como 'dominio de myshopify'.",
        )
    if not creds.shop_domain.endswith(".myshopify.com"):
        return _fail(
            DOMINIO,
            f"{creds.shop_domain!r} no es un dominio .myshopify.com. La Admin API solo responde ahi, "
            "no en el dominio publico de la tienda. Lo encuentras en Configuracion -> Dominios, como "
            "'dominio de myshopify'.",
        )
    if not creds.access_token:
        return _fail(TOKEN, "falta el Admin API access token de la app custom.")
    if http is None:
        raise ShopifyConnectError("check_connection necesita un cliente http (httpx.Client o doble)")

    path = _graphql_path(api_version)

    # 1. Alcance + token. `shop` es la consulta mas barata que existe y no exige
    #    ningun scope: separa "no llegue" de "llegue y me rechazaron", que es la
    #    primera bifurcacion del arbol de diagnostico.
    try:
        resp = _post(http, path, SHOP_QUERY, {})
    except Exception as exc:  # DNS, TLS, timeout, respuesta no-HTTP
        return _fail(
            DOMINIO,
            f"no se llego a {creds.shop_domain}: {_scrub(exc, creds.access_token)}. Revisa que el "
            "dominio sea el de tu tienda (Configuracion -> Dominios -> dominio de myshopify).",
        )

    status = _status(resp)
    try:
        cuerpo = resp.json()
    except Exception as exc:
        return _fail(
            DOMINIO,
            f"{creds.shop_domain} no respondio como una Admin API de Shopify: "
            f"{_scrub(exc, creds.access_token)}.",
        )

    falla = _classify_http(status, creds, _scrub(cuerpo, creds.access_token))
    if falla is not None:
        return falla

    if not isinstance(cuerpo, dict) or ("data" not in cuerpo and "errors" not in cuerpo):
        return _fail(
            DOMINIO,
            f"{creds.shop_domain} contesto 200 pero no con GraphQL de Shopify. Verifica que sea el "
            "dominio de tu tienda y no el de otro sistema.",
        )
    errores = cuerpo.get("errors") or []
    if errores:
        texto = _scrub(errores, creds.access_token)
        if _errors_are_access_denied(errores):
            return _fail(SCOPE, f"{creds.shop_domain} nego el acceso: {texto}", scopes_missing=REQUIRED_SCOPES)
        return _fail(SERVIDOR, f"{creds.shop_domain} devolvio un error que no se pudo clasificar: {texto}")

    shop = (cuerpo.get("data") or {}).get("shop") or {}
    shop_name = str(shop.get("name") or "")

    # 2. Drift de version. La cabecera dice que version uso Shopify REALMENTE.
    #    Shopify hace fall forward: un pin vencido devuelve 200 y sirve otra
    #    version en silencio, asi que esta cabecera es el unico sintoma. Se
    #    reporta como falla propia y no como advertencia de log porque en un
    #    embudo sin humano nadie lee un warning de log.
    servida = served_version(resp) or ""
    if servida and servida != api_version:
        return _fail(
            DRIFT,
            f"Shopify sirvio la version {servida!r} en vez de la pedida {api_version!r}: el pin esta "
            "fuera de soporte y Shopify esta haciendo fall forward. No es un problema de tu tienda. "
            "Se corrige bumpeando PINNED_VERSION en src/connectors/shopify_api_version.py despues de "
            "verificar las operaciones en uso.",
            api_version_served=servida,
            api_version_requested=api_version,
            shop_name=shop_name,
        )

    # 3. Scopes. Determinista, no por texto: `currentAppInstallation.accessScopes`
    #    devuelve exactamente lo concedido, asi que se puede nombrar CUAL falta
    #    sin leer el mensaje de error de Shopify -- que ademas viene traducido al
    #    idioma de la tienda y romperia el clasificador justo en la tienda del
    #    cliente, que es donde tiene que funcionar.
    try:
        resp_s = _post(http, path, SCOPES_QUERY, {})
        cuerpo_s = resp_s.json()
    except Exception as exc:
        return _fail(
            SERVIDOR,
            f"no se pudieron leer los permisos de la app: {_scrub(exc, creds.access_token)}.",
            shop_name=shop_name,
        )
    falla = _classify_http(_status(resp_s), creds, _scrub(cuerpo_s, creds.access_token))
    if falla is not None:
        return falla

    inst = ((cuerpo_s or {}).get("data") or {}).get("currentAppInstallation") or {}
    concedidos = tuple(
        str(s.get("handle", "")) for s in (inst.get("accessScopes") or []) if s.get("handle")
    )
    faltantes = tuple(s for s in REQUIRED_SCOPES if s not in concedidos)
    escritura = tuple(s for s in concedidos if s.startswith(WRITE_SCOPE_PREFIX))

    if faltantes:
        return _fail(
            SCOPE,
            f"a la app custom le faltan {len(faltantes)} permiso(s) de lectura. "
            + " ".join(f"{s}: {SCOPE_COPY.get(s, '')}" for s in faltantes)
            + " En tu admin: Configuracion -> Apps y canales de venta -> Desarrollar apps -> tu "
            "app -> Configuracion -> Admin API scopes, marcarlos y guardar. Despues hay que "
            "reinstalar la app para que el token tome los permisos nuevos.",
            scopes_missing=faltantes,
            scopes_granted=concedidos,
            write_scopes=escritura,
            shop_name=shop_name,
            api_version_served=servida,
        )

    return ConnectionCheck(
        ok=True,
        scopes_granted=concedidos,
        write_scopes=escritura,
        api_version_served=servida,
        api_version_requested=api_version,
        shop_name=shop_name,
        all_orders=ALL_ORDERS_SCOPE in concedidos,
        fulfillment_orders=FULFILLMENT_ORDERS_SCOPE in concedidos,
    )


# -- 2. lectura de ordenes de venta ---------------------------------------------


def _months_between(first: str, last: str) -> list[str]:
    """Todos los ``YYYY-MM`` del rango, INCLUIDOS los que no tuvieron venta.

    Los meses vacios no son relleno: un mes sin lineas de venta vendio cero, y
    ese cero es un dato observado dentro de una ventana que la lectura cubrio
    entera. Omitirlos es lo que rompe XYZ -- un SKU que vendio en 2 de 12 meses,
    con solo esos 2 en la serie, da un coeficiente de variacion cercano a cero y
    se clasifica X (estable) siendo el caso mas erratico del catalogo. El error
    no se ve en la forma de la salida: se ve como una letra equivocada al lado de
    un SKU, que es la clase de defecto que nadie encuentra revisando el reporte.
    """
    y0, m0 = int(first[:4]), int(first[5:7])
    y1, m1 = int(last[:4]), int(last[5:7])
    salida: list[str] = []
    y, m = y0, m0
    while (y, m) <= (y1, m1):
        salida.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return salida


def read_catalog(
    http: Any,
    *,
    api_version: str = PINNED_VERSION,
    max_variants: int = 50_000,
    access_token: str = "",
) -> Catalog:
    """Todas las variantes de la tienda, paginadas por cursor.

    El costo se toma de aqui y no de las lineas de venta: la linea solo trae el
    costo de lo que se vendio, asi que un catalogo leido desde las ordenes deja
    sin costo justo a los SKU que no rotan -- que son los que el analisis de
    excedente tiene que valorizar.

    Si el catalogo excede ``max_variants`` se marca ``truncated`` y el preflight
    lo nombra. Un tope silencioso se lee como "cubrimos todo" cuando no.
    """
    skus: list[str] = []
    cost_by_sku: dict[str, float] = {}
    dropped = {"variante_sin_sku": 0}
    n_variants = 0
    truncated = False
    path = _graphql_path(api_version)
    cursor: str | None = None

    while True:
        resp = _post(http, path, CATALOG_QUERY, {"cursor": cursor})
        status = _status(resp)
        if status >= 400:
            raise ShopifyConnectError(
                f"la lectura del catalogo devolvio HTTP {status}: "
                f"{_scrub(getattr(resp, 'text', ''), access_token)}"
            )
        cuerpo = resp.json()
        errores = (cuerpo or {}).get("errors") or []
        if errores:
            raise ShopifyConnectError(
                f"la lectura del catalogo fue rechazada: {_scrub(errores, access_token)}"
            )
        conexion = ((cuerpo or {}).get("data") or {}).get("productVariants") or {}
        for edge in conexion.get("edges", []):
            node = edge.get("node") or {}
            n_variants += 1
            sku = str(node.get("sku") or "").strip()
            if not sku:
                # Una variante sin SKU no se puede unir a nada. No se le inventa
                # uno a partir del id: el SKU es la llave con la que el
                # comerciante reconoce el producto en su propio sistema.
                dropped["variante_sin_sku"] += 1
                continue
            if sku not in cost_by_sku:
                costo = ((node.get("inventoryItem") or {}).get("unitCost") or {}).get("amount")
                if costo is not None:
                    try:
                        cost_by_sku[sku] = float(costo)
                    except (TypeError, ValueError):
                        pass
            if sku not in skus:
                skus.append(sku)
        info = conexion.get("pageInfo") or {}
        cursor = info.get("endCursor")
        if n_variants >= max_variants and info.get("hasNextPage"):
            truncated = True
            break
        if not info.get("hasNextPage") or not cursor:
            break

    return Catalog(
        skus=tuple(sorted(skus)),
        cost_by_sku=cost_by_sku,
        n_variants=n_variants,
        dropped=dropped,
        truncated=truncated,
    )


def _order_query(since: str | None, until: str | None) -> str:
    partes = ["status:any"]
    if since:
        partes.append(f"created_at:>={since}")
    if until:
        partes.append(f"created_at:<={until}")
    return " AND ".join(partes)


def _aggregate_orders(eventos: Any, *, catalog: Catalog | None = None) -> SalesHistory:
    """Los eventos de orden -> la historia bucketizada. La UNICA aritmetica.

    La comparten el lector paginado y el masivo. Tenerla dos veces seria tener
    dos definiciones de "demanda del mes": la misma tienda daria dos
    clasificaciones segun por que puerta entro, y ninguna de las dos se veria
    mal por separado.

    Un evento es ``{created_at, cancelled, test, lines: [(sku, qty)],
    lines_truncated}``. El filtrado vive aqui y no en cada lector, por lo mismo:
    "que cuenta como venta" se decide en un solo lugar.
    """
    por_periodo: dict[tuple[str, str], float] = {}
    # Sembrado con el catalogo en cero: un SKU que no vendio tiene una serie de
    # ceros observados, no una serie ausente.
    units_by_sku: dict[str, float] = {s: 0.0 for s in (catalog.skus if catalog else ())}
    last_sale: dict[str, str] = {}
    cost_by_sku: dict[str, float] = dict(catalog.cost_by_sku) if catalog else {}
    dropped = {
        "cancelada": 0, "de_prueba": 0, "sin_sku": 0,
        "lineas_truncadas": 0, "sin_costo": 0, "linea_huerfana": 0,
    }
    if catalog:
        dropped["variante_sin_sku"] = catalog.dropped.get("variante_sin_sku", 0)
        dropped["catalogo_truncado"] = int(catalog.truncated)
    n_orders = 0
    fechas: list[str] = []

    for ev in eventos:
        if ev.get("cancelled"):
            dropped["cancelada"] += 1
            continue
        if ev.get("test"):
            dropped["de_prueba"] += 1
            continue
        creado = str(ev.get("created_at") or "")
        if len(creado) < 10:
            continue
        n_orders += 1
        dia, periodo = creado[:10], creado[:7]
        fechas.append(dia)
        if ev.get("lines_truncated"):
            dropped["lineas_truncadas"] += 1
        dropped["linea_huerfana"] += int(ev.get("orphan_lines") or 0)
        for sku, qty in ev.get("lines") or ():
            sku = str(sku or "").strip()
            if not sku:
                dropped["sin_sku"] += 1
                continue
            cantidad = float(qty or 0)
            por_periodo[(sku, periodo)] = por_periodo.get((sku, periodo), 0.0) + cantidad
            units_by_sku[sku] = units_by_sku.get(sku, 0.0) + cantidad
            if dia > last_sale.get(sku, ""):
                last_sale[sku] = dia

    if not fechas:
        return SalesHistory(dropped=dropped, notes=(PLAZO_AUSENTE,))

    primera, ultima = min(fechas), max(fechas)
    periodos = _months_between(primera, ultima)
    dropped["sin_costo"] = len(units_by_sku) - len(cost_by_sku)
    # `cost_basis` describe de donde salio el numero, no si alcanza: el caso
    # MIXTO se resuelve como bloqueante en el preflight, porque este lector no
    # decide si la corrida procede.
    base = COSTO_REAL if cost_by_sku else COSTO_UNIDADES

    rows = tuple(
        {
            "product_id": sku,
            "period": periodo,
            "quantity": por_periodo.get((sku, periodo), 0.0),
            "unit_cost": cost_by_sku.get(sku, PLACEHOLDER_UNIT_COST),
        }
        for sku in sorted(units_by_sku)
        for periodo in periodos
    )
    return SalesHistory(
        rows=rows,
        units_by_sku=dict(units_by_sku),
        last_sale_by_sku=dict(last_sale),
        cost_by_sku=dict(cost_by_sku),
        n_orders=n_orders,
        first_order=primera,
        last_order=ultima,
        periods=tuple(periodos),
        cost_basis=base,
        dropped=dropped,
        notes=(PLAZO_AUSENTE,),
    )


def _paged_order_events(
    http: Any, *, since: str | None, until: str | None, api_version: str,
    max_orders: int, access_token: str,
) -> Any:
    """Los eventos de orden, paginando por CURSOR.

    No hay offset en GraphQL de Shopify, y emularlo con ``first: N`` sobre
    paginas sucesivas perderia filas en silencio en cuanto entre una orden nueva
    durante la lectura -- en una tienda viva eso pasa siempre.
    """
    path = _graphql_path(api_version)
    q = _order_query(since, until)
    cursor: str | None = None
    vistos = 0
    while True:
        resp = _post(http, path, ORDERS_QUERY, {"q": q, "cursor": cursor})
        status = _status(resp)
        if status >= 400:
            raise ShopifyConnectError(
                f"la lectura de ordenes devolvio HTTP {status}: "
                f"{_scrub(getattr(resp, 'text', ''), access_token)}"
            )
        cuerpo = resp.json()
        errores = (cuerpo or {}).get("errors") or []
        if errores:
            raise ShopifyConnectError(
                f"la lectura de ordenes fue rechazada: {_scrub(errores, access_token)}"
            )
        conexion = ((cuerpo or {}).get("data") or {}).get("orders") or {}
        for edge in conexion.get("edges", []):
            node = edge.get("node") or {}
            lineas = node.get("lineItems") or {}
            vistos += 1
            yield {
                "created_at": node.get("createdAt"),
                "cancelled": bool(node.get("cancelledAt")),
                "test": bool(node.get("test")),
                "lines_truncated": bool((lineas.get("pageInfo") or {}).get("hasNextPage")),
                "lines": [
                    ((le.get("node") or {}).get("sku"), (le.get("node") or {}).get("quantity"))
                    for le in lineas.get("edges", [])
                ],
            }
        info = conexion.get("pageInfo") or {}
        cursor = info.get("endCursor")
        if not info.get("hasNextPage") or not cursor or vistos >= max_orders:
            break


def read_sales_history(
    http: Any,
    *,
    since: str | None = None,
    until: str | None = None,
    api_version: str = PINNED_VERSION,
    max_orders: int = 50_000,
    access_token: str = "",
    catalog: Catalog | None = None,
) -> SalesHistory:
    """Las ordenes de venta -> filas con la forma del CSV de demanda (camino paginado).

    ``catalog`` fija el universo de SKU. Sin el, el universo sale de lo que
    vendio, y entonces un SKU que no vendio NADA no existe para la salida -- que
    es justo el que hay que ver: ``src.classification`` lo clasifica CZ (valor
    cero, variabilidad infinita: candidato a descontinuar) y el motor de E&O lo
    marca muerto. Ese SKU no deja hueco cuando falta, asi que la salida se ve
    completa igual. Por eso el universo se pide aparte y no se infiere.

    Este camino es el barato para una tienda chica y el que no escala: cada
    pagina son 20 ordenes y 820 puntos, asi que un historial largo se come el
    presupuesto de 25 minutos por cuenta. Para eso esta
    :func:`read_sales_history_bulk`.

    Lo que se descarta y por que esta en :func:`_aggregate_orders`, que comparten
    los dos caminos.
    """
    return _aggregate_orders(
        _paged_order_events(
            http, since=since, until=until, api_version=api_version,
            max_orders=max_orders, access_token=access_token,
        ),
        catalog=catalog,
    )


# -- 2b. el camino masivo (bulk operations) --------------------------------------

#: Estados que devuelve Shopify para una operacion masiva.
BULK_CREATED = "CREATED"
BULK_RUNNING = "RUNNING"
BULK_COMPLETED = "COMPLETED"
BULK_FAILED = "FAILED"
BULK_CANCELED = "CANCELED"
_BULK_TERMINALES = (BULK_COMPLETED, BULK_FAILED, BULK_CANCELED)

BULK_RUN_MUTATION = """
mutation StartBulkOrders($query: String!) {
  bulkOperationRunQuery(query: $query) {
    bulkOperation {
      id
      status
    }
    userErrors {
      field
      message
    }
  }
}
"""

BULK_POLL_QUERY = """
query BulkStatus($id: ID!) {
  bulkOperation(id: $id) {
    id
    status
    errorCode
    objectCount
    url
    partialDataUrl
  }
}
"""


def bulk_orders_query(since: str | None = None, until: str | None = None) -> str:
    """La consulta que corre ADENTRO de la operacion masiva.

    Sin un solo ``first``: una operacion masiva trae la conexion entera, y
    Shopify rechaza la consulta si lleva argumentos de paginacion. Eso tiene una
    consecuencia que vale mas que la velocidad -- ``lineItems`` deja de poder
    venir truncado, asi que el descarte por "orden con mas de 40 lineas"
    desaparece por construccion en vez de quedar reportado.
    """
    return (
        "{ orders(query: \"%s\") { edges { node { id createdAt cancelledAt test "
        "lineItems { edges { node { quantity sku } } } } } } }"
        % _order_query(since, until)
    )


@dataclass(frozen=True)
class BulkStatus:
    """Lo que Shopify dice de una operacion masiva."""

    id: str = ""
    status: str = ""
    error_code: str = ""
    object_count: int = 0
    url: str = ""
    partial_url: str = ""

    @property
    def done(self) -> bool:
        return self.status in _BULK_TERMINALES

    @property
    def ok(self) -> bool:
        return self.status == BULK_COMPLETED


def start_bulk_orders(
    http: Any, *, since: str | None = None, until: str | None = None,
    api_version: str = PINNED_VERSION, access_token: str = "", query: str | None = None,
) -> str:
    """Arranca la operacion y devuelve su id.

    ``query`` deja correr OTRA consulta de lectura por esta misma maquinaria --
    la del lado de servicio (``shopify_service.bulk_service_query``) -- sin
    duplicar el arranque, la sanitizacion del token ni el manejo de
    ``userErrors``. Sin argumento sigue siendo la lectura de demanda de siempre.

    ``bulkOperationRunQuery`` es una MUTACION, y es la unica que esta ruta
    ejecuta. No escribe nada en la tienda: le entrega a Shopify una consulta de
    LECTURA para que la corra del lado de ellos. Shopify lo confirma pidiendo
    solo los permisos de lectura de los datos consultados -- no hace falta ni un
    scope de escritura. La distincion importa y esta cubierta por un test: lo
    prohibido es escribir en el sistema de registro del comerciante, no invocar
    un campo que el esquema clasifica como mutacion.
    """
    resp = _post(
        http, _graphql_path(api_version), BULK_RUN_MUTATION,
        {"query": query if query is not None else bulk_orders_query(since, until)},
    )
    if _status(resp) >= 400:
        raise ShopifyConnectError(
            f"no se pudo arrancar la lectura masiva: HTTP {_status(resp)}"
        )
    cuerpo = resp.json() or {}
    if cuerpo.get("errors"):
        raise ShopifyConnectError(
            f"no se pudo arrancar la lectura masiva: {_scrub(cuerpo['errors'], access_token)}"
        )
    res = ((cuerpo.get("data") or {}).get("bulkOperationRunQuery")) or {}
    errores = res.get("userErrors") or []
    if errores:
        # Tipicamente: ya hay una operacion corriendo para esta app y tienda.
        raise ShopifyConnectError(
            f"Shopify rechazo la lectura masiva: {_scrub(errores, access_token)}"
        )
    op = res.get("bulkOperation") or {}
    if not op.get("id"):
        raise ShopifyConnectError(
            "Shopify acepto la lectura masiva pero no devolvio un id de operacion"
        )
    return str(op["id"])


def poll_bulk(
    http: Any, op_id: str, *, api_version: str = PINNED_VERSION, access_token: str = "",
) -> BulkStatus:
    """El estado de la operacion. Una consulta barata: se puede repetir."""
    resp = _post(http, _graphql_path(api_version), BULK_POLL_QUERY, {"id": op_id})
    if _status(resp) >= 400:
        raise ShopifyConnectError(f"no se pudo consultar la lectura masiva: HTTP {_status(resp)}")
    cuerpo = resp.json() or {}
    if cuerpo.get("errors"):
        raise ShopifyConnectError(
            f"no se pudo consultar la lectura masiva: {_scrub(cuerpo['errors'], access_token)}"
        )
    op = ((cuerpo.get("data") or {}).get("bulkOperation")) or {}
    return BulkStatus(
        id=str(op.get("id") or op_id),
        status=str(op.get("status") or ""),
        error_code=str(op.get("errorCode") or ""),
        object_count=int(op.get("objectCount") or 0),
        url=str(op.get("url") or ""),
        partial_url=str(op.get("partialDataUrl") or ""),
    )


def bare_client(*, timeout: float = 60.0) -> Any:
    """Un cliente HTTP SIN el token de Shopify, para bajar el resultado.

    La url del resultado NO es de la tienda: es un enlace firmado a un host de
    almacenamiento de terceros. Mandarle la cabecera ``X-Shopify-Access-Token``
    entregaria el Admin API token del comerciante a un tercero, en una peticion
    que nadie mira -- y el enlace ya lleva su propia autorizacion en la firma,
    asi que la cabecera no solo sobra: es una fuga. Por eso el cliente se
    construye aparte y no se reusa el de :func:`live_http`.
    """
    try:
        import httpx
    except ImportError as exc:  # pragma: no cover - depende del extra instalado
        raise ShopifyConnectError("httpx es necesario para bajar el resultado masivo") from exc
    return httpx.Client(timeout=timeout, follow_redirects=True)


def download_jsonl(url: str) -> Any:
    """Las lineas del JSONL del resultado, en streaming.

    En streaming y no ``.text``: el archivo de una tienda grande no tiene por que
    caber comodo en memoria, y aqui solo se necesita una linea a la vez.
    """
    with bare_client() as cliente:
        with cliente.stream("GET", url) as r:
            r.raise_for_status()
            for linea in r.iter_lines():
                if linea:
                    yield linea


def _bulk_order_events(lineas: Any) -> Any:
    """El JSONL -> los mismos eventos de orden que produce el camino paginado.

    El formato masivo aplana las conexiones: cada linea de pedido viene como su
    propio objeto con ``__parentId`` apuntando a la orden. Shopify las emite
    despues de su padre, asi que se puede reconstruir en streaming; una linea
    cuyo padre no es el ultimo visto se cuenta como huerfana en vez de
    adjudicarse a la orden equivocada, que seria demanda cargada al mes que no
    fue.
    """
    import json

    actual: dict | None = None
    for linea in lineas:
        texto = linea.decode("utf-8") if isinstance(linea, bytes) else str(linea)
        texto = texto.strip()
        if not texto:
            continue
        obj = json.loads(texto)
        padre = obj.get("__parentId")
        if padre is None:
            if actual is not None:
                yield actual
            actual = {
                "id": obj.get("id"),
                "created_at": obj.get("createdAt"),
                "cancelled": bool(obj.get("cancelledAt")),
                "test": bool(obj.get("test")),
                # Por construccion: una operacion masiva no pagina, asi que no
                # existe la orden con las lineas cortadas.
                "lines_truncated": False,
                "lines": [],
                "orphan_lines": 0,
            }
            continue
        if actual is None or padre != actual.get("id"):
            if actual is not None:
                actual["orphan_lines"] += 1
            continue
        actual["lines"].append((obj.get("sku"), obj.get("quantity")))
    if actual is not None:
        yield actual


def run_bulk_read(
    http: Any,
    *,
    query: str | None = None,
    since: str | None = None,
    until: str | None = None,
    api_version: str = PINNED_VERSION,
    access_token: str = "",
    bulk_id: str = "",
    max_wait: float = 120.0,
    poll_every: float = 3.0,
    sleep: Any = None,
    fetch_jsonl: Any = None,
) -> tuple[Any | None, BulkStatus]:
    """Arranca (o retoma) una lectura masiva y devuelve ``(lineas, estado)``.

    La maquinaria que comparten la lectura de demanda y la de servicio: arranque,
    espera acotada, los dos estados terminales que son un error, y el COMPLETED
    sin url que NO lo es. Vive en un solo lugar a proposito -- la regla de no
    tocar ``partialDataUrl`` es la que impide entregar un historial a medias como
    si estuviera completo, y una segunda copia de esta espera es una segunda
    oportunidad de olvidarla.

    ``lineas`` es ``None`` mientras la operacion sigue corriendo (el llamador
    vuelve mas tarde con ``bulk_id`` y no paga de nuevo el arranque), y ``()``
    cuando termino sin un solo objeto, que es un resultado vacio legitimo.
    """
    import time as _time

    dormir = sleep if sleep is not None else _time.sleep
    bajar = fetch_jsonl if fetch_jsonl is not None else download_jsonl

    op = bulk_id or start_bulk_orders(
        http, since=since, until=until, api_version=api_version,
        access_token=access_token, query=query,
    )
    estado = poll_bulk(http, op, api_version=api_version, access_token=access_token)
    esperado = 0.0
    while not estado.done and esperado < max_wait:
        dormir(poll_every)
        esperado += poll_every
        estado = poll_bulk(http, op, api_version=api_version, access_token=access_token)

    if not estado.done:
        return None, estado
    if estado.status == BULK_FAILED:
        raise ShopifyConnectError(
            f"la lectura masiva fallo ({estado.error_code or 'sin codigo'}). No se usa "
            f"partialDataUrl: un historial a medias da una demanda mas baja que la real y "
            f"empuja SKU hacia clase C y hacia excedente, sin que la salida se vea incompleta."
        )
    if estado.status == BULK_CANCELED:
        raise ShopifyConnectError("la lectura masiva fue cancelada antes de terminar")
    if not estado.url:
        return (), estado
    return bajar(estado.url), estado


def read_sales_history_bulk(
    http: Any,
    *,
    since: str | None = None,
    until: str | None = None,
    api_version: str = PINNED_VERSION,
    access_token: str = "",
    catalog: Catalog | None = None,
    bulk_id: str = "",
    max_wait: float = 120.0,
    poll_every: float = 3.0,
    sleep: Any = None,
    fetch_jsonl: Any = None,
) -> tuple[SalesHistory | None, BulkStatus]:
    """El historial por LECTURA MASIVA. Devuelve ``(historia, estado)``.

    ``historia`` es None mientras la operacion sigue corriendo: el llamador
    vuelve mas tarde con ``bulk_id`` y no se paga de nuevo el arranque. Es la
    diferencia con el camino paginado -- una operacion masiva no tiene techo de
    costo ni de tamano, pero tarda lo que tarda, y bloquear un request web hasta
    que termine no es una opcion.

    Sale por la MISMA :func:`_aggregate_orders` que el camino paginado, asi que
    la misma tienda da la misma clasificacion por las dos puertas.
    """
    lineas, estado = run_bulk_read(
        http, since=since, until=until, api_version=api_version, access_token=access_token,
        bulk_id=bulk_id, max_wait=max_wait, poll_every=poll_every, sleep=sleep,
        fetch_jsonl=fetch_jsonl,
    )
    if lineas is None:
        return None, estado
    return _aggregate_orders(_bulk_order_events(lineas), catalog=catalog), estado


# -- 3. lectura de stock por location -------------------------------------------


#: Niveles por pagina. La consulta es por LOCATION y no por variante: anidar
#: ``inventoryLevels`` dentro de ``productVariants`` multiplica el costo
#: calculado de Shopify (variantes x niveles) y obliga a paginas diminutas, lo
#: que devuelve el problema por la otra puerta. Una tienda tiene un punado de
#: bodegas y miles de SKU, asi que se itera lo poco y se pagina lo mucho.
#:
#: 100 y no 250 por el techo de 1.000 puntos por consulta: cada nivel cuesta
#: ~4 (el nivel, su cantidad, su item y la variante del item), asi que 250 daba
#: ~1.001 -- justo del lado equivocado del limite. 100 da ~401, que aguanta
#: cualquier error en esta aritmetica.
_LEVELS_PAGE = 100

#: Tope de niveles por bodega. Si se alcanza, la lectura se marca truncada y el
#: preflight BLOQUEA: medio inventario se ve igual de completo que uno entero.
_MAX_LEVELS_PER_LOCATION = 100_000

STOCK_QUERY = f"""
query StockAtLocation($id: ID!, $cursor: String) {{
  location(id: $id) {{
    inventoryLevels(first: {_LEVELS_PAGE}, after: $cursor) {{
      pageInfo {{
        hasNextPage
        endCursor
      }}
      edges {{
        node {{
          quantities(names: ["available"]) {{
            name
            quantity
          }}
          item {{
            tracked
            variant {{
              sku
            }}
          }}
        }}
      }}
    }}
  }}
}}
"""


def read_stock_on_hand(
    http: Any,
    locations: list[dict] | tuple[dict, ...],
    *,
    skus: list[str] | tuple[str, ...] | None = None,
    api_version: str = PINNED_VERSION,
    max_levels: int = _MAX_LEVELS_PER_LOCATION,
    access_token: str = "",
) -> StockRead:
    """El stock on hand por (SKU, location), en lotes por bodega.

    Antes esto reusaba ``get_inventory_level`` del conector, que resuelve UNA
    location por llamada: N*M viajes, o sea 15.000 pedidos para un catalogo de
    5.000 SKU en tres bodegas. Contra el limite de Shopify (bucket de puntos que
    se restaura por segundo) eso no es lento, es inviable, y el presupuesto de
    esta ruta son 25 minutos por cuenta al mes.

    Ahora son ``ceil(niveles / 250)`` pedidos POR BODEGA: el mismo catalogo pasa
    de 15.000 a ~60. El costo deja de escalar con el numero de SKU dentro de una
    pagina y deja de escalar con el numero de bodegas por SKU.

    La grilla de salida no cambia: una fila por (SKU, location) para TODAS las
    bodegas del roster, con 0.0 donde la bodega no tiene nivel para ese SKU.
    "Cero unidades en Concepcion" es el dato que sostiene la frase de pooling, y
    una fila ausente no lo dice -- se lee como "no sabemos".

    La dimension location SE CONSERVA. Plegarla aqui seria irreversible y se
    llevaria puesta la fuente de valor #2: "tienes 40 unidades muertas en
    Santiago y estas comprando las mismas 40 para Concepcion" es una frase que
    solo existe si las dos bodegas siguen separadas en la fila.
    """
    pedidos = set(skus) if skus is not None else None
    por_sku_loc: dict[tuple[str, str], float] = {}
    vistos: set[str] = set()
    dropped = {"nivel_sin_sku": 0, "no_rastreado": 0, "niveles_truncados": 0}
    n_levels = 0
    n_requests = 0
    path = _graphql_path(api_version)

    for loc in locations:
        cursor: str | None = None
        leidos = 0
        while True:
            resp = _post(http, path, STOCK_QUERY, {"id": loc["id"], "cursor": cursor})
            n_requests += 1
            status = _status(resp)
            if status >= 400:
                raise ShopifyConnectError(
                    f"la lectura de stock devolvio HTTP {status}: "
                    f"{_scrub(getattr(resp, 'text', ''), access_token)}"
                )
            cuerpo = resp.json()
            errores = (cuerpo or {}).get("errors") or []
            if errores:
                raise ShopifyConnectError(
                    f"la lectura de stock fue rechazada: {_scrub(errores, access_token)}"
                )
            niveles = (((cuerpo or {}).get("data") or {}).get("location") or {}).get(
                "inventoryLevels"
            ) or {}
            for edge in niveles.get("edges", []):
                node = edge.get("node") or {}
                n_levels += 1
                leidos += 1
                item = node.get("item") or {}
                sku = str(((item.get("variant") or {}).get("sku")) or "").strip()
                if not sku:
                    dropped["nivel_sin_sku"] += 1
                    continue
                if not item.get("tracked", True):
                    # Shopify no lleva la cuenta de este SKU: lo que devuelva no
                    # es una existencia medida. Se descarta nombrandolo, en vez
                    # de dejarlo entrar como un cero que el motor leeria como
                    # "hay cero unidades" y clasificaria como stock muerto.
                    dropped["no_rastreado"] += 1
                    continue
                if pedidos is not None and sku not in pedidos:
                    continue
                cantidad = 0.0
                for q in node.get("quantities") or []:
                    if q.get("name") == "available":
                        cantidad = float(q.get("quantity") or 0)
                vistos.add(sku)
                clave = (sku, loc["id"])
                por_sku_loc[clave] = por_sku_loc.get(clave, 0.0) + cantidad
            info = niveles.get("pageInfo") or {}
            cursor = info.get("endCursor")
            if leidos >= max_levels and info.get("hasNextPage"):
                dropped["niveles_truncados"] += 1
                break
            if not info.get("hasNextPage") or not cursor:
                break

    universo = sorted(pedidos & vistos) if pedidos is not None else sorted(vistos)
    rows = tuple(
        {
            "product_id": sku,
            "location_id": loc["id"],
            "location_name": loc.get("name", ""),
            "on_hand": por_sku_loc.get((sku, loc["id"]), 0.0),
        }
        for sku in universo
        for loc in locations
    )
    return StockRead(
        rows=rows,
        locations=tuple(locations),
        n_levels=n_levels,
        n_requests=n_requests,
        dropped=dropped,
    )


def fold_locations(stock_rows: tuple[dict, ...] | list[dict]) -> dict[str, float]:
    """(SKU, location) -> SKU, sumando. Explicito, y en un solo lugar.

    El pliegue existe porque la demanda de Shopify NO es atribuible por location:
    una orden de venta no dice de que bodega salio sin entrar a
    ``fulfillmentOrders``, asi que repartirla entre bodegas seria un supuesto
    disfrazado de dato. E&O se corre entonces sobre el stock TOTAL, y eso se
    declara en el preflight en vez de asumirse. Las filas por location siguen
    intactas para el analisis de pooling.
    """
    total: dict[str, float] = {}
    for r in stock_rows:
        total[r["product_id"]] = total.get(r["product_id"], 0.0) + float(r["on_hand"])
    return total


def eo_rows(
    history: SalesHistory, stock_rows: tuple[dict, ...] | list[dict], *, as_of: str = ""
) -> tuple[dict, ...]:
    """Las filas que come ``jobs/excess_obsolete_job.prepare_records``.

    ``daily_demand`` sale de las unidades observadas divididas por los dias de la
    ventana observada -- no por 365 ni por un mes nominal. Si la ventana es corta
    el numero es fragil, y por eso el preflight la reporta: un cover calculado
    sobre 60 dias de historial no se convierte en una recomendacion de liquidar
    sin que alguien vea de cuantos dias salio.

    Un SKU con stock y SIN ninguna venta en la ventana lleva ``daily_demand``
    0.0 a proposito, que el motor lee como DEAD. Eso es una observacion, no un
    faltante: la lectura cubrio la ventana entera y ese SKU no vendio en ella.
    """
    referencia = as_of or history.last_order
    dias = history.span_days
    ventana = float(dias) + 1.0 if dias is not None else 0.0
    totales = fold_locations(stock_rows)
    salida: list[dict] = []
    for sku in sorted(totales):
        unidades = history.units_by_sku.get(sku, 0.0)
        fila = {
            "product_id": sku,
            "on_hand": totales[sku],
            "daily_demand": (unidades / ventana) if ventana > 0 else 0.0,
            "unit_cost": history.cost_by_sku.get(sku, PLACEHOLDER_UNIT_COST),
        }
        ultima = history.last_sale_by_sku.get(sku, "")
        if ultima and referencia:
            fila["days_since_last_sale"] = float(
                (date.fromisoformat(referencia) - date.fromisoformat(ultima)).days
            )
        salida.append(fila)
    return tuple(salida)


# -- 4. preflight ----------------------------------------------------------------


def _sin_nada_que_medir(history: SalesHistory) -> tuple[str, ...]:
    """Los bloqueantes cuando no quedo ni una orden utilizable.

    Cada uno nombra la causa dominante del descarte, porque el remedio es
    distinto: sin SKU se revisa el catalogo, con todo cancelado se revisa el
    rango de fechas, y sin ordenes no hay nada que hacer todavia. Un unico "no se
    encontraron datos" los manda a los tres.
    """
    d = history.dropped
    mensajes: list[str] = []
    if d.get("sin_sku"):
        mensajes.append(
            f"{d['sin_sku']} linea(s) de venta no tienen SKU. Sin SKU no hay como unir la venta con "
            "el inventario, asi que la linea no entra en ninguna clasificacion. En tu admin: "
            "Productos -> la variante -> Inventario -> SKU."
        )
    if d.get("cancelada") or d.get("de_prueba"):
        mensajes.append(
            f"todas las ordenes del rango estan canceladas ({d.get('cancelada', 0)}) o son de prueba "
            f"({d.get('de_prueba', 0)}). Una orden cancelada no es demanda, y una de prueba la "
            "genero el propio comerciante probando el pago: contarlas inflaria la clasificacion."
        )
    if not mensajes:
        mensajes.append(
            "no hay ninguna orden de venta en el rango pedido. La clasificacion ABC/XYZ se construye "
            "sobre historial de venta: sin ordenes no hay demanda que clasificar. Amplia el rango, o "
            "conecta la tienda donde este el historial."
        )
    return tuple(mensajes)


def preflight(
    history: SalesHistory,
    stock: StockRead,
    *,
    check: ConnectionCheck | None = None,
    required_days: int | None = None,
    min_periods: int | None = None,
) -> Preflight:
    """Que se encontro y que falta. PURA: cero llamadas extra a Shopify.

    Corre sobre lo ya leido y no sobre una consulta propia: una segunda pasada
    para "contar antes de medir" duplica la latencia del onboarding y puede
    contar sobre un estado distinto del que despues se mide.

    ``required_days`` es la ventana que la tarea exige. Si el historial es mas
    corto, se RECHAZA nombrando cuanto hay y cuanto falta -- nunca se rellena, y
    nunca se corre igual "con lo que haya", que es como sale una clasificacion
    con la forma correcta y las letras equivocadas.
    """
    # Del roster y no de las filas: una bodega sin existencias sigue siendo una
    # bodega, y contarla solo cuando tiene stock esconde justo el lugar vacio.
    locations = {loc["id"] for loc in stock.locations}
    missing: list[str] = []
    warnings: list[str] = []

    if not history.rows:
        missing.extend(_sin_nada_que_medir(history))

    # El costo mixto: bloqueante, y no una advertencia. Un SKU sin costo entre
    # otros que si lo tienen entra al ranking con un marcador, y el marcador lo
    # manda al fondo de la lista por falta de dato -- o sea, se lo clasifica como
    # C por no saber. Eso no se distingue de un C medido, que es exactamente la
    # clase de numero que este proyecto no emite.
    sin_costo = history.dropped.get("sin_costo", 0)
    if history.rows and 0 < sin_costo < history.n_skus:
        missing.append(
            f"{sin_costo} de {history.n_skus} SKU no tienen costo unitario en Shopify, y el resto si. "
            "Mezclarlos rankea a unos por valor y a otros por un marcador: los que no tienen costo "
            "caerian a clase C por falta de dato, indistinguibles de un C medido. En tu admin: "
            "Productos -> la variante -> Costo por articulo. Cargalo en todos, o en ninguno (sin "
            "ningun costo la clasificacion se hace por unidades y la pagina lo declara)."
        )
    elif history.rows and history.cost_basis == COSTO_UNIDADES:
        warnings.append(
            "ningun producto tiene 'Costo por articulo' cargado en Shopify, asi que el ABC se rankea "
            "por UNIDADES y no por valor. Un SKU barato de alta rotacion no es un A. La pagina "
            "declara la base; para rankear por valor, carga el costo en Productos -> la variante -> "
            "Costo por articulo."
        )

    if history.rows and not locations:
        missing.append(
            "no se pudo leer stock de ninguna location. El analisis de excedente necesita las "
            "existencias actuales. Revisa que la app tenga read_inventory y read_locations, y que "
            "los productos tengan el inventario rastreado (Productos -> la variante -> Inventario -> "
            "Hacer seguimiento de la cantidad)."
        )

    # La ventana. Este es el caso que un embudo se come callado si nadie lo mira:
    # Shopify devuelve 200 y una lista corta, no un error.
    span = history.span_days
    if history.rows and required_days is not None and span is not None and span < required_days:
        causa = ""
        if check is not None and not check.all_orders:
            causa = (
                f" La causa mas probable es que a la app le falta el permiso {ALL_ORDERS_SCOPE}: sin "
                f"el, Shopify entrega SOLO los ultimos {ORDERS_WINDOW_WITHOUT_ALL_ORDERS_DAYS} dias "
                "de ordenes, y lo hace con una respuesta exitosa y una lista corta -- no con un "
                "error. Se marca en Admin API scopes junto a los demas, y despues hay que reinstalar "
                "la app."
            )
        missing.append(
            f"el historial cubre {span} dia(s) ({history.first_order} a {history.last_order}) y la "
            f"tarea pide {required_days}. No se completa con nada: una serie estirada da la misma "
            f"forma de salida con las letras equivocadas.{causa}"
        )

    if history.rows and min_periods is not None and len(history.periods) < min_periods:
        missing.append(
            f"el historial tiene {len(history.periods)} periodo(s) mensual(es) y la variabilidad "
            f"(XYZ) necesita al menos {min_periods}. Un coeficiente de variacion sobre dos meses no "
            "distingue un SKU estable de uno erratico."
        )

    if history.n_never_sold:
        warnings.append(
            f"{history.n_never_sold} SKU del catalogo no vendieron ni una unidad en la ventana. No "
            "es un faltante: la lectura cubrio la ventana entera y no vendieron. Entran a la "
            "clasificacion como CZ (candidatos a descontinuar) y al analisis de stock como muertos, "
            "que es precisamente lo que hay que ver."
        )
    if history.dropped.get("catalogo_truncado"):
        missing.append(
            "el catalogo excede el tope de variantes que esta lectura recorre, asi que quedo "
            "incompleto. Se corta aqui en vez de seguir con lo leido: un excedente calculado sobre "
            "medio catalogo se ve igual de completo que uno entero y no hay forma de notar la "
            "diferencia en la salida."
        )
    if history.dropped.get("variante_sin_sku"):
        warnings.append(
            f"{history.dropped['variante_sin_sku']} variante(s) del catalogo no tienen SKU y quedaron "
            "fuera. En tu admin: Productos -> la variante -> Inventario -> SKU."
        )
    if stock.dropped.get("niveles_truncados"):
        missing.append(
            f"la lectura de stock se corto en {stock.dropped['niveles_truncados']} bodega(s): hay "
            "mas niveles de inventario de los que esta lectura recorre. Se bloquea en vez de seguir "
            "con lo leido, porque medio inventario se ve igual de completo que uno entero."
        )
    if stock.dropped.get("no_rastreado"):
        warnings.append(
            f"{stock.dropped['no_rastreado']} nivel(es) de inventario son de SKU sin seguimiento de "
            "cantidad, y quedaron fuera. Shopify no lleva su cuenta, asi que no hay existencia que "
            "medir -- entrarian como cero, que el analisis leeria como stock muerto. En tu admin: "
            "Productos -> la variante -> Inventario -> Hacer seguimiento de la cantidad."
        )
    if history.dropped.get("lineas_truncadas"):
        warnings.append(
            f"{history.dropped['lineas_truncadas']} orden(es) tienen mas de 250 lineas y se leyeron "
            "truncadas. Los SKU que quedaron afuera de esas ordenes muestran menos demanda de la "
            "real, lo que los empuja hacia clase C y hacia 'excedente'."
        )
    if history.dropped.get("cancelada"):
        warnings.append(
            f"{history.dropped['cancelada']} orden(es) canceladas quedaron fuera de la demanda: una "
            "venta cancelada no ocurrio."
        )
    if history.dropped.get("de_prueba"):
        warnings.append(
            f"{history.dropped['de_prueba']} orden(es) de prueba quedaron fuera de la demanda."
        )
    if history.rows and history.dropped.get("sin_sku"):
        warnings.append(
            f"{history.dropped['sin_sku']} linea(s) de venta sin SKU quedaron fuera: no hay como "
            "unirlas al inventario."
        )
    if len(locations) > 1:
        warnings.append(
            f"la tienda tiene {len(locations)} locations. La demanda de Shopify no es atribuible por "
            "location (una orden no dice de que bodega salio), asi que el excedente se calcula sobre "
            "el stock TOTAL. El detalle por location se conserva aparte para el analisis de pooling."
        )
    if check is not None and not check.all_orders:
        # SIEMPRE, no solo cuando la ventana pedida no alcanza. Sin este aviso, un
        # comerciante que no pide una ventana explicita recibe un analisis sobre
        # 60 dias y nada se lo dice: Shopify contesta 200 con una lista corta, el
        # preflight cuenta lo que llego, y la clasificacion sale con la forma
        # correcta y las letras equivocadas. Un tope que nadie declara se lee como
        # "esto es todo lo que hay".
        warnings.append(
            f"a la app le falta el permiso {ALL_ORDERS_SCOPE}, asi que Shopify entrego SOLO los "
            f"ultimos {ORDERS_WINDOW_WITHOUT_ALL_ORDERS_DAYS} dias de ordenes -- con una respuesta "
            "exitosa y una lista corta, no con un error. Con dos meses de historial la variabilidad "
            "(XYZ) no distingue un SKU estable de uno erratico. Se marca en Admin API scopes junto "
            "a los demas y despues hay que reinstalar la app."
        )
    if check is not None and check.write_scopes:
        warnings.append(
            f"la app custom tiene permisos de ESCRITURA concedidos ({', '.join(check.write_scopes)}). "
            "Esta ruta no los usa y no escribe nada, pero el permiso concedido es mas de lo que "
            "necesita: se puede desmarcar en Admin API scopes."
        )

    return Preflight(
        n_orders=history.n_orders,
        n_skus=history.n_skus,
        n_locations=len(locations),
        first_order=history.first_order,
        last_order=history.last_order,
        n_periods=len(history.periods),
        cost_basis=history.cost_basis,
        dropped=dict(history.dropped),
        missing=tuple(missing),
        warnings=tuple(warnings),
    )


# -- la ruta completa ------------------------------------------------------------


def scan_input(
    creds: ShopifyCredentials,
    *,
    http: Any = None,
    since: str | None = None,
    until: str | None = None,
    api_version: str = PINNED_VERSION,
    max_orders: int = 50_000,
    required_days: int | None = None,
    min_periods: int | None = None,
    as_of: str = "",
    timeout: float = 30.0,
    orders_mode: str = ORDERS_PAGED,
    bulk_id: str = "",
    max_wait: float = 120.0,
    poll_every: float = 3.0,
    sleep: Any = None,
    fetch_jsonl: Any = None,
) -> ShopifyScanInput:
    """Dos campos -> las filas que comen T03 y T11, o el motivo por el que no.

    Nunca levanta por una falla del otro lado: valida, lee, hace el preflight y
    devuelve un resultado que el endpoint mapea a un 400 con el mensaje ya
    escrito. Una excepcion cruzando hasta la ruta HTTP es como un embudo
    autoservicio termina mostrandole "Internal Server Error" a un comerciante.

    Sin ``http``, se construye el cliente vivo y se CIERRA al salir por donde
    sea. Un ``httpx.Client`` que no se cierra deja el pool de conexiones abierto,
    y esta ruta corre una vez por cuenta dentro de un request web.

    Una sola tienda: TODO -- catalogo, ordenes, bodegas y niveles -- sale del
    mismo ``http``. Inyectar ese transporte (``InMemoryShopifyAdmin`` en los
    tests) ejercita de paso el ``ShopifyInventoryClient`` real.
    """
    propio = None
    if http is None:
        http = propio = live_http(creds, timeout=timeout)
    try:
        return _scan_input(
            creds, http=http, since=since, until=until, api_version=api_version,
            max_orders=max_orders, required_days=required_days, min_periods=min_periods,
            as_of=as_of, orders_mode=orders_mode, bulk_id=bulk_id, max_wait=max_wait,
            poll_every=poll_every, sleep=sleep, fetch_jsonl=fetch_jsonl,
        )
    finally:
        if propio is not None:
            propio.close()


def _scan_input(
    creds: ShopifyCredentials,
    *,
    http: Any,
    since: str | None = None,
    until: str | None = None,
    api_version: str = PINNED_VERSION,
    max_orders: int = 50_000,
    required_days: int | None = None,
    min_periods: int | None = None,
    as_of: str = "",
    orders_mode: str = ORDERS_PAGED,
    bulk_id: str = "",
    max_wait: float = 120.0,
    poll_every: float = 3.0,
    sleep: Any = None,
    fetch_jsonl: Any = None,
) -> ShopifyScanInput:
    """El cuerpo de :func:`scan_input`, con el transporte ya resuelto."""
    check = check_connection(creds, http=http, api_version=api_version)
    if not check.ok:
        return ShopifyScanInput(check=check, source_notes=(PLAZO_AUSENTE,))

    try:
        # El catalogo PRIMERO: fija el universo de SKU. Si saliera del historial,
        # el stock que no vendio -- lo que T11 existe para encontrar -- seria
        # invisible, y la salida se veria completa igual porque lo que falta no
        # deja hueco.
        catalog = read_catalog(
            http, api_version=api_version, access_token=creds.access_token
        )
        if orders_mode == ORDERS_BULK:
            history, estado = read_sales_history_bulk(
                http, since=since, until=until, api_version=api_version,
                access_token=creds.access_token, catalog=catalog, bulk_id=bulk_id,
                max_wait=max_wait, poll_every=poll_every, sleep=sleep,
                fetch_jsonl=fetch_jsonl,
            )
            if history is None:
                # Sigue corriendo. Se devuelve el id y NADA mas: un resultado
                # parcial presentado como final es indistinguible de uno completo.
                return ShopifyScanInput(
                    check=check, bulk=estado, source_notes=(PLAZO_AUSENTE,)
                )
        elif orders_mode == ORDERS_PAGED:
            estado = None
            history = read_sales_history(
                http,
                since=since,
                until=until,
                api_version=api_version,
                max_orders=max_orders,
                access_token=creds.access_token,
                catalog=catalog,
            )
        else:
            raise ShopifyConnectError(
                f"modo de lectura de ordenes desconocido: {orders_mode!r} "
                f"(esperaba {ORDERS_PAGED!r} o {ORDERS_BULK!r})"
            )
    except ShopifyConnectError as exc:
        return ShopifyScanInput(
            check=check,
            preflight=Preflight(
                0, 0, 0, "", "", 0, COSTO_UNIDADES,
                missing=(_scrub(exc, creds.access_token),),
            ),
            source_notes=(PLAZO_AUSENTE,),
        )

    # El roster sale del MISMO ``http`` que todo lo demas. Tuvo un parametro
    # ``rpc`` aparte y se quito: con el roster viniendo de un objeto y los
    # niveles de otro, nada garantizaba que ambos apuntaran a la misma tienda --
    # y el sintoma habria sido un stock de cero sobre bodegas que existen, que se
    # lee como "no hay nada" en vez de como "estas mirando dos tiendas".
    # ``list_locations`` se reusa del conector en vez de reescribir su consulta:
    # una segunda copia de la misma query es como se arregla en un archivo y se
    # olvida en el otro.
    from src.connectors.shopify_inventory import ShopifyInventoryClient

    stock = read_stock_on_hand(
        http,
        ShopifyInventoryClient(http, api_version=api_version).list_locations(),
        skus=sorted(history.units_by_sku),
        api_version=api_version,
        access_token=creds.access_token,
    )
    pre = preflight(history, stock, check=check, required_days=required_days, min_periods=min_periods)

    return ShopifyScanInput(
        check=check,
        preflight=pre,
        demand_rows=history.rows if pre.ok else (),
        stock_rows=stock.rows if pre.ok else (),
        eo_rows=eo_rows(history, stock.rows, as_of=as_of) if pre.ok else (),
        bulk=estado,
        dropped=dict(history.dropped),
        source_notes=(PLAZO_AUSENTE,) + pre.warnings,
    )


# -- doble offline del TRANSPORTE ------------------------------------------------


#: El id que devuelve el doble al arrancar una operacion masiva.
_BULK_ID_DEMO = "gid://shopify/BulkOperation/1"


class _FakeResponse:
    def __init__(self, payload: Any, *, status: int = 200, headers: dict | None = None) -> None:
        self.status_code = status
        self._payload = payload
        self.headers = headers or {}
        self.text = str(payload)

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")

    def json(self) -> Any:
        return self._payload


class InMemoryShopifyAdmin:
    """La Admin API GraphQL de Shopify, sobre datos en memoria.

    ``InMemoryShopifyInventory`` dobla el lado de inventario. Lo que este modulo
    valida esta una capa mas abajo: el POST a ``/admin/api/<v>/graphql.json``, su
    codigo HTTP, su cabecera ``X-Shopify-API-Version`` y la paginacion por cursor
    de ``orders``. Sin doblar esa capa, los cuatro modos de falla solo se podrian
    ejercitar contra una tienda real que tuviera ese defecto puesto -- o sea,
    nunca en CI, que es exactamente donde tienen que estar cubiertos.

    Se parametriza para producir cada falla: ``status=401`` (TOKEN), quitar un
    scope (SCOPE), ``served_version`` distinta (DRIFT), ``unreachable`` (DOMINIO).
    ``page_size`` fuerza varias paginas para que la paginacion por cursor se
    ejerza de verdad y no solo en el caso de una sola pagina. ``calls`` deja
    registrado todo lo que se invoco: es la prueba de solo-lectura.
    """

    def __init__(
        self,
        orders: list[dict] | None = None,
        *,
        scopes: tuple[str, ...] = REQUIRED_SCOPES,
        shop_name: str = "Tienda Demo",
        served_version: str | None = None,
        unreachable: Exception | None = None,
        status: int = 200,
        page_size: int = 2,
        errors: list | None = None,
        payload: Any = None,
        catalog: list[tuple[str, float | None]] | None = None,
        inventory: Any = None,
        stock_page_size: int = 250,
        bulk_polls_running: int = 0,
        bulk_status: str = BULK_COMPLETED,
        bulk_error_code: str = "",
        bulk_user_errors: list | None = None,
    ) -> None:
        self._stock_page_size = max(1, stock_page_size)
        #: Cuantas consultas de estado contestan RUNNING antes del estado final.
        #: Es lo que permite ejercitar la espera acotada sin dormir de verdad.
        self._bulk_polls_running = bulk_polls_running
        self._bulk_status = bulk_status
        self._bulk_error_code = bulk_error_code
        self._bulk_user_errors = bulk_user_errors or []
        self._bulk_polls = 0
        #: La consulta que se le entrego a Shopify para correr. Es lo que se
        #: inspecciona para probar que la mutacion NO lleva una escritura dentro.
        self.bulk_query = ""
        #: Un host de TERCEROS a proposito: la url del resultado no es la tienda,
        #: y bajarla con el token de Shopify seria entregarselo a ese tercero.
        self.bulk_url = "https://storage.example.com/bulk/resultado.jsonl?firma=abc"
        self._orders = list(orders or [])
        #: Un ``InMemoryShopifyInventory``. Cuando esta, este doble tambien sirve
        #: las tres consultas de ``ShopifyInventoryClient``, y entonces la ruta
        #: completa corre contra el cliente REAL en vez de saltearselo con
        #: ``rpc=``. Un test que inyecta el rpc no ejercita el cliente que se va
        #: a usar en produccion.
        self._inventory = inventory
        # Por defecto el catalogo es lo que aparece en las ordenes, que es el
        # caso comun. Pasarlo explicito es como se dobla la tienda que importa:
        # la que tiene un SKU con stock y sin una sola venta.
        self._catalog = catalog if catalog is not None else self._catalog_from_orders()
        self._scopes = tuple(scopes)
        self._shop_name = shop_name
        self._served = served_version if served_version is not None else PINNED_VERSION
        self._unreachable = unreachable
        self._status = status
        self._page_size = max(1, page_size)
        self._errors = errors
        self._payload = payload
        #: la operacion de cada llamada. La prueba de solo-lectura.
        self.calls: list[str] = []

    def close(self) -> None:
        """No-op: ``scan_input`` cierra el cliente que construyo el mismo.

        Existe para que el doble entre por la MISMA rama que el ``httpx.Client``
        vivo -- la que lo cierra en el ``finally``. Un doble que solo funciona
        por la rama de ``http=`` inyectado deja esa rama sin ejercitar.
        """

    def _catalog_from_orders(self) -> list[tuple[str, float | None]]:
        visto: dict[str, float | None] = {}
        for o in self._orders:
            for e in ((o.get("lineItems") or {}).get("edges") or []):
                n = e.get("node") or {}
                sku = str(n.get("sku") or "").strip()
                if not sku:
                    continue
                costo = ((n.get("variant") or {}).get("inventoryItem") or {}).get("unitCost") or {}
                monto = costo.get("amount")
                if sku not in visto or visto[sku] is None:
                    visto[sku] = float(monto) if monto is not None else None
        return sorted(visto.items())

    def _resp(self, payload: Any, *, status: int | None = None) -> _FakeResponse:
        # El header va en TODA respuesta, como lo hace Shopify.
        return _FakeResponse(
            payload,
            status=self._status if status is None else status,
            headers={"X-Shopify-API-Version": self._served},
        )

    def post(self, path: str, *, json: dict | None = None, **_kw: Any) -> _FakeResponse:
        body = json or {}
        query = str(body.get("query", ""))
        variables = body.get("variables") or {}
        if self._unreachable is not None:
            raise self._unreachable
        if self._status >= 400:
            self.calls.append("http_error")
            return self._resp({"errors": [{"message": f"HTTP {self._status}"}]})
        if self._payload is not None:
            self.calls.append("payload_crudo")
            return self._resp(self._payload)
        if self._errors is not None:
            self.calls.append("errors")
            return self._resp({"errors": self._errors})
        if "/graphql.json" not in path:
            return self._resp({"message": "not found"}, status=404)

        if "ShopInfo" in query:
            self.calls.append("shop")
            return self._resp(
                {"data": {"shop": {"name": self._shop_name, "myshopifyDomain": "demo.myshopify.com"}}}
            )
        if "AppScopes" in query:
            self.calls.append("accessScopes")
            return self._resp(
                {"data": {"currentAppInstallation": {"accessScopes": [{"handle": s} for s in self._scopes]}}}
            )
        if "Catalog" in query:
            self.calls.append("catalog")
            if "read_products" not in self._scopes:
                return self._resp(
                    {"errors": [{"message": "denegado", "extensions": {"code": "ACCESS_DENIED"}}]}
                )
            return self._resp(
                {
                    "data": {
                        "productVariants": {
                            "pageInfo": {"hasNextPage": False, "endCursor": None},
                            "edges": [
                                {
                                    "node": {
                                        "sku": sku,
                                        "inventoryItem": {
                                            "unitCost": ({"amount": str(c)} if c is not None else None)
                                        },
                                    }
                                }
                                for sku, c in self._catalog
                            ],
                        }
                    }
                }
            )
        if "StartBulkOrders" in query:
            self.calls.append("bulkRun")
            self.bulk_query = str((variables or {}).get("query", ""))
            if self._bulk_user_errors:
                return self._resp({"data": {"bulkOperationRunQuery": {
                    "bulkOperation": None, "userErrors": self._bulk_user_errors}}})
            return self._resp({"data": {"bulkOperationRunQuery": {
                "bulkOperation": {"id": _BULK_ID_DEMO, "status": BULK_CREATED},
                "userErrors": []}}})
        if "BulkStatus" in query:
            self.calls.append("bulkPoll")
            self._bulk_polls += 1
            corriendo = self._bulk_polls <= self._bulk_polls_running
            estado = BULK_RUNNING if corriendo else self._bulk_status
            listo = estado == BULK_COMPLETED
            return self._resp({"data": {"bulkOperation": {
                "id": str((variables or {}).get("id", _BULK_ID_DEMO)),
                "status": estado,
                "errorCode": self._bulk_error_code,
                "objectCount": len(self._orders) if listo else 0,
                "url": self.bulk_url if listo else None,
                "partialDataUrl": None,
            }}})
        if "SalesHistory" in query:
            self.calls.append("orders")
            if "read_orders" not in self._scopes:
                return self._resp(
                    {"errors": [{"message": "denegado", "extensions": {"code": "ACCESS_DENIED"}}]}
                )
            return self._resp(self._orders_page(variables.get("cursor")))
        inventario = self._inventory_response(query, variables)
        if inventario is not None:
            return self._resp(inventario)
        self.calls.append("desconocida")
        return self._resp({"errors": [{"message": f"operacion no doblada: {query[:40]}"}]})

    def _inventory_response(self, query: str, variables: dict) -> dict | None:
        """Las tres consultas de ``ShopifyInventoryClient``, o None si no es una."""
        inv = self._inventory
        if inv is None:
            return None
        if "ListLocations" in query:
            self.calls.append("locations")
            return {
                "data": {"locations": {"edges": [{"node": n} for n in inv.list_locations()]}}
            }
        if "FindVariantInventoryBySku" in query:
            self.calls.append("variantBySku")
            sku = str(variables.get("q", "")).split("sku:", 1)[-1]
            item = inv.find_inventory_item_by_sku(sku)
            if item is None:
                return {"data": {"productVariants": {"edges": []}}}
            return {
                "data": {
                    "productVariants": {
                        "edges": [
                            {
                                "node": {
                                    "id": item["variant_id"],
                                    "sku": item["sku"],
                                    "inventoryItem": {
                                        "id": item["inventory_item_id"],
                                        "tracked": item.get("tracked", True),
                                    },
                                }
                            }
                        ]
                    }
                }
            }
        if "StockAtLocation" in query:
            self.calls.append("stockAtLocation")
            return self._stock_page(str(variables.get("id", "")), variables.get("cursor"))
        if "GetInventoryLevel" in query:
            self.calls.append("inventoryLevel")
            item_id = str(variables.get("id", ""))
            return {
                "data": {
                    "inventoryItem": {
                        "inventoryLevels": {
                            "edges": [
                                {
                                    "node": {
                                        "location": {"id": loc["id"]},
                                        "quantities": [
                                            {
                                                "name": "available",
                                                "quantity": inv.get_inventory_level(item_id, loc["id"]),
                                            }
                                        ],
                                    }
                                }
                                for loc in inv.list_locations()
                            ]
                        }
                    }
                }
            }
        return None

    def jsonl(self) -> list[str]:
        """El resultado masivo con la forma que emite Shopify: aplanado y con __parentId."""
        import json as _json

        lineas: list[str] = []
        for o in self._orders:
            lineas.append(_json.dumps({
                "id": o["id"],
                "createdAt": o.get("createdAt"),
                "cancelledAt": o.get("cancelledAt"),
                "test": o.get("test", False),
            }))
            corto = o["id"].rsplit("/", 1)[-1]
            for i, e in enumerate(((o.get("lineItems") or {}).get("edges") or [])):
                n = e.get("node") or {}
                lineas.append(_json.dumps({
                    "id": f"gid://shopify/LineItem/{corto}-{i}",
                    "quantity": n.get("quantity"),
                    "sku": n.get("sku"),
                    # El lado de servicio viaja solo si la orden lo trae: una
                    # tienda sin datos de entrega se dobla omitiendolos, que es
                    # el caso que tiene que salir "no medido" y no cero.
                    **{k: n[k] for k in
                       ("currentQuantity", "unfulfilledQuantity", "originalUnitPriceSet")
                       if k in n},
                    "__parentId": o["id"],
                }))
            for i, e in enumerate(((o.get("fulfillments") or {}).get("edges") or [])):
                n = dict(e.get("node") or {})
                n["id"] = f"gid://shopify/Fulfillment/{corto}-{i}"
                n["__parentId"] = o["id"]
                lineas.append(_json.dumps(n))
            for i, e in enumerate(((o.get("fulfillmentOrders") or {}).get("edges") or [])):
                n = dict(e.get("node") or {})
                n["id"] = f"gid://shopify/FulfillmentOrder/{corto}-{i}"
                n["__parentId"] = o["id"]
                lineas.append(_json.dumps(n))
        return lineas

    def _stock_page(self, location_id: str, cursor: str | None) -> dict:
        """Los niveles de UNA bodega, paginados.

        El doble enumera el catalogo por SKU internamente, que es O(N*M) en
        memoria -- y da igual: lo que este doble mide es cuantos PEDIDOS HTTP
        hace el cliente, que es lo unico que Shopify cobra. Un doble que
        escondiera la paginacion detras de una sola respuesta dejaria el lote sin
        ejercitar.
        """
        inv = self._inventory
        todos = []
        for sku, _costo in self._catalog:
            if not sku:
                continue
            item = inv.find_inventory_item_by_sku(sku)
            if item is None:
                continue
            todos.append(
                {
                    "quantities": [
                        {
                            "name": "available",
                            "quantity": inv.get_inventory_level(item["inventory_item_id"], location_id),
                        }
                    ],
                    "item": {
                        "tracked": item.get("tracked", True),
                        "variant": {"sku": sku},
                    },
                }
            )
        inicio = int(cursor) if cursor else 0
        corte = inicio + self._stock_page_size
        pagina = todos[inicio:corte]
        hay_mas = corte < len(todos)
        return {
            "data": {
                "location": {
                    "inventoryLevels": {
                        "pageInfo": {
                            "hasNextPage": hay_mas,
                            "endCursor": str(corte) if hay_mas else None,
                        },
                        "edges": [{"node": n} for n in pagina],
                    }
                }
            }
        }

    def _orders_page(self, cursor: str | None) -> dict:
        inicio = int(cursor) if cursor else 0
        corte = inicio + self._page_size
        pagina = self._orders[inicio:corte]
        hay_mas = corte < len(self._orders)
        return {
            "data": {
                "orders": {
                    "pageInfo": {"hasNextPage": hay_mas, "endCursor": str(corte) if hay_mas else None},
                    "edges": [{"node": o} for o in pagina],
                }
            }
        }


def make_order(
    order_id: str,
    created_at: str,
    lines: list[tuple[str, float]],
    *,
    cancelled: bool = False,
    test: bool = False,
    unit_cost: float | None = None,
    lines_truncated: bool = False,
) -> dict:
    """Una orden con la forma que devuelve la Admin API. Para tests y demos."""
    return {
        "id": f"gid://shopify/Order/{order_id}",
        "createdAt": created_at,
        "cancelledAt": created_at if cancelled else None,
        "test": test,
        "lineItems": {
            "pageInfo": {"hasNextPage": lines_truncated},
            "edges": [
                {
                    "node": {
                        "quantity": qty,
                        "sku": sku,
                        "variant": {
                            "inventoryItem": {
                                "unitCost": ({"amount": str(unit_cost)} if unit_cost is not None else None)
                            }
                        },
                    }
                }
                for sku, qty in lines
            ],
        },
    }


def demo_shopify_admin() -> InMemoryShopifyAdmin:
    """Una tienda en memoria con doce meses de venta, para demos offline."""
    ordenes = [
        make_order(
            str(mes),
            f"2026-{mes:02d}-15T10:00:00Z",
            [("SKU-1", 10.0 + mes), ("SKU-2", 3.0), ("SKU-3", 1.0)],
            unit_cost=6.0,
        )
        for mes in range(1, 13)
    ]
    return InMemoryShopifyAdmin(ordenes)
