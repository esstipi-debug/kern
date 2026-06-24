"""Tests for the reverse-logistics / returns-disposition engine (roadmap #6).

For each returned lot the engine ranks the executable dispositions (restock / refurbish /
liquidate / scrap) by net recovery, picks the best, and rolls up recovery rate, value at
risk and the reason Pareto. Pure - no external deps.
"""

import pytest

from src.reverse_logistics import (
    DispositionRates,
    ReturnLine,
    best_disposition,
    options_for,
    reason_pareto,
    recovered_value,
    recovery_rate,
    returns_value_at_cost,
)

_RATES = DispositionRates(
    restock_handling_per_unit=2.0,
    refurbish_cost_per_unit=10.0,
    refurbish_resale_factor=0.6,
    liquidation_recovery_pct=0.2,
    scrap_cost_per_unit=3.0,
)
# unit_cost 50, resale 40 -> restock 38, refurbish 0.6*40-10=14, liquidate 0.2*50=10, scrap -3
_SELLABLE = ReturnLine("SKU-A", returned_units=10.0, reason="wrong_size", unit_cost=50.0, resale_value=40.0)
_DAMAGED = ReturnLine("SKU-B", returned_units=5.0, reason="damaged", unit_cost=50.0, resale_value=40.0, sellable=False)


def test_options_ranked_by_net_recovery_for_a_sellable_return():
    opts = options_for(_SELLABLE, _RATES)

    assert [o.action for o in opts] == ["restock", "refurbish", "liquidate", "scrap"]
    assert opts[0].net_recovery_per_unit == pytest.approx(38.0)
    assert opts[-1].net_recovery_per_unit == pytest.approx(-3.0)


def test_unsellable_return_drops_restock_and_refurbish():
    opts = options_for(_DAMAGED, _RATES)

    assert [o.action for o in opts] == ["liquidate", "scrap"]
    assert opts[0].net_recovery_per_unit == pytest.approx(10.0)


def test_best_disposition_picks_the_top_option_and_scales_by_units():
    d = best_disposition(_SELLABLE, _RATES)

    assert d.best.action == "restock"
    assert d.recovery_value == pytest.approx(38.0 * 10)   # per-unit * returned_units


def test_recovery_rate_is_recovered_over_value_at_risk():
    lines = [_SELLABLE, _DAMAGED]
    dispositions = [best_disposition(line, _RATES) for line in lines]

    # value at risk = 10*50 + 5*50 = 750; recovered = 38*10 + 10*5 = 430
    assert returns_value_at_cost(lines) == pytest.approx(750.0)
    assert recovered_value(dispositions) == pytest.approx(430.0)
    assert recovery_rate(dispositions, lines) == pytest.approx(430.0 / 750.0)


def test_reason_pareto_ranks_reasons_by_units_descending():
    lines = [
        ReturnLine("A", 10.0, "wrong_size", 1.0, 1.0),
        ReturnLine("B", 8.0, "defective", 1.0, 1.0),
        ReturnLine("C", 5.0, "wrong_size", 1.0, 1.0),
    ]

    pareto = reason_pareto(lines)

    assert pareto[0] == ("wrong_size", 15.0)   # 10 + 5, the biggest driver
    assert pareto[1] == ("defective", 8.0)
    assert [r for r, _ in pareto] == ["wrong_size", "defective"]
