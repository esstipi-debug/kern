"""Entre "no puedo dimensionar" y "aca esta tu politica" hay un escalon.

`inventory_optimization` se niega a dimensionar un SKU con menos de
`MIN_PERIODS_FOR_POLICY` (6) periodos, y esa negativa es correcta: sin sigma no hay
`z * sigma_x` que valga, y un buffer sin dispersion detras es el numero
confiado-pero-equivocado que la compuerta existe para impedir.

Lo que NO se seguia de eso es el silencio. Son dos preguntas distintas:

  "cuanto buffer"  -> necesita sigma. Sigue sin contestarse.
  "cuanto hace falta para llegar a la proxima reposicion" -> necesita la media de lo
      que se vendio y el plazo del proveedor, y los dos son hechos MEDIDOS que el
      archivo ya trae.

Medido en el marcador (`documentation/KERN_POR_QUE_BAJO_EL_PISO.md`), contestar la
segunda con silencio costaba el 56 % de la brecha contra un punto de reorden fijo: Kern
no podia decidir durante el primer tercio de la corrida y compraba CERO unidades ahi.

La cobertura provisional es `mu_x` de la ecuacion 6.4 -- la identidad del propio motor,
`src/risk_period.py` -- con el termino de seguridad OMITIDO. Y por eso NO es una
politica, cosa que el registro declara en la FORMA de sus campos: disparador si, buffer
no, z no, pronostico no.

LAS TRES FORMAS DE QUE ESTE ESCALON SE VUELVA UNA TRAMPA
--------------------------------------------------------
1. Que se lea como el piso de arriba. Cubre la MEDIA, o sea que se espera que se quede
   corta cerca de la mitad de las veces; un entregable que no lo diga vendio una
   politica.
2. Que su capital entre al titular. El plan optimizado pasa por el allocator de
   presupuesto y esto no; sumarlos haria parecer dimensionado lo que no lo esta.
3. Que se cuele donde una politica manda de verdad -- la planilla de pedidos del
   cliente --, pisando el numero propio del cliente bajo la etiqueta `kern_optimized`.
"""

from __future__ import annotations

import types
from dataclasses import replace

import pandas as pd
import pytest

from jobs import qa
from jobs.inventory_optimization import (
    MIN_PERIODS_FOR_COVER,
    NOT_FULLY_SIZED_POLICY_KINDS,
    PROVISIONAL_POLICY_KINDS,
    PROVISIONAL_REVIEW_PERIODS,
    UNSIZED_POLICY_KINDS,
    run,
    unsized_note,
)
from src.forecasting import MIN_PERIODS_FOR_POLICY
from src.risk_period import demand_over_risk_period, provisional_cover

_PERIOD = "W"
_LEAD_DIAS = 14.0        # dos semanas: tau = 2 + 1 de revision


def _archivo(cantidades, *, pid: str = "X", unit_cost: float = 10.0,
             lead_days: float = _LEAD_DIAS) -> pd.DataFrame:
    fechas = pd.date_range("2026-01-04", periods=len(cantidades), freq=_PERIOD)
    return pd.DataFrame([
        {"date": f, "product_id": pid, "quantity": float(q), "unit_cost": unit_cost,
         "lead_time_days": lead_days}
        for f, q in zip(fechas, cantidades, strict=True)
    ])


def _rec(df: pd.DataFrame, pid: str = "X", **kw):
    reporte = run(df, period_freq=_PERIOD, **kw)
    return reporte, {r.product_id: r for r in reporte.recommendations}[pid]


# ---------------------------------------------------------------------------
# 1. El escalon existe, y sigue habiendo un piso debajo
# ---------------------------------------------------------------------------


def test_debajo_del_minimo_para_una_media_la_negativa_se_queda_entera():
    """Con una sola observacion "el promedio" es esa observacion. Llamarlo tasa de
    demanda seria inventar, asi que la negativa no se ablanda: se queda entera."""
    _, r = _rec(_archivo([40]))

    assert r.policy_kind == "insufficient_history"
    for campo in ("forecast", "reorder_point", "safety_stock", "z_factor"):
        assert getattr(r, campo) is None


@pytest.mark.parametrize("n", range(MIN_PERIODS_FOR_COVER, MIN_PERIODS_FOR_POLICY))
def test_entre_los_dos_minimos_sale_una_cobertura(n):
    _, r = _rec(_archivo([40] * n))

    assert r.policy_kind == "provisional_cover"
    assert r.n_periods == n
    assert r.reorder_point is not None and r.reorder_point > 0


def test_con_historia_de_sobra_sigue_saliendo_una_politica_de_verdad():
    """El escalon no puede comerse el piso de arriba."""
    _, r = _rec(_archivo([20 + (i % 4) for i in range(24)]))

    assert r.policy_kind in {"(s, Q)", "(R, S)"}
    assert r.safety_stock is not None and r.z_factor is not None


# ---------------------------------------------------------------------------
# 2. Es la identidad del motor, no una segunda formula
# ---------------------------------------------------------------------------


def test_la_cobertura_es_mu_x_de_la_ecuacion_del_motor():
    """Si fuera una formula nueva, dos lineas del mismo entregable estarian cubriendo
    ventanas distintas sin decirlo."""
    assert provisional_cover(40.0, 3.0, 1.0) == demand_over_risk_period(
        40.0, 7.0, 3.0, 0.0, review_period=1.0
    ).mean_demand


def test_la_aritmetica_de_la_linea_cierra_y_viaja_en_el_registro():
    cantidades = [40, 44, 38, 47, 41]
    _, r = _rec(_archivo(cantidades))

    media = sum(cantidades) / len(cantidades)
    assert r.observed_mean_per_period == pytest.approx(media)
    # tau = plazo en periodos + el MISMO periodo de revision que usa el (R,S) del job
    assert r.cover_periods == pytest.approx(r.lead_periods + PROVISIONAL_REVIEW_PERIODS)
    assert r.reorder_point == pytest.approx(media * r.cover_periods)
    # Disparador y objetivo son el mismo numero: se repone HASTA la cobertura, sin lote.
    assert r.order_up_to == pytest.approx(r.reorder_point)
    assert r.order_quantity is None


def test_un_plazo_mas_corto_da_una_cobertura_mas_chica():
    """La cobertura depende del plazo, que es el hecho medido que la justifica."""
    _, largo = _rec(_archivo([40] * 4, lead_days=28.0))
    _, corto = _rec(_archivo([40] * 4, lead_days=7.0))

    assert largo.reorder_point > corto.reorder_point


# ---------------------------------------------------------------------------
# 3. No es una politica, y la FORMA de los campos es como lo declara
# ---------------------------------------------------------------------------


def test_una_cobertura_no_trae_buffer_ni_z_ni_pronostico():
    _, r = _rec(_archivo([40] * 4))

    assert r.safety_stock is None, "un buffer sin sigma detras es el numero prohibido"
    assert r.z_factor is None
    assert r.forecast is None, (
        "un pronostico es una extrapolacion que la compuerta prohibe citar; la media "
        "medida viaja en observed_mean_per_period, que es otra cosa"
    )


def test_qa_pasa_sobre_una_cobertura_bien_formada():
    reporte, _ = _rec(_archivo([40] * 4))
    assert qa.verify(reporte) == []


@pytest.mark.parametrize("campo", ["safety_stock", "z_factor", "forecast"])
def test_qa_rechaza_una_cobertura_que_trae_lo_que_no_puede_traer(campo):
    reporte, r = _rec(_archivo([40] * 4))
    mutado = replace(reporte, recommendations=[replace(r, **{campo: 3.0})])

    issues = qa.verify(mutado)
    assert any("a cover has no dispersion behind it" in i for i in issues), issues
    assert any(campo in i for i in issues), issues


def test_qa_rechaza_una_cobertura_sin_disparador():
    """La direccion contraria: una cobertura que no dice cuanto tener no sirve de nada."""
    reporte, r = _rec(_archivo([40] * 4))
    mutado = replace(reporte, recommendations=[replace(r, reorder_point=None)])

    issues = qa.verify(mutado)
    assert any("still has to say how much to hold" in i for i in issues), issues


def test_qa_rechaza_una_cobertura_cuya_cuenta_no_cierra():
    """El entregable publica `cobertura = media x periodos`. Si el numero no es ese
    producto, la planilla muestra una cuenta que no da."""
    reporte, r = _rec(_archivo([40] * 4))
    mutado = replace(reporte, recommendations=[
        replace(r, reorder_point=r.reorder_point * 2.0, order_up_to=r.reorder_point * 2.0)
    ])

    issues = qa.verify(mutado)
    assert any("is not the observed mean" in i for i in issues), issues


def test_qa_le_aplica_su_PROPIA_invariante_de_evidencia_no_una_exencion():
    """Una cobertura descansa en una media, y una media necesita dos observaciones.
    Eximirla de la invariante de evidencia seria abrirle un agujero; lo que se hace es
    darle la suya."""
    reporte, r = _rec(_archivo([40] * 4))
    mutado = replace(reporte, recommendations=[replace(r, n_periods=1)])

    issues = qa.verify(mutado)
    assert any("below the" in i and "minimum for an average" in i for i in issues), issues


# ---------------------------------------------------------------------------
# 4. El capital: aparte del titular, y nunca escondido
# ---------------------------------------------------------------------------


def _cartera_mixta() -> pd.DataFrame:
    fechas = pd.date_range("2026-01-04", periods=24, freq=_PERIOD)
    filas = [{"date": d, "product_id": "LARGO", "quantity": 20.0 + (i % 4),
              "unit_cost": 40.0, "lead_time_days": _LEAD_DIAS}
             for i, d in enumerate(fechas)]
    filas += [{"date": fechas[-i - 1], "product_id": "NUEVO", "quantity": 42.0,
               "unit_cost": 10.0, "lead_time_days": _LEAD_DIAS} for i in range(4)]
    return pd.DataFrame(filas)


def test_el_capital_de_la_cobertura_va_aparte_del_plan_optimizado():
    reporte = run(_cartera_mixta(), period_freq=_PERIOD)
    cobertura = {r.product_id: r for r in reporte.recommendations}["NUEVO"]

    assert reporte.n_provisional_cover == 1
    assert reporte.provisional_investment == pytest.approx(cobertura.investment)
    assert cobertura.investment > 0, "son unidades que alguien va a comprar"
    # ...y el titular NO las incluye: ese plan paso por el allocator y este no.
    assert reporte.requested_investment == pytest.approx(
        sum(r.investment for r in reporte.recommendations
            if r.policy_kind not in PROVISIONAL_POLICY_KINDS)
    )
    assert qa.verify(reporte) == []


def test_qa_rechaza_un_reporte_que_esconde_el_capital_de_la_cobertura():
    reporte = run(_cartera_mixta(), period_freq=_PERIOD)
    mutado = replace(reporte, provisional_investment=0.0)

    issues = qa.verify(mutado)
    assert any("provisional investment !=" in i for i in issues), issues


# ---------------------------------------------------------------------------
# 5. Lo que el operador lee
# ---------------------------------------------------------------------------


def test_la_prosa_dice_las_TRES_cosas_que_tiene_que_decir():
    reporte = run(_cartera_mixta(), period_freq=_PERIOD)
    _, cola = unsized_note(reporte)

    assert "PROVISIONAL COVER" in cola, "que el numero existe"
    assert "NO safety stock" in cola and "fall short about half the time" in cola, (
        "que no tiene buffer detras, y la consecuencia de eso"
    )
    assert "counted separately" in cola, "que su capital va aparte del titular"


def test_una_cartera_sin_coberturas_no_gana_una_frase_que_no_dice_nada():
    reporte = run(_archivo([20 + (i % 4) for i in range(24)]), period_freq=_PERIOD)
    titular, cola = unsized_note(reporte)

    assert titular is None and cola == ""


def test_las_options_llevan_la_cobertura_como_RESIDUAL_no_como_algo_ya_decidido():
    from scm_agent.tool_options import inventory_options

    reporte = run(_cartera_mixta(), period_freq=_PERIOD)
    outcome = inventory_options(reporte)
    textos = [r.description for r in outcome.residuals]

    assert any("provisional cover" in t for t in textos), textos
    riesgo = next(r.risk_if_skipped for r in outcome.residuals
                  if "provisional cover" in r.description)
    assert "running short about half the time" in riesgo
    assert "not the safer one" in riesgo, (
        "dejar el SKU en cero es la otra opcion, y el residual tiene que decir que "
        "tampoco es gratis -- si no, el operador lee 'ante la duda, nada'"
    )


def test_la_planilla_la_muestra_con_su_aritmetica():
    from jobs import deliverables

    _, r = _rec(_archivo([40] * 4))
    fila = deliverables._row(r)

    assert fila["reorder_point_s"] == pytest.approx(round(r.reorder_point, 2))
    assert fila["provisional_mean_per_period"] == pytest.approx(40.0)
    assert fila["provisional_cover_periods"] is not None
    assert fila["safety_stock"] is None


# ---------------------------------------------------------------------------
# 6. Donde NO puede entrar
# ---------------------------------------------------------------------------


def test_no_se_cuela_en_la_planilla_de_pedidos_del_cliente():
    """`package_specs` enhebra la politica del optimizador dentro de
    `excel_replenishment` bajo la etiqueta `kern_optimized`. Una cobertura sin sigma
    detras pisando el numero propio del cliente, en nombre de Kern, es exactamente la
    afirmacion a la que no tiene derecho. Viaja como OPCION, no como reemplazo."""
    from scm_agent.package_specs import _optimized_targets_from_inventory

    reporte = run(_cartera_mixta(), period_freq=_PERIOD)
    targets = _optimized_targets_from_inventory(
        {"inventory_optimization": reporte}
    )["optimized_targets"]

    assert "NUEVO" not in targets
    assert "LARGO" in targets


def test_las_invariantes_por_sku_de_coherencia_la_saltan():
    """Presuponen buffer y z, que una cobertura tiene en `None` a proposito. Bajarselas
    a todo el resto para que entre seria abrir un agujero en el gate de todas las
    politicas de verdad."""
    from src.verify.coherencia import POLITICAS_SALTADAS, verificar

    assert POLITICAS_SALTADAS == NOT_FULLY_SIZED_POLICY_KINDS
    assert PROVISIONAL_POLICY_KINDS <= POLITICAS_SALTADAS
    assert not (PROVISIONAL_POLICY_KINDS & UNSIZED_POLICY_KINDS), (
        "una cobertura NO es una linea sin dimensionar: trae un numero"
    )
    assert verificar(run(_cartera_mixta(), period_freq=_PERIOD)).coherente


# ---------------------------------------------------------------------------
# 7. El marcador la ejecuta, que es de lo que se trataba
# ---------------------------------------------------------------------------


def test_el_corredor_del_marcador_toma_la_cobertura_con_su_media_medida(monkeypatch):
    """`forecast` es `None` en una cobertura, asi que un carril que exija pronostico la
    descartaria -- justo las lineas que la cobertura existe para poder ejecutar."""
    from src.simworld import runners

    cobertura = types.SimpleNamespace(
        product_id="NUEVO", reorder_point=126.0, order_up_to=126.0, order_quantity=None,
        forecast=None, observed_mean_per_period=42.0,
    )
    sin_nada = types.SimpleNamespace(
        product_id="SIN", reorder_point=None, order_up_to=None, order_quantity=None,
        forecast=None, observed_mean_per_period=None,
    )
    resultado = types.SimpleNamespace(
        status="ok", summary="s", qa_issues=[], guided=None, confidence=1.0,
        report=types.SimpleNamespace(recommendations=[cobertura, sin_nada],
                                     intake_quality=None, intake_quality_threshold=0.2),
    )
    monkeypatch.setattr(runners, "export_history", lambda *a, **k: a[2])
    monkeypatch.setattr(runners, "Orchestrator",
                        lambda **k: types.SimpleNamespace(run=lambda *a, **k2: resultado))

    pol = runners.kern_policy(None, [], level=0, seed=0, out_dir="/tmp/x", forced=True)

    assert pol.reorder_point["NUEVO"] == pytest.approx(126.0)
    assert pol.forecast["NUEVO"] == pytest.approx(42.0), "la tasa sale de la media medida"
    assert "SIN" not in pol.reorder_point, "sin cobertura NI politica se sigue omitiendo"
