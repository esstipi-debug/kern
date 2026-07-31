"""Verdict rules for one area's KPI delta (spec 2026-07-31, sec 6.1 + 9)."""

import math

import pytest

from src.hats import HAT_CFO, HAT_COMERCIAL, HAT_COMPRADOR, HAT_KEYS, HAT_PLANNER
from src.objection import (
    ACEPTA,
    MAXIMIZED_HATS,
    NO_EVALUABLE,
    OBJETA,
    Verdict,
    deterioration,
    judge,
)


def test_only_comercial_is_maximized():
    assert MAXIMIZED_HATS == frozenset({HAT_COMERCIAL})


@pytest.mark.parametrize("hat", [HAT_COMPRADOR, HAT_PLANNER, HAT_CFO])
def test_minimized_hats_deteriorate_when_kpi_rises(hat):
    # cost-like KPI: going up is worse
    assert deterioration(hat, 100.0, 130.0) == pytest.approx(30.0)
    assert deterioration(hat, 100.0, 80.0) == pytest.approx(-20.0)


def test_comercial_deteriorates_when_fill_rate_falls():
    # fill rate: going DOWN is worse - the sign must invert
    assert deterioration(HAT_COMERCIAL, 0.95, 0.90) == pytest.approx(0.05)
    assert deterioration(HAT_COMERCIAL, 0.90, 0.95) == pytest.approx(-0.05)


def test_unknown_hat_raises():
    with pytest.raises(ValueError):
        deterioration("almacen", 1.0, 2.0)


def test_improvement_accepts_even_beyond_threshold():
    v = judge(HAT_CFO, 100.0, 10.0, threshold=1.0)
    assert v.status == ACEPTA
    assert v.deterioration == pytest.approx(-90.0)


def test_deterioration_equal_to_threshold_accepts():
    # the threshold is INCLUSIVE - only strictly greater objects
    assert judge(HAT_CFO, 100.0, 112.0, threshold=12.0).status == ACEPTA


def test_deterioration_above_threshold_objects():
    v = judge(HAT_CFO, 100.0, 112.01, threshold=12.0)
    assert v.status == OBJETA
    assert v.kpi_name == "capital_charge_usd"
    assert v.threshold == pytest.approx(12.0)
    assert v.reason


def test_missing_threshold_is_unevaluable_with_empty_fields():
    v = judge(HAT_COMPRADOR, 10.0, 99.0, threshold=None)
    assert v.status == NO_EVALUABLE
    assert v.kpi_current is None
    assert v.kpi_proposed is None
    assert v.deterioration is None
    assert v.threshold is None
    assert "umbral" in v.reason.lower()


def test_planner_objects_on_sl_breach_despite_improving_cost():
    # spec 9.1: policy_cost is the RAW cost - dropping SL makes it CHEAPER,
    # so the delta alone would report an improvement on a service breach.
    v = judge(HAT_PLANNER, 50_000.0, 41_000.0, threshold=8_000.0,
              breaches_sl_target=True)
    assert v.status == OBJETA
    assert v.deterioration == pytest.approx(-9_000.0)
    assert "sl_target" in v.reason or "servicio" in v.reason.lower()


def test_sl_breach_is_ignored_for_non_planner_hats():
    v = judge(HAT_CFO, 100.0, 90.0, threshold=5.0, breaches_sl_target=True)
    assert v.status == ACEPTA


def test_sl_breach_does_not_override_unevaluable():
    v = judge(HAT_PLANNER, 50_000.0, 41_000.0, threshold=None,
              breaches_sl_target=True)
    assert v.status == NO_EVALUABLE


def test_non_finite_kpi_raises():
    with pytest.raises(ValueError):
        judge(HAT_CFO, 100.0, math.nan, threshold=1.0)


def test_verdict_is_frozen():
    v = judge(HAT_CFO, 100.0, 101.0, threshold=5.0)
    assert isinstance(v, Verdict)
    with pytest.raises(Exception):
        v.status = ACEPTA


def test_every_hat_key_is_judgeable():
    for k in HAT_KEYS:
        assert judge(k, 1.0, 1.0, threshold=0.5).status == ACEPTA
