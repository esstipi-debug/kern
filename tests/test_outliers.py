"""Tests for forecast-error-based outlier detection (Vandeput 2021, ch. 10)."""

import math

import pytest

from src.outliers import OutlierReport, scan_outliers


def test_a_clean_series_flags_nothing():
    """The failure mode of naive winsorization: inventing outliers where there are none."""
    series = [10, 12, 9, 11, 10, 13, 9, 12, 10, 11, 12, 9]
    report = scan_outliers(series)
    assert report.n_flagged == 0
    assert report.flagged == ()
    assert list(report.cleaned) == [float(v) for v in series]


def test_a_single_spike_is_flagged_and_pulled_back():
    series = [10, 12, 9, 11, 10, 13, 9, 200, 10, 11, 12, 9]
    report = scan_outliers(series)
    assert 7 in report.flagged
    assert report.cleaned[7] < 200
    assert report.cleaned[7] > 0
    # every other period survives untouched
    assert [report.cleaned[i] for i in range(len(series)) if i != 7] == [
        float(v) for v in series if v != 200
    ]


def test_seasonal_peaks_are_not_outliers():
    """Where demand-mean thresholds fail: the season peaks look extreme, but they are expected."""
    series = [5, 6, 40, 5, 6, 42, 5, 6, 41, 5, 6, 40, 5, 6, 41] * 2
    report = scan_outliers(series, season_length=3)
    assert report.n_flagged == 0


def test_a_real_outlier_inside_a_season_is_still_caught():
    series = [5.0, 6.0, 40.0] * 10
    series[16] = 90.0  # a one-off spike in the low phase, not a recurring event
    report = scan_outliers(series, season_length=3)
    assert 16 in report.flagged
    assert report.cleaned[16] < 90.0


def test_a_tall_value_that_recurs_every_cycle_is_a_pattern_not_an_outlier():
    """Same phase, same magnitude, every cycle: that is the season, however tall it looks.
    A band pooled across phases would flag all twelve of these."""
    series = [5.0, 90.0, 40.0] * 12
    assert scan_outliers(series, season_length=3).n_flagged == 0


def test_thresholds_are_refined_excluding_the_flagged_points():
    """Step 3.5: the outlier inflates the error spread that judges it, so re-measure."""
    series = [10, 12, 9, 11, 10, 13, 9, 200, 10, 11, 12, 9]
    coarse = scan_outliers(series, refine=False)
    refined = scan_outliers(series, refine=True)
    assert refined.refined is True and coarse.refined is False
    # A tighter band after refinement pulls the outlier further back down.
    assert refined.upper_error < coarse.upper_error
    assert refined.cleaned[7] < coarse.cleaned[7]


def test_scan_is_idempotent_the_cleaned_series_has_no_outliers_left():
    series = [10, 12, 9, 11, 10, 13, 9, 200, 10, 11, 12, 9]
    once = scan_outliers(series)
    twice = scan_outliers(once.cleaned)
    assert twice.n_flagged == 0


def test_too_short_a_history_flags_nothing_rather_than_guessing():
    report = scan_outliers([10, 12, 9])
    assert report.n_flagged == 0
    assert report.reason == "history too short to estimate an error spread"


def test_a_flat_history_has_no_error_spread_and_flags_nothing():
    report = scan_outliers([7.0] * 12)
    assert report.n_flagged == 0
    assert report.reason == "forecast error has no spread; nothing can be exceptional"


def test_report_is_a_frozen_dataclass_with_the_audit_trail():
    report = scan_outliers([10, 12, 9, 11, 10, 13, 9, 200, 10, 11, 12, 9])
    assert isinstance(report, OutlierReport)
    assert report.z == pytest.approx(2.33)
    assert math.isfinite(report.lower_error) and math.isfinite(report.upper_error)
    with pytest.raises(Exception):
        report.n_flagged = 1  # frozen


def test_nan_and_negative_history_are_rejected_not_imputed():
    with pytest.raises(ValueError):
        scan_outliers([10, float("nan"), 12, 11, 10, 13])
    with pytest.raises(ValueError):
        scan_outliers([10, -5, 12, 11, 10, 13])


def test_intermittent_demand_does_not_have_every_sale_flagged():
    """The trap a robust spread estimator falls into: with mostly zeros the 'usual' value
    is 0, so every period that actually sold looks exceptional. It is not."""
    series = [0, 0, 12, 0, 0, 0, 10, 0, 0, 14, 0, 0, 0, 11, 0, 0, 13, 0, 0, 0]
    report = scan_outliers(series)
    assert report.n_flagged == 0


def test_a_trending_series_is_not_all_outliers():
    """Level drift must not read as a run of exceptional periods."""
    series = [float(100 + 5 * i) for i in range(24)]
    assert scan_outliers(series).n_flagged == 0


def test_cleaning_never_produces_negative_demand():
    series = [1, 0, 1, 0, 1, 0, 1, 0, 1, 0, 200, 0, 1, 0, 1]
    report = scan_outliers(series)
    assert all(v >= 0 for v in report.cleaned)
