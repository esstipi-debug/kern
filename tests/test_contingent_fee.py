"""Edge-case tests for src/contingent_fee.py: the Sprint de Liquidacion's
contingent-fee calculator and its real-vs-estimated measurement annex.

Acceptance criteria (Linchpin 2.0, E3): recupero 0, el piso, y los limites de
% del contrato comercial (10-20%).
"""
from __future__ import annotations

import math

import pytest

from src.contingent_fee import (
    DEFAULT_FEE_PCT,
    DEFAULT_FLOOR,
    MAX_FEE_PCT,
    MIN_FEE_PCT,
    calculate_contingent_fee,
    measure_recovery,
    render_fee_estimate,
    render_measurement_annex,
)

# ---- calculate_contingent_fee: edge cases -------------------------------------


def test_zero_recovery_yields_zero_fee_no_floor_charged():
    fee = calculate_contingent_fee(0.0, fee_pct=0.15, floor=1500.0)
    assert fee.fee == 0.0
    assert fee.floor_applied is False
    assert fee.capped_by_recovered is False
    assert fee.effective_pct == 0.0


def test_small_recovery_below_floor_never_exceeds_recovered_cash():
    # 15% of 1000 = 150, well under the 1500 floor -> floor would push it to
    # 1500, but that's MORE than what was recovered, so it caps at 1000.
    fee = calculate_contingent_fee(1000.0, fee_pct=0.15, floor=1500.0)
    assert fee.fee == 1000.0
    assert fee.floor_applied is True
    assert fee.capped_by_recovered is True
    assert fee.fee <= fee.recovered_cash


def test_recovery_between_floor_and_pct_break_even_applies_floor():
    # 15% of 12000 = 1800 > floor(1500) -> floor doesn't bind at all.
    # Pick a recovery where 15% < floor but recovery itself > floor:
    # recovered=8000 -> 15% = 1200 < 1500 floor; recovered(8000) > floor(1500)
    # -> floor applies cleanly, not capped by recovered.
    fee = calculate_contingent_fee(8000.0, fee_pct=0.15, floor=1500.0)
    assert fee.fee == 1500.0
    assert fee.floor_applied is True
    assert fee.capped_by_recovered is False


def test_large_recovery_floor_never_binds():
    fee = calculate_contingent_fee(50_000.0, fee_pct=0.15, floor=1500.0)
    assert fee.fee == pytest.approx(7_500.0)
    assert fee.floor_applied is False
    assert fee.capped_by_recovered is False
    assert fee.effective_pct == pytest.approx(0.15)


@pytest.mark.parametrize("pct", [MIN_FEE_PCT, MAX_FEE_PCT, 0.15])
def test_fee_pct_within_authorized_range_is_accepted(pct):
    fee = calculate_contingent_fee(10_000.0, fee_pct=pct, floor=0.0)
    assert fee.fee == pytest.approx(pct * 10_000.0)


@pytest.mark.parametrize("pct", [0.0, 0.09999, 0.20001, 0.5, 1.0, -0.1])
def test_fee_pct_outside_authorized_range_is_rejected(pct):
    with pytest.raises(ValueError, match="fee_pct"):
        calculate_contingent_fee(10_000.0, fee_pct=pct, floor=1500.0)


@pytest.mark.parametrize("bad", [-1.0, -0.01, math.inf, math.nan])
def test_negative_or_nonfinite_recovered_cash_is_rejected(bad):
    with pytest.raises(ValueError, match="recovered_cash"):
        calculate_contingent_fee(bad, fee_pct=0.15, floor=1500.0)


@pytest.mark.parametrize("bad", [-1.0, math.inf, math.nan])
def test_negative_or_nonfinite_floor_is_rejected(bad):
    with pytest.raises(ValueError, match="floor"):
        calculate_contingent_fee(10_000.0, fee_pct=0.15, floor=bad)


def test_nonfinite_fee_pct_is_rejected():
    with pytest.raises(ValueError, match="fee_pct"):
        calculate_contingent_fee(10_000.0, fee_pct=math.nan, floor=1500.0)


def test_zero_floor_is_allowed_and_never_forces_a_minimum():
    fee = calculate_contingent_fee(100.0, fee_pct=0.10, floor=0.0)
    assert fee.fee == pytest.approx(10.0)
    assert fee.floor_applied is False


def test_effective_pct_can_exceed_negotiated_fee_pct_when_floor_binds():
    # The floor is capped at recovered_cash, NOT at fee_pct -- effective_pct
    # can run well above the negotiated rate on a small recovery.
    fee = calculate_contingent_fee(2000.0, fee_pct=0.10, floor=1500.0)
    assert fee.effective_pct == pytest.approx(0.75)
    assert fee.effective_pct > fee.fee_pct
    assert fee.effective_pct <= 1.0  # the only real ceiling: never over 100%


def test_defaults_are_within_the_authorized_range():
    assert MIN_FEE_PCT <= DEFAULT_FEE_PCT <= MAX_FEE_PCT
    assert DEFAULT_FLOOR >= 0


# ---- render_fee_estimate: always reads as an estimate, never an invoice ------


def test_render_fee_estimate_disclaims_it_is_not_an_invoice():
    fee = calculate_contingent_fee(9_566.0, fee_pct=0.15, floor=1500.0)
    text = render_fee_estimate(fee, client="ACME")
    assert "NO UNA FACTURA" in text
    assert "ACME" in text
    assert "1,500" in text  # the floor bound


# ---- measure_recovery: estimated vs. actual -----------------------------------


def test_measure_recovery_computes_variance_and_real_fee():
    estimated = {"SKU-1": 5000.0, "SKU-2": 3000.0}
    actual = {"SKU-1": 5500.0, "SKU-2": 2000.0}
    measured = measure_recovery(estimated, actual, fee_pct=0.15, floor=1500.0)
    assert measured.total_estimated == 8000.0
    assert measured.total_actual == 7500.0
    assert measured.total_variance == -500.0
    assert measured.total_variance_pct == pytest.approx(-500.0 / 8000.0)
    assert measured.estimated_fee.fee == pytest.approx(1500.0)  # 15% of 8000=1200 -> floor
    assert measured.actual_fee.fee == pytest.approx(1500.0)     # 15% of 7500=1125 -> floor
    assert len(measured.lines) == 2


def test_measure_recovery_unsold_planned_sku_counts_as_zero_actual():
    measured = measure_recovery({"SKU-1": 4000.0}, {}, fee_pct=0.15, floor=0.0)
    assert measured.lines[0].actual_recovered == 0.0
    assert measured.lines[0].variance == -4000.0
    assert measured.total_actual == 0.0
    assert measured.actual_fee.fee == 0.0  # zero recovery -> zero fee, no floor


def test_measure_recovery_ignores_actual_sales_outside_the_plan():
    measured = measure_recovery({"SKU-1": 1000.0}, {"SKU-1": 900.0, "SKU-OUTSIDE": 5000.0}, floor=0.0)
    assert measured.total_actual == 900.0  # SKU-OUTSIDE never counted
    assert len(measured.lines) == 1


@pytest.mark.parametrize("bad", [math.nan, math.inf, -1.0])
def test_measure_recovery_rejects_bad_estimated_values(bad):
    with pytest.raises(ValueError, match=r"estimated_by_sku\['SKU-1'\]"):
        measure_recovery({"SKU-1": bad}, {"SKU-1": 500.0})


@pytest.mark.parametrize("bad", [math.nan, math.inf, -1.0])
def test_measure_recovery_rejects_bad_actual_values(bad):
    with pytest.raises(ValueError, match=r"actual_by_sku\['SKU-1'\]"):
        measure_recovery({"SKU-1": 500.0}, {"SKU-1": bad})


def test_measure_recovery_zero_estimated_sku_has_zero_variance_pct_not_nan():
    measured = measure_recovery({"SKU-1": 0.0}, {"SKU-1": 200.0}, floor=0.0)
    assert measured.lines[0].variance_pct == 0.0
    assert math.isfinite(measured.lines[0].variance_pct)


def test_render_measurement_annex_states_real_fee_governs_billing():
    measured = measure_recovery({"SKU-1": 5000.0}, {"SKU-1": 4000.0}, fee_pct=0.15, floor=0.0)
    text = render_measurement_annex(measured, client="ACME")
    assert "ACME" in text
    assert "SKU-1" in text
    assert "nunca sobre la estimacion inicial" in text
