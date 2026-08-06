"""Agrega muchas corridas de ``simworld`` en una distribucion, no en una anecdota.

**El problema que resuelve.** ``examples/run_world_sim.run_grid`` corre UN mundo
y devuelve la captura de Kern en el. Los tests fijan cinco semillas concretas
(3 a 7). Con eso se puede afirmar "en la semilla 4, Kern capturo el 58%", que no
es una propiedad de Kern: es una propiedad de la semilla 4. Un comprador que
pregunte "y en mi negocio?" no tiene respuesta.

Con N mundos la afirmacion cambia de naturaleza: "Kern captura 58% +/- 9% de la
brecha entre un punto de reorden fijo y un oraculo que ve el futuro, sobre 200
mundos, y su peor mundo fue 31%". Eso si es una propiedad del sistema, y el peor
caso es lo que un comprador realmente compra.

**Por que las corridas no medibles se cuentan en vez de descartarse.** Un mundo
donde el piso no cuesta mas que el techo (``span <= 0``) no discrimina, y
``run_grid`` devuelve ``capture=None`` con su nota. Promediar sobre los que si
midieron y callar cuantos no, convierte "la mitad de los mundos no distinguen a
Kern de un punto de reorden fijo" en una media limpia y falsa. Aqui la cobertura
viaja en el resultado y quien lea el numero ve sobre cuantos mundos se calculo.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Iterable, Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Distribucion:
    """La captura de un carril y nivel de suciedad sobre N mundos."""

    carril: str
    nivel: int
    n_mundos: int
    n_medibles: int
    media: float | None
    desviacion: float | None
    p10: float | None
    mediana: float | None
    p90: float | None
    peor: float | None
    mejor: float | None

    @property
    def cobertura(self) -> float:
        """Fraccion de mundos donde la captura fue medible."""
        return self.n_medibles / self.n_mundos if self.n_mundos else 0.0

    def frase(self) -> str:
        """La afirmacion vendible, o la razon por la que no hay ninguna."""
        if self.media is None:
            return (
                f"L{self.nivel}/{self.carril}: ninguno de los {self.n_mundos} mundos "
                "fue medible (piso no mas caro que techo)"
            )
        sd = f" +/- {self.desviacion * 100:.1f}%" if self.desviacion is not None else ""
        return (
            f"L{self.nivel}/{self.carril}: captura {self.media * 100:.1f}%{sd} "
            f"· peor mundo {self.peor * 100:.1f}% "
            f"· {self.n_medibles}/{self.n_mundos} mundos medibles"
        )


def _percentil(orden: Sequence[float], q: float) -> float:
    """Percentil por interpolacion lineal sobre la muestra ya ordenada.

    Se implementa aqui en vez de tirar de numpy porque este modulo lo consume
    tanto el CLI como los tests, y una dependencia de numpy en la agregacion
    haria que un fallo de percentil parezca un fallo de simulacion.
    """
    if not orden:
        raise ValueError("muestra vacia")
    if len(orden) == 1:
        return orden[0]
    pos = q * (len(orden) - 1)
    bajo = math.floor(pos)
    alto = math.ceil(pos)
    if bajo == alto:
        return orden[bajo]
    return orden[bajo] + (orden[alto] - orden[bajo]) * (pos - bajo)


def agregar(reportes: Iterable[dict]) -> tuple[Distribucion, ...]:
    """Convierte una coleccion de reportes de ``run_grid`` en distribuciones.

    Agrupa por (carril, nivel de suciedad). Cada reporte aporta un mundo, asi que
    ``n_mundos`` es el numero de semillas que llegaron a ese grupo -- si una
    semilla no produjo una entrada para un nivel, ese nivel tendra menos mundos
    y el numero lo dira, en vez de que la media se calcule sobre una muestra
    distinta de la anunciada.
    """
    por_grupo: dict[tuple[str, int], list[float | None]] = {}
    for rep in reportes:
        for entrada in rep["kern"].values():
            clave = (entrada["lane"], entrada["level"])
            por_grupo.setdefault(clave, []).append(entrada["capture"])

    out: list[Distribucion] = []
    for (carril, nivel), capturas in sorted(por_grupo.items(), key=lambda kv: (kv[0][1], kv[0][0])):
        medibles = sorted(c for c in capturas if c is not None)
        if not medibles:
            out.append(Distribucion(
                carril=carril, nivel=nivel, n_mundos=len(capturas), n_medibles=0,
                media=None, desviacion=None, p10=None, mediana=None, p90=None,
                peor=None, mejor=None,
            ))
            continue
        out.append(Distribucion(
            carril=carril, nivel=nivel,
            n_mundos=len(capturas), n_medibles=len(medibles),
            media=statistics.fmean(medibles),
            desviacion=statistics.stdev(medibles) if len(medibles) > 1 else None,
            p10=_percentil(medibles, 0.10),
            mediana=_percentil(medibles, 0.50),
            p90=_percentil(medibles, 0.90),
            peor=medibles[0], mejor=medibles[-1],
        ))
    return tuple(out)


def degrada_con_la_suciedad(dists: Sequence[Distribucion], carril: str) -> list[str]:
    """Comprueba que mas suciedad no MEJORE la captura, y devuelve las anomalias.

    No se exige que empeore -- Kern limpia parte de la suciedad a proposito, asi
    que un nivel 1 casi igual al 0 es exito, no fallo. Lo que no puede pasar es
    que ensuciar los datos haga a Kern *mejor*: eso significa que el nivel de
    suciedad esta entrando en la decision por una via que nadie diseno, y es el
    sintoma clasico de una fuga de informacion entre el generador de suciedad y
    el marcador.
    """
    del_carril = sorted(
        (d for d in dists if d.carril == carril and d.media is not None),
        key=lambda d: d.nivel,
    )
    anomalias: list[str] = []
    for previo, siguiente in zip(del_carril, del_carril[1:]):
        margen = _margen(previo, siguiente)
        if siguiente.media > previo.media + margen:
            anomalias.append(
                f"{carril}: L{siguiente.nivel} captura {siguiente.media * 100:.1f}% "
                f"> L{previo.nivel} {previo.media * 100:.1f}% "
                f"(margen {margen * 100:.1f}%) -- ensuciar mejoro el resultado"
            )
    return anomalias


#: Suelo del margen, en fraccion de captura. Por debajo de un punto porcentual
#: la diferencia no es interpretable ni con muestra infinita.
MARGEN_MINIMO = 0.01
#: Cuantos errores estandar de la diferencia hacen falta para llamarlo anomalia.
#: Dos deja el falso positivo en torno al 5%, que para una alarma que obliga a
#: investigar es el equilibrio razonable.
SIGMAS = 2.0


def _margen(a: Distribucion, b: Distribucion) -> float:
    """Cuanto tiene que mejorar la captura para no ser ruido de muestreo.

    **Por que no un umbral fijo.** La primera version usaba un punto porcentual
    constante y disparo con tres mundos y una desviacion del 11%: el error
    estandar de la media era ~6 puntos, asi que casi cualquier diferencia
    superaba el umbral. Una alarma que salta con la muestra pequena entrena a
    quien la lee a ignorarla, que es la peor forma de perder una verificacion.

    El margen escala con el error estandar de la diferencia de medias, asi que
    barrer mas mundos vuelve la alarma mas sensible -- que es justo el incentivo
    correcto: para poder afirmar algo, muestrea mas.
    """
    def se(d: Distribucion) -> float:
        if d.desviacion is None or d.n_medibles < 2:
            return float("inf")
        return d.desviacion / math.sqrt(d.n_medibles)

    se_dif = math.hypot(se(a), se(b))
    if not math.isfinite(se_dif):
        # Sin muestra para estimar el ruido no se puede distinguir senal de
        # azar. No se inventa una alarma: se calla y el informe ya publica
        # n_medibles para que quien lea sepa por que.
        return float("inf")
    return max(MARGEN_MINIMO, SIGMAS * se_dif)
