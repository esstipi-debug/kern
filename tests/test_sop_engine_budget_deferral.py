"""Tests de regresion para src/sop_engine/budget_deferral.py.

Lo que defienden, en una linea: la lista de buffers diferidos es una lista de
CORTES, y un corte que nadie hizo -- porque la linea no costaba nada, o porque
la financiaron entera y el float dejo migas -- no puede aparecer en ella. Un
comprador trabaja esa lista llamando proveedores; cada nombre de mas es una
llamada de mas.
"""

from __future__ import annotations

import pytest

from src.constraints import InventoryItem, allocate_under_budget
from src.sop_engine.budget_deferral import defer_by_priority, deferral_order
from src.sop_engine.demand_plan import NO_SHIFT_SOURCE, DemandPlanLine
from src.sop_engine.purchase_plan import (
    PurchasePlanLine,
    SkuPurchaseInputs,
    build_purchase_plan,
)


def _plan(*skus: tuple[str, float, float, float, float], budget: float | None = None):
    """(product_id, demanda, on_hand, unit_cost, reorder_point) -> lineas del plan."""
    demanda = tuple(
        DemandPlanLine(pid, dem, 0.0, dem, NO_SHIFT_SOURCE, "sin shift")
        for pid, dem, _, _, _ in skus
    )
    entradas = {
        pid: SkuPurchaseInputs(pid, on_hand=oh, unit_cost=uc, reorder_point=rp)
        for pid, _, oh, uc, rp in skus
    }
    lineas, _ = build_purchase_plan(demanda, entradas, budget=budget)
    return lineas


# ---- (a) un buffer que no consume presupuesto no se puede diferir ----

def test_un_buffer_de_costo_cero_se_conserva_entero_en_vez_de_borrarse():
    """Reproduccion del hallazgo (a).

    `_funded_buffers` saltaba la linea de unit_cost <= 0 con un `continue` que
    dejaba granted[i] en 0.0, o sea BORRABA el buffer entero -- lo contrario de
    lo que decia su propio comentario ("no consume presupuesto y no necesita
    decision"). Un consignado / muestra gratis salia diferido por un cap que
    nunca tuvo un peso que reclamarle.

    Antes del fix: FREE=0.0 (allocate_under_budget le habia dado 20.0).
    """
    items = [InventoryItem("FREE", 0.0, 50.0, 0.0), InventoryItem("PAID", 0.0, 50.0, 10.0)]
    proporcional = allocate_under_budget(items, 200.0)
    assert {i.product_id: i.safety_stock for i in proporcional.items} == {
        "FREE": pytest.approx(20.0), "PAID": pytest.approx(20.0),
    }

    por_prioridad = defer_by_priority(proporcional, items)
    resultado = {i.product_id: i.safety_stock for i in por_prioridad.items}
    assert resultado["FREE"] == pytest.approx(50.0), "el buffer gratis se conserva ENTERO"
    assert resultado["PAID"] == pytest.approx(20.0), "la plata sigue alcanzando para lo mismo"


def test_el_buffer_gratis_no_le_saca_plata_a_nadie():
    """El corolario del hallazgo (a): conservarlo entero no puede pagarse con el
    presupuesto de la linea que si cuesta. El gratis es gratis para todos."""
    caros = [InventoryItem("PAID", 0.0, 50.0, 10.0)]
    solo = defer_by_priority(allocate_under_budget(caros, 200.0), caros)
    con_gratis = [InventoryItem("FREE", 0.0, 50.0, 0.0), InventoryItem("PAID", 0.0, 50.0, 10.0)]
    junto = defer_by_priority(allocate_under_budget(con_gratis, 200.0), con_gratis)
    pagado = next(i for i in junto.items if i.product_id == "PAID")
    assert pagado.safety_stock == pytest.approx(solo.items[0].safety_stock)


# ---- (b) una linea financiada entera no es una linea cortada ----

def test_una_linea_financiada_entera_no_aparece_en_la_lista_de_diferidas():
    """Reproduccion del hallazgo (b).

    `granted` sale de spend / unit_cost donde spend fue safety_stock * unit_cost:
    ese ida y vuelta cae unas partes en 1e16 por debajo de lo pedido, asi que
    units_at_risk = max(0.0, rp - safety_stock) queda en 2.8e-14 en vez de 0.0.
    Con el filtro `> 0` pelado, CHEAP -- financiada al 100% -- entraba en la
    lista de cortes y el deck imprimia "2 line(s) gave up buffer" cuando fue una.

    Antes del fix: deferral_order -> ('EXPENSIVE', 'CHEAP').
    """
    lineas = _plan(
        ("CHEAP", 100.0, 100.0, 51.79, 210.34),
        ("EXPENSIVE", 100.0, 100.0, 196.56, 454.88),
        budget=20_000.0,
    )
    barata = next(ln for ln in lineas if ln.product_id == "CHEAP")
    # La premisa del bug: la financiaron entera y aun asi el float dejo migas.
    assert barata.reorder_buffer == pytest.approx(barata.reorder_buffer_requested)
    assert 0.0 < barata.units_at_risk < 1e-6

    cortes = deferral_order(lineas)
    assert [c.product_id for c in cortes] == ["EXPENSIVE"]


def test_un_corte_real_de_una_unidad_sigue_saliendo_en_la_lista():
    """La contracara del hallazgo (b): la tolerancia no puede empezar a tapar
    cortes de verdad. Una unidad de buffer perdida es un corte y se nombra."""
    lineas = _plan(
        ("A", 100.0, 100.0, 1.0, 100.0),
        ("B", 100.0, 100.0, 1.0, 100.0),
        budget=199.0,
    )
    cortes = deferral_order(lineas)
    assert [c.product_id for c in cortes] == ["B"]
    assert cortes[0].units_at_risk == pytest.approx(1.0)
    assert cortes[0].cost_at_risk == pytest.approx(1.0)


# ---- (d) la tolerancia tiene que medir la causa, no un numero redondo ----

def test_un_corte_real_por_debajo_de_1e_6_unidades_sigue_siendo_un_corte():
    """Reproduccion del hallazgo (d), la punta ANCHA.

    La tolerancia era 1e-6 ABSOLUTO sobre units_at_risk, ocho ordenes de magnitud
    mas ancha que el 3e-14 que su propio docstring declaraba como causa. Una linea
    de un item de 1e7 la unidad, a la que el cap le dejo faltando 5 de plata, queda
    diferida en 5e-7 unidades: es una decision de verdad con plata de verdad, y
    desaparecia de la lista que el comprador trabaja llamando proveedores.

    Antes del fix: deferral_order -> ().
    """
    lineas = _plan(("MACHINE", 1.0, 1.0, 1e7, 1.0), budget=1e7 - 5.0)
    maquina = next(ln for ln in lineas if ln.product_id == "MACHINE")
    # La premisa del bug: el corte es real y cae por debajo del umbral viejo.
    assert 0.0 < maquina.units_at_risk < 1e-6
    assert maquina.cost_at_risk == pytest.approx(5.0)

    assert [c.product_id for c in deferral_order(lineas)] == ["MACHINE"]


def test_una_linea_gigante_financiada_entera_tampoco_entra_en_la_lista():
    """La punta ANGOSTA del mismo hallazgo: un umbral fijo tampoco alcanzaba arriba.

    El ida y vuelta de `_funded_buffers` -- (safety_stock * unit_cost) / unit_cost --
    deja un residuo RELATIVO, unas 2 ULP del pedido. Sobre un buffer de ~1e11
    unidades eso son 1.5e-5: pasa 1e-6, asi que la linea financiada al 100% volvia a
    la lista de cortes. Es el bug original de vuelta, a una escala que un numero fijo
    nunca iba a cubrir; por eso el umbral es relativo y no un 1e-9 mas chico.

    Antes del fix: deferral_order -> ('BULK',).
    """
    pedido = 97_649_255_453.69908
    costo = 23.300874482071965
    otorgado = (pedido * costo) / costo          # exactamente lo que hace _funded_buffers
    residuo = pedido - otorgado
    assert residuo > 1e-6, "premisa: el ruido del float supera la tolerancia vieja"

    linea = PurchasePlanLine(
        product_id="BULK", shaped_demand=0.0, on_hand=0.0, incoming_po=0.0,
        unit_cost=costo, recommended_order=0.0, reorder_buffer=otorgado,
        projected_position=0.0, order_value=0.0,
        reorder_buffer_requested=pedido,
        units_at_risk=residuo, cost_at_risk=residuo * costo,
    )

    assert deferral_order([linea]) == ()


def test_sin_recorte_la_lista_de_cortes_esta_vacia():
    """Una lista de cortes con las lineas que nadie corto no es una lista de
    cortes -- y sin cap que ate, nadie corto nada."""
    lineas = _plan(("A", 100.0, 100.0, 1.0, 100.0), budget=1_000_000.0)
    assert deferral_order(lineas) == ()
