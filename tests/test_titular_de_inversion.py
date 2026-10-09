"""El tope por fecha de fin tiene que llegar al capital, y solo hacia abajo.

Hasta el 2026-09-23 `cap_policy` movia `reorder_point`, `order_up_to` y `Q`, y
`cycle_investment` / `ss_investment` / `investment` por SKU y el titular
`requested_investment` se calculaban sobre la politica SIN topear. Medido sobre una
cartera real de Olist (`documentation/KERN_FRACCION_CON_FECHA_DE_FIN.md`), el titular
salia identico al centavo mientras el deck anunciaba unidades sacadas del plan y las
OPCIONES se describian como la unica opcion que RESTA capital.

Lo que estos tests fijan no es el numero sino las DOS mitades de la regla
(`src.horizon.capped_cycle_stock`): el capital baja con la cantidad, y el buffer NO se
toca -- el tope dice "no compres mas de lo que podes vender", no "bajale el nivel de
servicio".
"""
from __future__ import annotations

import datetime as dt

import pandas as pd
import pytest

from jobs import inventory_optimization as inv
from jobs import qa
from src.horizon import capped_cycle_stock

PERIODOS = 40


def _frame(pid: str = "SKU-A", *, media: float = 40.0, costo: float = 10.0) -> pd.DataFrame:
    """Una serie larga y estable: el motor dimensiona sin negarse."""
    inicio = dt.date(2024, 1, 1)
    filas = []
    for i in range(PERIODOS):
        filas.append(
            {
                "date": pd.Timestamp(inicio + dt.timedelta(weeks=i)),
                "product_id": pid,
                "quantity": media + (3.0 if i % 2 else -3.0),
                "unit_cost": costo,
                "lead_time_days": 14,
            }
        )
    return pd.DataFrame(filas)


def _corre(frame, fechas=None):
    return inv.run(frame, end_dates=fechas or {})


def _fin(frame, semanas: float):
    """Una fecha de fin a `semanas` del final del historico."""
    ultimo = frame["date"].max()
    return {frame["product_id"].iat[0]: ultimo + pd.Timedelta(weeks=semanas)}


# --------------------------------------------------------------------------- motor


def test_el_ciclo_de_un_sQ_topeado_usa_la_identidad_del_motor():
    """`(s,Q)` -> `Q/2`, la misma identidad que aplica el redondeo a lote legal."""
    assert capped_cycle_stock(100.0, 0.0, remaining_demand=10_000.0, order_quantity=60.0) == 30.0


def test_el_capital_nunca_sube():
    """Las dos reglas son `min`: un tope solo puede LIBERAR capital, jamas pedir mas."""
    assert capped_cycle_stock(20.0, 0.0, remaining_demand=10_000.0, order_quantity=999.0) == 20.0
    assert capped_cycle_stock(20.0, 0.0, remaining_demand=10_000.0) == 20.0


def test_el_restante_acota_el_ciclo_contando_el_buffer():
    """`ciclo + buffer <= restante`: el capital del SKU no supera lo que se puede vender."""
    assert capped_cycle_stock(50.0, 30.0, remaining_demand=40.0) == 10.0


def test_el_buffer_es_el_piso_y_el_ciclo_se_va_a_cero_antes_de_tocarlo():
    """Si el buffer solo ya supera el restante, el ciclo se anula y ahi TERMINA.

    Encoger el buffer seria bajar en silencio un nivel de servicio que nadie renegocio.
    """
    assert capped_cycle_stock(50.0, 80.0, remaining_demand=40.0) == 0.0


def test_sin_restante_medido_el_ciclo_queda_intacto():
    """Un `None` no es un cero: sin restante no hay cota que aplicar."""
    assert capped_cycle_stock(50.0, 10.0, remaining_demand=None) == 50.0


# ------------------------------------------------------------------- punta a punta


def test_el_titular_baja_cuando_el_tope_muerde():
    """La prueba del defecto: el mismo archivo, con fecha y sin ella."""
    f = _frame()
    sin = _corre(f)
    con = _corre(f, _fin(f, 2))

    assert con.n_horizon_capped == 1, "el caso de prueba tiene que topear de verdad"
    assert con.requested_investment < sin.requested_investment
    assert con.horizon_capital_freed_usd > 0


def test_el_capital_liberado_es_exactamente_la_resta_de_las_filas():
    """El numero del resumen es la suma de lo que el cliente ve fila por fila."""
    f = _frame()
    con = _corre(f, _fin(f, 2))
    por_fila = sum(
        (r.cycle_investment_before_horizon or 0.0) - (r.cycle_investment or 0.0)
        for r in con.recommendations
        if r.cycle_investment_before_horizon is not None
    )
    assert con.horizon_capital_freed_usd == pytest.approx(por_fila)


def test_el_titular_baja_exactamente_lo_que_dice_haber_liberado():
    f = _frame()
    sin, con = _corre(f), _corre(f, _fin(f, 2))
    caida = sin.requested_investment - con.requested_investment
    assert caida == pytest.approx(con.horizon_capital_freed_usd, rel=1e-9)


def test_el_tope_NO_toca_el_buffer():
    """La otra mitad del diseno: mismo safety stock y misma inversion de seguridad."""
    f = _frame()
    sin, con = _corre(f), _corre(f, _fin(f, 2))
    a = {r.product_id: r for r in sin.recommendations}
    for r in con.recommendations:
        if r.horizon_verdict != "applied":
            continue
        assert r.safety_stock == pytest.approx(a[r.product_id].safety_stock)
        assert r.ss_investment == pytest.approx(a[r.product_id].ss_investment)


def test_las_dos_identidades_del_registro_sobreviven_al_tope():
    """`ss_investment == ss x costo` y `investment == ciclo + ss`, en la fila topeada."""
    f = _frame()
    con = _corre(f, _fin(f, 2))
    for r in con.recommendations:
        if r.horizon_verdict != "applied":
            continue
        assert r.ss_investment == pytest.approx(r.safety_stock * r.unit_cost)
        assert r.investment == pytest.approx(r.cycle_investment + r.ss_investment)


def test_una_fecha_lejana_no_mueve_un_centavo():
    """`inert`: la fecha existe, no ata, y el capital no se puede mover."""
    f = _frame()
    sin, con = _corre(f), _corre(f, _fin(f, 500))
    assert con.n_horizon_capped == 0
    assert con.requested_investment == pytest.approx(sin.requested_investment)
    assert con.horizon_capital_freed_usd == 0.0
    assert all(r.cycle_investment_before_horizon is None for r in con.recommendations)


def test_sin_fechas_el_capital_sale_IDENTICO():
    """El control que vuelve seguro desplegar esto sobre la base instalada."""
    f = _frame()
    a, b = _corre(f), _corre(f)
    assert a.requested_investment == b.requested_investment
    assert b.horizon_capital_freed_usd == 0.0
    for x, y in zip(a.recommendations, b.recommendations):
        assert x.cycle_investment == y.cycle_investment
        assert x.investment == y.investment
        assert y.cycle_investment_before_horizon is None


def test_el_before_horizon_del_capital_solo_aparece_si_se_movio():
    """Blanco, nunca cero: un tope puede bajar el objetivo sin liberar capital."""
    f = _frame()
    con = _corre(f, _fin(f, 2))
    for r in con.recommendations:
        if r.cycle_investment_before_horizon is None:
            continue
        assert r.cycle_investment_before_horizon > r.cycle_investment


# --------------------------------------------------------------------------- QA


def test_qa_rechaza_un_capital_que_el_tope_SUBIO():
    """Si un cambio futuro hiciera que topear pida MAS capital, la compuerta lo para."""
    f = _frame()
    con = _corre(f, _fin(f, 2))
    fila = next(r for r in con.recommendations if r.horizon_verdict == "applied")
    roto = [q for q in qa._horizon_issues(
        type(fila)(**{**fila.__dict__, "cycle_investment_before_horizon": fila.cycle_investment - 1.0})
    ) if "not above the capped one" in q]
    assert roto, "QA tiene que acusar un 'antes' que no supera al 'despues'"


def test_qa_rechaza_que_el_tope_encoja_el_buffer():
    """La invariante que fija la decision de diseno: el buffer no se toca."""
    f = _frame()
    con = _corre(f, _fin(f, 2))
    fila = next(r for r in con.recommendations if r.horizon_verdict == "applied")
    roto = [q for q in qa._horizon_issues(
        type(fila)(**{**fila.__dict__, "ss_investment": fila.ss_investment * 0.5})
    ) if "safety-stock investment" in q]
    assert roto, "QA tiene que acusar un ss_investment que no es ss x costo"


def test_qa_rechaza_un_capital_previo_en_una_fila_no_topeada():
    f = _frame()
    con = _corre(f, _fin(f, 500))
    fila = con.recommendations[0]
    roto = [q for q in qa._horizon_issues(
        type(fila)(**{**fila.__dict__, "cycle_investment_before_horizon": 123.0})
    ) if "pre-cap cycle investment" in q]
    assert roto


def test_la_corrida_topeada_pasa_la_compuerta_entera():
    f = _frame()
    con = _corre(f, _fin(f, 2))
    assert qa.verify(con) == []
