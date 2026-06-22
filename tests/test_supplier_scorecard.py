"""Tests for supplier scorecards — OTIF / quality (capability M8)."""

import pytest

from src.supplier_scorecard import SupplierScore, score_supplier


def _deliveries():
    return [
        {"on_time": True, "in_full": True, "lead_time_days": 30, "units": 1000, "defects": 2},
        {"on_time": True, "in_full": True, "lead_time_days": 32, "units": 1000, "defects": 1},
        {"on_time": True, "in_full": False, "lead_time_days": 28, "units": 1000, "defects": 0},
        {"on_time": False, "in_full": True, "lead_time_days": 40, "units": 1000, "defects": 2},
    ]


def test_otif_requires_both_on_time_and_in_full():
    s = score_supplier("Acme", _deliveries())
    assert isinstance(s, SupplierScore)
    assert s.otif == pytest.approx(0.5)        # 2 of 4 are both
    assert s.on_time_rate == pytest.approx(0.75)
    assert s.in_full_rate == pytest.approx(0.75)


def test_avg_lead_time():
    s = score_supplier("Acme", _deliveries())
    assert s.avg_lead_time == pytest.approx((30 + 32 + 28 + 40) / 4)


def test_quality_ppm():
    s = score_supplier("Acme", _deliveries())
    # 5 defects / 4000 units * 1e6 = 1250 ppm
    assert s.ppm == pytest.approx(1250.0)


def test_no_deliveries_is_zeroed():
    s = score_supplier("Empty", [])
    assert s.otif == 0.0 and s.deliveries == 0
