"""Tests for season-length detection (the MASE/RMSSE benchmark's season)."""

import numpy as np
import pytest

from src.seasonality import autocorrelation, detect_season_length


def test_flat_series_has_no_season():
    assert detect_season_length([5.0] * 40) == 1


def test_noise_has_no_season():
    rng = np.random.default_rng(7)
    assert detect_season_length(rng.normal(100, 5, 60).tolist()) == 1


def test_quarterly_cycle_is_detected():
    series = [10.0, 40.0, 20.0, 5.0] * 8
    assert detect_season_length(series, candidates=(4, 7, 12)) == 4


def test_weekly_cycle_is_detected():
    series = ([3.0, 3.0, 3.0, 3.0, 3.0, 20.0, 22.0]) * 9
    assert detect_season_length(series, candidates=(4, 7, 12)) == 7


def test_a_pure_trend_is_not_mistaken_for_a_season():
    """A trending series autocorrelates at every lag; only a *local peak* is a season."""
    assert detect_season_length([float(i) for i in range(60)], candidates=(4, 7, 12)) == 1


def test_a_candidate_needs_min_cycles_of_history():
    """One-and-a-bit cycles is not evidence of a season, however clean it looks."""
    series = [10.0, 40.0, 20.0, 5.0] * 1 + [10.0, 40.0]
    assert detect_season_length(series, candidates=(4,), min_cycles=2) == 1


def test_seasonal_series_shorter_than_the_candidate_is_safe():
    assert detect_season_length([1.0, 2.0, 3.0], candidates=(12, 52)) == 1


def test_empty_history_is_rejected():
    with pytest.raises(ValueError):
        detect_season_length([])


def test_autocorrelation_of_a_perfect_cycle_peaks_at_the_period():
    """The standard (biased) estimator caps at (n-lag)/n, not 1 - that damping is wanted:
    it stops a long candidate from winning on a series that barely covers it."""
    series = [1.0, 5.0, 3.0] * 10
    assert autocorrelation(series, 3) == pytest.approx(27 / 30, abs=1e-9)
    assert autocorrelation(series, 3) > autocorrelation(series, 2)


def test_autocorrelation_out_of_range_is_zero():
    assert autocorrelation([1.0, 2.0, 3.0], 5) == 0.0
    assert autocorrelation([1.0, 2.0, 3.0], 0) == 0.0
