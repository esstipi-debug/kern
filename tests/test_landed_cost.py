"""Tests for the landed-cost / TCO engine (capability M8)."""

import pytest

from src.landed_cost import LandedCost, landed_cost


def test_fob_duty_base_is_goods_only():
    lc = landed_cost(unit_cost=10.0, qty=100, freight=200, insurance=50,
                     duty_rate=0.05, handling=30, broker_fee=20, incoterm="FOB")
    assert isinstance(lc, LandedCost)
    assert lc.goods_value == pytest.approx(1000.0)
    assert lc.duty == pytest.approx(50.0)              # 1000 * 5%
    assert lc.total == pytest.approx(1350.0)           # 1000+200+50+30+50+20
    assert lc.per_unit == pytest.approx(13.5)


def test_cif_duty_base_includes_freight_and_insurance():
    lc = landed_cost(unit_cost=10.0, qty=100, freight=200, insurance=50,
                     duty_rate=0.05, handling=30, broker_fee=20, incoterm="CIF")
    assert lc.duty == pytest.approx(62.5)              # (1000+200+50) * 5%
    assert lc.total == pytest.approx(1362.5)
    assert lc.per_unit == pytest.approx(13.625)


def test_zero_qty_is_safe():
    lc = landed_cost(unit_cost=10.0, qty=0, duty_rate=0.1)
    assert lc.per_unit == 0.0
