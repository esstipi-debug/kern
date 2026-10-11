"""Motor del pack fletes: metricas, elegibilidad, puntaje y recomendacion."""

from __future__ import annotations

from decimal import Decimal

import pytest
from _fletes_fixtures import envio, lote

from packs.fletes.engine import (
    MIN_DIAS_ANUALIZAR,
    MIN_ENVIOS_DEFAULT,
    MOTIVO_POCOS_ENVIOS,
    MOTIVO_SIN_FECHAS,
    RUTA_ACTUAL_ES_EL_MEJOR,
    RUTA_ACTUAL_NO_ELEGIBLE,
    RUTA_MENOS_DE_DOS,
    RUTA_SIN_ALTERNATIVA,
    Pesos,
    agrupar,
    comparar,
    puntuar,
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


def _ruta(envios, **kw):
    (ruta,) = agrupar(envios, **kw)
    return ruta


def test_pesos_por_defecto_y_validacion() -> None:
    assert Pesos() == Pesos(Decimal("0.6"), Decimal("0.4"))
    with pytest.raises(ValueError, match="sumar 1"):
        Pesos(Decimal("0.5"), Decimal("0.4"))
    with pytest.raises(ValueError, match="negativ"):
        Pesos(Decimal("1.2"), Decimal("-0.2"))


def test_puntaje_desglosado_contra_el_mejor_de_la_ruta() -> None:
    # A: 1.5/kg, OTD 0.8 (15 envios, 1500 kg) - B: 1.2/kg, OTD 1.0 (12 envios)
    envios = lote("A", 15, a_tiempo=12, costo="150") + lote("B", 12, costo="120")
    res = puntuar(_ruta(envios), Pesos())
    a = next(c for c in res.celdas if c.transportista == "A")
    b = next(c for c in res.celdas if c.transportista == "B")
    assert a.indice_costo == Decimal("1.2") / Decimal("1.5")
    assert a.indice_servicio == Decimal("0.8")
    assert a.puntaje == Decimal("0.6") * a.indice_costo + Decimal("0.4") * Decimal("0.8")
    assert (b.indice_costo, b.indice_servicio, b.puntaje) == (Decimal(1), Decimal(1), Decimal(1))
    assert [c.transportista for c in res.celdas] == ["B", "A"]


def test_recomienda_y_calcula_ahorro_y_delta_otd() -> None:
    envios = lote("A", 15, a_tiempo=12, costo="150", paso=4) + lote("B", 12, costo="120", paso=4)
    ruta = _ruta(envios)
    res = puntuar(ruta, Pesos())
    rec = res.recomendacion
    assert res.actual == "A" and res.motivo is None
    assert rec.mejor.transportista == "B" and rec.actual.transportista == "A"
    assert rec.ahorro_periodo == (Decimal("1.5") - Decimal("1.2")) * Decimal("1500")
    assert ruta.dias_periodo == 57
    assert rec.ahorro_anual == rec.ahorro_periodo * Decimal(365) / Decimal(57)
    assert rec.delta_otd_pp == (Decimal(1) - Decimal("0.8")) * 100


def test_ahorro_negativo_se_informa_con_signo() -> None:
    # B es mas caro pero mucho mas puntual; con peso de servicio alto gana igual
    envios = lote("A", 15, a_tiempo=5, costo="100", paso=4) + lote("B", 12, costo="110", paso=4)
    rec = puntuar(_ruta(envios), Pesos(Decimal("0.2"), Decimal("0.8"))).recomendacion
    assert rec.mejor.transportista == "B"
    assert rec.ahorro_periodo < 0


def test_periodo_corto_no_se_anualiza() -> None:
    envios = lote("A", 15, a_tiempo=12, costo="150") + lote("B", 12, costo="120")  # 15 dias
    rec = puntuar(_ruta(envios), Pesos()).recomendacion
    assert rec.dias_periodo < MIN_DIAS_ANUALIZAR
    assert rec.ahorro_anual is None
    assert rec.ahorro_periodo > 0


def test_un_solo_transportista_es_sin_alternativa() -> None:
    res = puntuar(_ruta(lote("A", 12)), Pesos())
    assert (res.recomendacion, res.motivo) == (None, RUTA_SIN_ALTERNATIVA)


def test_actual_sin_muestra_no_recibe_recomendacion() -> None:
    envios = lote("A", 30, fechados=5) + lote("B", 12) + lote("C", 12, costo="90")
    res = puntuar(_ruta(envios), Pesos())
    assert res.actual == "A"
    assert (res.recomendacion, res.motivo) == (None, RUTA_ACTUAL_NO_ELEGIBLE)


def test_menos_de_dos_elegibles() -> None:
    envios = lote("A", 12) + lote("B", 11, fechados=8, costo="90")
    res = puntuar(_ruta(envios), Pesos())
    assert (res.recomendacion, res.motivo) == (None, RUTA_MENOS_DE_DOS)


def test_actual_ya_es_el_mejor() -> None:
    envios = lote("A", 15, costo="100") + lote("B", 12, costo="150")
    res = puntuar(_ruta(envios), Pesos())
    assert (res.recomendacion, res.motivo) == (None, RUTA_ACTUAL_ES_EL_MEJOR)


def test_no_elegibles_quedan_sin_puntaje_y_al_final() -> None:
    envios = lote("A", 15) + lote("B", 12, costo="90") + lote("C", 4, costo="10")
    res = puntuar(_ruta(envios), Pesos())
    assert [c.transportista for c in res.celdas][-1] == "C"
    c = res.celdas[-1]
    assert (c.indice_costo, c.indice_servicio, c.puntaje) == (None, None, None)


def test_empate_de_puntaje_desempata_por_costo_y_despues_por_nombre() -> None:
    envios = lote("Beta", 12) + lote("Alfa", 12)  # identicos
    res = puntuar(_ruta(envios), Pesos())
    assert [c.transportista for c in res.celdas] == ["Alfa", "Beta"]


def test_actual_es_el_de_mas_kg_con_desempate_por_nombre() -> None:
    envios = lote("Beta", 12) + lote("Alfa", 12)
    assert puntuar(_ruta(envios), Pesos()).actual == "Alfa"


def test_costo_cero_tiene_indice_uno() -> None:
    envios = lote("A", 12, costo="0") + lote("B", 12, costo="100")
    res = puntuar(_ruta(envios), Pesos())
    a = next(c for c in res.celdas if c.transportista == "A")
    b = next(c for c in res.celdas if c.transportista == "B")
    assert a.indice_costo == Decimal(1)
    assert b.indice_costo == Decimal(0)


def test_puntajes_quedan_entre_cero_y_uno() -> None:
    envios = lote("A", 12, a_tiempo=0, costo="1000") + lote("B", 12, costo="1")
    for c in puntuar(_ruta(envios), Pesos()).celdas:
        assert Decimal(0) <= c.puntaje <= Decimal(1)


def test_comparar_arma_la_corrida_completa() -> None:
    envios = (lote("A", 15, a_tiempo=12, costo="150", paso=4) + lote("B", 12, costo="120", paso=4)
              + lote("N", 10, destino="Antofagasta"))
    comp = comparar(envios)
    assert comp.moneda == "CLP"
    assert comp.envios == 37
    assert comp.kg_total == Decimal(3700)
    assert comp.pesos == Pesos() and comp.min_envios == MIN_ENVIOS_DEFAULT
    assert [r.ruta for r in comp.rutas] == ["Santiago -> Antofagasta", "Santiago -> Concepcion"]
    assert len(comp.recomendaciones) == 1
    assert comp.rutas_comparables == 1
    assert comp.ahorro_anual_total == comp.recomendaciones[0].ahorro_anual


def test_ahorro_anual_total_ignora_los_no_anualizables() -> None:
    envios = lote("A", 15, a_tiempo=12, costo="150") + lote("B", 12, costo="120")
    assert comparar(envios).ahorro_anual_total == Decimal(0)


def test_comparar_rechaza_vacio_y_monedas_mezcladas() -> None:
    with pytest.raises(ValueError):
        comparar([])
    with pytest.raises(ValueError, match="moneda"):
        comparar([envio(1, "A"), envio(2, "A", dia=1, moneda="USD")])


def test_mismos_datos_mismo_resultado() -> None:
    envios = lote("A", 15, a_tiempo=12, costo="150", paso=4) + lote("B", 12, costo="120", paso=4)
    assert comparar(envios) == comparar(list(reversed(envios)))


def test_empate_de_puntaje_no_recomienda_mover_volumen() -> None:
    # Beta mueve mas kg y es identico a Alfa en costo y OTD: mover no mejora nada.
    envios = lote("Beta", 15) + lote("Alfa", 12)
    res = puntuar(_ruta(envios), Pesos())
    assert res.actual == "Beta"
    assert (res.recomendacion, res.motivo) == (None, RUTA_ACTUAL_ES_EL_MEJOR)


def test_con_peso_de_servicio_cero_un_empate_en_costo_no_mueve_volumen_a_peor_otd() -> None:
    envios = lote("Beta", 15) + lote("Alfa", 12, a_tiempo=6)
    res = puntuar(_ruta(envios), Pesos(Decimal(1), Decimal(0)))
    assert res.actual == "Beta"
    assert (res.recomendacion, res.motivo) == (None, RUTA_ACTUAL_ES_EL_MEJOR)


def test_ruta_sin_ningun_elegible_no_rompe_y_no_recomienda() -> None:
    res = puntuar(_ruta(lote("A", 4) + lote("B", 3, costo="90")), Pesos())
    assert (res.recomendacion, res.motivo) == (None, RUTA_ACTUAL_NO_ELEGIBLE)
    assert all(c.puntaje is None for c in res.celdas)
