"""El quinto sombrero: transporte sobre la misma decision (Q, SL).

Dos propiedades sostienen todo lo demas y por eso van primero:

1. El flete es SERRUCHO en Q, no pendiente. ``src.logistics.modes`` cobra FTL por
   ``ceil(peso / capacidad)``, asi que la unidad que desborda a un segundo camion
   medio vacio cuesta lo que cuesta un camion entero. Un Q que el comprador ama
   por alcanzar un price break puede caer del lado malo de ese escalon: ESA es la
   tension que este sombrero hace visible y que antes no existia en el consejo.

2. Sin peso por unidad y sin distancia, transporte NO se puntua. No hay default
   sintetico al estilo D8 -- el tarifario de descuentos al menos se deriva del
   EOQ y del costo real; un kilogramo no se deriva de nada. El area sale
   declarada como fuera de la mesa, nunca como un cero.
"""

import pytest

from jobs import cross_impact_job, hats_job
from src.cross_impact import measure
from src.hat_council import settle, tension_map
from src.hats import (
    HAT_CFO,
    HAT_KEYS,
    HAT_TRANSPORTE,
    Candidate,
    FreightProfile,
    HatConfig,
    annual_freight,
    build_inputs,
    decision_cost,
    evaluable_hats,
    freight_mode,
    freight_per_unit,
    hat_kpis,
    headline_kpi,
    parse_weights,
)
from src.objection import NO_EVALUABLE, unmeasurable

# 12 kg/u y 900 km ponen el camion de 20.000 kg justo entre Q=1600 (19.200 kg,
# un camion) y Q=1700 (20.400 kg, dos). El escalon es el punto del test.
_PESADO = FreightProfile(weight_kg_per_unit=12.0, distance_km=900.0)


def _inputs(freight=None, **kw):
    base = dict(sku="SKU-T", annual_demand=52 * 400.0, mean_weekly=400.0, std_weekly=90.0,
                lead_time_weeks=2.0, unit_cost=30.0, config=HatConfig(), freight=freight)
    base.update(kw)
    return build_inputs(**base)


# -- 1. el serrucho -----------------------------------------------------------


def test_el_flete_por_unidad_salta_cuando_la_orden_desborda_el_camion():
    inp = _inputs(_PESADO)
    un_camion = Candidate(1600.0, 0.95)       # 19.200 kg
    dos_camiones = Candidate(1700.0, 0.95)    # 20.400 kg
    assert freight_mode(inp, un_camion) == "ftl"
    assert freight_mode(inp, dos_camiones) == "ftl"
    # Pedir 100 unidades MAS encarece el flete por unidad: no es una pendiente.
    assert freight_per_unit(inp, dos_camiones) > freight_per_unit(inp, un_camion)
    assert freight_per_unit(inp, dos_camiones) > 1.8 * freight_per_unit(inp, un_camion)


def test_el_flete_anual_es_exactamente_demanda_por_flete_unitario():
    """Son UNA senal en dos unidades, no dos evidencias: D es fijo en la grilla."""
    inp = _inputs(_PESADO)
    cand = Candidate(900.0, 0.95)
    assert annual_freight(inp, cand) == pytest.approx(
        inp.annual_demand * freight_per_unit(inp, cand))


def test_la_utilidad_de_transporte_es_plana_en_el_nivel_de_servicio():
    """Limite declarado del modelo: el mismo volumen anual se mueve igual, y aqui
    no hay modelo de expedite. Que sea plano en SL es una decision, no un olvido."""
    inp = _inputs(_PESADO)
    a = hat_kpis(inp, HAT_TRANSPORTE, Candidate(900.0, 0.90))["freight_per_unit_usd"]
    b = hat_kpis(inp, HAT_TRANSPORTE, Candidate(900.0, 0.99))["freight_per_unit_usd"]
    assert a == pytest.approx(b)


# -- 2. sin insumos no se puntua ---------------------------------------------


def test_sin_perfil_de_flete_el_area_queda_declarada_fuera_de_la_mesa():
    inp = _inputs()
    assert evaluable_hats(inp) == tuple(k for k in HAT_KEYS if k != HAT_TRANSPORTE)
    tmap = tension_map(inp)
    assert tmap.excluded_hats == (HAT_TRANSPORTE,)
    assert set(tmap.ideals) | set(tmap.excluded_hats) == set(HAT_KEYS)


def test_pedir_el_kpi_sin_perfil_levanta_en_vez_de_devolver_cero():
    """Un cero leeria como "esta politica despacha gratis"."""
    with pytest.raises(ValueError, match="no FreightProfile declared"):
        hat_kpis(_inputs(), HAT_TRANSPORTE, Candidate(900.0, 0.95))


def test_el_perfil_exige_sus_dos_hechos_fisicos():
    with pytest.raises(ValueError, match="weight_kg_per_unit"):
        FreightProfile(weight_kg_per_unit=0.0, distance_km=900.0)
    with pytest.raises(ValueError, match="distance_km"):
        FreightProfile(weight_kg_per_unit=12.0, distance_km=-1.0)


def test_un_tarifario_no_declarado_queda_marcado_como_supuesto():
    assert _PESADO.rates_assumed is True
    from src.logistics.modes import FreightRates
    propio = FreightProfile(weight_kg_per_unit=12.0, distance_km=900.0,
                            rates=FreightRates(ftl_cost_per_km=2.5))
    assert propio.rates_assumed is False
    assert propio.effective_rates.ftl_cost_per_km == 2.5


# -- 3. el juez ---------------------------------------------------------------


def test_el_juez_suma_flete_solo_cuando_hay_flete_declarado():
    sin, con = _inputs(), _inputs(_PESADO)
    cand = Candidate(900.0, 0.95)
    # Sin perfil, C(Q,SL) es EXACTAMENTE el de siempre -- de ahi que la
    # equivalencia con inventory_optimization (test_hats_valuation) siga en pie.
    assert decision_cost(con, cand) == pytest.approx(
        decision_cost(sin, cand) + annual_freight(con, cand))
    assert decision_cost(con, cand) > decision_cost(sin, cand)


# -- 4. el consejo con cinco areas -------------------------------------------


def test_con_flete_el_acta_cubre_las_cinco_areas():
    inp = _inputs(_PESADO)
    s = settle(inp, None)
    assert [e.hat_key for e in s.acta] == list(HAT_KEYS)
    assert s.excluded_hats == ()
    assert sum(s.weights.values()) == pytest.approx(1.0)
    assert len(s.weights) == 5


def test_transporte_y_cfo_tiran_para_lados_opuestos():
    """La tension que justifica el sombrero: el CFO quiere Q chico (capital), el
    transporte quiere el camion lleno. Si sus ideales coincidieran, el area no
    aportaria informacion nueva al consejo."""
    tmap = tension_map(_inputs(_PESADO))
    q_cfo = tmap.ideals[HAT_CFO].candidate.order_quantity
    q_transporte = tmap.ideals[HAT_TRANSPORTE].candidate.order_quantity
    assert q_transporte > q_cfo
    choque = next(c for c in tmap.clashes
                  if {c.hat_a, c.hat_b} == {HAT_CFO, HAT_TRANSPORTE})
    assert choque.delta_capital_usd != 0.0


def test_cinco_areas_producen_diez_choques():
    assert len(tension_map(_inputs(_PESADO)).clashes) == 10   # C(5,2)
    assert len(tension_map(_inputs()).clashes) == 6           # C(4,2)


# -- 5. pesos sobre una mesa parcial -----------------------------------------


def test_un_peso_sobre_un_area_fuera_de_la_mesa_se_renormaliza_y_se_declara():
    """Una politica se escribe por corrida y la evaluabilidad es por SKU: levantar
    aqui volveria inusable una cartera mixta."""
    inp = _inputs()
    w = parse_weights({k: 1.0 for k in HAT_KEYS}, evaluable_hats(inp))
    assert set(w) == set(evaluable_hats(inp))
    assert sum(w.values()) == pytest.approx(1.0)
    assert settle(inp, {k: 1.0 for k in HAT_KEYS}).excluded_hats == (HAT_TRANSPORTE,)


def test_una_politica_entera_sobre_un_area_no_evaluable_si_levanta():
    """El caso degenerado: no queda nada sobre lo que reconciliar, y caer a
    uniforme en silencio inventaria una politica que nadie escribio."""
    with pytest.raises(ValueError, match="not evaluable"):
        parse_weights({HAT_TRANSPORTE: 1.0}, evaluable_hats(_inputs()))


# -- 6. el veredicto de objecion ---------------------------------------------


def test_unmeasurable_deja_todos_los_campos_vacios_y_exige_un_motivo():
    v = unmeasurable(HAT_TRANSPORTE, "transporte: sin perfil de flete")
    assert v.status == NO_EVALUABLE
    assert v.kpi_name == headline_kpi(HAT_TRANSPORTE)
    assert (v.kpi_current, v.kpi_proposed, v.deterioration, v.threshold) == (None,) * 4
    with pytest.raises(ValueError, match="reason"):
        unmeasurable(HAT_TRANSPORTE, "   ")


def test_un_area_sin_fila_igual_recibe_veredicto_y_arrastra_el_global():
    """La regla que este cambio no puede romper: iterar solo las filas medidas
    dejaria a transporte fuera de la lista, y cero filas cuenta como cero
    objeciones en todo lo que viene despues."""
    inp = _inputs()
    impact = measure(inp, Candidate(900.0, 0.95), Candidate(1000.0, 0.95), HAT_CFO)
    thresholds = {k: 1e9 for k in HAT_KEYS}
    verdicts = cross_impact_job._judge_all(inp, impact, thresholds, Candidate(1000.0, 0.95))

    assert tuple(v.hat_key for v in verdicts) == HAT_KEYS
    transporte = next(v for v in verdicts if v.hat_key == HAT_TRANSPORTE)
    assert transporte.status == NO_EVALUABLE
    # El motivo nombra el insumo que falta: "no evaluable" a secas mueve la
    # pregunta en vez de contestarla.
    assert "peso por unidad" in transporte.reason
    n_unk = sum(1 for v in verdicts if v.status == NO_EVALUABLE)
    assert cross_impact_job._global_verdict(0, n_unk) == cross_impact_job.INDETERMINADO


# -- 7. punta a punta por el job ---------------------------------------------


def _csv(tmp_path, con_peso: bool):
    import pandas as pd
    path = tmp_path / ("con_peso.csv" if con_peso else "sin_peso.csv")
    data = {
        "product_id": ["SKU-A"] * 30,
        "quantity": ([100, 110, 95, 105, 120] * 6)[:30],
        "unit_cost": [30.0] * 30,
        "lead_time_days": [14.0] * 30,
    }
    if con_peso:
        data["weight_kg"] = [12.0] * 30
    pd.DataFrame(data).to_csv(path, index=False)
    return str(path)


def test_el_job_arma_el_perfil_desde_la_columna_y_el_parametro(tmp_path):
    payload = hats_job.prepare(_csv(tmp_path, True), {"distance_km": 900.0})
    (inp,) = payload["inputs"]
    assert inp.freight is not None
    assert inp.freight.weight_kg_per_unit == pytest.approx(12.0)
    assert inp.freight.distance_km == 900.0
    assert set(payload["weights"]) == set(HAT_KEYS)


@pytest.mark.parametrize("con_peso,distancia", [(True, None), (False, 900.0)])
def test_falta_cualquiera_de_las_dos_mitades_y_el_area_no_entra(tmp_path, con_peso, distancia):
    params = {"distance_km": distancia} if distancia else {}
    payload = hats_job.prepare(_csv(tmp_path, con_peso), params)
    (inp,) = payload["inputs"]
    assert inp.freight is None
    assert HAT_TRANSPORTE not in payload["weights"]


def test_el_entregable_de_tension_nombra_el_area_que_no_puntuo(tmp_path):
    report = hats_job.run_tension(hats_job.prepare(_csv(tmp_path, False)))
    assert hats_job.verify_tension(report) == []
    assert "transporte" in report.outcome.summary
    assert any("transporte" in r.description for r in report.outcome.residuals)


def test_con_las_dos_mitades_hay_una_opcion_mas_y_ningun_residual_de_exclusion(tmp_path):
    report = hats_job.run_tension(
        hats_job.prepare(_csv(tmp_path, True), {"distance_km": 900.0}))
    assert hats_job.verify_tension(report) == []
    assert len(report.outcome.options) == 6          # 5 ideales + baseline
    assert all(m.excluded_hats == () for m in report.maps)
    # El tarifario si es supuesto, y eso tiene que seguir dicho.
    assert report.freight_rates_assumed is True
    assert any("tarifario" in r.description.lower() for r in report.outcome.residuals)


def test_el_settlement_reparte_el_acta_entre_las_cinco_areas(tmp_path):
    payload = hats_job.prepare(_csv(tmp_path, True), {"distance_km": 900.0})
    report = hats_job.run_settlement(payload)
    assert hats_job.verify_settlement(report) == []
    (s,) = report.settlements
    assert [e.hat_key for e in s.acta] == list(HAT_KEYS)
    assert "AREAS FUERA DE LA MESA" in report.outcome.handoffs[0].artifact
    assert "las cinco areas estuvieron en la mesa" in report.outcome.handoffs[0].artifact
