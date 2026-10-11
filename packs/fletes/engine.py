"""Motor del pack fletes: puro, sin reloj, Decimal en todo el calculo.

Agrupa envios por ruta (origen -> destino, dirigida) y transportista, mide costo/kg
y OTD, decide quien tiene muestra suficiente, puntua y recomienda. Se llama OTD y
no OTIF: el "in full" no esta en un archivo de fletes, y nombrarlo seria fabricarlo.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
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
