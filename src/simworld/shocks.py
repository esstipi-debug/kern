"""Shocks deterministas sobre un mundo YA sorteado (N2b).

**Por que existe.** El marcador (``src/simworld/``) mide si las decisiones de Kern son
buenas bajo suciedad de datos, y nunca bajo DISRUPCION. El gemelo
(``src/digital_twin.py``) si sabe inyectar disrupciones -- ``Disruption(kind, target,
start, duration, magnitude)``, tres tipos -- pero nadie mide sus trazas: es el hallazgo
H5 de ``documentation/KERN_DISRUPCIONES_Y_DESAJUSTES.md``. Este modulo trae ese
vocabulario al marcador, que SI tiene piso, techo y captura normalizada, para que
"captura bajo disrupcion" sea un numero y no una intencion.

**Un shock es una transformacion PURA del mundo ya sorteado.** No toca ``rng``, no toca
``generate_world`` y no toca ``advance``. Esa es la unica forma de conservar la garantia
que sostiene todo el marcador -- *misma semilla, mismo mundo* -- y de poder decir *misma
semilla + mismo shock, mismo mundo perturbado*. Si el shock se sorteara adentro de la
generacion, dos corridas del mismo experimento dejarian de ser comparables en silencio.

**No es idempotente, y no debe serlo.** ``apply_shocks(w, s)`` es una funcion determinista
de ``(w, s)``: llamarla dos veces sobre el MISMO mundo base da dos mundos identicos. Pero
``apply_shocks(apply_shocks(w, s), s)`` NO es ``apply_shocks(w, s)``: un ``lead_time_spike``
suma semanas, y sumarlas dos veces es un shock mas grande. Es lo correcto (dos golpes
duelen mas que uno) y es una trampa facil de escribir, asi que se dice aca: aplicar sobre
el mundo BASE, siempre, no encadenar.

**El vocabulario es el del gemelo, a proposito.** Mismos tres nombres, misma ventana
semiabierta ``[start, start + weeks)``, mismo significado de ``magnitude`` por tipo. Dos
modulos que dicen ``lead_time_spike`` y significan cosas distintas serian peor que dos
nombres distintos.

**Donde pega cada tipo**, y por que ahi:

- ``lead_time_spike``: ``real_lead_weeks[i, t] += magnitude`` para las ordenes EMITIDAS en
  la ventana. ``advance`` lee el lead real en el momento de emitir
  (``arrival = t + real_lead_weeks[i, t]``), asi que perturbar la columna ``t`` es
  exactamente "la orden que pusiste esta semana tarda mas".
- ``supplier_outage``: el proveedor esta caido en la ventana, asi que una orden emitida
  ahi no puede ni empezar hasta que reabre: llega en ``end + promised_lead``. En
  terminos de la matriz, ``real_lead_weeks[i, t] = max(real, end - t + promised)``. El
  ``max`` no es decoracion: sin el, un SKU cuyo lead real ya era peor que el corte
  llegaria ANTES por culpa del shock.
- ``demand_surge``: ``demand[i, t] *= magnitude`` en la ventana, redondeado como el sorteo
  base. Solo la ventana; fuera de ella la demanda es la del mundo base, elemento a
  elemento.

Una orden cuya llegada cae fuera del horizonte simplemente no llega: ``advance`` la deja
en transito para siempre. Es la respuesta fisica correcta -- no llego dentro del
experimento -- y no hay que recortarla.

**Lo que Kern ve de un shock, y lo que no.** La lente
(``src/simworld/lens.py``) le muestra el plazo PROMETIDO del proveedor, nunca el real, asi
que un ``lead_time_spike`` es invisible para la replanificacion salvo por sus consecuencias
en los resultados de las semanas que ya pasaron. No es un descuido de este modulo: es la
asimetria de informacion que el marcador existe para medir, y es parte de por que la
captura bajo disrupcion puede salir negativa.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, replace

import numpy as np

from src.simworld.world import INITIAL_STOCK_WEEKS, World

#: Los tres tipos, con los nombres del gemelo (``src/digital_twin.py``).
SHOCK_KINDS = ("lead_time_spike", "supplier_outage", "demand_surge")

#: Un objetivo que significa "todos los SKU".
TARGET_ALL = "*"

#: Separador del formato de linea de comandos ``kind:target:start:weeks:magnitude``.
CLI_SEPARATOR = ":"


class ShockError(ValueError):
    """Un shock que no se puede aplicar: tipo desconocido, objetivo que no existe,
    ventana fuera del horizonte, magnitud sin sentido para su tipo.

    Levanta en vez de degradar a "no hizo nada". Un shock declarado que se aplica
    silenciosamente sobre cero filas produce una corrida que se lee como "Kern aguanto
    la disrupcion" cuando la disrupcion nunca ocurrio -- el peor resultado posible de
    este modulo.
    """


@dataclass(frozen=True)
class Shock:
    """Una disrupcion acotada en el tiempo, activa en ``[start_week, start_week + weeks)``.

    ``target`` es un SKU (``"SKU-1003"``), un proveedor (``"Proveedor-2"``, que alcanza a
    todos sus SKU) o ``"*"`` (todos). ``demand_surge`` NO acepta un proveedor: la demanda
    es de un SKU, y decir "la demanda del proveedor 2 se duplico" seria una frase sin
    referente que igual produciria numeros.

    ``magnitude`` significa una cosa distinta por tipo, igual que en el gemelo:
    semanas extra de lead (``lead_time_spike``), multiplicador de demanda
    (``demand_surge``), y NADA en ``supplier_outage`` -- ahi el largo del corte es toda
    la informacion, y se ignora en vez de inventarle un uso.
    """

    kind: str
    target: str
    start_week: int
    weeks: int
    magnitude: float = 1.0

    def __post_init__(self) -> None:
        if self.kind not in SHOCK_KINDS:
            raise ShockError(f"tipo de shock desconocido: {self.kind!r}; conocidos: {SHOCK_KINDS}")
        if self.start_week < 0:
            raise ShockError(f"start_week debe ser >= 0, no {self.start_week}")
        if self.weeks < 1:
            raise ShockError(f"weeks debe ser >= 1, no {self.weeks}")
        if not str(self.target).strip():
            raise ShockError("target vacio: nombra un SKU, un proveedor o '*'")
        # NaN e infinito antes que nada: `nan < 1` y `nan <= 0` son los dos False, asi
        # que se colarian por las guardas de abajo y recien reventarian -- o peor, NO
        # reventarian -- adentro de `apply_shocks`. Medido: un demand_surge con NaN deja
        # la matriz de demanda entera en NaN y la corrida sigue produciendo numeros.
        if not math.isfinite(self.magnitude):
            raise ShockError(
                f"magnitude={self.magnitude!r} no es un numero finito. Un NaN multiplica "
                "la demanda del mundo entero a NaN y la corrida sigue dando resultados con "
                "forma de medicion"
            )
        if self.kind == "lead_time_spike":
            if self.magnitude < 1:
                raise ShockError(
                    f"lead_time_spike con magnitude={self.magnitude}: son SEMANAS EXTRA de "
                    "lead, asi que por debajo de 1 el shock no atrasa nada y la corrida se "
                    "leeria como 'Kern aguanto' sobre una disrupcion que no ocurrio"
                )
            # El lead del mundo se mide en semanas ENTERAS (`real_lead_weeks` es int), asi
            # que "2,5 semanas mas tarde" no tiene representacion. Redondear seria peor que
            # rechazar: `round(2.5)` es 2 en Python (redondeo bancario), el mundo recibiria
            # un atraso de 2 y el reporte publicaria `magnitude 2.5` -- un shock que no
            # ocurrio, escrito en el JSON que despues alguien cita.
            if float(self.magnitude) != int(self.magnitude):
                raise ShockError(
                    f"lead_time_spike con magnitude={self.magnitude}: el lead del mundo se "
                    "mide en semanas enteras. Redondearlo publicaria en el reporte una "
                    "magnitud que el mundo nunca recibio; usa un entero"
                )
        if self.kind == "demand_surge" and self.magnitude <= 0:
            raise ShockError(
                f"demand_surge con magnitude={self.magnitude}: es un MULTIPLICADOR de "
                "demanda y tiene que ser > 0"
            )

    @property
    def end_week(self) -> int:
        """Primera semana YA no afectada. La ventana es ``[start_week, end_week)``."""
        return self.start_week + self.weeks

    def active(self, week: int) -> bool:
        return self.start_week <= week < self.end_week

    def as_cli(self) -> str:
        """El texto que ``parse_shock`` vuelve a leer. Va al reporte para que una corrida
        publicada se pueda reproducir copiando una linea."""
        return CLI_SEPARATOR.join(
            (self.kind, self.target, str(self.start_week), str(self.weeks), f"{self.magnitude:g}")
        )


def parse_shock(text: str) -> Shock:
    """``"lead_time_spike:SKU-1003:10:3:2"`` -> :class:`Shock`.

    Cinco campos, en el orden del diseno: ``kind:target:start:weeks:magnitude``. El
    quinto es opcional solo para ``supplier_outage``, que lo ignora; para los otros dos
    faltarlo es un error, no un default -- un ``magnitude`` por omision seria elegir la
    severidad del experimento por el operador.
    """
    partes = [p.strip() for p in str(text).split(CLI_SEPARATOR)]
    if len(partes) not in (4, 5):
        raise ShockError(
            f"shock mal formado: {text!r}. Formato: "
            "kind:target:start_week:weeks[:magnitude] "
            f"(kind en {SHOCK_KINDS})"
        )
    kind, target, start, weeks = partes[0], partes[1], partes[2], partes[3]
    if len(partes) == 4 and kind != "supplier_outage":
        raise ShockError(
            f"{kind} necesita magnitude: {text!r} trae cuatro campos. Solo "
            "supplier_outage puede omitirla (la ignora)."
        )
    try:
        start_i, weeks_i = int(start), int(weeks)
        magnitude = float(partes[4]) if len(partes) == 5 else 1.0
    except ValueError as exc:
        raise ShockError(f"shock mal formado: {text!r} ({exc})") from exc
    return Shock(kind=kind, target=target, start_week=start_i, weeks=weeks_i, magnitude=magnitude)


@dataclass(frozen=True)
class ShockWindow:
    """La perturbacion COMBINADA de varios shocks, para medirla como UNA sola.

    Con dos o mas shocks activos hay un solo mundo perturbado, asi que un impacto por
    shock no se puede atribuir: la ventana del primero incluye el dano del segundo, y
    los dos numeros dirian que cada shock hizo todo. Medir la combinacion es lo unico
    honesto -- una perturbacion, un impacto -- y el nombre dice cuales entraron.

    Tiene la forma que :func:`src.simworld.scorer.score_shock` consume (``kind``,
    ``target``, ``start_week``, ``end_week``), asi que el camino de un solo shock y el de
    varios pasan por la misma funcion.
    """

    kind: str
    target: str
    start_week: int
    end_week: int


def combined_window(shocks: tuple[Shock, ...] | list[Shock]) -> Shock | ShockWindow:
    """El objeto que se mide: el shock mismo cuando hay uno, la combinacion cuando hay mas.

    Con uno devuelve el propio ``Shock`` (mismo ``kind``/``target``/ventana de siempre).
    Con varios, una ventana que va del primer arranque al ultimo final, etiquetada con
    todos: ``"lead_time_spike+supplier_outage"`` sobre ``"SKU-1003, Proveedor-2"``.
    """
    shocks = tuple(shocks)
    if not shocks:
        raise ShockError("no hay shocks que combinar")
    if len(shocks) == 1:
        return shocks[0]
    return ShockWindow(
        kind="+".join(dict.fromkeys(s.kind for s in shocks)),
        target=", ".join(dict.fromkeys(s.target for s in shocks)),
        start_week=min(s.start_week for s in shocks),
        end_week=max(s.end_week for s in shocks),
    )


def _sku_indices(world: World, shock: Shock) -> tuple[int, ...]:
    """Las filas que el shock alcanza. Vacio nunca: sin filas, levanta."""
    if shock.target == TARGET_ALL:
        return tuple(range(len(world.skus)))
    por_sku = [i for i, s in enumerate(world.skus) if s.sku == shock.target]
    if por_sku:
        return tuple(por_sku)

    proveedores = {s.name: i for i, s in enumerate(world.suppliers)}
    if shock.target in proveedores:
        if shock.kind == "demand_surge":
            raise ShockError(
                f"demand_surge apunta a {shock.target!r}, que es un PROVEEDOR. La demanda "
                "es de un SKU: nombra un SKU o '*'."
            )
        idx = proveedores[shock.target]
        filas = tuple(i for i, s in enumerate(world.skus) if s.supplier_idx == idx)
        if not filas:
            raise ShockError(
                f"el proveedor {shock.target!r} no abastece ningun SKU de este mundo: el "
                "shock no golpearia nada"
            )
        return filas

    raise ShockError(
        f"objetivo desconocido: {shock.target!r}. SKU de este mundo: "
        f"{[s.sku for s in world.skus]}; proveedores: {sorted(proveedores)}; o '{TARGET_ALL}'"
    )


def _check_window(world: World, shock: Shock) -> None:
    if shock.start_week >= world.weeks:
        raise ShockError(
            f"el shock arranca en la semana {shock.start_week} y el horizonte tiene "
            f"{world.weeks}: no golpearia ninguna semana"
        )
    # `initial_state` dimensiona el stock de arranque con las primeras
    # INITIAL_STOCK_WEEKS semanas de demanda. Un surge que caiga ahi adentro se
    # AUTO-ABSORBE: el mundo le entrega al corredor exactamente el stock que hace falta
    # para servirlo (medido: un surge x3 en las semanas 0-3 triplica el stock inicial).
    # La corrida saldria "absorbido" y se leeria como robustez, cuando lo que paso es que
    # el arnes regalo el inventario. No se avisa: se rechaza, porque un aviso al lado de
    # un numero que se ve bien no lo desarma.
    if shock.kind == "demand_surge" and shock.start_week < INITIAL_STOCK_WEEKS:
        raise ShockError(
            f"un demand_surge que arranca en la semana {shock.start_week} cae dentro de "
            f"las {INITIAL_STOCK_WEEKS} semanas con las que `initial_state` dimensiona el "
            "stock de arranque: el mundo entregaria el inventario justo para absorberlo y "
            "la corrida mediria otra cosa (saldria 'absorbido' por construccion). "
            f"Arrancalo en la semana {INITIAL_STOCK_WEEKS} o despues."
        )


def apply_shocks(world: World, shocks: tuple[Shock, ...] | list[Shock]) -> World:
    """El mundo base, perturbado. Funcion pura: mismo ``(world, shocks)``, mismo resultado.

    Copia ``demand`` y ``real_lead_weeks`` antes de tocarlos -- el mundo base queda
    intacto, que es lo que permite correr los dos carriles (limpio y con shock) sobre la
    MISMA realizacion y atribuir la diferencia al shock y a nada mas.

    Con ``shocks`` vacio devuelve el mismo objeto: una corrida sin shocks no paga nada y
    no puede diferir del comportamiento anterior ni por un redondeo.

    Varios shocks se aplican EN ORDEN y se acumulan (dos ``lead_time_spike`` sobre la
    misma ventana suman sus semanas). Ver el docstring del modulo sobre por que esto no
    es, ni debe ser, idempotente.
    """
    shocks = tuple(shocks)
    if not shocks:
        return world

    demand = np.array(world.demand, dtype=world.demand.dtype, copy=True)
    lead = np.array(world.real_lead_weeks, dtype=world.real_lead_weeks.dtype, copy=True)

    for shock in shocks:
        _check_window(world, shock)
        filas = _sku_indices(world, shock)
        # La ventana se recorta al horizonte: un shock que se pasa del final golpea lo
        # que alcanza. Que ARRANQUE fuera del horizonte si es un error (arriba): ahi no
        # golpea nada y el experimento diria otra cosa de la que midio.
        inicio = shock.start_week
        fin = min(shock.end_week, world.weeks)

        if shock.kind == "demand_surge":
            bloque = demand[np.ix_(filas, range(inicio, fin))]
            # Mismo redondeo que el sorteo base (`_seasonal_trend_demand`): la demanda
            # del mundo son unidades enteras, y dejar medias unidades aca haria que la
            # conservacion de masa de `advance` compare contra otra aritmetica.
            demand[np.ix_(filas, range(inicio, fin))] = np.maximum(
                np.round(bloque * float(shock.magnitude)), 0.0
            )
        elif shock.kind == "lead_time_spike":
            # Entero por contrato (`__post_init__` rechaza una magnitud fraccionaria), asi
            # que este `int()` no redondea nada: solo cambia de tipo para sumar sobre una
            # matriz int sin que numpy se queje.
            extra = int(shock.magnitude)
            lead[np.ix_(filas, range(inicio, fin))] += extra
        else:  # supplier_outage
            for i in filas:
                promised = world.suppliers[world.skus[i].supplier_idx].promised_lead_weeks
                for t in range(inicio, fin):
                    # `shock.end_week` y no `fin`: cuando reabre el proveedor lo decide
                    # el largo del corte, no el horizonte del experimento. Si eso cae
                    # despues del final, la orden no llega -- y esa es la respuesta
                    # correcta, no una que haya que recortar para que quede linda.
                    lead[i, t] = max(int(lead[i, t]), shock.end_week - t + int(promised))

    return replace(world, demand=demand, real_lead_weeks=lead)
