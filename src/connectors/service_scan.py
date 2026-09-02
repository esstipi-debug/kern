"""La lectura de SERVICIO de un conector: lo medido, y lo que el backend no pudo dar.

Este modulo es el llamador que le faltaba a ``src.connectors.service_frame()``. Ese
constructor existe desde que aterrizo el trabajo de servicio realizado y su docstring se
describe a si mismo como "the missing constructor" del argumento ``order_lines`` que
``jobs.inventory_optimization.run`` acepta -- y no lo llamaba nadie. El motor sabia medir
OTIF saliente, la lectura sabia llenarlo, y las dos mitades nunca se tocaban: cada corrida
pasaba ``None`` y cada SKU volvia ``not_measured``.

``scan_service`` cierra eso, y arrastra una segunda cosa que el motor solo no puede saber:
el ``SERVICE_COVERAGE`` del adaptador, convertido en notas legibles. Un cliente cuyo ERP no
registra fecha de llegada tiene que LEER eso en su informe. Deducirlo de un OTIF que salio
mas alto de lo que su gente sabe que es, es como se pierde la confianza en un numero que
esta bien calculado.

Tres decisiones:

1. **Se prefiere la lectura mas completa que el adaptador ofrezca.** Si expone
   ``orders_with_shipments()`` -- hoy solo Unleashed, donde la cantidad servida vive en un
   segundo endpoint -- se usa esa. Quedarse con ``orders()`` porque es la del Protocol
   tiraria el unico numero que la competencia verificada del anillo 2 no puede dar.

2. **La demanda sale de las MISMAS ordenes que el servicio.** Dos lecturas separadas darian
   una demanda calculada sobre un universo de ordenes y un fill rate sobre otro, y esa
   diferencia no se ve en ninguna de las dos salidas por separado.

3. **Un adaptador sin declaracion produce una nota, no silencio.** En un informe, callarse
   y "lo medimos todo" se leen igual, y uno de los dos es falso.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

from src import order_fulfillment as of
from src.connectors import (
    Order,
    ServiceCoverage,
    observation_window_end,
    realized_service,
    service_frame,
)
from src.connectors.arrivals import ArrivalSource, coverage_of_arrivals, with_arrivals

#: Como se lee una ausencia declarada. El ``means`` del adaptador entra tal cual: ese texto
#: es el hallazgo, y un "no disponible" generico no se puede verificar contra nada.
_NOTA_CAMPO = "{campo}: NO MEDIBLE en {backend}. {means}"
#: Y como se lee un campo que si se mide pero significa algo distinto de lo que su nombre
#: sugiere -- el caso de Unleashed, donde la fecha de entrega es en realidad un despacho.
_NOTA_MATIZ = "{campo}: {means} (fuente: {source})"
_NOTA_SIN_DECLARACION = (
    "{backend} no declara cobertura de servicio (SERVICE_COVERAGE). No se sabe que campos "
    "puede medir este backend, asi que un metrica ausente aqui no distingue 'el ERP no lo "
    "tiene' de 'el adaptador no lo leyo'."
)

_NOTA_TRANSPORTISTA_SIN_DECLARACION = (
    "El transportista ({backend}) no declara cobertura de servicio, asi que la fecha de "
    "entrega de este informe se empalmo pero NO se pudo componer la declaracion: dice "
    "todavia lo que decia el sistema de registro. Verificar que significa antes de "
    "publicar el OTIF."
)

#: Que se le pide a un transportista construido a pedido. Los tres adaptadores reales se
#: construyen SABIENDO que ordenes van a consultar -- Starshipit toma la lista de
#: referencias del ERP, Shippit y Sendle un mapa de esas referencias a la llave propia de
#: cada uno -- y quien llama a ``scan_service`` todavia no leyo la fuente. Releerla para
#: conseguir los identificadores es una pasada entera de mas: contra Cin7, a 60 llamadas
#: por minuto. La fabrica los recibe de la lectura que ya se hizo.
CarrierFactory = Callable[[tuple[str, ...]], ArrivalSource]

#: Los campos cuyo ``means`` se transcribe aunque SI se midan. La fecha de entrega es el
#: caso: Odoo la deriva de un movimiento de stock y Unleashed de un despacho, o sea que en
#: los dos el OTIF mide salir a tiempo y no llegar a tiempo. Un cliente que lee "OTIF" y
#: piensa "llego a tiempo" esta leyendo mal un numero bien calculado.
_CAMPOS_CON_MATIZ = ("delivered_date",)


@dataclass(frozen=True)
class ServiceScan:
    """Lo que un conector pudo medir del servicio al cliente, y lo que no.

    ``order_lines`` y ``demand`` son los dos frames que los jobs ya saben consumir; no hay
    una tercera forma de los mismos datos a proposito.
    """

    #: Como se llama el backend, segun su propia declaracion. ``"desconocido"`` cuando no
    #: declara -- que es en si mismo un hallazgo, y por eso no es una cadena vacia.
    source_name: str
    #: Servicio medido por SKU, ordenado por SKU.
    records: tuple[of.RealizedService, ...]
    #: El roll-up de cartera, ponderado por evidencia y nunca por cantidad de SKU.
    portfolio: of.RealizedService
    #: El frame de ``order_lines`` que come ``jobs.inventory_optimization.run``.
    order_lines: object
    #: ``(date, product_id, quantity, unit_cost)``, lo que come ``DataFrameDemandSource``.
    demand: object
    #: Cuantas ORDENES sostienen todo lo de arriba. Se publica porque de las lineas no se
    #: puede deducir -- dos pedidos del mismo dia son una sola fecha -- y un llamador que
    #: la necesite tendria que releer la fuente. Contra Cin7 eso es una segunda pasada
    #: entera a 60 llamadas por minuto.
    n_orders: int = 0
    #: La declaracion del adaptador, o ``None`` si no publica una.
    coverage: ServiceCoverage | None = None
    #: Lo que hay que decirle al operador sobre lo que NO se pudo medir, y sobre lo que se
    #: midio pero significa otra cosa. Va al entregable, no a un log.
    notes: tuple[str, ...] = field(default_factory=tuple)
    #: Fraccion de ordenes para las que el transportista reporto una fecha de llegada.
    #: ``None`` es "no se empalmo ningun transportista" y NO es lo mismo que ``0.0``, que
    #: es "se empalmo y no conocia ni una". Un OTIF de llegada sobre cobertura parcial no
    #: es el de la cartera, y esta fraccion es la unica forma de dimensionarlo.
    arrival_coverage: float | None = None


def _coverage_of(source: object) -> ServiceCoverage | None:
    """La declaracion del adaptador: primero la del objeto, si no la de su modulo.

    Se mira el objeto antes que el modulo para que un envoltorio (un conector que compone
    a otro, un doble de test) pueda declarar la suya sin tener modulo propio.
    """
    propia = getattr(source, "SERVICE_COVERAGE", None)
    if isinstance(propia, ServiceCoverage):
        return propia
    modulo = getattr(type(source), "__module__", "")
    if not modulo:
        return None
    try:
        declarada = getattr(importlib.import_module(modulo), "SERVICE_COVERAGE", None)
    except ImportError:  # pragma: no cover - un modulo que no reimporta
        return None
    return declarada if isinstance(declarada, ServiceCoverage) else None


def _notes(coverage: ServiceCoverage | None, source: object) -> tuple[str, ...]:
    """La declaracion -> frases para el informe. Una por ausencia, una por matiz."""
    if coverage is None:
        return (_NOTA_SIN_DECLARACION.format(backend=type(source).__name__),)
    notas: list[str] = []
    for campo, cobertura in coverage.fields().items():
        if not cobertura.reports:
            notas.append(
                _NOTA_CAMPO.format(campo=campo, backend=coverage.backend, means=cobertura.means)
            )
        elif campo in _CAMPOS_CON_MATIZ:
            notas.append(
                _NOTA_MATIZ.format(campo=campo, means=cobertura.means, source=cobertura.source)
            )
    return tuple(notas)


def _read_orders(source: object, *, since: str | None) -> list[Order]:
    """La lectura mas completa que el adaptador ofrezca.

    ``orders_with_shipments`` es opcional y hoy solo Unleashed la tiene, porque es el unico
    backend donde la cantidad servida vive en un segundo endpoint. Su ausencia no es un
    error: es la mayoria de los backends, que ya la traen en la propia linea de pedido.
    """
    completa = getattr(source, "orders_with_shipments", None)
    if callable(completa):
        return list(completa(since=since))
    return list(source.orders(since=since))


def _demand_frame(orders: list[Order], source: object):
    """``(date, product_id, quantity, unit_cost)`` desde las mismas ordenes.

    El costo sale del catalogo del conector, no del precio de la linea: el precio es lo que
    el cliente cobra y el costo es lo que le cuesta, y confundirlos infla cada valorizacion
    de inventario por el margen entero. Un SKU sin costo publicado viaja en 0.0, que es como
    ya viajaba en ``simulator.demand_frame``.
    """
    import pandas as pd

    try:
        costos = {p.sku: p.cost for p in source.list_products()}
    except Exception:  # pragma: no cover - un catalogo que no se puede leer no frena el servicio
        costos = {}
    filas = [
        {
            "date": o.created_at,
            "product_id": ln.sku,
            "quantity": ln.quantity,
            "unit_cost": costos.get(ln.sku, 0.0),
        }
        for o in orders
        for ln in o.lines
    ]
    return pd.DataFrame(filas, columns=["date", "product_id", "quantity", "unit_cost"])


def _empalmar(
    orders: list[Order],
    carrier: ArrivalSource | CarrierFactory,
    *,
    since: str | None,
    coverage: ServiceCoverage | None,
) -> tuple[list[Order], ServiceCoverage | None, float | None, tuple[str, ...]]:
    """El empalme con el transportista: ordenes con la LLEGADA puesta, y lo que eso cuesta.

    Devuelve las ordenes empalmadas, la declaracion compuesta, la fraccion de cobertura y
    las notas nuevas. Se separo de ``scan_service`` porque lo que decide no es el flujo
    principal sino tres reglas que se justifican solas.

    1. **La fabrica se llama con los identificadores ya leidos.** Ver ``CarrierFactory``. Un
       transportista ya construido tambien se acepta -- es lo que hace un test o un
       llamador que ya sabia que ordenes consultar.

    2. **La declaracion se COMPONE.** Publicar la del sistema de registro a secas despues de
       empalmar afirmaria una llegada que ese sistema no puede dar, que es el error exacto
       que ``coverage_of_arrivals`` existe para impedir: el ``means`` del ERP dice
       "despacho" y el numero ya seria de llegada.

    3. **Las notas del transportista se leen DESPUES de consultarlo.** En los tres
       adaptadores ``notes`` nace vacia y solo se llena durante la consulta -- el aviso de
       tope de lecturas, el de ordenes sin llave. Leerlas antes devuelve una tupla vacia sin
       error, y el sintoma es que el aviso no aparece nunca.
    """
    transportista = (
        carrier
        if isinstance(carrier, ArrivalSource)
        else carrier(tuple(o.order_id for o in orders))
    )
    empalme = with_arrivals(orders, transportista, since=since)
    declarada = _coverage_of(transportista)
    extras: list[str] = []
    if coverage is not None and declarada is not None:
        coverage = coverage_of_arrivals(coverage, declarada)
    elif declarada is None:
        extras.append(
            _NOTA_TRANSPORTISTA_SIN_DECLARACION.format(backend=type(transportista).__name__)
        )
    extras.append(empalme.note)
    extras.extend(getattr(transportista, "notes", ()))
    return list(empalme.orders), coverage, empalme.coverage, tuple(extras)


def scan_service(
    source: object,
    *,
    since: str | None = None,
    stockout_days: Mapping[str, int] | None = None,
    carrier: ArrivalSource | CarrierFactory | None = None,
) -> ServiceScan:
    """El servicio que un conector puede medir, listo para entrar a un job.

    ``since`` viaja al adaptador y se filtra en el servidor donde el backend lo soporte.
    ``stockout_days`` es el historial de stock en cero del cliente, cuando lo tiene: sin el,
    los dias sin servir se cuentan como cota inferior sobre las fechas que fueron cortas, y
    el ``method`` de cada registro lo declara.

    ``carrier`` es el transportista, y es lo que convierte un OTIF de "salio a tiempo del
    deposito" en uno de "llego a tiempo al cliente" -- la unica diferencia que ningun ERP
    del canal AU/NZ registra, y que sobre 98.666 pedidos reales de Olist son 6,2 puntos.
    Puede ser un ``ArrivalSource`` ya construido o una ``CarrierFactory``.

    Una cuenta sin ordenes devuelve un scan vacio con un ``portfolio`` ``not_measured``, no
    una excepcion: una cuenta recien conectada no tiene historia todavia, y eso es un
    informe que lo dice.

    LA VENTANA SE FIJA ANTES DEL EMPALME, y no es un detalle. El empalme deja sin fecha a
    toda orden que el transportista no reporto, y la ventana de observacion se deriva de
    las fechas que hay: sin fijarla, se encoge hasta la ultima fecha de creacion, mas
    lineas cortas caen en la guarda de "en curso" y salen del denominador... y son
    justamente las que bajaban el fill rate. Medido sobre una cartera de dos ordenes: fill
    0,85 -> 1,0, ventas perdidas 3,0 -> 0,0, dias sin stock 1 -> 0. Ni un ``None``, ni un
    error, y un numero mejor. El fill rate no depende de fechas -- son cantidades -- asi que
    cualquier movimiento suyo al empalmar un transportista es un artefacto.
    """
    orders = _read_orders(source, since=since)
    #: Fijada sobre las ordenes del sistema de registro, antes de perder ninguna fecha.
    ventana = observation_window_end(orders)
    coverage = _coverage_of(source)
    arrival_coverage: float | None = None
    extras: tuple[str, ...] = ()
    if carrier is not None:
        orders, coverage, arrival_coverage, extras = _empalmar(
            orders, carrier, since=since, coverage=coverage
        )
        # EL MAXIMO, no la fijada pre-empalme a secas. Fijarla a secas defiende un solo
        # lado: protege a las ordenes que el transportista NO reporto de encogerse hacia
        # una fecha mas vieja, pero tambien descarta la evidencia que SI reporto. Una
        # llegada tardia en una orden -- el transportista confirmando que el pedido llego
        # el 15, no el 9 que el ERP conocia -- prueba que el reloj del mundo real ya paso
        # el 15 para TODA la cartera, incluidas las ordenes que el transportista nunca
        # toco. Descartar esa prueba deja a un stockout total sin reportar, con promesa
        # anterior a esa fecha, escondido en la guarda de "en curso": un incumplimiento
        # entero se leeria como que todavia no paso nada. El maximo nunca es menor a la
        # ventana pre-empalme -- eso preserva la defensa contra cobertura cero, que se
        # encogia por el motivo opuesto -- y crece exactamente cuando el empalme aporta
        # una fecha mas nueva que ninguna que el sistema de registro conociera solo.
        ventana_post = observation_window_end(orders)
        if ventana_post is not None:
            ventana = ventana_post if ventana is None else max(ventana, ventana_post)
    registros = tuple(
        realized_service(orders, stockout_days=stockout_days, as_of=ventana)
    )
    return ServiceScan(
        source_name=coverage.backend if coverage else "desconocido",
        records=registros,
        n_orders=len(orders),
        portfolio=of.aggregate(registros),
        order_lines=service_frame(orders),
        demand=_demand_frame(orders, source),
        coverage=coverage,
        # ``source.notes`` se lee ACA y no antes: como en los transportistas, un adaptador
        # de ERP la llena durante su propia lectura (Cin7 avisa asi cuando trunco la
        # paginacion). Copiarla al empezar se lleva una tupla vacia, siempre y en silencio.
        notes=_notes(coverage, source) + tuple(getattr(source, "notes", ())) + extras,
        arrival_coverage=arrival_coverage,
    )
