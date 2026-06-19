"""Tests for histogram / KDE / discrete PMF demand (Ch. 12)."""

import numpy as np
import pytest

from src.discrete_demand import (
    DiscretePMF,
    histogram_pmf,
    kde_pmf,
    rmse_percent,
    scott_bandwidth,
)


def test_discrete_pmf_cdf_and_ppf():
    pmf = DiscretePMF(values=np.array([1, 2, 3]), probabilities=np.array([0.2, 0.3, 0.5]))
    assert pmf.cdf(2) == pytest.approx(0.5)
    assert pmf.cdf(3) == pytest.approx(1.0)
    assert pmf.ppf(0.0) == 1
    assert pmf.ppf(0.5) == 2
    assert pmf.ppf(1.0) == 3


def test_discrete_pmf_ppf_rejects_out_of_range():
    pmf = DiscretePMF(values=np.array([1, 2]), probabilities=np.array([0.5, 0.5]))
    with pytest.raises(ValueError):
        pmf.ppf(1.5)


def test_histogram_pmf_probabilities_sum_to_one():
    data = np.array([10, 12, 11, 13, 14, 12, 11, 10, 15, 13], dtype=float)
    pmf = histogram_pmf(data)
    assert pmf.probabilities.sum() == pytest.approx(1.0)


def test_kde_pmf_integer_support_sums_to_one():
    rng = np.random.default_rng(0)
    data = rng.normal(100, 15, size=200)
    pmf = kde_pmf(data)
    assert pmf.probabilities.sum() == pytest.approx(1.0, rel=1e-6)
    assert np.issubdtype(pmf.values.dtype, np.integer)


def test_scott_bandwidth_matches_formula():
    data = np.array([1.0, 2.0, 3.0, 4.0, 5.0])
    sigma = float(np.std(data, ddof=1))
    expected = 0.9 * sigma * len(data) ** (-1 / 5)
    assert scott_bandwidth(data) == pytest.approx(expected)


def test_rmse_percent_zero_for_perfect_fit():
    arr = np.array([10.0, 20.0, 30.0])
    assert rmse_percent(arr, arr) == pytest.approx(0.0)


def test_rmse_percent_length_mismatch_raises():
    with pytest.raises(ValueError):
        rmse_percent(np.array([1.0, 2.0]), np.array([1.0]))
