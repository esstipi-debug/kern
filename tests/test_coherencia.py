"""Cada invariante de coherencia tiene que MORDER.

Un invariante que no puede fallar no vale nada, y es peor que no tenerlo: da la
sensacion de cobertura sin la cobertura. Por eso cada test aqui construye una
violacion deliberada y confirma que el invariante la nombra -- el mismo patron
con el que se prueban los guardarrailes de frontera del core.

El reporte sano de referencia sale del pipeline de verdad sobre una cartera de
verdad, no de un dataclass a mano: si el contrato de ``JobReport`` cambia, estos
tests tienen que romperse, no seguir verdes sobre una maqueta obsoleta.
"""

from __future__ import annotations

import dataclasses

import pandas as pd
import pytest

from jobs import inventory_optimization
from src.verify.carteras import generar_cartera
from src.verify.coherencia import (
    POLITICAS_SALTADAS,
    nombres_de_invariantes,
    verificar,
)


@pytest.fixture(scope="module")
def reporte_sano():
    """Una cartera pequena pero con los CUATRO degenerados dentro.

    El generador produce los dos degenerados de DATO (demanda cero, costo cero).
    Los otros dos son de CONOCIMIENTO y hay que fabricarlos, y hoy son dos y no uno
    porque el motor tiene un escalon donde antes tenia un abismo:

    - ``SKU-RECIEN`` ve tres periodos -- menos que ``MIN_PERIODS_FOR_POLICY`` pero
      al menos ``MIN_PERIODS_FOR_COVER`` --, asi que recibe una COBERTURA
      PROVISIONAL: disparador si, buffer no.
    - ``SKU-PRIMERA-VENTA`` ve UNO solo, que no alcanza ni para una media, y por eso
      sigue siendo la negativa entera (``insufficient_history``).

    Las dos entraron a ``POLITICAS_SALTADAS`` -- que desde el 2026-09-20 es
    ``NOT_FULLY_SIZED_POLICY_KINDS`` -- porque las invariantes por SKU presuponen
    buffer y z. Sin estas dos lineas el invariante de conteo volveria a probarse en
    vacio sobre las clases recien agregadas.
    """
    demanda = generar_cartera(24, semanas=40, semilla=11)
    fechas = sorted(demanda["date"].unique())
    nuevas = pd.DataFrame(
        [{"date": f, "product_id": "SKU-RECIEN", "quantity": 12.0,
          "unit_cost": 55.0, "lead_time_days": 14} for f in fechas[-3:]]
        + [{"date": fechas[-1], "product_id": "SKU-PRIMERA-VENTA", "quantity": 9.0,
            "unit_cost": 55.0, "lead_time_days": 14}]
    )
    return inventory_optimization.run(pd.concat([demanda, nuevas], ignore_index=True))


def _analizada(reporte):
    """La primera recomendacion que el job realmente analizo."""
    for i, r in enumerate(reporte.recommendations):
        if r.policy_kind not in POLITICAS_SALTADAS:
            return i, r
    raise AssertionError("la cartera de prueba no produjo ninguna recomendacion analizada")


def _con_rec(reporte, indice, **cambios):
    """Copia del reporte con una recomendacion alterada."""
    recs = list(reporte.recommendations)
    recs[indice] = dataclasses.replace(recs[indice], **cambios)
    return dataclasses.replace(reporte, recommendations=recs)


def _invariantes_violados(coh) -> set[str]:
    return {v.invariante for v in coh.violaciones}


# --------------------------------------------------------------------------
# La linea base: sobre datos sanos no puede saltar nada
# --------------------------------------------------------------------------


def test_una_cartera_sana_no_viola_ningun_invariante(reporte_sano):
    coh = verificar(reporte_sano)
    assert coh.coherente, coh.resumen()
    assert coh.n_recomendaciones == reporte_sano.n_skus


def test_la_cartera_de_prueba_ejerce_TODAS_las_ramas_de_salto(reporte_sano):
    """Si no hay saltadas, el invariante de conteo se estaria probando en vacio.

    Se compara contra el conjunto ENTERO, no contra una lista escrita a mano, para
    que agregar una policy_kind saltada obligue a producirla aca tambien.
    """
    politicas = {r.policy_kind for r in reporte_sano.recommendations}
    assert POLITICAS_SALTADAS <= politicas, (
        f"la cartera no produjo todos los degenerados; faltan: "
        f"{sorted(POLITICAS_SALTADAS - politicas)}; vistas: {sorted(politicas)}"
    )


def test_todos_los_invariantes_registrados_se_corren(reporte_sano):
    coh = verificar(reporte_sano)
    assert set(coh.invariantes_corridos) == set(nombres_de_invariantes())
    assert len(coh.invariantes_corridos) >= 13


# --------------------------------------------------------------------------
# Cada invariante, con su violacion deliberada
# --------------------------------------------------------------------------


def test_buffer_negativo_se_detecta(reporte_sano):
    i, _ = _analizada(reporte_sano)
    coh = verificar(_con_rec(reporte_sano, i, safety_stock=-1.0))
    assert "buffer_no_negativo" in _invariantes_violados(coh)


def test_rop_por_debajo_del_buffer_se_detecta(reporte_sano):
    i, r = _analizada(reporte_sano)
    coh = verificar(_con_rec(reporte_sano, i,
                             safety_stock=r.reorder_point + 10.0))
    assert "rop_cubre_el_buffer" in _invariantes_violados(coh)


@pytest.mark.parametrize("valor", [0.0, 1.0, 1.5, -0.2])
def test_servicio_fuera_de_cero_uno_se_detecta(reporte_sano, valor):
    i, _ = _analizada(reporte_sano)
    coh = verificar(_con_rec(reporte_sano, i, service_level=valor))
    assert "servicio_es_probabilidad" in _invariantes_violados(coh)


@pytest.mark.parametrize("valor", [float("nan"), float("inf"), float("-inf")])
def test_nan_e_infinito_se_detectan(reporte_sano, valor):
    i, _ = _analizada(reporte_sano)
    coh = verificar(_con_rec(reporte_sano, i, forecast=valor))
    assert "sin_nan_ni_infinito" in _invariantes_violados(coh)


def test_un_order_quantity_no_finito_se_detecta_aunque_el_campo_sea_opcional(reporte_sano):
    """None es legitimo; NaN no. La distincion es el punto del invariante."""
    i, _ = _analizada(reporte_sano)
    coh = verificar(_con_rec(reporte_sano, i, policy_kind="(R, S)",
                             order_up_to=float("nan"), order_quantity=None))
    assert "sin_nan_ni_infinito" in _invariantes_violados(coh)


def test_sq_sin_cantidad_de_pedido_se_detecta(reporte_sano):
    i, _ = _analizada(reporte_sano)
    coh = verificar(_con_rec(reporte_sano, i, policy_kind="(s, Q)",
                             order_quantity=None))
    assert "la_politica_trae_su_parametro" in _invariantes_violados(coh)


def test_rs_sin_nivel_objetivo_se_detecta(reporte_sano):
    i, _ = _analizada(reporte_sano)
    coh = verificar(_con_rec(reporte_sano, i, policy_kind="(R, S)",
                             order_up_to=None, order_quantity=None))
    assert "la_politica_trae_su_parametro" in _invariantes_violados(coh)


def test_una_politica_desconocida_se_detecta(reporte_sano):
    """Una politica nueva sin invariante propio no puede pasar en silencio."""
    i, _ = _analizada(reporte_sano)
    coh = verificar(_con_rec(reporte_sano, i, policy_kind="(t, U)"))
    assert "la_politica_trae_su_parametro" in _invariantes_violados(coh)


def test_la_descomposicion_de_inversion_que_no_cuadra_se_detecta(reporte_sano):
    i, r = _analizada(reporte_sano)
    coh = verificar(_con_rec(reporte_sano, i, investment=r.investment + 1_000.0))
    assert "la_inversion_por_sku_cuadra" in _invariantes_violados(coh)


def test_costo_unitario_no_positivo_en_una_analizada_se_detecta(reporte_sano):
    i, _ = _analizada(reporte_sano)
    coh = verificar(_con_rec(reporte_sano, i, unit_cost=0.0))
    assert "el_costo_unitario_es_positivo" in _invariantes_violados(coh)


def test_un_sku_que_desaparece_del_conteo_se_detecta(reporte_sano):
    manipulado = dataclasses.replace(reporte_sano, n_skus=reporte_sano.n_skus - 1)
    coh = verificar(manipulado)
    assert "nadie_desaparece" in _invariantes_violados(coh)


def test_el_agregado_que_no_cuadra_con_el_detalle_se_detecta(reporte_sano):
    manipulado = dataclasses.replace(
        reporte_sano,
        requested_investment=reporte_sano.requested_investment * 1.05,
    )
    coh = verificar(manipulado)
    assert "el_agregado_cuadra_con_el_detalle" in _invariantes_violados(coh)


def test_un_final_por_encima_del_pedido_se_detecta(reporte_sano):
    manipulado = dataclasses.replace(
        reporte_sano,
        final_investment=reporte_sano.requested_investment * 2.0,
    )
    coh = verificar(manipulado)
    assert "el_recorte_respeta_el_suelo" in _invariantes_violados(coh)


def test_un_recorte_por_debajo_del_suelo_de_ciclo_se_detecta(reporte_sano):
    manipulado = dataclasses.replace(
        reporte_sano, feasible=True,
        final_investment=reporte_sano.cycle_floor * 0.5,
    )
    coh = verificar(manipulado)
    assert "el_recorte_respeta_el_suelo" in _invariantes_violados(coh)


@pytest.mark.parametrize("escala", [-0.1, 1.5])
def test_una_escala_de_buffer_fuera_de_rango_se_detecta(reporte_sano, escala):
    manipulado = dataclasses.replace(reporte_sano, safety_stock_scale=escala)
    coh = verificar(manipulado)
    assert "el_recorte_respeta_el_suelo" in _invariantes_violados(coh)


def test_declararse_factible_pasandose_del_tope_se_detecta(reporte_sano):
    """El unico modo que tiene este job de mentirle a finanzas, y es silencioso."""
    manipulado = dataclasses.replace(
        reporte_sano, budget=1_000.0, feasible=True,
        final_investment=50_000.0,
    )
    coh = verificar(manipulado)
    assert "el_presupuesto_se_respeta" in _invariantes_violados(coh)


def test_sin_presupuesto_el_invariante_de_tope_no_opina(reporte_sano):
    """No declarar tope no puede convertirse en violar un tope inexistente."""
    manipulado = dataclasses.replace(reporte_sano, budget=None)
    coh = verificar(manipulado, solo=["el_presupuesto_se_respeta"])
    assert coh.coherente


def test_un_conteo_derivado_que_no_cuadra_se_detecta(reporte_sano):
    manipulado = dataclasses.replace(reporte_sano,
                                     n_intermittent=reporte_sano.n_intermittent + 3)
    coh = verificar(manipulado)
    assert "los_conteos_derivados_cuadran" in _invariantes_violados(coh)


def test_un_sku_duplicado_se_detecta(reporte_sano):
    """Imposible de producir con dos SKU; trivial de producir con miles."""
    recs = list(reporte_sano.recommendations)
    recs.append(recs[0])
    manipulado = dataclasses.replace(reporte_sano, recommendations=recs,
                                     n_skus=len(recs))
    coh = verificar(manipulado)
    assert "los_identificadores_son_unicos" in _invariantes_violados(coh)


# --------------------------------------------------------------------------
# El arnes en si
# --------------------------------------------------------------------------


def test_pedir_un_invariante_inexistente_falla_fuerte(reporte_sano):
    """Un typo en el nombre no puede silenciar una comprobacion."""
    with pytest.raises(KeyError):
        verificar(reporte_sano, solo=["invariante_que_no_existe"])


def test_la_evidencia_nombra_el_sku_y_el_valor(reporte_sano):
    i, r = _analizada(reporte_sano)
    coh = verificar(_con_rec(reporte_sano, i, safety_stock=-7.5))
    v = next(v for v in coh.violaciones if v.invariante == "buffer_no_negativo")
    assert v.ambito == r.product_id
    assert "-7.5" in v.evidencia


def test_el_resumen_reporta_cuantas_saltadas_hubo(reporte_sano):
    coh = verificar(reporte_sano)
    saltadas = sum(1 for r in reporte_sano.recommendations
                   if r.policy_kind in POLITICAS_SALTADAS)
    assert coh.n_saltadas == saltadas
    assert f"{saltadas} saltadas" in coh.resumen()
