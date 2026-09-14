"""Tests for src/sop_engine/purchase_plan.py (Linchpin 3.0 PR-20, A5 step 3)."""

from __future__ import annotations

import pytest

from src.sop_engine.cash_plan import DEFAULT_PAYMENT_TERMS, TERMS_ASSUMED, TERMS_STATED
from src.sop_engine.demand_plan import NO_SHIFT_SOURCE, DemandPlanLine
from src.sop_engine.purchase_plan import (
    NOT_WORTH_IT,
    UNCONSTRAINED,
    UNMEASURED,
    WORTH_IT,
    SkuPurchaseInputs,
    build_purchase_plan,
    constraint_verdict,
)


def _line(product_id: str, base: float, shaped: float, source: str = NO_SHIFT_SOURCE) -> DemandPlanLine:
    return DemandPlanLine(
        product_id=product_id, base_forecast=base, demand_shift_pct=0.0 if source == NO_SHIFT_SOURCE else 1.0,
        shaped_demand=shaped, source=source, reason="test fixture",
    )


# ---- SkuPurchaseInputs validation ----

def test_sku_purchase_inputs_rejects_negative_on_hand():
    with pytest.raises(ValueError):
        SkuPurchaseInputs("A", on_hand=-1.0, unit_cost=1.0)


def test_sku_purchase_inputs_rejects_nonpositive_unit_cost():
    with pytest.raises(ValueError):
        SkuPurchaseInputs("A", on_hand=0.0, unit_cost=0.0)


def test_sku_purchase_inputs_rejects_negative_incoming_po():
    with pytest.raises(ValueError):
        SkuPurchaseInputs("A", on_hand=0.0, unit_cost=1.0, incoming_po=-5.0)


# ---- build_purchase_plan basics ----

def test_gap_fill_orders_exactly_the_shortfall_when_no_rules_apply():
    demand_plan = (_line("A", 100.0, 100.0),)
    inputs = {"A": SkuPurchaseInputs("A", on_hand=30.0, unit_cost=5.0, reorder_point=20.0)}
    lines, allocation = build_purchase_plan(demand_plan, inputs)
    assert allocation is None
    line = lines[0]
    assert line.recommended_order == pytest.approx(70.0)  # 100 - 30 - 0(incoming)
    assert line.projected_position == pytest.approx(0.0)  # 30 + 0 + 70 - 100
    assert line.reorder_buffer == pytest.approx(20.0)  # unscaled, no budget
    assert line.order_value == pytest.approx(350.0)  # 70 * 5


def test_incoming_po_reduces_the_recommended_top_up():
    demand_plan = (_line("A", 100.0, 100.0),)
    inputs = {"A": SkuPurchaseInputs("A", on_hand=30.0, unit_cost=5.0, incoming_po=40.0)}
    lines, _ = build_purchase_plan(demand_plan, inputs)
    line = lines[0]
    assert line.recommended_order == pytest.approx(30.0)  # 100 - 30 - 40
    assert line.projected_position == pytest.approx(0.0)  # 30 + 40 + 30 - 100


def test_moq_and_case_pack_rules_are_applied_via_constraints_module():
    demand_plan = (_line("A", 40.0, 40.0),)
    inputs = {"A": SkuPurchaseInputs("A", on_hand=10.0, unit_cost=2.0, minimum_order_quantity=50.0, order_multiple=12.0)}
    lines, _ = build_purchase_plan(demand_plan, inputs)
    line = lines[0]
    # gap = 40 - 10 = 30; MOQ raises 30 -> 50; case-pack of 12 rounds 50 -> 60
    # (same MOQ-then-pack rounding test_constraints.py::test_apply_order_rules_moq_then_pack_then_cap
    # exercises on the identical 30/50/12 inputs, minus that test's additional max_quantity cap).
    assert line.recommended_order == pytest.approx(60.0)


def test_missing_sku_inputs_raises_value_error_naming_the_sku():
    demand_plan = (_line("A", 10.0, 10.0), _line("B", 5.0, 5.0))
    inputs = {"A": SkuPurchaseInputs("A", on_hand=0.0, unit_cost=1.0)}
    with pytest.raises(ValueError, match="B"):
        build_purchase_plan(demand_plan, inputs)


# ---- budget allocation: mirrors tests/test_constraints.py's own hand-verified examples ----

def test_budget_defers_whole_buffers_but_never_touches_recommended_order():
    """The portfolio arithmetic is still ``allocate_under_budget``'s; the
    DISTRIBUTION is no longer proportional.

    Same numbers as test_constraints.py::test_allocate_under_budget_scales_safety_
    stock_to_fit: cycle floor = 100 + 100 = 200; safety(reorder) = 100 + 100 = 200;
    total = 400; budget = 300 -> portfolio scale 0.5, final_investment = 300. What
    changed is who pays for it: this used to hand BOTH lines 50 units (half a buffer
    each, neither SKU actually protected and nobody told which supplier to call).
    Now the 100 usd of affordable buffer funds ONE line whole and defers the other,
    named, with the coverage the deferral costs on the line itself.
    """
    demand_plan = (_line("A", 200.0, 200.0), _line("B", 200.0, 200.0))
    inputs = {
        "A": SkuPurchaseInputs("A", on_hand=0.0, unit_cost=1.0, reorder_point=100.0),
        "B": SkuPurchaseInputs("B", on_hand=0.0, unit_cost=1.0, reorder_point=100.0),
    }
    lines, allocation = build_purchase_plan(demand_plan, inputs, budget=300.0)
    assert allocation.feasible
    assert allocation.safety_stock_scale == pytest.approx(0.5)   # portfolio ratio, unchanged
    assert allocation.final_investment == pytest.approx(300.0)   # same money as before
    for line in lines:
        assert line.recommended_order == pytest.approx(200.0)  # cycle floor untouched
        assert line.projected_position == pytest.approx(0.0)   # 0 + 0 + 200 - 200
        assert line.reorder_buffer_requested == pytest.approx(100.0)
    kept, deferred = lines  # equal unit cost -> product_id breaks the tie, A funded first
    assert (kept.product_id, deferred.product_id) == ("A", "B")
    assert kept.reorder_buffer == pytest.approx(100.0)
    assert kept.units_at_risk == pytest.approx(0.0)
    assert deferred.reorder_buffer == pytest.approx(0.0)
    assert deferred.units_at_risk == pytest.approx(100.0)
    assert deferred.cost_at_risk == pytest.approx(100.0)
    # The buffer investment still adds up to exactly what the cap approved.
    assert sum(line.reorder_buffer * line.unit_cost for line in lines) == pytest.approx(100.0)


def test_the_expensive_buffer_is_the_one_deferred_first():
    """The ordering rule, on the case where it is not a tie: a dollar released
    from a 10-usd buffer costs 0.1 units of cover, from a 4-usd buffer 0.25. To
    free the dollars the cap demands with the fewest units of protection given
    up, the 10-usd buffer goes first.

    cycle floor = (100/2 * 10) + (200/2 * 4) = 500 + 400 = 900; safety = 600 + 360
    = 960; requested = 1860; cap = 1000 -> 100 usd of buffer is affordable, and it
    all goes to the cheap SKU.
    """
    demand_plan = (_line("EXPENSIVE", 100.0, 100.0), _line("CHEAP", 200.0, 200.0))
    inputs = {
        "EXPENSIVE": SkuPurchaseInputs("EXPENSIVE", on_hand=0.0, unit_cost=10.0, reorder_point=60.0),
        "CHEAP": SkuPurchaseInputs("CHEAP", on_hand=0.0, unit_cost=4.0, reorder_point=90.0),
    }
    lines, allocation = build_purchase_plan(demand_plan, inputs, budget=1000.0)
    assert allocation.feasible
    assert allocation.final_investment == pytest.approx(1000.0)
    expensive, cheap = lines
    assert expensive.reorder_buffer == pytest.approx(0.0)
    assert expensive.units_at_risk == pytest.approx(60.0)
    assert expensive.cost_at_risk == pytest.approx(600.0)
    assert cheap.reorder_buffer == pytest.approx(25.0)   # 100 usd / 4 usd per unit
    assert cheap.units_at_risk == pytest.approx(65.0)
    # 85 units of cover kept for the same 100 usd; a proportional scrape (scale
    # 100/960) would have kept 6.25 + 9.375 = 15.6 units for the identical money.
    kept = sum(line.reorder_buffer for line in lines)
    assert kept == pytest.approx(25.0)
    assert kept > (60.0 + 90.0) * (100.0 / 960.0)


def test_a_budget_that_fits_leaves_every_buffer_exactly_as_requested():
    """The deferral must not fire when there is nothing to defer -- the
    unconstrained path stays bit-for-bit what ``allocate_under_budget`` returns."""
    demand_plan = (_line("A", 200.0, 200.0), _line("B", 200.0, 200.0))
    inputs = {
        "A": SkuPurchaseInputs("A", on_hand=0.0, unit_cost=1.0, reorder_point=100.0),
        "B": SkuPurchaseInputs("B", on_hand=0.0, unit_cost=3.0, reorder_point=100.0),
    }
    lines, allocation = build_purchase_plan(demand_plan, inputs, budget=10_000.0)
    assert allocation.safety_stock_scale == pytest.approx(1.0)
    for line in lines:
        assert line.reorder_buffer == pytest.approx(100.0)
        assert line.units_at_risk == pytest.approx(0.0)
        assert line.cost_at_risk == pytest.approx(0.0)


def test_budget_infeasible_zeroes_reorder_buffer_and_keeps_cycle_floor():
    # Same numbers as test_constraints.py::test_allocate_under_budget_infeasible_when_cycle_floor_exceeds:
    # cycle floor = 100 (200 units / 2 * $1); budget 80 < cycle floor -> infeasible.
    demand_plan = (_line("A", 200.0, 200.0),)
    inputs = {"A": SkuPurchaseInputs("A", on_hand=0.0, unit_cost=1.0, reorder_point=50.0)}
    lines, allocation = build_purchase_plan(demand_plan, inputs, budget=80.0)
    assert allocation.feasible is False
    assert allocation.final_investment == pytest.approx(100.0)
    line = lines[0]
    assert line.recommended_order == pytest.approx(200.0)  # units never trimmed
    assert line.reorder_buffer == pytest.approx(0.0)       # buffer zeroed first


# --- Lo que la restriccion del proveedor compro de mas ------------------------


def _plan_con_moq(*, moq=0.0, multiple=0.0, demand=100.0, on_hand=40.0, incoming=10.0):
    demanda = (DemandPlanLine("A", demand, 0.0, demand, NO_SHIFT_SOURCE, "sin shift"),)
    entradas = {"A": SkuPurchaseInputs(
        "A", on_hand=on_hand, unit_cost=10.0, reorder_point=5.0, incoming_po=incoming,
        minimum_order_quantity=moq, order_multiple=multiple,
    )}
    lineas, _ = build_purchase_plan(demanda, entradas)
    return lineas[0]


def test_sin_restricciones_no_hay_exceso():
    linea = _plan_con_moq()
    assert linea.recommended_order == 50.0        # 100 - 40 - 10
    assert linea.constraint_extra_units == 0.0
    assert linea.constraint_cost_delta == 0.0
    assert linea.constraint_extra_cover_periods == 0.0


def test_el_moq_y_la_caja_cuantifican_lo_que_compraron_de_mas():
    """238 u de mas sobre una necesidad de 50: 2.380 usd y 2,38 periodos de cobertura."""
    linea = _plan_con_moq(moq=250.0, multiple=48.0)
    assert linea.recommended_order == 288.0
    assert linea.constraint_extra_units == 238.0
    assert linea.constraint_cost_delta == 2380.0
    assert linea.constraint_extra_cover_periods == pytest.approx(2.38)


def test_un_moq_sobre_una_necesidad_de_cero_ya_no_compra_nada():
    """Antes esto compraba 100 u contra una necesidad de 0 y las rotulaba como
    exceso de la restriccion. Rotularlo era mejor que esconderlo, pero el pedido no
    tendria que existir: el MOQ condiciona un pedido, no obliga a hacerlo.
    """
    linea = _plan_con_moq(moq=100.0, demand=0.0, on_hand=0.0, incoming=0.0)
    assert linea.recommended_order == 0.0
    assert linea.constraint_extra_units == 0.0
    assert linea.constraint_cost_delta == 0.0


# ---- constraint_verdict: los tres numeros ya estaban, faltaba el juicio ----

def test_el_redondeo_que_la_demanda_absorbe_se_recomienda_tomar():
    """238 u de sobra sobre 100 de demanda son 2,38 periodos: fuera del horizonte."""
    (linea,), _ = build_purchase_plan(
        (_line("A", 100.0, 100.0),),
        {"A": SkuPurchaseInputs(product_id="A", on_hand=40.0, unit_cost=10.0,
                                reorder_point=5.0, incoming_po=10.0,
                                minimum_order_quantity=250.0, order_multiple=48.0)},
    )
    assert linea.constraint_extra_cover_periods == pytest.approx(2.38)
    assert linea.constraint_verdict == NOT_WORTH_IT

    (holgado,), _ = build_purchase_plan(
        (_line("A", 100.0, 100.0),),
        {"A": SkuPurchaseInputs(product_id="A", on_hand=40.0, unit_cost=10.0,
                                reorder_point=5.0, incoming_po=10.0,
                                minimum_order_quantity=250.0, order_multiple=48.0)},
        cover_horizon=3.0,
    )
    assert holgado.constraint_verdict == WORTH_IT


def test_el_sku_cubierto_por_lo_que_ya_viene_tampoco_ordena_el_moq():
    """Mismo corte con demanda real: 100 pedidas, 60 en mano y 40 en camino."""
    (linea,), _ = build_purchase_plan(
        (_line("A", 100.0, 100.0),),
        {"A": SkuPurchaseInputs(product_id="A", on_hand=60.0, unit_cost=10.0,
                                reorder_point=5.0, incoming_po=40.0,
                                minimum_order_quantity=250.0, order_multiple=48.0)},
    )
    assert linea.recommended_order == 0.0
    assert linea.constraint_verdict == UNCONSTRAINED


def test_la_funcion_pura_no_juzga_sin_demanda_contra_que_medir():
    """El par imposible por construccion tiene respuesta propia, no una plausible."""
    assert constraint_verdict(250.0, 0.0, 1.0) == UNMEASURED


# --- El eje temporal: cuando sale la plata de cada linea ----------------------


def test_cada_linea_declara_su_plazo_y_si_lo_leyo_o_lo_asumio():
    """Dos SKU en el mismo plan: uno trae condiciones del proveedor y el otro no.
    El que no las trae NO sale igual que el que si: sale rotulado como supuesto."""
    demanda = (_line("DECLARADO", 100.0, 100.0), _line("MUDO", 100.0, 100.0))
    entradas = {
        "DECLARADO": SkuPurchaseInputs(
            "DECLARADO", on_hand=0.0, unit_cost=2.0, payment_terms="NET 60",
        ),
        "MUDO": SkuPurchaseInputs("MUDO", on_hand=0.0, unit_cost=2.0),
    }
    declarado, mudo = build_purchase_plan(demanda, entradas)[0]

    assert (declarado.payment_terms, declarado.payment_due_days) == ("net_60", 60)
    assert declarado.payment_terms_source == TERMS_STATED
    assert declarado.payment_due_week == 8

    assert mudo.payment_terms == DEFAULT_PAYMENT_TERMS
    assert mudo.payment_terms_source == TERMS_ASSUMED
    assert (mudo.payment_due_days, mudo.payment_due_week) == (30, 4)


def test_el_default_del_plazo_es_un_parametro_y_viaja_rotulado_como_supuesto():
    """Se puede cambiar el supuesto de cartera sin tocar el modulo, y sigue
    diciendo que fue un supuesto."""
    (linea,), _ = build_purchase_plan(
        (_line("A", 100.0, 100.0),),
        {"A": SkuPurchaseInputs("A", on_hand=0.0, unit_cost=2.0)},
        default_payment_terms="contado",
    )
    assert (linea.payment_terms, linea.payment_due_days, linea.payment_due_week) == ("cash", 0, 0)
    assert linea.payment_terms_source == TERMS_ASSUMED


def test_un_plazo_ilegible_revienta_nombrando_el_sku_en_vez_de_caer_al_default():
    """El default es para la linea que no dijo NADA. Una condicion que nadie sabe
    leer no puede convertirse en net 30 sin que el comprador se entere."""
    with pytest.raises(ValueError, match="SKU-RARO"):
        SkuPurchaseInputs("SKU-RARO", on_hand=0.0, unit_cost=1.0, payment_terms="45/60 split")


def test_build_purchase_plan_sigue_devolviendo_dos_elementos():
    """Contrato de aridad: hay callers (y sondas) que desempacan exactamente dos."""
    resultado = build_purchase_plan(
        (_line("A", 10.0, 10.0),), {"A": SkuPurchaseInputs("A", on_hand=0.0, unit_cost=1.0)},
    )
    assert len(resultado) == 2
    lineas, asignacion = resultado
    assert len(lineas) == 1 and asignacion is None


def test_sin_condiciones_del_proveedor_no_hay_nada_que_juzgar():
    (linea,), _ = build_purchase_plan(
        (_line("A", 100.0, 100.0),),
        {"A": SkuPurchaseInputs(product_id="A", on_hand=40.0, unit_cost=10.0,
                                reorder_point=5.0, incoming_po=10.0)},
    )
    assert linea.constraint_extra_units == 0.0
    assert linea.constraint_verdict == UNCONSTRAINED
