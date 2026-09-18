"""Shocks deterministas en el marcador, y que le hacen a cada corredor (B / N2b).

El marcador medía si las decisiones de Kern son buenas bajo SUCIEDAD DE DATOS, y nunca
bajo DISRUPCION. El gemelo (``src/digital_twin.py``) sabe inyectar disrupciones y nadie
mide sus trazas (hallazgo H5); el marcador tiene piso, techo y captura normalizada y no
sabia inyectarlas. Esto une las dos mitades para que "captura bajo disrupcion" sea un
numero.

Lo que este archivo fija, en el orden en que importa:

1. **El mundo perturbado sigue siendo el mismo mundo.** El shock es una transformacion
   pura DESPUES del sorteo: misma semilla + mismo shock => mismo mundo, elemento a
   elemento, y fuera de la ventana nada se movio. Sin eso, comparar la corrida limpia
   contra la perturbada no mide el shock, mide dos sorteos distintos.
2. **Cada tipo hace lo que su nombre dice**, verificado CORRIENDO ``advance``, no
   leyendo la matriz: una orden emitida en la ventana llega exactamente cuando debe.
3. **Las dos definiciones que la investigacion dejo fijadas**: el TTR se cuenta desde la
   primera semana fuera de la banda (no desde el arranque del shock), y bajo un
   ``demand_surge`` las unidades perdidas se miden contra la demanda PERTURBADA -- las
   dos medidas viajan juntas y con signo (H5).
4. **Un shock que no golpea nada levanta**, en vez de producir una corrida que se lee
   como "Kern aguanto la disrupcion".
"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.simworld.scorer import score_shock, weekly_fill  # noqa: E402
from src.simworld.shocks import (  # noqa: E402
    SHOCK_KINDS,
    Shock,
    ShockError,
    apply_shocks,
    parse_shock,
)
from src.simworld.world import (  # noqa: E402
    WeekOutcome,
    advance,
    generate_world,
    initial_state,
)


def _mundo(seed: int = 11, n_skus: int = 3, n_suppliers: int = 1, weeks: int = 16):
    return generate_world(seed=seed, n_skus=n_skus, n_suppliers=n_suppliers, weeks=weeks,
                          regime="seasonal_trend")


# -- 1. el mundo perturbado sigue siendo el mismo mundo ---------------------------


def test_misma_semilla_y_mismo_shock_dan_el_mismo_mundo():
    """La garantia que sostiene todo el marcador, extendida al shock. Si el shock se
    sorteara adentro de la generacion, dos corridas del mismo experimento dejarian de
    ser comparables sin que nada avise."""
    shock = Shock("lead_time_spike", "*", 5, 3, 2)
    a = apply_shocks(_mundo(), (shock,))
    b = apply_shocks(_mundo(), (shock,))
    assert np.array_equal(a.demand, b.demand)
    assert np.array_equal(a.real_lead_weeks, b.real_lead_weeks)


def test_fuera_de_la_ventana_no_se_movio_nada():
    """Elemento a elemento, no "parecido": una diferencia de un solo dia fuera de la
    ventana haria que el exceso de costo atribuido al shock incluya ruido."""
    base = _mundo()
    con = apply_shocks(base, (Shock("lead_time_spike", "*", 5, 3, 2),))
    fuera = [t for t in range(base.weeks) if not 5 <= t < 8]
    assert np.array_equal(base.real_lead_weeks[:, fuera], con.real_lead_weeks[:, fuera])
    assert np.array_equal(base.demand, con.demand), "un shock de lead no toca la demanda"


def test_el_mundo_base_no_se_muta():
    """Los dos carriles (limpio y con shock) corren sobre el MISMO sorteo. Si aplicar el
    shock mutara el base, la corrida limpia correria sobre el mundo perturbado y la
    diferencia daria cero."""
    base = _mundo()
    lead_antes = base.real_lead_weeks.copy()
    demanda_antes = base.demand.copy()
    apply_shocks(base, (Shock("lead_time_spike", "*", 5, 3, 4),
                        Shock("demand_surge", "*", 5, 2, 3.0)))
    assert np.array_equal(base.real_lead_weeks, lead_antes)
    assert np.array_equal(base.demand, demanda_antes)


def test_sin_shocks_devuelve_el_mismo_objeto():
    """Una corrida sin shocks no puede diferir del comportamiento anterior ni por un
    redondeo: no se copia nada."""
    base = _mundo()
    assert apply_shocks(base, ()) is base


def test_aplicar_dos_veces_encadenado_acumula_y_eso_esta_documentado():
    """NO es idempotente, y no debe serlo: dos golpes duelen mas que uno. El test existe
    para que el dia que alguien "arregle" la idempotencia se entere de que la estaba
    rompiendo a proposito."""
    base = _mundo()
    shock = Shock("lead_time_spike", "*", 5, 3, 2)
    una = apply_shocks(base, (shock,))
    dos = apply_shocks(una, (shock,))
    assert dos.real_lead_weeks[0, 5] == una.real_lead_weeks[0, 5] + 2
    # Y la forma correcta de aplicar dos shocks: los dos en la misma llamada, sobre el
    # mundo base.
    juntos = apply_shocks(base, (shock, shock))
    assert np.array_equal(juntos.real_lead_weeks, dos.real_lead_weeks)


# -- 2. cada tipo hace lo que dice, CORRIENDO advance ------------------------------


def test_lead_time_spike_atrasa_la_orden_exactamente_las_semanas_del_shock():
    """Plantilla: ``test_order_arrives_after_the_real_lead_time_not_the_promised_one``.
    Se corre ``advance`` de verdad; no alcanza con mirar la matriz, porque lo que importa
    es cuando ACREDITA la llegada."""
    base = _mundo()
    t = 4
    extra = 3
    con = apply_shocks(base, (Shock("lead_time_spike", base.skus[0].sku, t, 2, extra),))
    lead_base = int(base.real_lead_weeks[0, t])
    assert t + lead_base + extra < base.weeks, "el fixture tiene que dejar llegar la orden"

    def _llegada(mundo) -> int:
        st = initial_state(mundo)
        for _ in range(t):
            st, _ = advance(mundo, st, {})
        st, _ = advance(mundo, st, {mundo.skus[0].sku: 100.0})
        for offset in range(1, mundo.weeks - t):
            st, out = advance(mundo, st, {})
            if out.received[0] > 0:
                return offset
        raise AssertionError("la orden nunca llego dentro del horizonte")

    assert _llegada(con) == _llegada(base) + extra


def test_lead_time_spike_no_toca_a_los_sku_de_otro_objetivo():
    base = _mundo()
    con = apply_shocks(base, (Shock("lead_time_spike", base.skus[0].sku, 4, 3, 5),))
    assert np.array_equal(base.real_lead_weeks[1:], con.real_lead_weeks[1:])
    assert not np.array_equal(base.real_lead_weeks[0], con.real_lead_weeks[0])


def test_supplier_outage_ninguna_orden_de_la_ventana_llega_antes_de_que_reabra():
    """El corte no es "tarda un poco mas": es que el proveedor no puede ni empezar hasta
    que reabre, y recien ahi corre su plazo prometido."""
    base = _mundo()
    inicio, largo = 3, 4
    con = apply_shocks(base, (Shock("supplier_outage", "Proveedor-1", inicio, largo),))
    prometido = con.suppliers[con.skus[0].supplier_idx].promised_lead_weeks

    for t in range(inicio, inicio + largo):
        llegada = t + int(con.real_lead_weeks[0, t])
        assert llegada >= inicio + largo, f"una orden de la semana {t} llega en {llegada}"
        assert llegada == max(t + int(base.real_lead_weeks[0, t]),
                              inicio + largo + prometido)


def test_supplier_outage_nunca_adelanta_una_llegada():
    """El ``max`` no es decoracion. Sin el, un SKU cuyo lead real ya era peor que el
    corte llegaria ANTES por culpa del shock -- un shock que mejora el mundo."""
    base = _mundo(seed=3, n_skus=6, n_suppliers=2, weeks=20)
    con = apply_shocks(base, (Shock("supplier_outage", "*", 2, 3),))
    assert (con.real_lead_weeks >= base.real_lead_weeks).all()


def test_demand_surge_multiplica_solo_la_ventana():
    base = _mundo()
    con = apply_shocks(base, (Shock("demand_surge", "*", 6, 4, 2.0),))
    ventana = range(6, 10)
    esperado = np.maximum(np.round(base.demand[:, ventana] * 2.0), 0.0)
    assert np.array_equal(con.demand[:, ventana], esperado)
    fuera = [t for t in range(base.weeks) if t not in ventana]
    assert np.array_equal(base.demand[:, fuera], con.demand[:, fuera])
    assert np.array_equal(base.real_lead_weeks, con.real_lead_weeks), "un surge no toca el lead"


def test_la_demanda_perturbada_sigue_siendo_unidades_enteras():
    """El sorteo base redondea; si el surge dejara medias unidades, la conservacion de
    masa de ``advance`` compararia contra otra aritmetica."""
    con = apply_shocks(_mundo(), (Shock("demand_surge", "*", 4, 3, 1.5),))
    assert np.array_equal(con.demand, np.round(con.demand))
    assert (con.demand >= 0).all()


def test_advance_conserva_la_masa_sobre_el_mundo_perturbado():
    """La invariante del motor no puede depender de si hubo shock."""
    mundo = apply_shocks(_mundo(), (Shock("demand_surge", "*", 4, 4, 2.5),
                                    Shock("lead_time_spike", "*", 9, 3, 2)))
    st = initial_state(mundo)
    for semana in range(mundo.weeks):
        antes = st.on_hand.copy()
        st, out = advance(mundo, st, {mundo.skus[0].sku: 20.0} if semana % 3 == 0 else {})
        assert np.allclose(antes + out.received, out.sold + out.on_hand_end)


# -- 3. un shock que no golpea nada levanta ---------------------------------------


def test_un_objetivo_que_no_existe_levanta_nombrando_los_que_si():
    base = _mundo()
    with pytest.raises(ShockError) as exc:
        apply_shocks(base, (Shock("lead_time_spike", "SKU-9999", 2, 2, 1),))
    assert "SKU-1000" in str(exc.value) and "Proveedor-1" in str(exc.value)


def test_un_shock_que_arranca_fuera_del_horizonte_levanta():
    """Se aplicaria sobre cero semanas y la corrida se leeria como "Kern aguanto"."""
    base = _mundo(weeks=10)
    with pytest.raises(ShockError, match="horizonte"):
        apply_shocks(base, (Shock("lead_time_spike", "*", 10, 2, 1),))


def test_una_ventana_que_se_pasa_del_final_golpea_lo_que_alcanza():
    """Distinto del caso de arriba: este SI golpea semanas, solo que menos de las
    pedidas. Recortar es correcto; rechazarlo obligaria a que el operador calcule el
    horizonte a mano."""
    base = _mundo(weeks=10)
    con = apply_shocks(base, (Shock("lead_time_spike", "*", 8, 5, 2),))
    assert (con.real_lead_weeks[:, 8:] == base.real_lead_weeks[:, 8:] + 2).all()


def test_demand_surge_contra_un_proveedor_levanta():
    """La demanda es de un SKU. "La demanda del proveedor 2 se duplico" es una frase sin
    referente que igual produciria numeros."""
    with pytest.raises(ShockError, match="PROVEEDOR"):
        apply_shocks(_mundo(), (Shock("demand_surge", "Proveedor-1", 6, 2, 2.0),))


def test_un_surge_en_las_primeras_semanas_se_rechaza_porque_el_arnes_lo_absorbe_solo():
    """La interaccion que hace que una corrida se lea al reves.

    ``initial_state`` dimensiona el stock de arranque con la media de las primeras
    ``INITIAL_STOCK_WEEKS`` semanas de demanda. Un surge que caiga ahi adentro se
    AUTO-ABSORBE: medido, un x3 en las semanas 0-3 triplica el stock inicial, y la
    corrida sale "absorbido" por construccion -- se leeria como robustez de Kern cuando
    lo que paso es que el mundo le regalo el inventario. Se rechaza en vez de avisar: un
    aviso al lado de un numero que se ve bien no lo desarma.
    """
    from src.simworld.world import INITIAL_STOCK_WEEKS, initial_state

    base = _mundo()
    with pytest.raises(ShockError, match="stock de arranque"):
        apply_shocks(base, (Shock("demand_surge", "*", 0, 4, 3.0),))
    # Y el limite es exacto: desde INITIAL_STOCK_WEEKS el arranque queda intacto.
    tardio = apply_shocks(base, (Shock("demand_surge", "*", INITIAL_STOCK_WEEKS, 4, 3.0),))
    assert np.array_equal(initial_state(tardio).on_hand, initial_state(base).on_hand)


def test_un_shock_de_suministro_temprano_si_se_permite():
    """El arranque se dimensiona solo con la DEMANDA, asi que un spike o un corte en las
    primeras semanas no lo mueve y no hay nada que rechazar."""
    from src.simworld.world import initial_state

    base = _mundo()
    for shock in (Shock("lead_time_spike", "*", 0, 3, 2), Shock("supplier_outage", "*", 0, 3)):
        con = apply_shocks(base, (shock,))
        assert np.array_equal(initial_state(con).on_hand, initial_state(base).on_hand)


def test_las_magnitudes_sin_sentido_para_su_tipo_levantan():
    with pytest.raises(ShockError, match="SEMANAS EXTRA"):
        Shock("lead_time_spike", "*", 2, 2, 0)
    with pytest.raises(ShockError, match="MULTIPLICADOR"):
        Shock("demand_surge", "*", 2, 2, 0.0)
    with pytest.raises(ShockError, match="desconocido"):
        Shock("meteorito", "*", 2, 2, 1)
    with pytest.raises(ShockError):
        Shock("lead_time_spike", "*", -1, 2, 1)
    with pytest.raises(ShockError):
        Shock("lead_time_spike", "*", 2, 0, 1)


def test_una_magnitud_no_finita_se_rechaza_antes_de_envenenar_el_mundo():
    """``nan < 1`` y ``nan <= 0`` son los dos False, asi que un NaN se colaba por las
    guardas de rango. Medido antes del arreglo: un ``demand_surge`` con NaN dejaba la
    matriz de demanda ENTERA en NaN y la corrida seguia produciendo numeros con forma de
    medicion. Un infinito hacia lo mismo con inf."""
    for magnitud in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ShockError, match="finito"):
            Shock("demand_surge", "*", 5, 2, magnitud)
        with pytest.raises(ShockError, match="finito"):
            Shock("lead_time_spike", "*", 5, 2, magnitud)


def test_un_spike_fraccionario_se_rechaza_en_vez_de_redondearse():
    """El lead del mundo se mide en semanas ENTERAS. Redondear era peor que rechazar:
    ``round(2.5)`` es 2 en Python (redondeo bancario), el mundo recibia un atraso de 2 y
    ``as_cli()`` publicaba ``magnitude 2.5`` en el reporte -- un shock que no ocurrio,
    escrito en el JSON que despues alguien cita."""
    with pytest.raises(ShockError, match="semanas enteras"):
        Shock("lead_time_spike", "*", 5, 2, 2.5)
    # Un float entero sigue siendo valido: es lo que produce `parse_shock`.
    assert Shock("lead_time_spike", "*", 5, 2, 2.0).magnitude == 2.0


def test_lo_que_dice_el_reporte_es_lo_que_recibio_el_mundo():
    """La propiedad que las dos guardas de arriba existen para sostener: la magnitud que
    ``as_cli()`` publica es exactamente la que se sumo al lead."""
    base = _mundo()
    shock = Shock("lead_time_spike", "*", 5, 2, 3)
    con = apply_shocks(base, (shock,))
    aplicado = int(con.real_lead_weeks[0, 5] - base.real_lead_weeks[0, 5])
    assert aplicado == int(shock.magnitude)
    assert shock.as_cli().endswith(f":{aplicado}")


def test_un_surge_fraccionario_si_es_valido():
    """Un multiplicador de demanda SI puede ser fraccionario: 1,5 veces la demanda es una
    frase con sentido, y el redondeo posterior es el mismo del sorteo base."""
    con = apply_shocks(_mundo(), (Shock("demand_surge", "*", 5, 3, 1.5),))
    assert np.array_equal(con.demand, np.round(con.demand))


def test_el_vocabulario_es_el_del_gemelo():
    """Dos modulos que dicen ``lead_time_spike`` y significan cosas distintas serian peor
    que dos nombres distintos."""
    from src.digital_twin import _DISRUPTION_KINDS

    assert set(SHOCK_KINDS) == set(_DISRUPTION_KINDS)


# -- el formato de linea de comandos ----------------------------------------------


def test_el_texto_del_cli_va_y_vuelve():
    shock = parse_shock("lead_time_spike:SKU-1003:10:3:2")
    assert (shock.kind, shock.target, shock.start_week, shock.weeks, shock.magnitude) == (
        "lead_time_spike", "SKU-1003", 10, 3, 2.0)
    assert parse_shock(shock.as_cli()) == shock


def test_solo_el_corte_de_proveedor_puede_omitir_la_magnitud():
    """Un default de severidad seria elegir el experimento por el operador."""
    assert parse_shock("supplier_outage:Proveedor-1:5:3").magnitude == 1.0
    with pytest.raises(ShockError, match="necesita magnitude"):
        parse_shock("lead_time_spike:SKU-1000:5:3")
    with pytest.raises(ShockError, match="mal formado"):
        parse_shock("lead_time_spike:SKU-1000:5")
    with pytest.raises(ShockError, match="mal formado"):
        parse_shock("lead_time_spike:SKU-1000:cinco:3:2")


# -- 4. score_shock: las dos definiciones que la investigacion dejo fijadas --------


def _traza(demandas, servidos) -> list[WeekOutcome]:
    salida = []
    for t, (d, s) in enumerate(zip(demandas, servidos, strict=True)):
        dd, ss = np.array([float(d)]), np.array([float(s)])
        salida.append(WeekOutcome(week=t, demand=dd, sold=ss, lost=dd - ss,
                                  received=np.zeros(1), ordered=np.zeros(1),
                                  on_hand_end=np.zeros(1)))
    return salida


_MUNDO_TRAZA = generate_world(seed=1, n_skus=1, n_suppliers=1, weeks=10)


def test_la_instancia_verificada_a_mano_del_plan_de_resilience():
    """``docs/superpowers/plans/2026-07-20-resilience_stress_test.md``: base sirve 10 de
    10 todas las semanas; con shock sirve 0, 0, 5 en t=3,4,5 y vuelve a 10.
    Unidades perdidas sobre [3, 6): (10-0)+(10-0)+(10-5) = 25. TTR 3."""
    base = _traza([10] * 10, [10] * 10)
    con = _traza([10] * 10, [10, 10, 10, 0, 0, 5, 10, 10, 10, 10])
    imp = score_shock(_MUNDO_TRAZA, base, con, Shock("supplier_outage", "*", 3, 3),
                      band=0.05, hold=1)
    assert imp.first_out_of_band_week == 3
    assert imp.ttr_weeks == 3.0
    assert imp.recovered is True
    assert imp.lost_units_post_shock == 25.0
    assert imp.lost_vs_baseline == 25.0
    assert imp.min_fill == 0.0


def test_el_ttr_se_cuenta_desde_la_primera_salida_de_banda_y_no_desde_el_shock():
    """La correccion v2 del Anexo A.1, y el caso donde las dos definiciones difieren.

    El shock arranca en t=2 y el servicio recien cae en t=5: "el 'cae en t+10' del outage
    no es lentitud de deteccion sino el pipeline -- la tienda vive del stock del CD hasta
    que se vacia". Cobrarle esas tres semanas al sistema como tiempo de recuperacion
    mediria la holgura que lo protegio, no la recuperacion.
    """
    base = _traza([10] * 10, [10] * 10)
    con = _traza([10] * 10, [10, 10, 10, 10, 10, 0, 0, 10, 10, 10])
    imp = score_shock(_MUNDO_TRAZA, base, con, Shock("supplier_outage", "*", 2, 3),
                      band=0.05, hold=1)
    assert imp.first_out_of_band_week == 5, "cae en 5, no en 2"
    assert imp.ttr_weeks == 2.0, "dos semanas desde la caida; desde el shock serian 5"


def test_hold_rechaza_un_rebote_de_una_sola_semana():
    """``min_hold`` del plan: una semana adentro y recaida no es recuperacion."""
    base = _traza([10] * 9, [10] * 9)
    con = _traza([10] * 9, [10, 0, 0, 10, 0, 0, 10, 10, 10])
    laxo = score_shock(_MUNDO_TRAZA, base, con, Shock("supplier_outage", "*", 1, 2),
                       band=0.05, hold=1)
    estricto = score_shock(_MUNDO_TRAZA, base, con, Shock("supplier_outage", "*", 1, 2),
                           band=0.05, hold=3)
    assert laxo.ttr_weeks == 2.0, "el rebote de t=3 cuenta con hold=1"
    assert estricto.ttr_weeks == 5.0, "con hold=3 recien cuenta desde t=6"


def test_no_recuperar_deja_el_ttr_vacio_y_no_un_numero_censurado():
    """Un TTR censurado con el resto del horizonte se promedia junto a los reales y deja
    de distinguirse de ellos. Se dice ``recovered=False`` y el numero queda vacio."""
    base = _traza([10] * 8, [10] * 8)
    con = _traza([10] * 8, [10, 10, 0, 0, 0, 0, 0, 0])
    imp = score_shock(_MUNDO_TRAZA, base, con, Shock("supplier_outage", "*", 2, 4),
                      band=0.05, hold=3)
    assert imp.recovered is False
    assert imp.ttr_weeks is None
    assert imp.first_out_of_band_week == 2


def test_un_shock_absorbido_lo_dice_en_vez_de_inventar_un_ttr():
    """Anexo A.1: con la holgura por defecto, un shock moderado no sale de la banda. Es
    un resultado real y frecuente, no una medicion fallida."""
    base = _traza([10] * 8, [10] * 8)
    con = _traza([10] * 8, [10] * 8)
    imp = score_shock(_MUNDO_TRAZA, base, con, Shock("lead_time_spike", "*", 2, 3, 2),
                      band=0.02, hold=3)
    assert imp.first_out_of_band_week is None
    assert imp.ttr_weeks is None
    assert imp.recovered is True
    assert imp.min_fill == 1.0


def test_la_banda_es_de_un_solo_lado():
    """Servir MEJOR que el baseline no es estar fuera de la banda. Con un corredor
    simetrico, una corrida que sobre-recupera figuraria como no recuperada."""
    base = _traza([10] * 6, [5] * 6)          # baseline fill 0,5
    con = _traza([10] * 6, [10] * 6)          # con shock sirve TODO: fill 1,0
    imp = score_shock(_MUNDO_TRAZA, base, con, Shock("demand_surge", "*", 1, 3, 2.0),
                      band=0.02, hold=2)
    assert imp.first_out_of_band_week is None


# -- H5: las dos medidas de unidades perdidas, juntas y con signo -----------------


def test_bajo_un_surge_con_servicio_pleno_lo_perdido_es_cero_y_vs_base_es_negativo():
    """El hallazgo H5, en una linea: "las unidades perdidas bajo un surge son
    demanda_disrupta - servido_disrupto, no baseline - servido". Medirlo contra el
    baseline da ~0 (o negativo) aunque el fill se hunda; medirlo contra la demanda
    perturbada dice la verdad. Las dos van, con nombres distintos."""
    base = _traza([10] * 8, [10] * 8)
    con = _traza([10, 10, 10, 20, 20, 20, 10, 10], [10, 10, 10, 20, 20, 20, 10, 10])
    imp = score_shock(_MUNDO_TRAZA, base, con, Shock("demand_surge", "*", 3, 3, 2.0))
    assert imp.lost_units_post_shock == 0.0, "se sirvio toda la demanda perturbada"
    assert imp.lost_vs_baseline == -30.0, "vendio 30 unidades MAS que sin el shock"


def test_lo_perdido_vs_baseline_no_se_recorta_a_cero():
    """Un ``max(0, ...)`` convertiria el -30 de arriba en 0 y borraria la unica senal de
    que el shock fue de demanda y no de suministro."""
    base = _traza([10] * 6, [10] * 6)
    con = _traza([30] * 6, [20] * 6)
    imp = score_shock(_MUNDO_TRAZA, base, con, Shock("demand_surge", "*", 0, 6, 3.0))
    assert imp.lost_units_post_shock == 60.0, "10 sin servir por semana x 6"
    assert imp.lost_vs_baseline == -60.0, "y aun asi vendio 10 mas por semana"


def test_el_exceso_de_costo_viaja_con_signo():
    """Un shock de suministro suele ABARATAR la ventana -- no se paga lo que no llega --
    y ese negativo se informa en vez de recortarse."""
    base = _traza([10] * 6, [10] * 6)
    con = _traza([10] * 6, [0] * 6)
    imp = score_shock(_MUNDO_TRAZA, base, con, Shock("supplier_outage", "*", 0, 6))
    # Sin ordenes ni stock en ninguna de las dos trazas, el unico costo que difiere es la
    # penalidad por quiebre: la corrida con shock cuesta MAS.
    assert imp.cost_excess_post_shock > 0


def test_dos_corridas_de_distinto_largo_no_se_comparan():
    base = _traza([10] * 6, [10] * 6)
    con = _traza([10] * 5, [10] * 5)
    with pytest.raises(ValueError, match="largo distinto"):
        score_shock(_MUNDO_TRAZA, base, con, Shock("supplier_outage", "*", 1, 2))


def test_una_semana_sin_demanda_da_fill_uno_y_no_nan():
    """Un NaN seria contagioso: ensuciaria el minimo y la comparacion contra la banda de
    todas las demas semanas."""
    fills = weekly_fill(_traza([10, 0, 10], [10, 0, 5]))
    assert list(fills) == [1.0, 1.0, 0.5]


# -- 5. punta a punta, por el CLI real ---------------------------------------------


def test_el_cli_produce_la_seccion_de_shock_y_la_captura_bajo_disrupcion(tmp_path):
    """La grilla entera con un shock: piso, techo y Kern sobre el mundo perturbado, mas
    la corrida limpia del mismo carril para poder atribuir."""
    from examples.run_world_sim import run_grid

    rep = run_grid(
        seed=4, n_skus=4, n_suppliers=2, weeks=16, levels=(0,), lanes=("forced",),
        replan_every=4, out_dir=tmp_path,
        shocks=(Shock("supplier_outage", "Proveedor-1", 6, 4),),
    )
    assert rep["shocks"] == ["supplier_outage:Proveedor-1:6:4:1"]
    entrada = rep["kern"]["forced-L0"]
    assert "capture_under_shock" in entrada
    assert entrada["capture_under_shock"] == entrada["capture"]
    impacto = entrada["shock"][0]
    assert impacto["shock_kind"] == "supplier_outage"
    assert impacto["start_week"] == 6 and impacto["end_week"] == 10
    # El piso y el techo tambien se miden: sin ellos no se sabe si el shock fue duro o si
    # Kern fue malo.
    assert len(rep["shock_floor"]["floor"]) == 1
    assert len(rep["shock_floor"]["ceiling"]) == 1
    assert (tmp_path / "report.json").exists()
    assert "Bajo disrupcion" in (tmp_path / "report.md").read_text(encoding="utf-8")


def test_sin_shocks_el_reporte_es_exactamente_el_de_antes(tmp_path):
    """El camino sin shock no paga nada: una sola pasada, sin seccion nueva y sin
    directorio de referencia."""
    from examples.run_world_sim import run_grid

    rep = run_grid(seed=4, n_skus=4, n_suppliers=2, weeks=16, levels=(0,),
                   lanes=("forced",), replan_every=4, out_dir=tmp_path)
    assert rep["shocks"] == []
    assert "shock_floor" not in rep
    assert "shock" not in rep["kern"]["forced-L0"]
    assert "capture_under_shock" not in rep["kern"]["forced-L0"]
    assert not (tmp_path / "sin-shock").exists()


def test_la_captura_bajo_disrupcion_puede_ser_negativa(tmp_path):
    """Y ese es el resultado que hay que QUERER ver antes que lo vea un cliente: un
    corte de proveedor donde Kern decide peor que un punto de reorden fijo. El arnes
    tiene que poder emitirlo, no protegerse de el."""
    from examples.run_world_sim import run_grid

    rep = run_grid(
        seed=4, n_skus=5, n_suppliers=2, weeks=20, levels=(0,), lanes=("forced",),
        replan_every=4, out_dir=tmp_path,
        shocks=(Shock("supplier_outage", "Proveedor-1", 8, 4),),
    )
    captura = rep["kern"]["forced-L0"]["capture_under_shock"]
    assert captura is not None
    assert captura < 0, f"esta semilla mide a Kern peor que el piso bajo el corte: {captura}"


def test_el_barrido_agrega_la_captura_bajo_disrupcion():
    """``agregar(metrica=...)``: la media de capturas limpias y la de capturas bajo
    disrupcion nunca se mezclan -- una media de las dos no seria de ninguna."""
    from src.verify.barrido import agregar

    reportes = [
        {"kern": {"forced-L0": {"lane": "forced", "level": 0,
                                "capture": 0.5, "capture_under_shock": -0.1}}},
        {"kern": {"forced-L0": {"lane": "forced", "level": 0,
                                "capture": 0.7, "capture_under_shock": 0.1}}},
    ]
    limpia = agregar(reportes)[0]
    bajo = agregar(reportes, metrica="capture_under_shock")[0]
    assert limpia.media == pytest.approx(0.6)
    assert bajo.media == pytest.approx(0.0)
    assert bajo.peor == pytest.approx(-0.1)


def test_una_entrada_sin_la_metrica_cuenta_como_no_medible():
    """Un reporte viejo (sin shock) agregado con la metrica de shock no puede inflar ni
    desinflar la media: sale del denominador y ``n_medibles`` lo dice."""
    from src.verify.barrido import agregar

    reportes = [
        {"kern": {"forced-L0": {"lane": "forced", "level": 0, "capture": 0.5}}},
        {"kern": {"forced-L0": {"lane": "forced", "level": 0,
                                "capture": 0.7, "capture_under_shock": 0.2}}},
    ]
    d = agregar(reportes, metrica="capture_under_shock")[0]
    assert d.n_mundos == 2 and d.n_medibles == 1
    assert d.media == pytest.approx(0.2)


def test_el_costo_del_shock_se_parte_en_anunciado_y_silencio(tmp_path):
    """``degradation`` reusada tal cual, con el mundo perturbado en el lugar del sucio.

    Y una igualdad que vale la pena fijar: el ``drop`` de esa atribucion -- que suma
    TODAS las semanas -- coincide con ``cost_excess_post_shock``, que suma solo desde el
    arranque del shock. Coinciden porque las semanas previas son identicas en las dos
    corridas: mismo mundo hasta la ventana, misma politica, mismos pedidos. Si algun dia
    dejan de coincidir, algo se filtro del mundo perturbado hacia atras en el tiempo.
    """
    from examples.run_world_sim import run_grid

    rep = run_grid(
        seed=4, n_skus=4, n_suppliers=2, weeks=16, levels=(0,), lanes=("forced",),
        replan_every=4, out_dir=tmp_path,
        shocks=(Shock("supplier_outage", "Proveedor-1", 6, 4),),
    )
    entrada = rep["kern"]["forced-L0"]
    deg = entrada["shock_degradation"]
    assert deg["drop"] == pytest.approx(entrada["shock"][0]["cost_excess_post_shock"])
    assert deg["silent"] == pytest.approx(deg["drop"] - deg["announced"])
    assert "Del costo del shock, cuanto aviso Kern" in (
        tmp_path / "report.md").read_text(encoding="utf-8")


def test_un_shock_de_suministro_dentro_del_calentamiento_se_rechaza_en_la_grilla(tmp_path):
    """El gemelo del caso anterior, del lado del suministro, y encontrado por la revision
    adversarial de esta misma entrega.

    Los corredores devuelven ``EMPTY_POLICY`` mientras tienen menos de ``WARMUP_WEEKS`` de
    historia, asi que no emiten NINGUNA orden en esas semanas. Un spike o un corte que
    termine ahi adentro perturba una matriz que nadie consulta. Medido antes de la
    guarda: ``cost_excess_post_shock`` exactamente 0,00 y ``first_out_of_band_week``
    ``None`` -- el reporte decia "absorbido" sobre una disrupcion que no ocurrio.

    La guarda vive en la grilla y NO en ``apply_shocks``: la transformacion del mundo es
    pura y no sabe que politica lo va a correr. El test de abajo fija esa division.
    """
    from examples.run_world_sim import WARMUP_WEEKS, run_grid

    with pytest.raises(ShockError, match="calentamiento"):
        run_grid(seed=4, n_skus=3, n_suppliers=1, weeks=16, levels=(0,), lanes=("forced",),
                 replan_every=4, out_dir=tmp_path,
                 shocks=(Shock("supplier_outage", "*", 0, WARMUP_WEEKS),))
    # Una ventana que CRUZA el calentamiento se acepta: ahi SI hay ordenes que perturbar.
    # La guarda rechaza el no-op GARANTIZADO, no promete que todo lo aceptado muerda --
    # que un shock aceptado no mueva el costo es un resultado legitimo (el mundo puede
    # tener holgura, o el carril puede no haber pedido nada esa semana), y confundir las
    # dos cosas convertiria la guarda en una promesa que no puede cumplir.
    rep = run_grid(seed=4, n_skus=3, n_suppliers=1, weeks=16, levels=(0,), lanes=("forced",),
                   replan_every=4, out_dir=tmp_path / "ok",
                   shocks=(Shock("supplier_outage", "*", 0, WARMUP_WEEKS + 2),))
    assert "shock" in rep["kern"]["forced-L0"]

    # Y una ventana bien adentro del horizonte, donde el carril si estaba pidiendo:
    # el costo se mueve, que es la prueba de que la perturbacion llega al motor.
    muerde = run_grid(seed=4, n_skus=4, n_suppliers=2, weeks=16, levels=(0,),
                      lanes=("forced",), replan_every=4, out_dir=tmp_path / "muerde",
                      shocks=(Shock("supplier_outage", "Proveedor-1", 6, 4),))
    assert muerde["kern"]["forced-L0"]["shock"][0]["cost_excess_post_shock"] != 0.0


def test_apply_shocks_sigue_aceptando_lo_que_la_grilla_rechaza():
    """La division de responsabilidades, fijada: ``apply_shocks`` es una transformacion
    pura del mundo y un test que corre ``advance`` a mano SI emite ordenes desde la
    semana 0. Meterle la regla del calentamiento la volveria inusable para eso."""
    con = apply_shocks(_mundo(), (Shock("supplier_outage", "*", 0, 3),))
    assert (con.real_lead_weeks[:, 0:3] >= _mundo().real_lead_weeks[:, 0:3]).all()


# -- lo que encontro la revision adversarial de esta entrega ----------------------


def test_un_tramo_sin_demanda_no_declara_recuperado_a_un_carril_muerto():
    """El peor error posible del marcador de shock, y el mas facil de no ver.

    ``weekly_fill`` le da 1,0 a una semana sin demanda (no se dejo de servir nada, y un
    NaN seria contagioso). Para la recuperacion eso NO es evidencia: nadie probo el
    servicio esa semana. Sin la guarda, tres semanas muertas alcanzaban el ``hold`` y un
    carril que no sirvio UNA unidad desde el shock salia ``recovered=True`` con TTR 2.
    """
    base = _traza([10] * 9, [10] * 9)
    demanda = [10, 10, 10, 10, 0, 0, 0, 10, 10]
    servido = [10, 10, 0, 0, 0, 0, 0, 0, 0]
    con = _traza(demanda, servido)
    imp = score_shock(_MUNDO_TRAZA, base, con, Shock("supplier_outage", "*", 2, 3),
                      band=0.05, hold=3)
    assert imp.recovered is False
    assert imp.ttr_weeks is None


def test_una_semana_sin_demanda_tampoco_rompe_una_racha_buena():
    """Neutral en los dos sentidos: no acredita (no hubo prueba) y no rompe (no hubo
    falla). Recortar solo de un lado convertiria una pausa en una recaida."""
    base = _traza([10] * 9, [10] * 9)
    demanda = [10, 10, 10, 10, 10, 0, 10, 10, 10]
    servido = [10, 10, 0, 10, 10, 0, 10, 10, 10]
    con = _traza(demanda, servido)
    imp = score_shock(_MUNDO_TRAZA, base, con, Shock("supplier_outage", "*", 2, 1),
                      band=0.05, hold=3)
    assert imp.recovered is True
    assert imp.ttr_weeks == 1.0, "la racha arranca en t=3 y la semana muerta no la corta"


def test_varios_shocks_se_miden_como_UNA_perturbacion():
    """Con dos shocks hay un solo mundo perturbado: la ventana del primero incluiria el
    dano del segundo, y dos impactos dirian que cada shock hizo todo. Se emite uno, con
    la ventana combinada y los dos nombres."""
    from src.simworld.shocks import ShockWindow, combined_window

    uno = Shock("supplier_outage", "Proveedor-1", 6, 3)
    assert combined_window((uno,)) is uno, "con uno solo, el shock mismo"

    dos = combined_window((uno, Shock("demand_surge", "*", 10, 4, 2.0)))
    assert isinstance(dos, ShockWindow)
    assert dos.kind == "supplier_outage+demand_surge"
    assert dos.target == "Proveedor-1, *"
    assert (dos.start_week, dos.end_week) == (6, 14)


def test_la_grilla_emite_un_solo_impacto_aunque_haya_dos_shocks(tmp_path):
    from examples.run_world_sim import run_grid

    rep = run_grid(
        seed=4, n_skus=4, n_suppliers=2, weeks=20, levels=(0,), lanes=("forced",),
        replan_every=4, out_dir=tmp_path,
        shocks=(Shock("supplier_outage", "Proveedor-1", 6, 3),
                Shock("demand_surge", "*", 10, 4, 2.0)),
    )
    impactos = rep["kern"]["forced-L0"]["shock"]
    assert len(impactos) == 1
    assert impactos[0]["shock_kind"] == "supplier_outage+demand_surge"
    assert rep["shocks"] == ["supplier_outage:Proveedor-1:6:3:1", "demand_surge:*:10:4:2"]


def test_una_linea_base_de_otro_experimento_se_rechaza_en_vez_de_dar_una_regresion(tmp_path):
    """Una captura bajo disrupcion contra una linea base limpia sale peor POR
    CONSTRUCCION. Sin la guarda, el driver lo reportaba como "Kern decide peor que cuando
    se grabo la referencia" -- una frase falsa sobre el producto."""
    from src.verify.barrido import (
        ContextoIncomparable,
        Distribucion,
        comparar_con_linea_base,
        guardar_linea_base,
    )

    d = Distribucion(carril="forced", nivel=0, n_mundos=3, n_medibles=3, media=0.5,
                     desviacion=0.1, p10=0.4, mediana=0.5, p90=0.6, peor=0.4, mejor=0.6)
    ruta = guardar_linea_base([d], tmp_path / "lb.json",
                              contexto={"metrica": "capture", "shocks": []})
    # Mismo experimento: compara normal.
    assert comparar_con_linea_base(
        [d], ruta, contexto={"metrica": "capture", "shocks": []}) == ([], [])
    # Otro experimento: se niega.
    with pytest.raises(ContextoIncomparable, match="otro experimento"):
        comparar_con_linea_base([d], ruta, contexto={
            "metrica": "capture_under_shock", "shocks": ["supplier_outage:*:6:3:1"]})
    # Y sin contexto sigue comparando, como siempre (compatibilidad).
    assert comparar_con_linea_base([d], ruta) == ([], [])
