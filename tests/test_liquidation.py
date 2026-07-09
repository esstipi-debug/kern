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


def test_salvage_line_recovers_a_fraction_of_cost_basis_without_a_price() -> None:
    report = plan_liquidation(_mixed_stocks(), _mixed_history(), salvage_recovery_pct=0.30)
    c = _line(report, "C")
    assert c.method == SALVAGE
    assert c.clearance_price is None
    assert c.at_risk_value == pytest.approx(110.0 * 3.0)  # (200 - 90) * 3
    assert c.recovered_value == pytest.approx(0.30 * 110.0 * 3.0)
    assert math.isinf(c.weeks_to_clear)


def test_dead_stock_without_elasticity_falls_back_to_salvage() -> None:
    report = plan_liquidation(_mixed_stocks(), _mixed_history())
    d = _line(report, "D")
    assert d.classification == "dead"
    assert d.method == SALVAGE
    assert d.clearance_price is None
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


def test_no_price_history_makes_every_line_salvage() -> None:
    report = plan_liquidation(_mixed_stocks(), price_history=None)
    assessed = [line for line in report.lines]
    assert assessed  # excess + dead still assessed
    assert all(line.method == SALVAGE for line in assessed)
    assert all(line.clearance_price is None for line in assessed)


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
