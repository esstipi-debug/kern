"""Motor del pack fletes: metricas, elegibilidad, puntaje y recomendacion."""

from __future__ import annotations

from decimal import Decimal

import pytest
from _fletes_fixtures import envio, lote

from packs.fletes.engine import (
    MIN_ENVIOS_DEFAULT,
    MOTIVO_POCOS_ENVIOS,
    MOTIVO_SIN_FECHAS,
    agrupar,
)


def _celda(rutas, ruta, transportista):
    (r,) = [x for x in rutas if x.ruta == ruta]
    (c,) = [x for x in r.celdas if x.transportista == transportista]
    return c


def test_costo_kg_es_cociente_de_sumas_no_promedio_de_cocientes() -> None:
    envios = [envio(1, "A", costo="100", kg="100"), envio(2, "A", costo="300", kg="100", dia=1),
              envio(3, "A", costo="10", kg="800", dia=2)]
    c = _celda(agrupar(envios), "Santiago -> Concepcion", "A")
    assert c.kg_total == Decimal("1000")
    assert c.costo_total == Decimal("410")
    assert c.costo_kg == Decimal("410") / Decimal("1000")  # promedio de cocientes daria 1.3375


def test_otd_cuenta_solo_envios_con_las_dos_fechas() -> None:
    envios = lote("A", 12, fechados=10, a_tiempo=8)
    c = _celda(agrupar(envios), "Santiago -> Concepcion", "A")
    assert c.envios == 12
    assert c.envios_fechados == 10
    assert c.otd == Decimal(8) / Decimal(10)


def test_llegar_el_dia_prometido_es_a_tiempo() -> None:
    c = _celda(agrupar([envio(1, "A", prometida=2, entrega=2)]), "Santiago -> Concepcion", "A")
    assert c.otd == Decimal(1)


def test_sin_fechas_el_otd_es_none_nunca_cero() -> None:
    c = _celda(agrupar(lote("A", 12, fechados=0)), "Santiago -> Concepcion", "A")
    assert c.otd is None
    assert not c.elegible
    assert c.motivo == MOTIVO_SIN_FECHAS


def test_elegibilidad_cuenta_envios_fechados_no_envios() -> None:
    rutas = agrupar(lote("A", 15, fechados=9) + lote("B", 10, dia0=100))
    a = _celda(rutas, "Santiago -> Concepcion", "A")
    b = _celda(rutas, "Santiago -> Concepcion", "B")
    assert (a.elegible, a.motivo) == (False, MOTIVO_POCOS_ENVIOS)
    assert (b.elegible, b.motivo) == (True, None)


def test_min_envios_es_parametro() -> None:
    c = _celda(agrupar(lote("A", 3), min_envios=3), "Santiago -> Concepcion", "A")
    assert c.elegible
    assert MIN_ENVIOS_DEFAULT == 10


def test_min_envios_menor_a_uno_se_rechaza() -> None:
    with pytest.raises(ValueError):
        agrupar(lote("A", 3), min_envios=0)


def test_la_ruta_es_dirigida() -> None:
    envios = [envio(1, "A"), envio(2, "A", origen="Concepcion", destino="Santiago")]
    assert [r.ruta for r in agrupar(envios)] == ["Concepcion -> Santiago", "Santiago -> Concepcion"]


def test_transportista_se_agrupa_sin_mayusculas() -> None:
    envios = [envio(1, "Chilexpress"), envio(2, "CHILEXPRESS", dia=1)]
    (ruta,) = agrupar(envios)
    (c,) = ruta.celdas
    assert c.transportista == "Chilexpress"  # grafia de la primera aparicion
    assert c.envios == 2


def test_origen_y_destino_se_normalizan_como_el_transportista() -> None:
    envios = [envio(1, "A", origen="Santiago"), envio(2, "A", origen="SANTIAGO", destino="concepcion", dia=1)]
    (ruta,) = agrupar(envios)
    assert ruta.ruta == "Santiago -> Concepcion"
    assert ruta.celdas[0].envios == 2


def test_dias_periodo_va_de_la_primera_a_la_ultima_fecha_de_la_ruta_inclusive() -> None:
    envios = lote("A", 3, dia0=0, paso=10) + lote("B", 2, dia0=5, paso=40)  # dias 0,10,20 y 5,45
    (ruta,) = agrupar(envios)
    assert ruta.dias_periodo == 46


def test_orden_estable_de_rutas_y_celdas() -> None:
    envios = [envio(1, "zeta", destino="b"), envio(2, "Alfa", destino="b"), envio(3, "A", destino="a")]
    rutas = agrupar(envios)
    assert [r.ruta for r in rutas] == ["Santiago -> a", "Santiago -> b"]
    assert [c.transportista for c in rutas[1].celdas] == ["Alfa", "zeta"]


def test_el_motor_no_muta_la_entrada() -> None:
    envios = tuple(lote("A", 12))
    antes = list(envios)
    agrupar(envios)
    assert list(envios) == antes
