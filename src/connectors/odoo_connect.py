"""Conexion autoservicio a un Odoo Online (el SaaS de odoo.com), en SOLO LECTURA.

Odoo Online no admite codigo Python de terceros: `odoo_addon/linchpin_dry_run`
solo se instala en Odoo.sh y on-premise. Para el SaaS la unica puerta es la
**External API** (XML-RPC), que ya habla `src/connectors/odoo.py`. Lo que faltaba
no era el conector: era el tramo que va de **cuatro campos escritos por el
cliente** (url, base, usuario, clave de API) hasta el MISMO motor de la salida #1
que hoy alimenta un CSV subido -- sin ingenieria por cuenta y sin que el motor se
entere de donde vinieron las filas.

Tres piezas, y ninguna calcula un plazo:

1. :func:`check_connection` -- valida y **clasifica la falla**. Un "no se pudo
   conectar" generico es inutil en un embudo sin humano al otro lado: el cliente
   no sabe si escribio mal el subdominio, la base, el usuario, o si su usuario de
   Odoo no tiene el grupo de Compras. Cada caso manda a una pantalla distinta de
   su Odoo, asi que cada caso tiene que salir nombrado.
2. :func:`read_receipts` -- NO reimplementa la lectura. Llama a
   ``OdooConnector.receipts()``, que ya arma las filas de recepcion desde
   ``stock.move`` + ``purchase.order`` con las mismas claves que emite el camino
   del CSV (``webapp/demo_lead_time_scan.to_deliveries``). Aqui solo se traduce
   el `OdooError` de "este Odoo no puede medirse" a un resultado estructurado, en
   vez de dejar que una excepcion cruce hasta el endpoint.
3. :func:`preflight` -- antes de correr, que encontro: cuantas recepciones,
   cuantos proveedores, entre que fechas, y **que falta**. Es una funcion PURA
   sobre lo ya leido: cero llamadas RPC extra, porque el presupuesto del proyecto
   son 25 minutos por cuenta al mes y una segunda pasada por el ERP se paga dos
   veces (latencia del cliente y tiempo del operador mirando la consola).

**La regla que hace honesta esta ruta, y la unica que se rompe sola:** el plazo
observado sale de restar ``stock.move.date`` menos ``purchase.order.date_order``
y de ningun otro lado. ``product.supplierinfo.delay`` es la PROMESA de la ficha;
usarlo como observacion compara la promesa consigo misma y devuelve brecha cero
**sobre el defecto exacto que el escaneo viene a mostrar**. Cuando no hay fechas
de recepcion, esto se NIEGA a correr y nombra lo que falta -- nunca se cae al
campo del ERP. El camino del CSV tiene un test dedicado a esa regla
(``test_sin_fechas_no_se_mide_aunque_venga_el_campo_del_erp``); este tiene el
suyo.

**Solo lectura, sin excepciones.** Este modulo no importa ``nosilo_core.writeback``
y no ejecuta ningun metodo que no sea de lectura. No es una precaucion: es el
argumento de venta entero. Un cliente aprueba una clave de API de solo lectura en
una reunion; una que escribe pasa por su comite.

**El secreto.** La ``api_key`` no se registra, no se persiste y no viaja en
ningun mensaje de error: :func:`_scrub` la borra de todo texto que venga del
servidor antes de que salga de aqui, y :class:`OdooCredentials` la excluye de su
propio ``repr``. Que Odoo hoy no la devuelva en un fault no es garantia -- un
traceback del servidor es texto que nadie de este lado controla.
"""

from __future__ import annotations

import xmlrpc.client
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from urllib.parse import urlsplit

from src.connectors.odoo import (
    OdooClient,
    OdooConnector,
    OdooError,
    OdooReceipts,
    OdooTransportError,
    server_proxies,
)

# -- los motivos de falla ------------------------------------------------------

#: El host no contesta, o lo que contesta no es la External API de un Odoo.
UNREACHABLE = "host_inalcanzable"
#: El host es un Odoo, pero esa base de datos no existe / no es seleccionable.
DATABASE = "base_inexistente"
#: La base existe y el par usuario+clave no autentica.
CREDENTIALS = "credencial_invalida"
#: Autentica, pero el usuario no puede leer algun modelo que la medicion necesita.
PERMISSIONS = "permisos_insuficientes"

#: Los cuatro que el embudo tiene que saber distinguir. Los dos de abajo existen
#: porque, sin ellos, cada uno se diagnosticaria como uno de estos cuatro y
#: mandaria al cliente a la pantalla equivocada.
FOUR_MODES = (UNREACHABLE, DATABASE, CREDENTIALS, PERMISSIONS)

#: Conecta y tiene permisos, pero el modelo no existe: la app de Compras no esta
#: instalada. NO es `PERMISSIONS`: manda a Aplicaciones -> instalar Compras, no a
#: Ajustes -> permisos de usuario. Confundirlos es media hora perdida del cliente.
MODULE = "modulo_ausente"
#: El servidor devolvio un fault que no se pudo clasificar. Se reporta con el
#: texto (depurado de la clave) en vez de forzarlo dentro de uno de los cuatro:
#: un diagnostico inventado es peor que uno que admite no saber.
SERVER = "error_del_servidor"

#: Los modelos que la salida #1 lee. Estan aqui, y no dentro de `receipts()`,
#: porque el punto del preflight es fallar ANTES de la lectura larga nombrando
#: cual de estos el usuario del cliente no puede abrir.
REQUIRED_MODELS = (
    "stock.move",          # la recepcion y su fecha: la mitad observada del plazo
    "purchase.order",      # date_order: la otra mitad
    "purchase.order.line", # el puente entre el movimiento y la orden
    "res.partner",         # el proveedor, plegado a su empresa comercial
    "stock.picking.type",  # que operaciones son de entrada
    "product.product",     # el SKU (default_code) de las filas
    "product.supplierinfo",# el plazo DECLARADO: la promesa contra la cual se compara
)

#: `ir.model` lo lee cualquier usuario interno de Odoo. Se consulta primero para
#: separar "el modelo no existe" de "no lo puedo leer" SIN mirar el texto del
#: error: Odoo traduce sus mensajes al idioma del usuario, asi que un
#: `"AccessError" in fault` funciona en la maquina del que lo escribio y falla en
#: el Odoo en espanol del cliente, que es exactamente donde tiene que funcionar.
_M_IR_MODEL = "ir.model"

#: Un fault de `authenticate` con estos pedazos es la base inexistente. Es el
#: UNICO lugar del modulo donde se mira el texto del servidor, y va documentado
#: como tal: Odoo Online corre con `list_db=False`, o sea que no hay forma
#: autenticada ni anonima de preguntar "existe esta base?" antes de intentarla.
_DB_MARKERS = ("does not exist", "no existe", "database", "base de datos", "dbname")

REDACTED = "<redactado>"


def _scrub(text: Any, secret: str) -> str:
    """``text`` sin el secreto. Se aplica a TODO lo que venga del servidor.

    Un ``faultString`` es un traceback armado del otro lado: nadie de este lado
    controla que lleva dentro. Que hoy no lleve la clave no es una garantia que
    se pueda escribir en un contrato de seguridad, y el costo de asumirlo es una
    clave de API de un cliente en un log.
    """
    salida = str(text)
    if secret:
        salida = salida.replace(secret, REDACTED)
    return salida


@dataclass(frozen=True)
class OdooCredentials:
    """Los cuatro campos que escribe el cliente. La clave nunca entra al ``repr``.

    ``field(repr=False)`` no es cosmetico: un ``repr`` de esta clase termina en el
    mensaje de una excepcion no atrapada, en un breakpoint, o en el cuerpo de un
    reporte de error automatico. Excluirla ahi es mas barato que auditar cada
    lugar donde alguien pueda imprimir el objeto.
    """

    url: str
    db: str
    login: str
    api_key: str = field(repr=False)

    def __post_init__(self) -> None:
        object.__setattr__(self, "url", normalize_url(self.url))
        object.__setattr__(self, "db", self.db.strip())
        object.__setattr__(self, "login", self.login.strip())

    @property
    def host(self) -> str:
        return urlsplit(self.url).hostname or ""

    @property
    def db_hint(self) -> str:
        """La base que Odoo Online suele usar: el subdominio.

        Se ofrece como sugerencia en el mensaje de falla, jamas se aplica sola.
        Adivinar la base y conectar a la equivocada es lo unico peor que fallar:
        el escaneo saldria correcto sobre los datos de otra empresa.
        """
        host = self.host
        if host.endswith(".odoo.com"):
            return host[: -len(".odoo.com")].split(".")[-1]
        return ""


def normalize_url(url: str) -> str:
    """Lo que el cliente pega -> el origen que xmlrpc necesita.

    Un cliente copia ``mycompany.odoo.com``, o la barra de direcciones entera
    (``https://mycompany.odoo.com/odoo/inventory``), o deja un espacio al pegar.
    Los tres son la misma instancia y los tres tienen que funcionar: exigir la
    forma canonica es una pregunta de soporte por cuenta, y el presupuesto son 25
    minutos al mes.
    """
    limpio = url.strip().strip("<>").rstrip("/")
    if not limpio:
        return ""
    if "://" not in limpio:
        limpio = f"https://{limpio}"
    partes = urlsplit(limpio)
    if partes.scheme not in ("http", "https") or not partes.hostname:
        return limpio
    # Se descarta el path: `/odoo/inventory` o `/web` son la UI, no la API, y
    # concatenarlos daria `.../web/xmlrpc/2/common`, que es un 404.
    return f"{partes.scheme}://{partes.netloc}"


@dataclass(frozen=True)
class ConnectionCheck:
    """Conecta o no, y cuando no: cual de los casos y que hacer al respecto."""

    ok: bool
    reason: str = ""
    #: En el idioma del cliente y con la pantalla de SU Odoo donde se arregla.
    #: Nunca contiene la clave de API (ver :func:`_scrub`).
    message: str = ""
    #: Los modelos que fallaron, cuando el motivo es de permisos o de modulo.
    #: Se reportan TODOS de una vez: un cliente que va donde su administrador
    #: cuatro veces seguidas abandona.
    models: tuple[str, ...] = ()
    server_version: str = ""

    @property
    def actionable(self) -> bool:
        """Falso solo si algo devolvio una falla sin mensaje: no deberia pasar."""
        return bool(self.message)


@dataclass(frozen=True)
class Preflight:
    """Que se encontro del otro lado, antes de correr el motor.

    ``missing`` son los BLOQUEANTES de esta capa: sin eso, el plazo observado no
    se puede medir en absoluto. ``warnings`` es lo que el scan juzga con su
    propia compuerta de QA -- y se deja ahi a proposito: duplicar aqui el minimo
    de recepciones por proveedor o la exigencia de plazo declarado crearia una
    SEGUNDA definicion de "comparable", y la segunda copia de un guardia es como
    se reintroduce el defecto que el primero atajaba.
    """

    n_receipts: int
    n_suppliers: int
    #: ISO (``YYYY-MM-DD``) o "" si no hubo ninguna recepcion medible.
    first_receipt: str
    last_receipt: str
    #: Proveedores con promesa Y comportamiento: los unicos que dan brecha.
    n_with_declared: int
    dropped: dict
    missing: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.missing

    @property
    def span_days(self) -> int | None:
        if not (self.first_receipt and self.last_receipt):
            return None
        d0 = datetime.strptime(self.first_receipt, "%Y-%m-%d")
        d1 = datetime.strptime(self.last_receipt, "%Y-%m-%d")
        return (d1 - d0).days


@dataclass(frozen=True)
class OdooScanInput:
    """Lo que la ruta de conexion entrega al motor de la salida #1.

    ``grouped`` / ``dropped`` / ``source_notes`` son exactamente los tres
    argumentos de ``webapp.demo_lead_time_scan.scan_from_deliveries``, y con la
    misma forma que produce el camino del CSV. Ese es el punto entero: el motor
    no se entera de si las filas vinieron de una planilla o de un Odoo vivo.
    """

    check: ConnectionCheck
    preflight: Preflight | None = None
    grouped: dict = field(default_factory=dict)
    dropped: dict = field(default_factory=dict)
    source_notes: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return self.check.ok and self.preflight is not None and self.preflight.ok


# -- 1. validacion de conexion -------------------------------------------------


def _fail(reason: str, message: str, *, models: tuple[str, ...] = ()) -> ConnectionCheck:
    return ConnectionCheck(ok=False, reason=reason, message=message, models=models)


def _probe_models(models_proxy: Any, creds: OdooCredentials, uid: int) -> ConnectionCheck | None:
    """None si el usuario puede leer todo lo que la medicion necesita.

    Dos pasadas, y el orden importa. Primero ``ir.model`` dice que modelos
    EXISTEN en esa base: lo que falta ahi es una app sin instalar, no un permiso.
    Recien despues se intenta un ``search`` acotado sobre los que si existen, y
    ahi un fallo si es de permisos por descarte -- sin leer el texto del error,
    que Odoo traduce al idioma del usuario.
    """
    def _call(model: str, method: str, args: list, kwargs: dict | None = None) -> Any:
        return models_proxy.execute_kw(
            creds.db, uid, creds.api_key, model, method, args, kwargs or {}
        )

    def _caido(exc: Exception) -> ConnectionCheck:
        # El sondeo va contra el proxy pelado, sin el reintento acotado de
        # `OdooClient`: un corte de red aqui llegaria hasta el endpoint como un
        # 500. Autenticar y despues perder la conexion es un problema de red, no
        # de permisos, y decirle al cliente "permisos insuficientes" lo manda a
        # pelear con su administrador por algo que no esta ahi.
        return _fail(
            UNREACHABLE,
            f"la conexion con {creds.host} se corto mientras se revisaban los permisos: "
            f"{_scrub(exc, creds.api_key)}. Reintenta.",
        )

    _TRANSPORTE = (OSError, TimeoutError, xmlrpc.client.ProtocolError, xmlrpc.client.ResponseError)

    try:
        instalados = {
            r["model"]
            for r in _call(
                _M_IR_MODEL, "search_read",
                [[["model", "in", list(REQUIRED_MODELS)]]], {"fields": ["model"]},
            )
        }
    except _TRANSPORTE as exc:
        return _caido(exc)
    except xmlrpc.client.Fault:
        # Un usuario interno de Odoo siempre lee `ir.model`. Que falle significa
        # que esta cuenta no es interna -- tipicamente un usuario de Portal, que
        # ademas no vera ninguna compra. Es un problema de permisos, y nombrarlo
        # asi ahorra la vuelta entera de probar modelo por modelo.
        return _fail(
            PERMISSIONS,
            f"el usuario {creds.login} entro, pero no es un usuario interno de Odoo (no puede leer "
            "ni la lista de modelos). Un usuario de Portal o Publico no ve las compras. En Odoo: "
            "Ajustes -> Usuarios y companias -> Usuarios -> el usuario -> Tipo de usuario = Interno.",
        )

    faltantes = tuple(m for m in REQUIRED_MODELS if m not in instalados)
    if faltantes:
        return _fail(
            MODULE,
            "este Odoo no tiene instalada la aplicacion que guarda las compras: faltan los modelos "
            f"{', '.join(faltantes)}. Sin ordenes de compra no hay fecha contra la cual medir la "
            "recepcion, y el plazo de la ficha del proveedor no reemplaza a la medicion: es "
            "justamente la promesa que veniamos a contrastar. En Odoo: Aplicaciones -> instalar "
            "Compras (y Inventario).",
            models=faltantes,
        )

    denegados: list[str] = []
    for modelo in REQUIRED_MODELS:
        try:
            _call(modelo, "search", [[]], {"limit": 1})
        except _TRANSPORTE as exc:
            return _caido(exc)
        except xmlrpc.client.Fault:
            # El modelo existe (lo confirmo `ir.model`) y no se puede leer: es
            # permiso, por descarte y sin mirar el texto traducido del fault.
            denegados.append(modelo)
    if denegados:
        return _fail(
            PERMISSIONS,
            f"el usuario {creds.login} conecta, pero no puede leer {', '.join(denegados)}. La "
            "medicion es de solo lectura y necesita esos modelos. En Odoo: Ajustes -> Usuarios y "
            "companias -> Usuarios -> el usuario -> darle acceso de lectura a Inventario y Compras.",
            models=tuple(denegados),
        )
    return None


def _connect(
    creds: OdooCredentials, *, common: Any = None, models: Any = None, timeout: float = 30.0
) -> tuple[ConnectionCheck, Any | None]:
    """El check + los proxies vivos. Publico via :func:`check_connection`."""
    if not creds.url:
        return _fail(UNREACHABLE, "falta la direccion del Odoo (por ejemplo https://miempresa.odoo.com)."), None
    if not creds.db:
        return _fail(DATABASE, "falta el nombre de la base de datos."), None
    if not creds.login or not creds.api_key:
        return _fail(CREDENTIALS, "faltan el usuario o la clave de API."), None
    if urlsplit(creds.url).scheme not in ("http", "https"):
        return _fail(UNREACHABLE, f"la direccion {creds.url!r} no es una URL http(s) valida."), None

    if common is None or models is None:
        hechos = server_proxies(creds.url, timeout=timeout)
        common = common if common is not None else hechos[0]
        models = models if models is not None else hechos[1]

    # 1. Alcance. `version()` no autentica: separa "no llego" de "llegue y me
    #    rechazaron", que son la primera bifurcacion del arbol de diagnostico.
    try:
        version = common.version()
    except (OSError, TimeoutError) as exc:
        return _fail(
            UNREACHABLE,
            f"no se llego a {creds.host or creds.url}: {_scrub(exc, creds.api_key)}. Revisa que la "
            "direccion sea la de tu Odoo (la que usas para entrar) y que no tenga una VPN o "
            "lista blanca de IP delante.",
        ), None
    except (xmlrpc.client.ProtocolError, xmlrpc.client.ResponseError, xmlrpc.client.Fault) as exc:
        # Contesto algo, pero no la External API: tipicamente un proxy, una pagina
        # de login de otro producto, o un Odoo con la API deshabilitada.
        return _fail(
            UNREACHABLE,
            f"{creds.host or creds.url} contesta, pero no con la API externa de Odoo "
            f"({_scrub(exc, creds.api_key)}). Verifica que sea la direccion de tu Odoo y no la de "
            "otro sistema.",
        ), None
    except Exception as exc:  # XML mal formado, respuesta HTML, etc.
        return _fail(
            UNREACHABLE,
            f"{creds.host or creds.url} no respondio como un Odoo: {_scrub(exc, creds.api_key)}.",
        ), None

    server_version = str((version or {}).get("server_version", "")) if isinstance(version, dict) else ""

    # 2. Base + credencial. La separacion es determinista, no por texto: con una
    #    base que existe y un par usuario/clave malo, `authenticate` devuelve
    #    False; con una base que no existe NO PUEDE devolver False, porque el
    #    servidor no logra ni abrir el cursor -- levanta. Solo dentro de esa rama
    #    se mira el mensaje, y esta declarado por que no hay alternativa
    #    (`list_db` viene apagado en Odoo Online: no existe forma de preguntar si
    #    una base existe antes de intentar autenticarse contra ella).
    try:
        uid = common.authenticate(creds.db, creds.login, creds.api_key, {})
    except xmlrpc.client.Fault as exc:
        texto = _scrub(exc.faultString, creds.api_key)
        bajo = texto.lower()
        # El acceso denegado se mira ANTES que los marcadores de base, y no al
        # reves: `_DB_MARKERS` es deliberadamente ancho ("database", "dbname")
        # para atrapar el mensaje de postgres en cualquier version, asi que un
        # traceback de AccessDenied que de paso nombre la base se leeria como
        # "la base no existe" -- y el cliente se pondria a cambiar el nombre de
        # su base cuando lo que tiene mal es la clave. El error mas especifico
        # gana.
        if "denied" in bajo or "accessdenied" in bajo.replace(" ", ""):
            return _fail(CREDENTIALS, f"{creds.host} rechazo el usuario {creds.login} o su clave de API."), None
        if any(m in bajo for m in _DB_MARKERS):
            pista = (
                f" En Odoo Online la base suele llamarse igual que el subdominio: {creds.db_hint!r}."
                if creds.db_hint and creds.db_hint != creds.db
                else ""
            )
            return _fail(DATABASE, f"la base de datos {creds.db!r} no existe en {creds.host}.{pista}"), None
        return _fail(SERVER, f"{creds.host} devolvio un error que no se pudo clasificar: {texto}"), None
    except (OSError, TimeoutError) as exc:
        return _fail(UNREACHABLE, f"se corto la conexion con {creds.host}: {_scrub(exc, creds.api_key)}."), None

    if not uid:
        pista = (
            f" Si la base no fuera {creds.db!r}, en Odoo Online suele ser {creds.db_hint!r}."
            if creds.db_hint and creds.db_hint != creds.db
            else ""
        )
        return _fail(
            CREDENTIALS,
            f"la base {creds.db!r} existe, pero el usuario {creds.login} con esa clave de API no "
            "autentica. La clave de API no es la contrasena: se genera en Odoo desde el avatar -> "
            f"Mi perfil -> Seguridad de la cuenta -> Claves de API.{pista}",
        ), None

    # 3. Permisos (y, antes, modulos instalados).
    problema = _probe_models(models, creds, int(uid))
    if problema is not None:
        return problema, None

    return ConnectionCheck(ok=True, server_version=server_version), (common, models)


def check_connection(
    creds: OdooCredentials, *, common: Any = None, models: Any = None, timeout: float = 30.0
) -> ConnectionCheck:
    """Valida los cuatro campos y, si falla, dice CUAL de los casos es.

    ``common`` / ``models`` inyectan el transporte para poder testear los cuatro
    modos sin red, igual que ``OdooClient`` ya permite.
    """
    check, _ = _connect(creds, common=common, models=models, timeout=timeout)
    return check


# -- 2. lectura -----------------------------------------------------------------


def read_receipts(rpc: Any, *, since: str | None = None, max_moves: int = 20_000) -> OdooReceipts:
    """Las recepciones, con la forma que come el motor. NO reimplementa nada.

    ``OdooConnector.receipts()`` ya mide ``stock.move.date`` menos
    ``purchase.order.date_order`` y emite las mismas claves que
    ``demo_lead_time_scan.to_deliveries``. Esta funcion existe solo para que el
    llamador no tenga que saber que hay un ``OdooConnector`` en el medio.
    """
    return OdooConnector(rpc).receipts(since=since, max_moves=max_moves)


# -- 3. preflight ---------------------------------------------------------------


def _sin_nada_que_medir(pull: OdooReceipts) -> tuple[str, ...]:
    """Los bloqueantes cuando no quedo ni una recepcion medible.

    Cada uno nombra la causa dominante del descarte, porque el remedio es
    distinto: sin fechas se revisa el estado de las recepciones, sin proveedor se
    revisa el maestro de contactos, y sin ordenes confirmadas no hay nada que
    hacer todavia. Un unico "no se encontraron datos" los manda a los tres.
    """
    d = pull.dropped
    mensajes: list[str] = []
    if d.get("sin_fecha"):
        mensajes.append(
            f"{d['sin_fecha']} recepcion(es) no tienen las DOS fechas (la de la orden de compra y "
            "la de validacion de la recepcion). Sin las dos el plazo no se resta, y el campo "
            "'Plazo de entrega' de la ficha del proveedor no sirve de reemplazo: es la promesa que "
            "veniamos a contrastar, no una medicion."
        )
    if d.get("po_no_confirmada"):
        mensajes.append(
            f"{d['po_no_confirmada']} movimiento(s) cuelgan de ordenes de compra sin confirmar. "
            "Una orden en borrador no tiene fecha de pedido firme contra la cual medir."
        )
    if d.get("sin_proveedor"):
        mensajes.append(
            f"{d['sin_proveedor']} recepcion(es) no tienen proveedor en su orden de compra. El "
            "plazo se mide por proveedor: sin el, la fila no entra en ningun promedio."
        )
    if d.get("plazo_negativo"):
        mensajes.append(
            f"{d['plazo_negativo']} recepcion(es) tienen fecha anterior a su propia orden. Es un "
            "dato roto, no un plazo de cero dias: promediarlo bajaria el observado y disimularia "
            "justo la brecha."
        )
    if not mensajes:
        mensajes.append(
            "no hay ninguna recepcion de compra validada en este Odoo. La medicion necesita "
            "recepciones hechas (Inventario -> Recepciones, en estado Hecho) con su orden de "
            "compra confirmada detras. Pedile a compras que confirme el historico, o conecta la "
            "base donde este."
        )
    return tuple(mensajes)


def preflight(pull: OdooReceipts) -> Preflight:
    """Que se encontro y que falta. PURA: cero RPC, cero recalculo del plazo.

    Se corre sobre lo ya leido y no sobre una consulta propia: una segunda pasada
    al ERP para "contar antes de medir" duplica la latencia del onboarding y
    puede contar sobre un estado distinto del que despues se mide.
    """
    filas = [f for rows in pull.grouped.values() for f in rows]
    fechas = sorted(f["receipt_date"] for f in filas if isinstance(f.get("receipt_date"), datetime))
    con_declarado = sum(
        1 for rows in pull.grouped.values() if any("declared_lead_time_days" in f for f in rows)
    )

    missing: tuple[str, ...] = ()
    warnings: list[str] = list(pull.qa_issues)
    if not filas:
        missing = _sin_nada_que_medir(pull)
    elif not con_declarado:
        # NO bloquea aqui: la compuerta de QA del scan es la unica autoridad
        # sobre "hay con que comparar". Se nombra temprano para que el cliente
        # vaya a buscar el dato mientras tanto, no para juzgarlo dos veces.
        warnings.append(
            "ningun proveedor tiene plazo declarado en su ficha (Compras -> Proveedores -> "
            "'Plazo de entrega'), asi que hay plazo observado pero nada contra que compararlo. "
            "Un plazo declarado de 1 dia se ignora a proposito: es el valor por defecto de Odoo, "
            "no una promesa."
        )

    return Preflight(
        n_receipts=len(filas),
        n_suppliers=len(pull.grouped),
        first_receipt=fechas[0].date().isoformat() if fechas else "",
        last_receipt=fechas[-1].date().isoformat() if fechas else "",
        n_with_declared=con_declarado,
        dropped=dict(pull.dropped),
        missing=missing,
        warnings=tuple(warnings),
    )


# -- la ruta completa -----------------------------------------------------------


def scan_input(
    creds: OdooCredentials,
    *,
    since: str | None = None,
    max_moves: int = 20_000,
    common: Any = None,
    models: Any = None,
    rpc: Any = None,
    timeout: float = 30.0,
) -> OdooScanInput:
    """Cuatro campos -> las filas que come el motor de la salida #1, o el motivo.

    Nunca levanta por una falla del otro lado: valida, lee, hace el preflight y
    devuelve un resultado que el endpoint mapea a un 400 con el mensaje ya
    escrito. Una excepcion cruzando hasta la ruta HTTP es como un embudo
    autoservicio termina mostrando "Internal Server Error" a un comprador.

    ``rpc`` inyecta un ``execute_kw`` ya listo (``InMemoryOdoo`` en los tests, o
    un cliente reusado): salta la validacion de transporte, no el preflight.
    """
    if rpc is None:
        check, proxies = _connect(creds, common=common, models=models, timeout=timeout)
        if not check.ok or proxies is None:
            return OdooScanInput(check=check)
        # Segundo `authenticate`, a proposito: `OdooClient` trae el reintento
        # acotado de los metodos de lectura y el timeout de socket, y reusarlo
        # entero vale mas que ahorrar un viaje en una ruta que corre una vez por
        # cuenta. Quien "optimice" esto pasandole el uid a mano se lleva puesto
        # el reintento.
        rpc = OdooClient(
            creds.url, creds.db, creds.login, creds.api_key, common=proxies[0], models=proxies[1]
        )
    else:
        check = ConnectionCheck(ok=True)

    try:
        pull = read_receipts(rpc, since=since, max_moves=max_moves)
    except OdooTransportError as exc:
        # La conexion valido y se cayo a mitad de la lectura. NO es dato
        # faltante: reportarlo como tal manda al cliente a revisar recepciones en
        # su ERP por un problema que esta en la red.
        return OdooScanInput(
            check=_fail(
                UNREACHABLE,
                f"la conexion con {creds.host} se corto durante la lectura: "
                f"{_scrub(exc, creds.api_key)}. Reintenta, o acota el rango con `since`.",
            )
        )
    except OdooError as exc:
        # `receipts()` se niega a medir cuando no hay con que (tipicamente
        # `stock.move` sin `purchase_line_id`: Compras instalado pero sin el
        # puente de inventario). Se convierte en un bloqueante nombrado, no en un
        # 500, y sobre todo NO en una caida al `delay` de la ficha.
        return OdooScanInput(
            check=check,
            preflight=Preflight(0, 0, "", "", 0, {}, missing=(_scrub(exc, creds.api_key),)),
        )

    pre = preflight(pull)
    return OdooScanInput(
        check=check,
        preflight=pre,
        grouped=pull.grouped if pre.ok else {},
        dropped=pull.dropped,
        source_notes=pull.qa_issues,
    )


# -- doble offline del TRANSPORTE ----------------------------------------------


class InMemoryOdooServer:
    """Los dos endpoints XML-RPC de Odoo, sobre un :class:`InMemoryOdoo`.

    ``InMemoryOdoo`` dobla el ORM (``execute_kw(model, method, args, kwargs)``).
    Lo que este modulo valida esta una capa mas abajo: ``common.version()``,
    ``common.authenticate(db, login, clave, {})`` y la firma de siete argumentos
    de ``/xmlrpc/2/object``. Sin doblar esa capa, los cuatro modos de falla solo
    se podrian ejercitar contra un Odoo real -- o sea, nunca en CI, que es
    exactamente donde tienen que estar cubiertos.

    Se parametriza para producir cada falla: ``db`` distinta, ``login``/``key``
    distintos, ``denied`` para negar la lectura de un modelo, y quitar un modelo
    del ``InMemoryOdoo`` lo vuelve "app no instalada". ``calls`` deja registrado
    todo lo que se invoco, que es como se comprueba que esta ruta no escribe.
    """

    def __init__(
        self,
        orm: Any,
        *,
        db: str = "prod",
        login: str = "ops@cliente.cl",
        api_key: str = "clave-de-api",
        uid: int = 2,
        denied: tuple[str, ...] = (),
        version: dict | None = None,
        installed: tuple[str, ...] | None = None,
        unreachable: Exception | None = None,
        auth_fault: str | None = None,
        transport_on: Any = None,
    ) -> None:
        self._orm = orm
        self._db, self._login, self._key, self._uid = db, login, api_key, uid
        self._denied = set(denied)
        self._version = version if version is not None else {"server_version": "17.0+e"}
        self._installed = set(installed) if installed is not None else set(getattr(orm, "_data", {}))
        self._unreachable = unreachable
        self._auth_fault = auth_fault
        #: Predicado ``(metodo, modelo) -> bool``. Cuando da True se levanta un
        #: error de TRANSPORTE en vez de un `Fault`. Los dos llegan por el mismo
        #: cable y significan cosas opuestas: un fault es el servidor
        #: contestando (permiso, modelo inexistente), un OSError es que no hubo
        #: respuesta. Sin poder producir el segundo, el unico modo de comprobar
        #: que no se disfraza de permisos seria un corte de red real.
        self._transport_on = transport_on
        #: (metodo, modelo) de todo lo que se pidio. La prueba de solo-lectura.
        self.calls: list[tuple[str, str]] = []

    # -- /xmlrpc/2/common
    def version(self) -> dict:
        if self._unreachable is not None:
            raise self._unreachable
        return self._version

    def authenticate(self, db: str, login: str, key: str, user_agent_env: dict) -> Any:
        # Odoo exige el cuarto argumento y falla sin el. El doble lo valida en vez
        # de aceptarlo y no mirarlo: si alguien "simplifica" la llamada de
        # `OdooClient`, tiene que romperse aqui y no recien contra el Odoo del
        # cliente, que es donde no hay nadie para leer el error.
        if not isinstance(user_agent_env, dict):
            raise xmlrpc.client.Fault(1, "authenticate() expects a user_agent_env dict")
        if self._auth_fault is not None:
            raise xmlrpc.client.Fault(1, self._auth_fault)
        if db != self._db:
            # Odoo no puede devolver False con una base que no existe: no logra
            # abrir el cursor. Levanta, y por eso el clasificador puede separar
            # este caso del de credencial mala sin adivinar.
            raise xmlrpc.client.Fault(1, f'FATAL:  database "{db}" does not exist')
        if login != self._login or key != self._key:
            return False
        return self._uid

    # -- /xmlrpc/2/object
    def execute_kw(self, db: str, uid: int, key: str, model: str, method: str, args: list, kwargs: dict) -> Any:
        if db != self._db or uid != self._uid or key != self._key:
            raise xmlrpc.client.Fault(1, "AccessDenied")
        self.calls.append((method, model))
        if self._transport_on is not None and self._transport_on(method, model):
            raise OSError("connection reset by peer")
        if model in self._denied:
            raise xmlrpc.client.Fault(1, "odoo.exceptions.AccessError: No tiene permitido acceder")
        if model == _M_IR_MODEL:
            pedidos = args[0][0][2] if args and args[0] else []
            return [{"id": i, "model": m} for i, m in enumerate(sorted(self._installed & set(pedidos)), 1)]
        if model not in self._installed:
            raise xmlrpc.client.Fault(1, f"Object {model} doesn't exist")
        return self._orm.execute_kw(model, method, args, kwargs)
