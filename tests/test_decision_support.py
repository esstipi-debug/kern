"""Tests for src/decision_support.py — guardrail calculators vs known numbers + the
never-dead-end invariant (every card hands back >=2 ranked options, exactly one
recommended)."""

import pytest

from src import decision_support as ds

ALL_CARDS = "all_cards"


def _make_one_of_each():
    """One representative card from each calculator (healthy/green inputs)."""
    return [
        ds.viabilidad_operativa(entrada_unidades_dia=400, salida_unidades_dia=400,
                                num_operarios=5, unidades_hora_operario=25, horas_turno=8),
        ds.negociacion_proveedor(costo_unitario=60, precio_venta=100, margen_minimo_pct=0.3,
                                 lead_time_actual_dias=7, demanda_std_dia=5),
        ds.cambiar_proveedor(actual_precio=10, actual_otif=0.95, actual_lead=5, actual_defectos=0.01,
                             alt_precio=12, alt_otif=0.85, alt_lead=8, alt_defectos=0.03),
        ds.niveles_inventario(demanda_media_periodo=100, demanda_std_periodo=20, lead_time_periodos=2,
                              nivel_servicio=0.95, costo_unitario=10, costo_ordenar=50,
                              holding_rate_anual=0.25),
        ds.aprobar_compra(monto_compra=5000, inventario_actual_valor=50000,
                          cogs_anual=400000, ventas_anual=600000),
        ds.liquidar_stock(unidades_excedente=100, costo_unitario=20, precio_actual=50),
    ]


# ---- the system-wide invariant ------------------------------------------------------
def test_every_card_offers_at_least_two_ranked_options_one_recommended():
    for card in _make_one_of_each():
        assert len(card.opciones) >= 2, card.titulo
        recommended = [o for o in card.opciones if o.recommended]
        assert len(recommended) == 1, f"{card.titulo}: {len(recommended)} recommended"


def test_every_card_serializes_and_has_required_shape():
    for card in _make_one_of_each():
        d = card.to_dict()
        assert set(d) == {"titulo", "veredicto", "guardrails", "opciones", "por_que", "cita", "supuestos"}
        assert d["veredicto"]["nivel"] in {ds.VERDE, ds.AMARILLO, ds.ROJO}
        assert len(d["guardrails"]) >= 1


# ---- 1. capacidad -------------------------------------------------------------------
def test_capacidad_verde_when_utilizacion_below_band():
    # carga 800/dia, capacidad 5*25*8 = 1000 -> utilizacion 80% -> verde
    card = ds.viabilidad_operativa(entrada_unidades_dia=400, salida_unidades_dia=400,
                                   num_operarios=5, unidades_hora_operario=25, horas_turno=8)
    assert card.veredicto.nivel == ds.VERDE
    util = next(g.valor for g in card.guardrails if g.etiqueta == "Utilizacion")
    assert util == "80%"


def test_capacidad_rojo_and_backlog_when_overloaded():
    # carga 1800/dia, capacidad 2*10*8 = 160 -> no factible
    card = ds.viabilidad_operativa(entrada_unidades_dia=900, salida_unidades_dia=900,
                                   num_operarios=2, unidades_hora_operario=10, horas_turno=8)
    assert card.veredicto.nivel == ds.ROJO
    backlog = next(g.valor for g in card.guardrails if g.etiqueta == "Backlog diario")
    assert "1,640" in backlog  # 1800 - 160


def test_capacidad_zero_load_raises_spanish():
    with pytest.raises(ValueError, match="al menos una unidad"):
        ds.viabilidad_operativa(entrada_unidades_dia=0, salida_unidades_dia=0,
                                num_operarios=1, unidades_hora_operario=1, horas_turno=1)


# ---- 2. negociacion -----------------------------------------------------------------
def test_negociacion_verde_with_room():
    # margen actual 40% >= minimo 30% + 5% -> verde; walk-away = 100*(1-0.3) = 70
    card = ds.negociacion_proveedor(costo_unitario=60, precio_venta=100,
                                    margen_minimo_pct=0.3, lead_time_actual_dias=7)
    assert card.veredicto.nivel == ds.VERDE
    walk = next(g.valor for g in card.guardrails if "walk-away" in g.etiqueta)
    assert walk == "$70"


def test_negociacion_rojo_below_minimum_margin():
    # margen actual 20% < minimo 30% -> rojo
    card = ds.negociacion_proveedor(costo_unitario=80, precio_venta=100,
                                    margen_minimo_pct=0.3, lead_time_actual_dias=7)
    assert card.veredicto.nivel == ds.ROJO


# ---- 3. cambiar de proveedor --------------------------------------------------------
def test_proveedor_recommends_dominant_incumbent():
    # actual mejor en todo -> gana actual
    card = ds.cambiar_proveedor(actual_precio=10, actual_otif=0.95, actual_lead=5, actual_defectos=0.01,
                                alt_precio=14, alt_otif=0.80, alt_lead=9, alt_defectos=0.05)
    rec = next(g.valor for g in card.guardrails if g.etiqueta == "Recomendado")
    assert rec == "Actual"


# ---- 4. niveles de inventario -------------------------------------------------------
def test_inventario_rojo_below_safety_stock():
    card = ds.niveles_inventario(demanda_media_periodo=100, demanda_std_periodo=20,
                                 lead_time_periodos=2, nivel_servicio=0.95, costo_unitario=10,
                                 costo_ordenar=50, holding_rate_anual=0.25, stock_actual=10)
    assert card.veredicto.nivel == ds.ROJO


def test_inventario_verde_in_band():
    card = ds.niveles_inventario(demanda_media_periodo=100, demanda_std_periodo=20,
                                 lead_time_periodos=2, nivel_servicio=0.95, costo_unitario=10,
                                 costo_ordenar=50, holding_rate_anual=0.25, stock_actual=300)
    assert card.veredicto.nivel == ds.VERDE


# ---- 5. aprobar compra / liquidar ---------------------------------------------------
def test_compra_rojo_blows_dio_ceiling():
    card = ds.aprobar_compra(monto_compra=50000, inventario_actual_valor=100000,
                             cogs_anual=400000, ventas_anual=600000, dio_objetivo_max=90)
    assert card.veredicto.nivel == ds.ROJO
    cap = next(g.valor for g in card.guardrails if g.etiqueta == "Cuanto es sano comprar")
    assert cap == "$0"  # already over the healthy inventory level


def test_liquidar_verde_recovers_cost():
    card = ds.liquidar_stock(unidades_excedente=100, costo_unitario=20, precio_actual=50)
    assert card.veredicto.nivel == ds.VERDE  # markdown 35 >= cost 20


def test_liquidar_rojo_only_salvage():
    card = ds.liquidar_stock(unidades_excedente=100, costo_unitario=20, precio_actual=10,
                             valor_recuperacion_pct=0.4)
    assert card.veredicto.nivel == ds.ROJO  # markdown clamps to salvage floor (8)
