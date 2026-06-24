"""Tests for the forecastability engine (Syntetos-Boylan-Croston demand segmentation).

Classifies each demand series by ADI (average demand interval) and CV^2 (squared
coefficient of variation of non-zero demand) into the SBC quadrants - smooth /
erratic / intermittent / lumpy - and recommends the matching forecast method. Pure.
"""

import math

import pytest

from src.forecastability import (
    ADI_THRESHOLD,
    CV2_THRESHOLD,
    classify_series,
    segment,
    squared_cv_nonzero,
)

# Demand shapes hand-built to land in each SBC quadrant.
_SMOOTH = [10, 11, 9, 10, 12, 8, 10, 11]          # every period, low size variation
_ERRATIC = [1, 20, 2, 18, 3, 25, 1, 22]            # every period, high size variation
_INTERMITTENT = [10, 0, 10, 0, 10, 0, 10, 0]       # gaps, stable size
_LUMPY = [0, 30, 0, 0, 5, 0, 0, 40, 0, 2]          # gaps AND variable size


# -- CV^2 of non-zero demand --------------------------------------------------


def test_squared_cv_nonzero_is_zero_for_constant_nonzero_demand():
    assert squared_cv_nonzero([10, 0, 10, 0, 10]) == pytest.approx(0.0)


def test_squared_cv_nonzero_matches_definition():
    # non-zero sizes [2, 4, 6]; mean 4, sample std 2 -> CV=0.5 -> CV^2=0.25
    assert squared_cv_nonzero([2, 0, 4, 0, 6]) == pytest.approx(0.25)


def test_squared_cv_nonzero_is_zero_when_all_zero():
    assert squared_cv_nonzero([0, 0, 0]) == 0.0


# -- quadrant classification --------------------------------------------------


def test_classifies_smooth():
    f = classify_series("A", _SMOOTH)
    assert f.adi < ADI_THRESHOLD
    assert f.cv2 < CV2_THRESHOLD
    assert f.quadrant == "smooth"
    assert f.recommended_method == "ses"


def test_classifies_erratic():
    f = classify_series("B", _ERRATIC)
    assert f.adi < ADI_THRESHOLD
    assert f.cv2 >= CV2_THRESHOLD
    assert f.quadrant == "erratic"
    assert f.recommended_method == "ses"


def test_classifies_intermittent():
    f = classify_series("C", _INTERMITTENT)
    assert f.adi >= ADI_THRESHOLD
    assert f.cv2 < CV2_THRESHOLD
    assert f.quadrant == "intermittent"
    assert f.recommended_method == "croston"


def test_classifies_lumpy():
    f = classify_series("D", _LUMPY)
    assert f.adi >= ADI_THRESHOLD
    assert f.cv2 >= CV2_THRESHOLD
    assert f.quadrant == "lumpy"
    assert f.recommended_method == "croston"


def test_classify_reports_period_counts():
    f = classify_series("C", _INTERMITTENT)
    assert f.n_periods == 8
    assert f.nonzero_periods == 4
    assert f.adi == pytest.approx(2.0)


# -- portfolio segmentation ---------------------------------------------------


def test_segment_counts_the_quadrant_mix():
    report = segment({"A": _SMOOTH, "B": _ERRATIC, "C": _INTERMITTENT, "D": _LUMPY})

    assert report.mix == {"smooth": 1, "erratic": 1, "intermittent": 1, "lumpy": 1}
    assert {i.name for i in report.items} == {"A", "B", "C", "D"}


def test_segment_orders_items_hardest_first_and_flags_the_hardest():
    report = segment({"A": _SMOOTH, "D": _LUMPY, "C": _INTERMITTENT})

    # lumpy "D" has the highest ADI x (1+CV^2) difficulty -> ranked first and flagged
    assert report.items[0].name == "D"
    assert report.hardest[0] == "D"
    assert math.isfinite(report.hardest[1])


def test_segment_empty_is_safe():
    report = segment({})
    assert report.items == ()
    assert report.hardest == ("n/a", 0.0)
    assert report.mix == {"smooth": 0, "erratic": 0, "intermittent": 0, "lumpy": 0}
