"""No acumules por encima de lo que todavia podes vender.

La regla que `documentation/KERN_HORIZONTE_POR_SKU.md` midio y que
`documentation/KERN_PALANCAS_SOBRE_25_MUNDOS.md` confirmo sobre 25 mundos: con
`restante` = la demanda que queda hasta la fecha de fin del SKU, se topean las DOS
mitades de la politica.

    disparador = min(disparador, restante)
    objetivo   = min(objetivo,   restante)

**Las dos, o no sirve.** `apply_policy` pide hasta el objetivo cuando la posicion cae al
disparador, asi que topear solo una deja la orden igual de grande. Medido: +0,1309 de
captura en 23 de 25 mundos (t=+5,81, p=0,00001), llevando a Kern de -13,8% a -0,7%. Es la
palanca mas grande de la rama y la unica que resiste la muestra completa -- las de
"compra mas" se encogen o cambian de signo.

QUE CUENTA COMO `restante`, Y POR QUE NO HACE FALTA SABER EL FUTURO
    `media medida por periodo x periodos que faltan`. Se midieron los dos carriles -- ese
    y el que topea con la demanda futura VERDADERA -- y son equivalentes: +0,1309 contra
    +0,1413, una diferencia de +0,0104 con p=0,378. La palanca no necesita informacion
    que nadie tiene; necesita una FECHA.

LO QUE ESTE MODULO SE NIEGA A HACER, QUE ES LA MITAD DEL DISENO
    El tope puede ENCOGER una politica; **nunca puede anularla**. Un tope de cero no es
    una politica chica, es "no compres nunca mas", o sea una DISCONTINUACION -- una
    decision de negocio que toma una persona, no un redondeo que cae de una celda mal
    tipeada. Y una fecha mal tipeada es el modo de falla realista: un `2025-01-01` donde
    iba `2026-01-01` apaga la reposicion de un SKU que vende todas las semanas.

    Por eso hay tres verdictos que NO topean y se declaran por nombre
    (`expired`, `would_zero`, `unusable`): la linea sale INTACTA y la superficie tiene
    que nombrarla. Es la misma regla que `tests/test_negativa_no_es_cero.py` fija en el
    productor -- una negativa no puede salir vestida de numero -- aplicada a la entrada
    en vez de a la salida.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from src.data_loader import lead_time_periods_from_days

#: Los verdictos que NO mueven la politica. Cada uno nombra por que, y una superficie
#: que renderice el consejo tiene que poder distinguirlos: "no tenia fecha" y "la fecha
#: ya paso" mandan al operador a cosas distintas.
SIN_TOPE = ("no_date", "expired", "would_zero", "unusable")

#: Los dos que SI representan un problema con el dato del cliente, frente a `no_date`
#: (que es la ausencia normal) e `inert` (que es una fecha lejana, o sea todo bien).
DECLARABLES = ("expired", "would_zero", "unusable")


@dataclass(frozen=True)
class HorizonCap:
    """Que hacer con esta politica dada la fecha de fin de su SKU.

    `trigger` y `target` son SIEMPRE utilizables: cuando el verdicto esta en `SIN_TOPE`
    vuelven los valores que entraron, sin tocar. Un llamador que ignore `verdict` obtiene
    el comportamiento de antes de que esta capacidad existiera, que es la forma segura de
    fallar.

    Los dos campos se llaman asi -- y no `reorder_point`/`order_up_to` -- porque **cual
    es la segunda mitad depende de la politica**, y ponerle el nombre de una de las dos
    formas escondio un defecto real mientras se escribia esto: un `(s,Q)` trae
    `order_up_to=None`, asi que el tope se decidia mirando SOLO el disparador y dejaba
    pasar una `Q` que era mayor que todo lo que quedaba por vender. Las dos mitades son
    `s` y `Q` en un `(s,Q)`, y `s` y `S` en un `(R,S)`; esta clase no necesita saber
    cual, solo que son dos y que las dos se acotan.
    """

    trigger: float
    target: float | None
    verdict: str
    periods_remaining: float | None = None
    remaining_demand: float | None = None

    @property
    def capped(self) -> bool:
        return self.verdict == "applied"

    @property
    def declarable(self) -> bool:
        """El dato del cliente tiene un problema que la superficie debe nombrar."""
        return self.verdict in DECLARABLES


def periods_until(
    as_of: date | datetime | None,
    end_date: date | datetime | None,
    *,
    periods_per_year: float = 52.0,
) -> float | None:
    """Periodos que faltan entre las dos fechas, o ``None`` si falta alguna.

    Cruza una frontera de unidades -- del calendario del cliente a la grilla del motor --
    y por eso delega en `src.data_loader.lead_time_periods_from_days`, la MISMA identidad
    con la que el job convierte un plazo de proveedor. Una segunda formula aca haria que
    dos numeros del mismo entregable midieran ventanas distintas sin decirlo.

    Devuelve un valor <= 0 cuando la fecha ya paso: quien decide que hacer con eso es
    `cap_policy`, no esta funcion, porque es una decision de producto y no de aritmetica.
    """
    if as_of is None or end_date is None:
        return None
    a = as_of.date() if isinstance(as_of, datetime) else as_of
    b = end_date.date() if isinstance(end_date, datetime) else end_date
    return lead_time_periods_from_days((b - a).days, periods_per_year=periods_per_year)


def cap_policy(
    trigger: float | None,
    target: float | None,
    *,
    mean_per_period: float | None,
    periods_remaining: float | None,
) -> HorizonCap:
    """Topea las DOS mitades por lo que queda por vender, o se niega y dice por que.

    ``trigger`` es el disparador (``s``) y ``target`` la otra cantidad de la politica:
    ``S`` en un ``(R,S)`` o en una cobertura, ``Q`` en un ``(s,Q)``. Las dos entran a la
    decision: si solo una de ellas supera el restante, el verdicto ya es ``applied``.
    Decidir mirando una sola era el defecto que este nombre existe para no repetir.

    Los cinco verdictos:

    - ``no_date``    -- el SKU no trae fecha de fin. Intacto. Es la ausencia NORMAL y la
      mayoria de una cartera real esta aca; no es un problema del archivo.
    - ``unusable``   -- hay fecha pero no hay media medida (o no es finita). Intacto y
      declarado: inventarle una media al SKU seria exactamente el cero fabricado que
      esta rama persigue.
    - ``expired``    -- la fecha ya paso (o es hoy). Intacto y declarado. Topear aca
      daria un tope <= 0, o sea "no compres nunca mas", y eso es una discontinuacion:
      la decide una persona.
    - ``would_zero`` -- la fecha no paso pero el restante da 0. Mismo razonamiento que
      ``expired``, distinto sintoma, y por eso distinto nombre: aca el problema puede ser
      la media y no la fecha.
    - ``inert``      -- el restante es mayor que las dos mitades: la fecha existe y no
      cambia nada. Es el caso de una fecha lejana y se distingue de ``applied`` para que
      el reporte no cuente como "topeado" a un SKU que no se movio.
    - ``applied``    -- al menos una mitad bajo.
    """
    rp = float(trigger) if trigger is not None else None
    oup = float(target) if target is not None else None
    if rp is None:
        # Una linea sin disparador no es una politica que se pueda topear.
        return HorizonCap(0.0, oup, "unusable", periods_remaining, None)

    if periods_remaining is None:
        return HorizonCap(rp, oup, "no_date", None, None)
    if mean_per_period is None or not _finito(mean_per_period) or mean_per_period < 0:
        return HorizonCap(rp, oup, "unusable", periods_remaining, None)
    if not _finito(periods_remaining):
        return HorizonCap(rp, oup, "unusable", None, None)
    if periods_remaining <= 0:
        return HorizonCap(rp, oup, "expired", periods_remaining, None)

    restante = float(mean_per_period) * float(periods_remaining)
    if restante <= 0:
        return HorizonCap(rp, oup, "would_zero", periods_remaining, restante)

    nuevo_rp = min(rp, restante)
    nuevo_oup = min(oup, restante) if oup is not None else None
    movio = nuevo_rp < rp or (oup is not None and nuevo_oup < oup)
    return HorizonCap(
        nuevo_rp, nuevo_oup, "applied" if movio else "inert", periods_remaining, restante,
    )


def _finito(x: float) -> bool:
    return x == x and x not in (float("inf"), float("-inf"))
