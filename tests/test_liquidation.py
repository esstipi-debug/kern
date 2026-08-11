"""Tests for the markdown-liquidation engine (src/liquidation.py).

The crossover of E&O classification and clearance pricing: for every excess/dead
SKU, decide HOW to dispose of it at a price (elasticity-derived clearance,
documented default markdown, or a salvage recovery rate) and size the cash
recovered vs. writing the stock to zero. Pure/deterministic — no I/O.
"""

from __future__ import annotations

import math

import pytest

from src.excess_obsolete import SkuStock
from src.liquidation import (
    DEFAULT_DISCOUNT,
    ELASTICITY,
    SALVAGE,
    LiquidationLine,
    LiquidationReport,
    announces_discount,
    plan_liquidation,
)

# A price history that fits a clean constant-elasticity curve q = 160 * p**-2:
#   demand_at(4) = 160/16 = 10 ;  demand_at(2) = 160/4 = 40  ->  elasticity = -2.
_ELASTIC_HISTORY = ([4.0, 2.0, 4.0, 2.0], [10.0, 40.0, 10.0, 40.0])
_ELASTIC_MEDIAN_PRICE = 3.0  # median of [4, 2, 4, 2]


def _mixed_stocks() -> list[SkuStock]:
    return [
        # A: excess with real price-response history -> elasticity path.
        SkuStock(product_id="A", on_hand=1000.0, daily_demand=1.0, unit_cost=5.0),
        # B: excess, price known but flat (no elasticity) -> default discount.
        SkuStock(product_id="B", on_hand=500.0, daily_demand=1.0, unit_cost=4.0),
        # C: excess, no price history at all -> salvage.
        SkuStock(product_id="C", on_hand=200.0, daily_demand=1.0, unit_cost=3.0),
        # D: dead (no demand), no history -> salvage.
        SkuStock(product_id="D", on_hand=100.0, daily_demand=0.0, unit_cost=2.0),
        # E: healthy (cover under target) -> excluded entirely.
        SkuStock(product_id="E", on_hand=10.0, daily_demand=1.0, unit_cost=9.0),
    ]


def _mixed_history() -> dict[str, tuple[list[float], list[float]]]:
    return {
        "A": _ELASTIC_HISTORY,
        "B": ([10.0, 10.0, 10.0, 10.0], [5.0, 6.0, 4.0, 5.0]),  # flat price
    }


def _line(report: LiquidationReport, pid: str) -> LiquidationLine:
    return next(line for line in report.lines if line.product_id == pid)


def test_elasticity_line_clears_within_horizon_at_a_marked_down_price() -> None:
    report = plan_liquidation(_mixed_stocks(), _mixed_history(), horizon_weeks=13.0)
    a = _line(report, "A")
    assert a.method == ELASTICITY
    assert a.classification == "excess"
    # excess_units = on_hand - target_cover_days * daily_demand = 1000 - 90 = 910
    assert a.units_to_clear == pytest.approx(910.0)
    assert a.at_risk_value == pytest.approx(910.0 * 5.0)  # units * unit_cost
    # clears exactly over the horizon: demand_at(price)*13 = 910 -> price = sqrt(160/70)
    expected_price = math.sqrt(160.0 / (910.0 / 13.0))
    assert a.clearance_price == pytest.approx(expected_price)
    assert a.clearance_price < _ELASTIC_MEDIAN_PRICE  # a genuine markdown
    assert a.weeks_to_clear == pytest.approx(13.0)
    assert a.recovered_value == pytest.approx(expected_price * 910.0)


def test_default_discount_line_uses_the_documented_markdown_pct() -> None:
    report = plan_liquidation(_mixed_stocks(), _mixed_history(), default_markdown_pct=0.40)
    b = _line(report, "B")
    assert b.method == DEFAULT_DISCOUNT
    assert b.clearance_price == pytest.approx(10.0 * 0.60)  # 40% off the flat $10 price
    assert b.units_to_clear == pytest.approx(410.0)  # 500 - 90
    assert b.recovered_value == pytest.approx(6.0 * 410.0)
    # conservative clear time at the current (pre-markdown) demand rate: 410 / (1*7)
    assert b.weeks_to_clear == pytest.approx(410.0 / 7.0)


def test_salvage_line_prices_a_fraction_of_cost_basis_per_unit() -> None:
    """No demand curve to solve against is a reason to price off the OTHER side of
    the economics (cost), not a reason to hand back an empty price column."""
    report = plan_liquidation(_mixed_stocks(), _mixed_history(), salvage_recovery_pct=0.30)
    c = _line(report, "C")
    assert c.method == SALVAGE
    assert c.clearance_price == pytest.approx(0.30 * 3.0)  # salvage_recovery_pct * unit_cost
    assert c.at_risk_value == pytest.approx(110.0 * 3.0)  # (200 - 90) * 3
    assert c.recovered_value == pytest.approx(0.30 * 110.0 * 3.0)
    assert math.isinf(c.weeks_to_clear)


def test_dead_stock_without_elasticity_falls_back_to_salvage() -> None:
    report = plan_liquidation(_mixed_stocks(), _mixed_history())
    d = _line(report, "D")
    assert d.classification == "dead"
    assert d.method == SALVAGE
    assert d.clearance_price == pytest.approx(0.30 * 2.0)  # default salvage pct * unit_cost
    assert d.units_to_clear == pytest.approx(100.0)  # whole on-hand for dead stock
    assert math.isinf(d.weeks_to_clear)


def test_healthy_skus_are_excluded() -> None:
    report = plan_liquidation(_mixed_stocks(), _mixed_history())
    assert all(line.product_id != "E" for line in report.lines)
    assert report.n_excess + report.n_dead == len(report.lines)


def test_lines_ranked_by_cash_at_risk_descending() -> None:
    report = plan_liquidation(_mixed_stocks(), _mixed_history())
    at_risk = [line.at_risk_value for line in report.lines]
    assert at_risk == sorted(at_risk, reverse=True)
    assert [line.product_id for line in report.lines] == ["A", "B", "C", "D"]


def test_report_rollups_and_method_counts() -> None:
    report = plan_liquidation(_mixed_stocks(), _mixed_history())
    assert report.n_excess == 3  # A, B, C
    assert report.n_dead == 1  # D
    assert report.n_elasticity == 1
    assert report.n_default_discount == 1
    assert report.n_salvage == 2
    assert report.total_at_risk == pytest.approx(4550.0 + 1640.0 + 330.0 + 200.0)
    assert report.total_recovered == pytest.approx(
        sum(line.recovered_value for line in report.lines)
    )
    assert 0.0 <= report.recovery_pct <= 2.0
    assert report.recovery_pct == pytest.approx(
        report.total_recovered / report.total_at_risk
    )


def test_no_price_history_makes_every_line_salvage_but_still_priced() -> None:
    """The degraded path is still a priced plan: every line names its method AND
    quotes a per-unit number. "We could not fit an elasticity" must never reach the
    client as a blank price column."""
    report = plan_liquidation(_mixed_stocks(), price_history=None, salvage_recovery_pct=0.30)
    assessed = [line for line in report.lines]
    assert assessed  # excess + dead still assessed
    assert all(line.method == SALVAGE for line in assessed)
    assert all(line.clearance_price is not None for line in assessed)
    assert all(line.current_price is None for line in assessed)  # nothing to discount FROM
    assert all(line.discount_pct is None for line in assessed)


def test_recovered_never_negative_and_recovery_pct_zero_when_nothing_at_risk() -> None:
    # A single healthy SKU -> no assessed lines, zero-safe rollups.
    healthy = [SkuStock(product_id="H", on_hand=1.0, daily_demand=1.0, unit_cost=5.0)]
    report = plan_liquidation(healthy)
    assert report.lines == ()
    assert report.total_at_risk == 0.0
    assert report.total_recovered == 0.0
    assert report.recovery_pct == 0.0


def test_floor_ratio_clamps_the_clearance_price_off_the_floor() -> None:
    # A high floor (>= the freely-solved clearance price) must lift the price to it.
    report = plan_liquidation(
        _mixed_stocks(), _mixed_history(), horizon_weeks=13.0, floor_ratio=0.5
    )
    a = _line(report, "A")
    floor = 0.5 * 5.0  # floor_ratio * unit_cost = 2.5
    assert a.clearance_price is not None
    assert a.clearance_price >= floor - 1e-9


@pytest.mark.parametrize(
    "kwargs",
    [
        {"default_markdown_pct": 0.0},
        {"default_markdown_pct": 1.0},
        {"salvage_recovery_pct": -0.1},
        {"salvage_recovery_pct": 1.5},
        {"horizon_weeks": 0.0},
        {"floor_ratio": -0.1},
    ],
)
def test_out_of_range_parameters_raise(kwargs: dict) -> None:
    with pytest.raises(ValueError):
        plan_liquidation(_mixed_stocks(), _mixed_history(), **kwargs)


def test_a_positive_slope_fit_falls_through_to_a_heuristic_not_elasticity() -> None:
    """Regression: a flat/upward-sloping fitted demand curve (elasticity >= 0) must
    never take the elasticity branch - it would recommend cutting price to sell FEWER
    modeled units, an economically inverted 'markdown'. Excess falls through to
    default_discount (the price is still known, just not usable for a curve solve)."""
    stocks = [SkuStock(product_id="P", on_hand=1000.0, daily_demand=1.0, unit_cost=5.0)]
    # price and quantity co-move (both rise together) -> elasticity > 0.
    history = {"P": ([2.0, 4.0, 2.0, 4.0], [10.0, 40.0, 10.0, 40.0])}
    report = plan_liquidation(stocks, history, horizon_weeks=13.0)
    line = _line(report, "P")
    assert line.method != ELASTICITY
    assert line.method == DEFAULT_DISCOUNT


def test_duplicate_product_ids_never_cross_contaminate_cost_or_demand() -> None:
    """Regression: two stock rows sharing a product_id must each be planned using
    ONLY their own unit_cost/daily_demand. A prior bug keyed cost/demand lookups by
    product_id in a last-wins dict, so the row with the true $5 cost silently
    inherited a same-id row's $1000 floor, producing a >100% 'recovery' on a line
    that was actually salvage-priced far above its own cost basis."""
    stocks = [
        SkuStock(product_id="DUP", on_hand=1000.0, daily_demand=1.0, unit_cost=5.0),
        SkuStock(product_id="DUP", on_hand=1000.0, daily_demand=1.0, unit_cost=1000.0),
    ]
    history = {"DUP": ([4.0, 2.0, 4.0, 2.0], [10.0, 40.0, 10.0, 40.0])}
    report = plan_liquidation(stocks, history, horizon_weeks=13.0, floor_ratio=1.0)
    assert len(report.lines) == 2
    # each line's floor is its OWN unit_cost * floor_ratio, so neither can recover
    # more than its own at-risk value (100%) - cross-contamination would blow past it.
    for line in report.lines:
        assert line.recovery_pct <= 1.0 + 1e-9


# --- El trade-off en dinero, no solo "bajá el precio" -------------------------


def test_el_markdown_reporta_descuento_margen_y_cobertura_liberada():
    """Un precio de liquidacion sin el % de descuento, el margen y la cobertura que
    libera es "sugerir bajar el precio": no se puede aprobar ni rechazar."""
    stock = SkuStock(product_id="SKU-EXC", on_hand=900.0, daily_demand=1.0, unit_cost=8.0,
                     days_since_last_sale=10)
    historial = {"SKU-EXC": ([20.0 - i * 0.5 for i in range(12)], [5 + i * 2 for i in range(12)])}
    linea = plan_liquidation([stock], historial).lines[0]

    assert linea.method == "elasticity"
    assert linea.current_price is not None and linea.current_price > 0
    assert linea.discount_pct == pytest.approx(1 - linea.clearance_price / linea.current_price)
    assert linea.gross_margin_value == pytest.approx(
        linea.recovered_value - linea.units_to_clear * 8.0
    )
    # 810 u de exceso a 1 u/dia de demanda: liberan 810 dias de cobertura.
    assert linea.cover_days_released == pytest.approx(810.0)


def test_un_remate_con_historial_de_precios_igual_reporta_el_precio_de_gondola():
    """La invariante REAL de current_price/discount_pct.

    El docstring afirmo dos veces "both None on a salvage recovery" y el codigo
    nunca lo cumplio: este SKU muerto trae historial, sale por salvage_heuristic
    con clearance_price 3.00, y current_price vale 12.00. Los dos campos no van
    juntos y no responden a la misma pregunta.

    discount_pct se calla en TODO remate porque es la AFIRMACION de que el precio
    es una rebaja de algo, y un remate no la hace. current_price es una
    OBSERVACION del historial que el cliente entrego: borrarla porque la
    disposicion resulto ser un remate tira el unico dato que hace legible un
    margen de -700 al lado de una "recuperacion del 30%".
    """
    muerto = SkuStock(product_id="DEAD", on_hand=100.0, daily_demand=0.0, unit_cost=10.0,
                      days_since_last_sale=400)
    historial = {"DEAD": ([12.0] * 6, [5.0] * 6)}

    linea = plan_liquidation([muerto], historial, salvage_recovery_pct=0.30).lines[0]

    assert linea.method == SALVAGE
    assert linea.clearance_price == pytest.approx(3.0)
    assert linea.current_price == pytest.approx(12.0)   # el dato observado NO se borra
    assert linea.discount_pct is None                   # la inferencia que nadie hizo, si
    assert linea.gross_margin_value == pytest.approx(-700.0)


def test_el_descuento_lo_decide_el_metodo_no_si_hubo_precio_observado():
    """El "si y solo si" que el docstring ahora escribe, sobre los tres metodos a la
    vez: discount_pct es None exactamente en los remates. Un anuncio sin su
    porcentaje no se puede auditar contra Omnibus, y un remate con porcentaje
    anuncia una rebaja que nadie va a publicar."""
    report = plan_liquidation(_mixed_stocks(), _mixed_history(), salvage_recovery_pct=0.30)
    assert {line.method for line in report.lines} == {ELASTICITY, DEFAULT_DISCOUNT, SALVAGE}

    for line in report.lines:
        assert (line.discount_pct is None) == (not announces_discount(line.method)), line.product_id
        if announces_discount(line.method):
            # El ancla es precondicion de las dos ramas anunciadas, no un accidente.
            assert line.current_price is not None and line.current_price > 0, line.product_id


def test_los_tres_metodos_cierran_la_misma_identidad_precio_por_unidades():
    """El precio propuesto y la caja recuperada tienen que cerrar entre si en los
    TRES metodos. Mientras el remate no publicaba precio, la identidad solo valia
    para los dos anunciados y nadie podia auditar el tercero."""
    report = plan_liquidation(_mixed_stocks(), _mixed_history(), salvage_recovery_pct=0.30)
    metodos = {line.method for line in report.lines}
    assert metodos == {ELASTICITY, DEFAULT_DISCOUNT, SALVAGE}
    for line in report.lines:
        assert line.clearance_price is not None, line.product_id
        assert line.recovered_value == pytest.approx(
            line.clearance_price * line.units_to_clear
        ), line.product_id


def test_sin_ancla_de_costo_el_remate_se_queda_sin_precio_a_proposito():
    """El unico caso sin precio propuesto: no hay costo al que anclarlo. Ahi tampoco
    hay caja en riesgo, asi que no hay economia que precificar - y un cero inventado
    seria peor que la ausencia declarada."""
    sin_costo = [SkuStock(product_id="Z", on_hand=100.0, daily_demand=0.0, unit_cost=0.0)]
    linea = plan_liquidation(sin_costo).lines[0]
    assert linea.method == SALVAGE
    assert linea.clearance_price is None
    assert linea.at_risk_value == 0.0
    assert linea.recovered_value == 0.0


def test_un_remate_con_historial_de_precios_no_anuncia_un_descuento():
    """Un muerto con historial plano SI tiene precio de referencia observado, pero
    sigue siendo una disposicion (jobber / write-down), no un markdown anunciado:
    reportarle un % de descuento lo convertiria en una promocion regulada que nadie
    penso publicar."""
    muerto = SkuStock(product_id="M", on_hand=100.0, daily_demand=0.0, unit_cost=4.0,
                      days_since_last_sale=400)
    plano = {"M": ([10.0, 10.0, 10.0, 10.0], [5.0, 6.0, 4.0, 5.0])}
    linea = plan_liquidation([muerto], plano, salvage_recovery_pct=0.30).lines[0]

    assert linea.method == SALVAGE
    assert linea.current_price == pytest.approx(10.0)   # la referencia SI existe
    assert linea.clearance_price == pytest.approx(1.2)  # 0.30 * 4.0, anclado al costo
    assert linea.discount_pct is None                   # y aun asi no anuncia un corte
    assert not announces_discount(linea.method)


def test_un_remate_sin_precio_de_referencia_no_inventa_un_descuento():
    """Sin historial no hay precio contra el cual medir el corte: None, no 0%."""
    muerto = SkuStock(product_id="SKU-MUERTO", on_hand=500.0, daily_demand=0.0, unit_cost=12.5,
                      days_since_last_sale=400)
    linea = plan_liquidation([muerto]).lines[0]
    assert linea.method == "salvage_heuristic"
    assert linea.current_price is None
    assert linea.discount_pct is None
    assert linea.cover_days_released is None      # sin demanda no libera cobertura, libera caja
    assert linea.gross_margin_value < 0           # se recupera 30% del costo: es perdida
