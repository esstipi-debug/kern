"""Tests for src/sop_engine/cash_plan.py -- WHEN the purchase plan's money leaves.

Lo que estos tests defienden, en una linea: un total de compra sin fecha de pago
no es una decision de caja, y un plazo asumido que no se declara es peor que no
tener plazo, porque se lee como medido.
"""

from __future__ import annotations

from datetime import date

import pytest

from src.sop_engine.cash_plan import (
    CASH,
    DAYS_PER_WEEK,
    DEFAULT_NET_DAYS,
    DEFAULT_PAYMENT_TERMS,
    NET_30,
    TERMS_ASSUMED,
    TERMS_STATED,
    build_cash_commitment_schedule,
    parse_payment_terms,
    payment_week,
    resolve_payment_terms,
)
from src.sop_engine.demand_plan import NO_SHIFT_SOURCE, DemandPlanLine
from src.sop_engine.purchase_plan import SkuPurchaseInputs, build_purchase_plan


def _plan(*skus: tuple[str, float, float, str], budget: float | None = None):
    """(product_id, demand, unit_cost, payment_terms) -> las lineas del plan."""
    demanda = tuple(
        DemandPlanLine(pid, dem, 0.0, dem, NO_SHIFT_SOURCE, "sin shift") for pid, dem, _, _ in skus
    )
    entradas = {
        pid: SkuPurchaseInputs(pid, on_hand=0.0, unit_cost=cost, payment_terms=terms)
        for pid, _, cost, terms in skus
    }
    lineas, _ = build_purchase_plan(demanda, entradas, budget=budget)
    return lineas


# ---- parse_payment_terms: lo que un export de AP realmente trae ----

@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("net_30", ("net_30", 30)),
        ("NET 30", ("net_30", 30)),
        ("net-30", ("net_30", 30)),
        ("  60  ", ("net_60", 60)),
        ("45 days", ("net_45", 45)),
        ("15 dias", ("net_15", 15)),
        ("cash", (CASH, 0)),
        ("Contado", (CASH, 0)),
        ("COD", (CASH, 0)),
        ("net_0", (CASH, 0)),
    ],
)
def test_parse_payment_terms_normaliza_las_grafias_que_existen(texto, esperado):
    assert parse_payment_terms(texto) == esperado


@pytest.mark.parametrize("texto", ["", "   ", "45/60 split", "on delivery maybe", "net_treinta"])
def test_parse_payment_terms_revienta_en_vez_de_adivinar(texto):
    """Un termino que nadie sabe leer NO puede caer al default: el default es
    para la linea que no dijo nada, no para la que dijo algo raro."""
    with pytest.raises(ValueError):
        parse_payment_terms(texto)


def test_el_error_nombra_el_texto_que_recibio():
    with pytest.raises(ValueError, match="45/60 split"):
        parse_payment_terms("45/60 split")


# ---- resolve_payment_terms: el default es explicito y viaja declarado ----

def test_un_plazo_declarado_sale_marcado_como_leido():
    assert resolve_payment_terms("net_60") == ("net_60", 60, TERMS_STATED)


def test_un_plazo_ausente_cae_al_default_y_sale_marcado_como_supuesto():
    token, dias, fuente = resolve_payment_terms("")
    assert (token, dias) == (DEFAULT_PAYMENT_TERMS, DEFAULT_NET_DAYS)
    assert fuente is TERMS_ASSUMED or fuente == TERMS_ASSUMED
    assert DEFAULT_PAYMENT_TERMS == NET_30 and DEFAULT_NET_DAYS == 30


def test_el_default_se_puede_cambiar_sin_dejar_de_ser_un_supuesto():
    assert resolve_payment_terms("", default="net_60") == ("net_60", 60, TERMS_ASSUMED)


# ---- payment_week: el vencimiento cae en una semana, no dura semanas ----

@pytest.mark.parametrize(("dias", "semana"), [(0, 0), (6, 0), (7, 1), (30, 4), (60, 8), (90, 12)])
def test_payment_week_bucketea_el_vencimiento_hacia_abajo(dias, semana):
    assert payment_week(dias) == semana


def test_payment_week_rechaza_dias_negativos():
    with pytest.raises(ValueError):
        payment_week(-1)


# ---- el calendario ----

def test_el_calendario_agrupa_cada_linea_en_la_semana_en_que_vence():
    lineas = _plan(
        ("CONTADO", 100.0, 1.0, "cash"),
        ("A30", 100.0, 2.0, "net_30"),
        ("B60", 100.0, 3.0, "net_60"),
    )
    cal = build_cash_commitment_schedule(lineas)
    por_semana = {w.week: w.disbursement for w in cal.weeks}
    assert por_semana[0] == pytest.approx(100.0)   # 100 u * 1
    assert por_semana[4] == pytest.approx(200.0)   # 100 u * 2
    assert por_semana[8] == pytest.approx(300.0)   # 100 u * 3
    assert cal.total_committed == pytest.approx(600.0)


def test_el_calendario_no_tiene_huecos_y_acumula():
    """Una semana sin egreso es informacion -- es la semana en la que se puede
    respirar -- asi que se emite en cero en vez de saltearse."""
    cal = build_cash_commitment_schedule(_plan(("A", 10.0, 1.0, "net_60")))
    assert [w.week for w in cal.weeks] == list(range(9))
    assert all(w.disbursement == 0.0 for w in cal.weeks[:8])
    assert cal.weeks[8].disbursement == pytest.approx(10.0)
    assert [w.cumulative for w in cal.weeks] == [0.0] * 8 + [pytest.approx(10.0)]
    assert cal.weeks[8].n_lines == 1


def test_sin_tope_semanal_el_calendario_sale_sin_veredicto_y_lo_dice():
    cal = build_cash_commitment_schedule(_plan(("A", 100.0, 5.0, "net_30")))
    assert cal.cap_per_week is None
    assert cal.n_weeks_over_cap == 0
    assert cal.first_week_over_cap is None
    assert all(w.within_cap and w.cap is None and w.headroom is None for w in cal.weeks)
    assert "no weekly cash cap supplied" in cal.summary


def test_con_tope_semanal_marca_la_semana_que_no_entra_y_cuanto_falta():
    cal = build_cash_commitment_schedule(
        _plan(("A", 100.0, 5.0, "net_30"), ("B", 100.0, 1.0, "cash")), cap_per_week=200.0,
    )
    semana_0, semana_4 = cal.weeks[0], cal.weeks[4]
    assert semana_0.disbursement == pytest.approx(100.0)
    assert semana_0.within_cap and semana_0.headroom == pytest.approx(100.0)
    assert semana_4.disbursement == pytest.approx(500.0)
    assert not semana_4.within_cap and semana_4.headroom == pytest.approx(-300.0)
    assert cal.n_weeks_over_cap == 1
    assert cal.first_week_over_cap == 4
    assert "exceed the 200 usd weekly cap" in cal.summary


def test_un_tope_negativo_revienta():
    with pytest.raises(ValueError):
        build_cash_commitment_schedule(_plan(("A", 10.0, 1.0, "cash")), cap_per_week=-1.0)


def test_sin_as_of_las_semanas_son_relativas_y_el_resumen_lo_declara():
    """src/ es determinista: no hay date.today() que invente un lunes."""
    cal = build_cash_commitment_schedule(_plan(("A", 10.0, 1.0, "net_30")))
    assert all(w.week_start == "" for w in cal.weeks)
    assert "relative to the order date" in cal.summary


def test_con_as_of_cada_semana_trae_su_fecha_iso():
    cal = build_cash_commitment_schedule(
        _plan(("A", 10.0, 1.0, "net_30")), as_of=date(2026, 8, 10),
    )
    assert cal.weeks[0].week_start == "2026-08-10"
    assert cal.weeks[4].week_start == "2026-09-07"  # 4 * 7 dias
    assert "relative to the order date" not in cal.summary


def test_los_sku_cuyo_plazo_se_asumio_salen_nombrados():
    """Lo que no se puede permitir es que el calendario se lea como medido."""
    cal = build_cash_commitment_schedule(
        _plan(("LEIDO", 10.0, 1.0, "net_15"), ("ASUMIDO", 10.0, 1.0, "")),
    )
    assert cal.terms_assumed_for == ("ASUMIDO",)
    assert "ASSUMED" in cal.summary and "ASUMIDO" in cal.summary


def test_una_linea_que_no_ordena_nada_no_reserva_semana():
    """Sin pedido no sale plata: bookear 0 usd en la semana 4 seria inventar un
    compromiso."""
    lineas = _plan(("CUBIERTO", 0.0, 5.0, ""))
    assert lineas[0].order_value == 0.0
    cal = build_cash_commitment_schedule(lineas)
    assert cal.total_committed == 0.0
    assert [w.week for w in cal.weeks] == [0]
    assert cal.weeks[0].n_lines == 0


def test_una_linea_que_no_ordena_nada_tampoco_infla_el_aviso_de_plazo_supuesto():
    """Reproduccion del hallazgo (d).

    terms_assumed_for se llenaba ANTES del filtro `order_value <= 0`, asi que un
    SKU que no pide NADA entraba igual en el Finding "Payment terms ASSUMED, not
    read" y en su KPI. Ese aviso habla de las FECHAS DE ESTE CALENDARIO: una
    linea sin desembolso no tiene semana de pago aca, asi que ningun renglon del
    calendario descansa sobre su plazo supuesto y nombrarla infla la advertencia.

    Antes del fix: terms_assumed_for == ("CUBIERTO", "PIDE").
    """
    cal = build_cash_commitment_schedule(
        _plan(("CUBIERTO", 0.0, 5.0, ""), ("PIDE", 10.0, 5.0, "")),
    )
    assert cal.terms_assumed_for == ("PIDE",)
    assert "1 line(s)" in cal.summary and "CUBIERTO" not in cal.summary


def test_una_semana_que_calza_exacto_con_el_tope_no_sale_excedida():
    """Reproduccion del hallazgo (c).

    El desembolso semanal es una suma corrida de productos quantity*unit_cost:
    tres lineas de 1.10 usd contra un tope de 3.30 dan 3.3000000000000003, y el
    `<=` pelado reportaba como EXCEDIDO al plan que calza exacto -- Finding en
    rojo, "over by 0 usd". El veredicto lleva tolerancia; headroom se deja como
    la resta exacta, que es el numero, no el juicio.

    Antes del fix: within_cap=False, n_weeks_over_cap=1.
    """
    lineas = _plan(("A", 1.1, 1.0, "cash"), ("B", 1.1, 1.0, "cash"), ("C", 1.1, 1.0, "cash"))
    cal = build_cash_commitment_schedule(lineas, cap_per_week=3.3)
    semana = cal.weeks[0]
    assert semana.disbursement != 3.3, "la premisa del bug: el float no da redondo"
    assert semana.within_cap
    assert cal.n_weeks_over_cap == 0
    assert cal.first_week_over_cap is None
    assert "every week fits" in cal.summary


def test_un_dolar_de_mas_sobre_el_tope_sigue_siendo_un_exceso():
    """La contracara del hallazgo (c): la tolerancia es para el ruido del float,
    no para perdonar plata que de verdad no entra."""
    cal = build_cash_commitment_schedule(
        _plan(("A", 4.0, 1.0, "cash")), cap_per_week=3.3,
    )
    assert not cal.weeks[0].within_cap
    assert cal.n_weeks_over_cap == 1
    assert cal.weeks[0].headroom == pytest.approx(-0.7)


def test_la_semana_pico_es_la_que_mas_le_pide_al_banco():
    cal = build_cash_commitment_schedule(
        _plan(("A", 100.0, 1.0, "cash"), ("B", 100.0, 9.0, "net_30")),
    )
    assert cal.peak_week is not None
    assert cal.peak_week.week == 4
    assert cal.peak_week.disbursement == pytest.approx(900.0)


def test_una_cartera_toda_al_contado_sigue_teniendo_calendario():
    """El caso borde que un calendario expresado solo en numeros perderia: la
    semana 0 es una semana, no una ausencia de semana."""
    cal = build_cash_commitment_schedule(_plan(("A", 50.0, 2.0, "contado")), cap_per_week=50.0)
    assert len(cal.weeks) == 1
    assert cal.weeks[0].week == 0
    assert cal.weeks[0].disbursement == pytest.approx(100.0)
    assert cal.n_weeks_over_cap == 1
    assert DAYS_PER_WEEK == 7


# ---- el signo menos: un plazo negativo no puede salir positivo ----

@pytest.mark.parametrize("texto", ["-30", "net -30", "net_-30", "-0", "-15 days"])
def test_un_plazo_negativo_revienta_en_vez_de_salir_positivo(texto):
    """Reproduccion del hallazgo: parse_payment_terms('-30') devolvia
    ('net_30', 30) EN SILENCIO.

    El .replace('-', ' ') existe para que 'net-30' se lea, pero se comia
    tambien el signo: un plazo negativo salia POSITIVO, contra el contrato
    explicito del propio docstring (un termino ilegible revienta, no se
    adivina). Un vencimiento negativo dataria el desembolso ANTES de la orden
    que lo causa, y payment_week ya rechaza net_days < 0 -- asi que el unico
    modo de que llegara ahi era este, con el signo ya perdido.
    """
    with pytest.raises(ValueError, match="unreadable payment terms"):
        parse_payment_terms(texto)


@pytest.mark.parametrize(
    "texto, esperado",
    [("net-30", ("net_30", 30)), ("NET-60", ("net_60", 60)), ("30-60", None)],
)
def test_el_guion_que_separa_dos_tokens_sigue_siendo_un_separador(texto, esperado):
    """La otra mitad: el arreglo no puede romper 'net-30', que es la grafia por
    la que el replace existe. Un guion ENTRE dos tokens separa; uno que ABRE un
    token es un signo. '30-60' (plazo partido) sigue siendo ilegible, como
    antes."""
    if esperado is None:
        with pytest.raises(ValueError, match="unreadable payment terms"):
            parse_payment_terms(texto)
    else:
        assert parse_payment_terms(texto) == esperado
