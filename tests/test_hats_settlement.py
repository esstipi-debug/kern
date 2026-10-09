"""Settlement (N5) tests: weight collapse, bounds, determinism, acta, value (spec 12.2)."""

from datetime import date

import pytest

from src.eoq import PriceBreak
from src.hat_council import (
    UNASSIGNED,
    UNRESOLVED_CONCESSION_THRESHOLD,
    agreement_at_1,
    settle,
    tension_map,
    top1_by_judge,
    value_row,
)
from src.hats import (
    HAT_KEYS,
    Candidate,
    FreightProfile,
    HatConfig,
    baseline_plan,
    build_inputs,
    evaluable_hats,
)


def _inputs(**kw):
    """The testbed SKU with NO freight profile: four areas on the table."""
    base = dict(sku="SKU-T", annual_demand=5200.0, mean_weekly=100.0, std_weekly=30.0,
                lead_time_weeks=1.0, unit_cost=10.0, config=HatConfig())
    base.update(kw)
    return build_inputs(**base)


def _inputs_freight(**kw):
    """The same SKU with transport facts declared: all five areas on the table."""
    return _inputs(freight=FreightProfile(weight_kg_per_unit=8.0, distance_km=750.0), **kw)


@pytest.mark.parametrize("hat", list(HAT_KEYS))
def test_single_weight_collapses_to_that_hats_ideal(hat):
    """Spec acceptance #3: --weights <hat>=1 == that hat's ideal, for EVERY hat --
    which is why this runs on the freight-bearing SKU: on the four-hat one,
    weighting transporte is a refusal, not a collapse (see test below)."""
    inp = _inputs_freight()
    s = settle(inp, {hat: 1.0})
    assert s.chosen == tension_map(inp).ideals[hat].candidate
    entry = next(e for e in s.acta if e.hat_key == hat)
    assert entry.concesion == pytest.approx(0.0)


def test_equal_weights_land_between_the_extreme_ideals():
    inp = _inputs()
    tmap = tension_map(inp)
    qs = [e.candidate.order_quantity for e in tmap.ideals.values()]
    s = settle(inp, None)
    assert min(qs) <= s.chosen.order_quantity <= max(qs)


def test_settlement_is_deterministic():
    inp = _inputs()
    assert settle(inp, None) == settle(inp, None)


def test_acta_covers_all_hats_with_unit_interval_concessions():
    s = settle(_inputs(), "cfo=2,planner=1,comprador=1,comercial=1")
    assert sum(s.weights.values()) == pytest.approx(1.0)
    assert s.weights["cfo"] == pytest.approx(0.4)
    # Four entries on the four-hat SKU, in HAT_KEYS order. The acta records the
    # areas that were on the table; the ones that were not are in excluded_hats.
    assert [e.hat_key for e in s.acta] == list(evaluable_hats(_inputs()))
    assert s.excluded_hats == ("transporte",)
    for e in s.acta:
        assert 0.0 <= e.concesion <= 1.0
        assert e.concesion == pytest.approx(1.0 - e.utility_norm_at_chosen)
        assert e.kpi_ideal == e.kpi_ideal and e.kpi_chosen == e.kpi_chosen  # not NaN


def test_settlement_value_is_baseline_minus_chosen_signed():
    s = settle(_inputs(), None)
    assert s.value_vs_baseline_usd == pytest.approx(s.judge_cost_baseline - s.judge_cost_chosen)
    assert s.judge_cost_chosen > 0 and s.judge_cost_baseline > 0


def test_settle_rejects_bad_weights():
    with pytest.raises(ValueError):
        settle(_inputs(), "gerente=1")


def test_top1_by_judge_is_one_of_ideals_or_baseline():
    inp = _inputs()
    tmap = tension_map(inp)
    top1 = top1_by_judge(inp, tmap)
    pool = [e.candidate for e in tmap.ideals.values()] + [baseline_plan(inp)]
    assert top1 in pool


def test_agreement_at_1_bounds_and_values():
    a, b = Candidate(100.0, 0.95), Candidate(200.0, 0.99)
    assert agreement_at_1([(a, a)]) == 1.0
    assert agreement_at_1([(a, a), (a, b)]) == 0.5
    with pytest.raises(ValueError):
        agreement_at_1([])


def test_value_row_mirrors_settlement_and_ideals():
    inp = _inputs()
    tmap = tension_map(inp)
    s = settle(inp, None)
    row = value_row(inp, tmap, s)
    assert row.sku == "SKU-T"
    assert row.c_baseline == s.judge_cost_baseline
    assert row.c_n5 == s.judge_cost_chosen
    assert row.delta_usd == pytest.approx(row.c_baseline - row.c_n5)


# ---- T14: el acta tiene que cerrar, no solo registrar el desacuerdo ----------
#
# El criterio de T14 pide un acta con: numero de demanda acordado, plan de
# suministro que lo soporta, brechas sin resolver DECLARADAS, y al menos una
# decision con nombre y fecha. El Settlement registraba el desacuerdo (cuanto
# cede cada sombrero) y elegia Q*/SL*, pero no cerraba: no decia sobre que
# demanda se acordo, no declaraba que concesiones quedaron sin resolver, y no
# tenia dueno ni fecha. Un acta que nadie firma no es un acta.


def _entrada(**cambios):
    base = dict(
        sku="A", annual_demand=5200.0, mean_weekly=100.0, std_weekly=20.0,
        lead_time_weeks=2.0, unit_cost=10.0, config=HatConfig(),
        price_breaks=(PriceBreak(0.0, 10.0),),
    )
    base.update(cambios)
    return build_inputs(**base)


def test_el_acta_registra_sobre_que_demanda_se_acordo():
    s = settle(_entrada(), None)

    assert s.agreed_demand_plan == 5200.0


def test_una_concesion_grande_queda_declarada_como_brecha_sin_resolver():
    """El sombrero que cede por encima del umbral no se esconde en el promedio."""
    s = settle(_entrada(), {"cfo": 1.0})   # politica extrema: los demas ceden mucho

    assert s.unresolved_gaps, "con pesos extremos alguien tiene que quedar sin resolver"
    for brecha in s.unresolved_gaps:
        entrada = next(e for e in s.acta if e.hat_key == brecha.hat_key)
        assert brecha.concesion == entrada.concesion
        assert brecha.concesion > UNRESOLVED_CONCESSION_THRESHOLD


def test_sin_concesiones_grandes_no_se_inventan_brechas():
    """Pesos equilibrados: nadie cede de mas, y el acta no puede fabricar conflicto."""
    s = settle(_entrada(), None)

    assert all(b.concesion > UNRESOLVED_CONCESSION_THRESHOLD for b in s.unresolved_gaps)


def test_el_acta_lleva_dueno_y_fecha_de_la_decision():
    s = settle(_entrada(), None, owner="planning", decided_on=date(2026, 8, 3))

    assert s.decision_owner == "planning"
    assert s.decision_date == date(2026, 8, 3)


def test_un_acta_sin_dueno_lo_dice_en_vez_de_fingir_uno():
    """Un owner vacio se leeria como 'ya esta asignado'. Se declara sin asignar."""
    s = settle(_entrada(), None)

    assert s.decision_owner == UNASSIGNED
    assert s.decision_date is not None      # la fecha del acuerdo si existe siempre
