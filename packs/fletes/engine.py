"""Motor del pack fletes: puro, sin reloj, Decimal en todo el calculo.

Agrupa envios por ruta (origen -> destino, dirigida) y transportista, mide costo/kg
y OTD, decide quien tiene muestra suficiente, puntua y recomienda. Se llama OTD y
no OTIF: el "in full" no esta en un archivo de fletes, y nombrarlo seria fabricarlo.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import date
from decimal import Decimal

from .loader import Envio, name_key

MIN_ENVIOS_DEFAULT = 10

MOTIVO_POCOS_ENVIOS = "pocos_envios"
MOTIVO_SIN_FECHAS = "sin_fechas"


@dataclass(frozen=True)
class Celda:
    """Un transportista en una ruta. Los tres ultimos campos los llena `puntuar`."""

    ruta: str
    transportista: str
    envios: int
    kg_total: Decimal
    costo_total: Decimal
    costo_kg: Decimal
    envios_fechados: int
    otd: Decimal | None
    elegible: bool
    motivo: str | None
    indice_costo: Decimal | None = None
    indice_servicio: Decimal | None = None
    puntaje: Decimal | None = None


@dataclass(frozen=True)
class RutaAgrupada:
    ruta: str
    celdas: tuple[Celda, ...]
    dias_periodo: int


def agrupar(envios: Sequence[Envio], min_envios: int = MIN_ENVIOS_DEFAULT) -> tuple[RutaAgrupada, ...]:
    if min_envios < 1:
        raise ValueError(f"min_envios debe ser >= 1 (es {min_envios})")

    nombres_ruta: dict[tuple[str, str], str] = {}
    fechas: dict[tuple[str, str], list[date]] = {}
    por_celda: dict[tuple[str, str], dict[str, list[Envio]]] = {}
    nombres_transp: dict[tuple[tuple[str, str], str], str] = {}

    for e in envios:
        rkey = (name_key(e.origen), name_key(e.destino))
        tkey = name_key(e.transportista)
        nombres_ruta.setdefault(rkey, f"{e.origen} -> {e.destino}")
        nombres_transp.setdefault((rkey, tkey), e.transportista)
        fechas.setdefault(rkey, []).append(e.fecha_despacho)
        por_celda.setdefault(rkey, {}).setdefault(tkey, []).append(e)

    rutas: list[RutaAgrupada] = []
    for rkey, celdas_envios in por_celda.items():
        nombre = nombres_ruta[rkey]
        celdas = sorted(
            (_celda(nombre, nombres_transp[(rkey, tkey)], lista, min_envios) for tkey, lista in celdas_envios.items()),
            key=lambda c: c.transportista.casefold(),
        )
        dias = (max(fechas[rkey]) - min(fechas[rkey])).days + 1
        rutas.append(RutaAgrupada(ruta=nombre, celdas=tuple(celdas), dias_periodo=dias))
    return tuple(sorted(rutas, key=lambda r: r.ruta.casefold()))


def _celda(ruta: str, transportista: str, envios: list[Envio], min_envios: int) -> Celda:
    kg = sum((e.kg for e in envios), Decimal(0))
    costo = sum((e.costo for e in envios), Decimal(0))
    fechados = [e for e in envios if e.fecha_prometida is not None and e.fecha_entrega is not None]
    a_tiempo = sum(1 for e in fechados if e.fecha_entrega <= e.fecha_prometida)  # type: ignore[operator]
    otd = Decimal(a_tiempo) / Decimal(len(fechados)) if fechados else None

    if len(fechados) >= min_envios:
        elegible, motivo = True, None
    elif not fechados:
        elegible, motivo = False, MOTIVO_SIN_FECHAS
    else:
        elegible, motivo = False, MOTIVO_POCOS_ENVIOS

    return Celda(
        ruta=ruta,
        transportista=transportista,
        envios=len(envios),
        kg_total=kg,
        costo_total=costo,
        costo_kg=costo / kg,
        envios_fechados=len(fechados),
        otd=otd,
        elegible=elegible,
        motivo=motivo,
    )


MIN_DIAS_ANUALIZAR = 28

RUTA_SIN_ALTERNATIVA = "sin_alternativa"
RUTA_ACTUAL_NO_ELEGIBLE = "actual_no_elegible"
RUTA_MENOS_DE_DOS = "menos_de_dos_elegibles"
RUTA_ACTUAL_ES_EL_MEJOR = "actual_ya_es_el_mejor"


@dataclass(frozen=True)
class Pesos:
    costo: Decimal = Decimal("0.6")
    servicio: Decimal = Decimal("0.4")

    def __post_init__(self) -> None:
        if self.costo < 0 or self.servicio < 0:
            raise ValueError("los pesos no pueden ser negativos")
        if self.costo + self.servicio != 1:
            raise ValueError(f"los pesos deben sumar 1 (suman {self.costo + self.servicio})")


@dataclass(frozen=True)
class Recomendacion:
    ruta: str
    actual: Celda
    mejor: Celda
    ahorro_periodo: Decimal
    ahorro_anual: Decimal | None
    dias_periodo: int
    delta_otd_pp: Decimal


@dataclass(frozen=True)
class ResultadoRuta:
    ruta: str
    celdas: tuple[Celda, ...]
    actual: str
    dias_periodo: int
    recomendacion: Recomendacion | None
    motivo: str | None


@dataclass(frozen=True)
class Comparacion:
    rutas: tuple[ResultadoRuta, ...]
    moneda: str
    pesos: Pesos
    min_envios: int
    envios: int
    kg_total: Decimal

    @property
    def recomendaciones(self) -> tuple[Recomendacion, ...]:
        return tuple(r.recomendacion for r in self.rutas if r.recomendacion is not None)

    @property
    def ahorro_anual_total(self) -> Decimal:
        return sum(
            (rec.ahorro_anual for rec in self.recomendaciones if rec.ahorro_anual is not None),
            Decimal(0),
        )

    @property
    def rutas_comparables(self) -> int:
        return sum(1 for r in self.rutas if sum(1 for c in r.celdas if c.elegible) >= 2)


def puntuar(ruta: RutaAgrupada, pesos: Pesos) -> ResultadoRuta:
    ranking = sorted(
        _puntuar_elegibles([c for c in ruta.celdas if c.elegible], pesos),
        key=lambda c: (-c.puntaje, c.costo_kg, c.transportista.casefold()),  # type: ignore[operator]
    )
    no_elegibles = [c for c in ruta.celdas if not c.elegible]
    actual = sorted(ruta.celdas, key=lambda c: (-c.kg_total, c.transportista.casefold()))[0]

    motivo = _motivo(ruta, actual, ranking)
    recomendacion = None
    if motivo is None:
        recomendacion = _recomendacion(ruta, _en_ranking(ranking, actual.transportista), ranking[0])

    return ResultadoRuta(
        ruta=ruta.ruta,
        celdas=(*ranking, *no_elegibles),
        actual=actual.transportista,
        dias_periodo=ruta.dias_periodo,
        recomendacion=recomendacion,
        motivo=motivo,
    )


def comparar(
    envios: Sequence[Envio],
    pesos: Pesos = Pesos(),
    min_envios: int = MIN_ENVIOS_DEFAULT,
) -> Comparacion:
    if not envios:
        raise ValueError("no hay envios para comparar")
    monedas = sorted({e.moneda for e in envios})
    if len(monedas) > 1:
        raise ValueError(f"mas de una moneda ({', '.join(monedas)}): no se convierte")
    return Comparacion(
        rutas=tuple(puntuar(r, pesos) for r in agrupar(envios, min_envios)),
        moneda=monedas[0],
        pesos=pesos,
        min_envios=min_envios,
        envios=len(envios),
        kg_total=sum((e.kg for e in envios), Decimal(0)),
    )


def _puntuar_elegibles(elegibles: list[Celda], pesos: Pesos) -> list[Celda]:
    if not elegibles:
        return []
    minimo = min(c.costo_kg for c in elegibles)
    puntuadas: list[Celda] = []
    for c in elegibles:
        indice_costo = Decimal(1) if c.costo_kg == 0 else minimo / c.costo_kg
        indice_servicio = c.otd  # elegible => tiene envios fechados => otd no es None
        puntaje = pesos.costo * indice_costo + pesos.servicio * indice_servicio  # type: ignore[operator]
        puntuadas.append(replace(c, indice_costo=indice_costo, indice_servicio=indice_servicio, puntaje=puntaje))
    return puntuadas


def _motivo(ruta: RutaAgrupada, actual: Celda, ranking: list[Celda]) -> str | None:
    if len(ruta.celdas) == 1:
        return RUTA_SIN_ALTERNATIVA
    if not actual.elegible:
        return RUTA_ACTUAL_NO_ELEGIBLE
    if len(ranking) < 2:
        return RUTA_MENOS_DE_DOS
    # Mover volumen exige una mejora ESTRICTA de puntaje: con un empate el desempate por
    # nombre elegiria un "mejor" que no mejora nada (ahorro 0, o peor OTD si peso_servicio=0).
    if ranking[0].puntaje <= _en_ranking(ranking, actual.transportista).puntaje:  # type: ignore[operator]
        return RUTA_ACTUAL_ES_EL_MEJOR
    return None


def _en_ranking(ranking: list[Celda], transportista: str) -> Celda:
    return next(c for c in ranking if c.transportista == transportista)


def _recomendacion(ruta: RutaAgrupada, actual: Celda, mejor: Celda) -> Recomendacion:
    ahorro = (actual.costo_kg - mejor.costo_kg) * actual.kg_total
    anual = (
        ahorro * Decimal(365) / Decimal(ruta.dias_periodo)
        if ruta.dias_periodo >= MIN_DIAS_ANUALIZAR
        else None
    )
    return Recomendacion(
        ruta=ruta.ruta,
        actual=actual,
        mejor=mejor,
        ahorro_periodo=ahorro,
        ahorro_anual=anual,
        dias_periodo=ruta.dias_periodo,
        delta_otd_pp=(mejor.otd - actual.otd) * 100,  # type: ignore[operator]
    )
