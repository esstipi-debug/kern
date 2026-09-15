"""Reproduccion del hallazgo ALTO: el entregable declaraba un plazo de pago que
contradecia sus propias fechas.

`build_purchase_plan(default_payment_terms=...)` es parametrizable y esta
expuesto al cliente por `params['default_payment_terms']`. Con 'net_90' la linea
se fechaba BIEN (payment_due_week=12) pero los tres textos que nombran el plazo
-- el summary del calendario, el Finding "Payment terms ASSUMED, not read" y el
rationale del KPI "Lines on ASSUMED payment terms" -- interpolaban la CONSTANTE
de modulo DEFAULT_PAYMENT_TERMS y decian 'net_30'. El cliente leia un plazo que
contradecia el calendario impreso al lado.

Antes del fix: summary/finding/KPI decian net_30 con las lineas en la semana 12.
"""

from __future__ import annotations

import pytest

from jobs.integrated_plan_cash import cash_findings, cash_kpis
from src.sop_engine.cash_plan import build_cash_commitment_schedule
from src.sop_engine.demand_plan import NO_SHIFT_SOURCE, DemandPlanLine
from src.sop_engine.engine import run_integrated_plan
from src.sop_engine.purchase_plan import SkuPurchaseInputs, build_purchase_plan

_OTRO_DEFAULT = "net_90"
_LA_CONSTANTE = "net_30"


def _calendario(*skus: tuple[str, float, str], default_terms: str):
    """(product_id, unit_cost, payment_terms) planeados contra `default_terms`."""
    demanda = tuple(
        DemandPlanLine(pid, 10.0, 0.0, 10.0, NO_SHIFT_SOURCE, "sin shift") for pid, _, _ in skus
    )
    entradas = {
        pid: SkuPurchaseInputs(pid, on_hand=0.0, unit_cost=cost, payment_terms=terms)
        for pid, cost, terms in skus
    }
    lineas, _ = build_purchase_plan(demanda, entradas, default_payment_terms=default_terms)
    return lineas, build_cash_commitment_schedule(lineas)


def _plan_con_default(default_terms: str):
    return run_integrated_plan(
        {"SKU-A": 100.0},
        {"SKU-A": SkuPurchaseInputs("SKU-A", on_hand=0.0, unit_cost=2.0)},
        default_payment_terms=default_terms,
    )


def _finding_de_plazo(plan):
    return next(f for f in cash_findings(plan) if f.title == "Payment terms ASSUMED, not read")


def _kpi_de_plazo(plan):
    return next(k for k in cash_kpis(plan) if k.name == "Lines on ASSUMED payment terms")


def test_la_premisa_del_bug_la_linea_si_se_fecha_con_el_plazo_en_vigor():
    """El calendario nunca estuvo mal: net_90 vence el dia 90, dentro de la
    semana 12. El defecto era solo el TEXTO que lo acompanaba."""
    lineas, cal = _calendario(("SKU-A", 2.0, ""), default_terms=_OTRO_DEFAULT)
    assert lineas[0].payment_terms == _OTRO_DEFAULT
    assert lineas[0].payment_due_week == 12
    assert cal.weeks[12].disbursement == pytest.approx(20.0)


def test_el_resumen_nombra_el_plazo_en_vigor_y_no_la_constante():
    _, cal = _calendario(("SKU-A", 2.0, ""), default_terms=_OTRO_DEFAULT)
    assert cal.default_terms_in_effect == _OTRO_DEFAULT
    assert f"ASSUMED ({_OTRO_DEFAULT})" in cal.summary
    assert _LA_CONSTANTE not in cal.summary


def test_el_finding_nombra_el_plazo_en_vigor_y_no_la_constante():
    plan = _plan_con_default(_OTRO_DEFAULT)
    detalle = _finding_de_plazo(plan).detail
    assert f"planned on {_OTRO_DEFAULT}" in detalle
    assert _LA_CONSTANTE not in detalle


def test_el_kpi_nombra_el_plazo_en_vigor_y_no_la_constante():
    plan = _plan_con_default(_OTRO_DEFAULT)
    kpi = _kpi_de_plazo(plan)
    assert kpi.value == "1"
    assert _OTRO_DEFAULT in kpi.rationale
    assert _LA_CONSTANTE not in kpi.rationale


def test_el_resumen_del_plan_completo_arrastra_el_plazo_corregido():
    """El summary del calendario viaja dentro del summary del plan integrado, que
    es la frase que el deck imprime arriba de todo."""
    plan = _plan_con_default(_OTRO_DEFAULT)
    assert _OTRO_DEFAULT in plan.summary
    assert _LA_CONSTANTE not in plan.summary


def test_con_el_default_de_fabrica_el_texto_sigue_diciendo_net_30():
    """La contracara: la correccion sigue al valor en vigor, no lo esconde. Sin
    parametro el plazo en vigor ES la constante y los tres textos la nombran."""
    plan = _plan_con_default(_LA_CONSTANTE)
    assert plan.cash_schedule.default_terms_in_effect == _LA_CONSTANTE
    assert f"ASSUMED ({_LA_CONSTANTE})" in plan.cash_schedule.summary
    assert f"planned on {_LA_CONSTANTE}" in _finding_de_plazo(plan).detail
    assert _LA_CONSTANTE in _kpi_de_plazo(plan).rationale


def test_sin_lineas_asumidas_el_kpi_no_inventa_ningun_plazo():
    """El KPI se emite siempre, tambien en 0. Nombrar ahi la constante seria
    imprimir un plazo que esta corrida no uso, al lado de un conteo que dice que
    ninguna linea descansa sobre el."""
    plan = run_integrated_plan(
        {"SKU-A": 100.0},
        {"SKU-A": SkuPurchaseInputs("SKU-A", on_hand=0.0, unit_cost=2.0, payment_terms="net_45")},
        default_payment_terms=_OTRO_DEFAULT,
    )
    assert plan.cash_schedule.terms_assumed_for == ()
    assert plan.cash_schedule.default_terms_in_effect == ""
    kpi = _kpi_de_plazo(plan)
    assert kpi.value == "0"
    assert "net_" not in kpi.rationale
    assert not [f for f in cash_findings(plan) if f.title == "Payment terms ASSUMED, not read"]


def test_una_linea_sin_pedido_no_pone_su_plazo_en_vigor():
    """`default_terms_in_effect` habla de las FECHAS DE ESTE CALENDARIO, igual que
    `terms_assumed_for`: una linea que no ordena nada no tiene semana de pago aca,
    asi que su plazo supuesto no esta en vigor sobre ningun renglon."""
    lineas, cal = _calendario(("CUBIERTO", 5.0, ""), default_terms=_OTRO_DEFAULT)
    lineas_sin_pedido = build_purchase_plan(
        (DemandPlanLine("CUBIERTO", 0.0, 0.0, 0.0, NO_SHIFT_SOURCE, "sin shift"),),
        {"CUBIERTO": SkuPurchaseInputs("CUBIERTO", on_hand=0.0, unit_cost=5.0)},
        default_payment_terms=_OTRO_DEFAULT,
    )[0]
    assert lineas_sin_pedido[0].order_value == 0.0
    vacio = build_cash_commitment_schedule(lineas_sin_pedido)
    assert vacio.terms_assumed_for == ()
    assert vacio.default_terms_in_effect == ""
    assert "ASSUMED" not in vacio.summary
    assert cal.default_terms_in_effect == _OTRO_DEFAULT  # la que si pide, si lo pone


def test_dos_planes_con_defaults_distintos_no_colapsan_a_uno_solo():
    """Elegir el primero seria el mismo defecto -- nombrar un plazo que la mitad
    de las lineas no uso -- movido a un lugar mas dificil de ver."""
    de_90, _ = _calendario(("A", 2.0, ""), default_terms=_OTRO_DEFAULT)
    de_30, _ = _calendario(("B", 2.0, ""), default_terms=_LA_CONSTANTE)
    mezclado = build_cash_commitment_schedule(de_90 + de_30)
    assert mezclado.default_terms_in_effect == f"{_OTRO_DEFAULT}, {_LA_CONSTANTE}"
    assert _OTRO_DEFAULT in mezclado.summary and _LA_CONSTANTE in mezclado.summary
