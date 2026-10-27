"""Constructores de envios para los tests del pack fletes."""

from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal

from packs.fletes.loader import Envio

INICIO = date(2026, 1, 1)


def envio(
    n: int,
    transportista: str,
    *,
    origen: str = "Santiago",
    destino: str = "Concepcion",
    dia: int = 0,
    costo: str = "100",
    kg: str = "100",
    prometida: int | None = 2,
    entrega: int | None = 2,
    moneda: str = "CLP",
) -> Envio:
    """Un envio; `prometida`/`entrega` son dias despues del despacho (None = sin fecha)."""
    despacho = INICIO + timedelta(days=dia)
    return Envio(
        envio_id=f"{transportista}|{origen}|{destino}|{n}",
        transportista=transportista,
        origen=origen,
        destino=destino,
        fecha_despacho=despacho,
        costo=Decimal(costo),
        moneda=moneda,
        kg=Decimal(kg),
        fecha_prometida=None if prometida is None else despacho + timedelta(days=prometida),
        fecha_entrega=None if entrega is None else despacho + timedelta(days=entrega),
    )


def lote(
    transportista: str,
    n: int,
    *,
    a_tiempo: int | None = None,
    fechados: int | None = None,
    dia0: int = 0,
    paso: int = 1,
    **kw: str,
) -> list[Envio]:
    """n envios cada `paso` dias desde `dia0`. Los primeros `fechados` llevan fechas;
    de esos, los primeros `a_tiempo` llegan a tiempo y el resto un dia tarde."""
    fechados = n if fechados is None else fechados
    a_tiempo = fechados if a_tiempo is None else a_tiempo
    out: list[Envio] = []
    for i in range(n):
        if i >= fechados:
            out.append(envio(i, transportista, dia=dia0 + i * paso, prometida=None, entrega=None, **kw))
        else:
            out.append(envio(i, transportista, dia=dia0 + i * paso, entrega=2 if i < a_tiempo else 3, **kw))
    return out
