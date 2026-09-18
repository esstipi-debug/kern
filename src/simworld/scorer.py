"""El marcador: contrato de KPI portado de gym-invmgmt (MIT) mas la captura
normalizada entre piso y techo.

Un numero de costo absoluto es discutible. ``capture`` aguanta a un comprador
esceptico: 0 es no mejor que un punto de reorden fijo, 1 es igualar a un oraculo
que ve el futuro. El oraculo es inalcanzable por construccion, asi que superarlo
no es un logro sino una fuga de informacion -- y por eso ``capture`` levanta en
vez de devolver un numero bonito.

**Consistencia de costo con el oraculo (carga critica de esta tarea):**
``weekly_cost``/``score_run`` calculan el costo EXACTAMENTE como
``src/simworld/runners.py``'s ``oracle_orders`` lo optimiza -- procurement +
holding + shortage + costo fijo de ordenar, con los mismos parametros por
defecto (``order_cost=75.0, holding_rate=0.25, shortage_penalty_rate=1.0``).
Si esta formula se desincroniza de la del oraculo, el techo deja de ser un
techo genuino respecto de lo que este modulo mide.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from src.simworld.runners import WEEKS_PER_YEAR
from src.simworld.world import WeekOutcome, World


@dataclass(frozen=True)
class Kpis:
    weeks: int
    service_level: float       # fraccion de semanas-SKU sin quiebre
    fill_rate: float           # fraccion de la demanda total satisfecha
    unfulfilled_units: float
    mean_on_hand: float
    # --- sesgo, portado de AIM-Bench ---
    bullwhip: float            # var(ordenes) / var(demanda)
    anchoring: float           # |media(ordenes) - media(demanda)| / media(demanda)
    demand_chasing: float      # corr(orden_t, demanda_{t-1})
    procurement_cost: float
    holding_cost: float
    shortage_cost: float
    fixed_order_cost: float
    total_cost: float
    revenue: float
    profit: float


def weekly_cost(
    world: World,
    outcomes: list[WeekOutcome],
    *,
    order_cost: float = 75.0,
    holding_rate: float = 0.25,
    shortage_penalty_rate: float = 1.0,
) -> np.ndarray:
    """Costo total de cada semana. La suma es ``Kpis.total_cost``.

    La atribucion de la degradacion anunciada (Task 10) es por periodo, asi
    que el costo tiene que existir semana a semana, no solo agregado.

    Levanta ``ValueError`` si ``outcomes`` esta vacio, igual que
    ``score_run`` -- ambas funciones documentadas como sumando al mismo
    total deben coincidir en este limite: "cero semanas" es un error real
    de quien llama, no una respuesta valida de costo cero.
    """
    if not outcomes:
        raise ValueError("no hay semanas que puntuar")

    unit_cost = np.array([s.unit_cost for s in world.skus], dtype=float)
    margin = np.array([s.price - s.unit_cost for s in world.skus], dtype=float)
    weekly_holding = unit_cost * holding_rate / WEEKS_PER_YEAR

    out = np.zeros(len(outcomes), dtype=float)
    for j, o in enumerate(outcomes):
        procurement = float((o.ordered * unit_cost).sum())
        holding = float((o.on_hand_end * weekly_holding).sum())
        shortage = float((o.lost * margin * shortage_penalty_rate).sum())
        fixed = float(order_cost * int((o.ordered > 0).sum()))
        out[j] = procurement + holding + shortage + fixed
    return out


def score_run(
    world: World,
    outcomes: list[WeekOutcome],
    *,
    order_cost: float = 75.0,
    holding_rate: float = 0.25,
    shortage_penalty_rate: float = 1.0,
) -> Kpis:
    """El contrato de KPI completo para una corrida."""
    if not outcomes:
        raise ValueError("no hay semanas que puntuar")

    unit_cost = np.array([s.unit_cost for s in world.skus], dtype=float)
    price = np.array([s.price for s in world.skus], dtype=float)
    margin = price - unit_cost
    weekly_holding = unit_cost * holding_rate / WEEKS_PER_YEAR

    demand = np.stack([o.demand for o in outcomes], axis=1)
    sold = np.stack([o.sold for o in outcomes], axis=1)
    lost = np.stack([o.lost for o in outcomes], axis=1)
    ordered = np.stack([o.ordered for o in outcomes], axis=1)
    on_hand = np.stack([o.on_hand_end for o in outcomes], axis=1)

    total_demand = float(demand.sum())
    procurement = float((ordered * unit_cost[:, None]).sum())
    holding = float((on_hand * weekly_holding[:, None]).sum())
    shortage = float((lost * margin[:, None] * shortage_penalty_rate).sum())
    fixed = float(order_cost * int((ordered > 0).sum()))
    revenue = float((sold * price[:, None]).sum())
    total_cost = procurement + holding + shortage + fixed

    demand_series = demand.sum(axis=0)
    order_series = ordered.sum(axis=0)
    demand_var = float(demand_series.var())
    order_var = float(order_series.var())
    bullwhip = order_var / demand_var if demand_var > 0 else 0.0

    return Kpis(
        weeks=len(outcomes),
        service_level=float((lost <= 0).mean()),
        fill_rate=float(sold.sum() / total_demand) if total_demand > 0 else 1.0,
        unfulfilled_units=float(lost.sum()),
        mean_on_hand=float(on_hand.mean()),
        bullwhip=bullwhip,
        anchoring=_anchoring(order_series, demand_series),
        demand_chasing=_demand_chasing(order_series, demand_series),
        procurement_cost=procurement,
        holding_cost=holding,
        shortage_cost=shortage,
        fixed_order_cost=fixed,
        total_cost=total_cost,
        revenue=revenue,
        profit=revenue - total_cost,
    )


def _anchoring(orders: np.ndarray, demand: np.ndarray) -> float:
    """Cuanto se pega el volumen pedido a la demanda media, ignorando su forma.

    AIM-Bench lo llama pull-to-centre. Un corredor que pide siempre lo mismo,
    igual a la media, marca 0 aca y aun asi puede ser pesimo: por eso convive
    con ``demand_chasing``, que mira la forma en vez del nivel.
    """
    mean_d = float(demand.mean())
    if mean_d <= 0:
        return 0.0
    return float(abs(orders.mean() - mean_d) / mean_d)


def _demand_chasing(orders: np.ndarray, demand: np.ndarray) -> float:
    """Correlacion entre lo que se pide hoy y lo que se vendio la semana pasada.

    Alto = el corredor persigue el ultimo dato en vez de anticipar. Un
    pronostico de media movil hace exactamente esto, asi que **un motor
    determinista puede perseguir demanda igual que un LLM**: no es un sesgo
    cognitivo, es una propiedad del estimador. Por eso se mide -- y por eso
    esto no es exclusivo de politicas no deterministas o basadas en un LLM.
    """
    if len(orders) < 3:
        return 0.0
    x = orders[1:].astype(float)
    y = demand[:-1].astype(float)
    if x.std() == 0 or y.std() == 0:
        return 0.0
    return float(np.corrcoef(x, y)[0, 1])


def capture(*, floor_cost: float, kern_cost: float, oracle_cost: float) -> float:
    """Donde cae Kern entre el piso y el techo.

    0 = no mejor que el punto de reorden fijo. 1 = igual al oraculo. Negativo
    es peor que el piso, y se informa con su signo. Por encima de 1 es
    imposible: el oraculo ve el futuro, asi que superarlo significa fuga de
    informacion del mundo hacia la lente. Falla ruidosa, no silenciosa.
    """
    if not (math.isfinite(floor_cost) and math.isfinite(kern_cost) and math.isfinite(oracle_cost)):
        raise ValueError(
            f"entrada no finita: floor_cost={floor_cost!r}, kern_cost={kern_cost!r}, "
            f"oracle_cost={oracle_cost!r} deben ser numeros finitos "
            "(nan/inf pasarian en silencio por las comparaciones de mas abajo)"
        )
    span = floor_cost - oracle_cost
    if span <= 0:
        raise ValueError(
            f"span degenerado: el piso ({floor_cost:.2f}) no cuesta mas que el "
            f"oraculo ({oracle_cost:.2f}); el mundo no discrimina"
        )
    value = (floor_cost - kern_cost) / span
    if value > 1.0:
        raise ValueError(
            f"captura {value:.3f} > 1: Kern le gano al oraculo, que ve el futuro. "
            "Hay fuga de informacion del mundo hacia la lente."
        )
    return float(value)


@dataclass(frozen=True)
class Degradation:
    """Cuanto le costo la suciedad a Kern, y cuanto de eso aviso.

    La atribucion es por periodo (semana a semana), nunca global: comparar
    solo los totales de dos corridas conflaria "esta semana empeoro por la
    suciedad que Kern leyo" con "las dos corridas simplemente divergieron con
    el tiempo por timing de ordenes distinto" -- una afirmacion mucho mas
    debil. Misma semilla, mismo mundo, asi que la semana ``t`` del nivel 0 y
    la semana ``t`` de cualquier otro nivel son directamente comparables.
    """

    level: int
    drop: float              # exceso de costo total contra el nivel 0 (puede ser negativo)
    announced: float         # la parte del exceso en semanas donde Kern aviso (puede ser negativa)
    silent: float            # drop - announced. El numero que importa (puede ser negativo).
    weeks_announced: int


def degradation(
    clean_weekly: np.ndarray,
    dirty_weekly: np.ndarray,
    announced_weeks: list[bool],
    *,
    level: int,
) -> Degradation:
    """Atribuye el exceso de costo semana a semana, no en bloque.

    El exceso de una semana (``dirty - clean``) puede ser **negativo** -- la
    suciedad puede abaratar una semana por accidente (una fila descartada que
    por casualidad reduce una sobre-orden). No se recorta a cero en ningun
    punto: ni por semana, ni el total, ni la parte anunciada, ni la parte
    silenciosa -- todo se suma con su signo. Si el total sale negativo, o si
    ``silent`` sale negativo mientras ``announced`` es positivo, el reporte lo
    dice en vez de esconderlo detras de un ``max(0, ...)`` o un ``abs(...)``.

    "Anunciada" se ata a la semana ``t`` usando el flag de ``announced_weeks``
    en la posicion ``t`` -- la misma semana cuyo exceso se esta atribuyendo,
    nunca la semana anterior ni la siguiente.

    Levanta ``ValueError`` si ``clean_weekly``/``dirty_weekly`` estan vacios,
    igual que ``weekly_cost``: "cero semanas es un error real de quien llama,
    no una respuesta valida" -- aca la respuesta invalida seria un
    ``Degradation`` todo en cero, es decir "Kern no degrado nada y no
    escondio nada", una afirmacion muy distinta de "no hay datos".
    """
    if len(clean_weekly) == 0:
        raise ValueError("no hay semanas que puntuar")
    clean = np.asarray(clean_weekly, dtype=float)
    dirty = np.asarray(dirty_weekly, dtype=float)
    if clean.shape != dirty.shape:
        raise ValueError(f"largo distinto: limpio {clean.shape} vs sucio {dirty.shape}")
    if len(announced_weeks) != len(clean):
        raise ValueError(
            f"largo distinto: {len(announced_weeks)} avisos para {len(clean)} semanas"
        )

    excess = dirty - clean
    flags = np.asarray(announced_weeks, dtype=bool)
    drop = float(excess.sum())
    announced = float(excess[flags].sum())
    return Degradation(
        level=level,
        drop=drop,
        announced=announced,
        silent=drop - announced,
        weeks_announced=int(flags.sum()),
    )


# ---- N2b: que le hizo un shock al carril -------------------------------------


def weekly_fill(outcomes: list[WeekOutcome]) -> np.ndarray:
    """Fill rate de cada semana: unidades servidas sobre demandadas, agregado por SKU.

    ``Kpis.fill_rate`` es el mismo cociente sobre toda la corrida; esto lo necesita
    semana a semana porque la recuperacion de un shock es una pregunta temporal -- "cuando
    volvio a la banda" no se puede contestar con un agregado.

    Una semana sin demanda da 1,0 y no NaN: no se dejo de servir nada. El NaN seria
    peor que discutible, seria contagioso -- una sola semana muerta ensuciaria el minimo
    y la comparacion contra la banda de todas las demas.
    """
    if not outcomes:
        raise ValueError("no hay semanas que puntuar")
    out = np.zeros(len(outcomes), dtype=float)
    for j, o in enumerate(outcomes):
        demanded = float(o.demand.sum())
        out[j] = 1.0 if demanded <= 0 else float(o.sold.sum()) / demanded
    return out


@dataclass(frozen=True)
class ShockImpact:
    """Lo que un shock le costo a un carril, medido contra el MISMO carril sin shock.

    Las dos medidas de unidades perdidas viajan JUNTAS y con nombres distintos, que es
    la correccion que el hallazgo H5 de ``documentation/KERN_DISRUPCIONES_Y_DESAJUSTES.md``
    dejo escrita. Bajo un ``demand_surge`` la demanda sube, asi que el carril con shock
    puede SERVIR MAS que el limpio: medir "perdido" como ``servido_base - servido_shock``
    da cero (o negativo) mientras el fill se hunde a 0,27. Las dos son ciertas y miden
    cosas distintas -- una es "cuanta demanda del mundo perturbado quedo sin servir", la
    otra es "cuanto menos vendiste que sin el shock" -- y por eso ninguna reemplaza a la
    otra, y la segunda va CON SIGNO, sin ``max(0, ...)``.
    """

    #: Procedencia, para que una seccion de JSON se entienda sin el comando que la produjo.
    shock_kind: str
    shock_target: str
    start_week: int
    end_week: int
    band: float
    hold: int

    #: Si el shock LLEGO a este carril: las dos corridas difieren en alguna semana.
    #:
    #: ``False`` significa que la corrida perturbada salio identica a la limpia, elemento
    #: a elemento, asi que **este carril no midio la disrupcion** y ningun veredicto de
    #: abajo es sobre ella. Pasa por causas que la grilla no puede anticipar: el SKU
    #: apuntado no se pide dentro de la ventana, o el carril no emite ordenes todavia con
    #: el ``replan_every`` elegido. Un shock de suministro solo muerde a traves de una
    #: orden EMITIDA -- si no hubo ninguna, se perturbo una matriz que nadie consulto.
    #:
    #: Existe porque ``first_out_of_band_week=None`` ya significaba otra cosa
    #: ("salio y nunca cayo fuera de la banda", o sea ABSORBIDO) y las dos se
    #: renderizaban igual. Medido antes de esta guarda, con el ejemplo literal del
    #: diseno (``--seed 4 --skus 8 --shock lead_time_spike:SKU-1003:10:3:2``): el carril
    #: de Kern publicaba "absorbido, recupero si" y una captura bajo disrupcion de
    #: -0,221 con un ``total_cost`` identico al ultimo digito al de la corrida limpia.
    #: "El carril aguanto la disrupcion" y "la disrupcion nunca llego al carril" son
    #: cosas distintas y hasta aca se leian igual.
    reached: bool
    #: Primera semana, en o despues del arranque del shock, en la que el fill cayo fuera
    #: de la banda. ``None`` = nunca salio: el shock se ABSORBIO. Es un resultado real y
    #: frecuente (Anexo A.1: con holgura por defecto, un shock moderado no se nota).
    #: Tambien ``None`` cuando ``reached`` es ``False``, y ahi no quiere decir absorbido
    #: sino que no hubo nada que medir: mira ``reached`` antes de leerlo.
    first_out_of_band_week: int | None
    #: Semanas desde esa primera salida hasta ``hold`` semanas seguidas de vuelta adentro.
    #: ``None`` cuando no hubo salida (nada que recuperar) o cuando no recupero antes del
    #: final del horizonte. Ese segundo caso NO se censura con el resto del horizonte: un
    #: numero censurado promediado junto a TTR reales los diluye y no se distingue de
    #: ellos. Se dice ``recovered=False`` y se deja el numero vacio.
    ttr_weeks: float | None
    #: ``None`` cuando ``reached`` es ``False``: sin disrupcion que recibir no hay
    #: recuperacion que afirmar. Un ``True`` ahi seria la peor lectura posible de este
    #: modulo -- resiliencia acreditada por un golpe que nunca llego.
    recovered: bool | None
    #: El peor fill de la ventana post-shock. Lo que un operador mira primero.
    #:
    #: ABSOLUTO, no incremental: es el peor fill que hubo, no el que el shock CAUSO. Un
    #: carril que ya servia mal sin shock reporta un minimo bajo igual. La parte causal
    #: de este impacto vive en ``lost_vs_baseline`` y en ``cost_excess_post_shock``, que
    #: son diferencias contra la corrida limpia.
    min_fill: float
    #: Demanda del mundo CON SHOCK que quedo sin servir desde el arranque del shock
    #: hasta el final del horizonte. Nunca negativo por construccion.
    #:
    #: Tambien ABSOLUTO, y a proposito: es la definicion que el hallazgo H5 exige para
    #: poder decir algo bajo un ``demand_surge``, donde la comparacion contra el baseline
    #: da cero o negativo. Incluye las perdidas que el carril habria tenido igual sin el
    #: shock -- leerlo como "esto costo el shock" lo sobre-estima, y para eso esta
    #: ``lost_vs_baseline`` al lado.
    lost_units_post_shock: float
    #: Servido sin shock menos servido con shock, en la misma ventana, CON SIGNO.
    #: Negativo significa que el carril con shock vendio MAS (tipico de un surge).
    lost_vs_baseline: float
    #: Exceso de costo del carril con shock contra el mismo carril limpio, sumado semana
    #: a semana sobre la ventana. Puede ser negativo, y se informa asi.
    #:
    #: **Un shock de suministro lo suele poner NEGATIVO, y eso no significa que salio
    #: gratis.** Un corte de proveedor frena las llegadas: no se paga la compra ni el
    #: almacenamiento de lo que no llego, y ese ahorro puede superar a la penalidad por
    #: quiebre. El dano de un shock de suministro vive en ``lost_units_post_shock`` y en
    #: ``min_fill``, no aca. Se informa igual, con su signo, porque recortarlo a cero
    #: diria que el shock no movio el costo cuando si lo movio, en la otra direccion.
    cost_excess_post_shock: float


def _misma_traza(baseline: list[WeekOutcome], shocked: list[WeekOutcome]) -> bool:
    """Las dos corridas salieron identicas elemento a elemento: el shock no llego.

    Es la senal barata y en banda de que no hay disrupcion que puntuar. No se infiere de
    ``cost_excess_post_shock == 0`` porque un exceso de costo nulo tambien puede salir de
    dos trayectorias distintas que se compensan; la igualdad de las trayectorias, en
    cambio, no admite otra lectura.

    Compara los seis campos de ``WeekOutcome`` (``lost`` es redundante con
    ``demand - sold``, y va igual: cuesta nada y no obliga a quien lea esto a re-derivar
    la identidad para convencerse de que la comparacion es exhaustiva).
    """
    return all(
        a.week == b.week
        and np.array_equal(a.demand, b.demand)
        and np.array_equal(a.sold, b.sold)
        and np.array_equal(a.lost, b.lost)
        and np.array_equal(a.received, b.received)
        and np.array_equal(a.ordered, b.ordered)
        and np.array_equal(a.on_hand_end, b.on_hand_end)
        for a, b in zip(baseline, shocked)
    )


def score_shock(
    world: World,
    baseline: list[WeekOutcome],
    shocked: list[WeekOutcome],
    shock,
    *,
    band: float = 0.02,
    hold: int = 3,
) -> ShockImpact:
    """Compara dos corridas del MISMO carril sobre la MISMA semilla: una limpia y una
    con ``shock`` aplicado (``src.simworld.shocks.apply_shocks``).

    **TTR se mide desde la primera semana FUERA DE LA BANDA, no desde el arranque del
    shock.** Es la version 2 del experimento A.1 de la investigacion, y la diferencia no
    es cosmetica: el Anexo mide un corte de proveedor que recien se nota diez periodos
    despues de empezar -- "el 'cae en t+10' no es lentitud de deteccion sino el pipeline:
    la tienda vive del stock del CD hasta que se vacia". Contar esas diez semanas como
    tiempo de recuperacion le cobraria al sistema la holgura que lo protegio.

    **La banda es de un solo lado.** Estar POR ENCIMA del fill del baseline no es estar
    fuera de la banda: es servir mejor, y exigir volver a un corredor simetrico haria que
    una corrida que sobre-recupera figure como no recuperada. La investigacion la escribe
    como "+/-2%"; el lado que decide una recuperacion es el de abajo, y aca se dice cual
    se usa en vez de dejarlo a la lectura.

    ``hold`` son semanas CONSECUTIVAS adentro, para que un rebote de una semana no cuente
    como recuperacion (el ``min_hold`` del plan de resilience). Una ventana de ``hold``
    semanas que no entra antes del final del horizonte no cuenta como recuperada: no se
    sabe si hubiera seguido adentro.

    **Solo cuentan las semanas con demanda.** Una semana sin nada que servir no prueba
    que el servicio volvio, asi que no suma al ``hold``; tampoco rompe la racha, porque
    tampoco fallo. Es neutral. Sin esto, un tramo muerto de ``hold`` semanas declara
    recuperado a un carril que no sirvio una sola unidad desde el shock.

    **Un shock que no llego a este carril se declara, no se puntua.** Si las dos
    corridas salieron identicas (:func:`_misma_traza`), ``reached`` es ``False`` y el
    veredicto queda vacio: ``first_out_of_band_week`` y ``ttr_weeks`` en ``None`` y
    ``recovered`` tambien en ``None``, en vez del "absorbido / recupero si" que salia
    antes. El caso no es raro ni exotico -- basta que el SKU apuntado no se pida dentro
    de la ventana -- y es el unico de este modulo en el que un numero bien formado
    afirmaria lo contrario de lo que paso.

    El costo semanal sale de :func:`weekly_cost` sobre cada corrida. Se le pasa el mundo
    del carril limpio a las dos porque el costo solo usa ``world.skus`` (costo unitario y
    margen) y un shock no toca los SKU: pasar dos mundos distintos daria el mismo numero
    y sugeriria que podria no darlo.
    """
    if not baseline or not shocked:
        raise ValueError("no hay semanas que puntuar")
    if len(baseline) != len(shocked):
        raise ValueError(
            f"largo distinto: {len(baseline)} semanas limpias vs {len(shocked)} con shock; "
            "las dos corridas tienen que ser del mismo horizonte para compararse semana a semana"
        )
    if hold < 1:
        raise ValueError(f"hold debe ser >= 1, no {hold}")
    if band < 0:
        raise ValueError(f"band debe ser >= 0, no {band}")

    n = len(shocked)
    start = int(shock.start_week)
    if not 0 <= start < n:
        raise ValueError(f"el shock arranca en la semana {start}, fuera del horizonte de {n}")

    fill_base = weekly_fill(baseline)
    fill_shock = weekly_fill(shocked)
    dentro = [bool(fill_shock[t] >= fill_base[t] - band) for t in range(n)]
    # Una semana SIN DEMANDA no prueba nada sobre el servicio: no hubo nada que servir.
    # `weekly_fill` le da 1,0 (no se dejo de servir nada, y un NaN seria contagioso),
    # pero para la recuperacion eso no es evidencia -- y contarlo como tal produce el
    # peor error posible aca: un carril que no sirvio una sola unidad desde el shock
    # sale `recovered=True` con un TTR, porque tres semanas muertas alcanzaron el
    # `hold`. Medido antes de esta guarda: TTR 2 sobre un carril muerto.
    probada = [bool(float(o.demand.sum()) > 0) for o in shocked]

    # Antes de cualquier veredicto: ¿el shock llego siquiera a este carril? Si las dos
    # corridas son identicas no hay disrupcion que puntuar, y decir "absorbido" seria
    # acreditarle al carril una resistencia que nunca se le pidio.
    alcanzado = not _misma_traza(baseline, shocked)

    first_out: int | None = None
    ttr: float | None = None
    recovered: bool | None = None
    if alcanzado:
        first_out = next(
            (t for t in range(start, n) if probada[t] and not dentro[t]), None
        )
        recovered = True
    if first_out is not None:
        recovered = False
        seguidas = 0
        arranque: int | None = None
        for t in range(first_out, n):
            if not probada[t]:
                # Neutral: ni rompe la racha (no hubo falla) ni la acredita (no hubo
                # prueba). Saltarla es lo unico que no afirma nada de mas.
                continue
            if dentro[t]:
                if seguidas == 0:
                    arranque = t
                seguidas += 1
                if seguidas >= hold:
                    ttr = float(arranque - first_out)
                    recovered = True
                    break
            else:
                seguidas = 0
                arranque = None

    ventana = range(start, n)
    lost_post = float(sum(float(shocked[t].lost.sum()) for t in ventana))
    servido_base = float(sum(float(baseline[t].sold.sum()) for t in ventana))
    servido_shock = float(sum(float(shocked[t].sold.sum()) for t in ventana))

    costo_base = weekly_cost(world, baseline)
    costo_shock = weekly_cost(world, shocked)
    exceso = float((costo_shock[start:] - costo_base[start:]).sum())

    return ShockImpact(
        shock_kind=str(shock.kind),
        shock_target=str(shock.target),
        start_week=start,
        end_week=int(shock.end_week),
        band=float(band),
        hold=int(hold),
        reached=alcanzado,
        first_out_of_band_week=first_out,
        ttr_weeks=ttr,
        recovered=recovered,
        min_fill=float(fill_shock[start:].min()),
        lost_units_post_shock=lost_post,
        lost_vs_baseline=servido_base - servido_shock,
        cost_excess_post_shock=exceso,
    )
