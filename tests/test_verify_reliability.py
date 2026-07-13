"""Tests for src/verify/reliability.py (Linchpin 3.0 PR-8, Control Tower A4 "verify").

Mirrors the plan's own worked example (section 2.3): "Mes 3:
inventory_optimization tiene 94% de precision en reorden" must be
distinguishable, in the same report, from a tool with <70% hit rate.
"""

from __future__ import annotations

import math

import pytest

from src.verify.backtest import MatchedObservation
from src.verify.reliability import build_all_reliability_reports, build_reliability_report


def _reliable_tool_observations(n_hits: int, n_misses: int, tool: str) -> list[MatchedObservation]:
    """``n_hits`` observations with predicted == actual (a trivial, exact hit at
    the default 10% tolerance) and ``n_misses`` observations with a 100%
    relative error (predicted double the actual -- always a miss at 10%)."""
    obs = [
        MatchedObservation(f"{tool}-HIT-{i}", "2026-01", predicted=100.0, actual=100.0, tool=tool)
        for i in range(n_hits)
    ]
    obs += [
        MatchedObservation(f"{tool}-MISS-{i}", "2026-01", predicted=200.0, actual=100.0, tool=tool)
        for i in range(n_misses)
    ]
    return obs


def test_headline_precision_94_percent_is_distinguishable_from_below_70_percent():
    """Plan section 2.3's worked example, reproduced with hand-verifiable counts:
    47/50 = 0.94 exactly, and 6/10 = 0.60 (below both 0.70 and the 0.85 default
    promotion threshold)."""
    good_tool_obs = _reliable_tool_observations(n_hits=47, n_misses=3, tool="inventory_optimization")
    weak_tool_obs = _reliable_tool_observations(n_hits=6, n_misses=4, tool="flaky_tool")

    good_report = build_reliability_report("inventory_optimization", good_tool_obs)
    weak_report = build_reliability_report("flaky_tool", weak_tool_obs)

    assert good_report.n_decisions == 50
    assert good_report.n_hits == 47
    assert good_report.hit_rate == pytest.approx(0.94)
    assert good_report.headline_precision == pytest.approx(0.94)
    assert good_report.meets_threshold is True  # 0.94 >= default 0.85 threshold

    assert weak_report.n_decisions == 10
    assert weak_report.n_hits == 6
    assert weak_report.hit_rate == pytest.approx(0.60)
    assert weak_report.headline_precision < 0.70
    assert weak_report.meets_threshold is False

    assert good_report.headline_precision > weak_report.headline_precision


def test_build_all_reliability_reports_covers_every_tool_sorted_and_ignores_untagged_obs():
    obs = (
        _reliable_tool_observations(n_hits=47, n_misses=3, tool="inventory_optimization")
        + _reliable_tool_observations(n_hits=6, n_misses=4, tool="flaky_tool")
        + [MatchedObservation("SKU-Z", "2026-01", predicted=1.0, actual=1.0, tool=None)]
    )

    reports = build_all_reliability_reports(obs)

    assert [r.tool for r in reports] == ["flaky_tool", "inventory_optimization"]
    assert sum(r.n_decisions for r in reports) == 60  # the untagged obs is not double-counted anywhere


def test_zero_actual_observations_are_excluded_not_fabricated_into_a_hit_or_miss():
    """A tool whose every observation has actual == 0: hit_rate/headline_precision
    must be None (an honest 'no signal'), not silently 0.0 or 1.0, and
    meets_threshold must be False -- never crashes on the division."""
    obs = [
        MatchedObservation("SKU-A", "2026-01", predicted=5.0, actual=0.0, tool="edge_case_tool"),
        MatchedObservation("SKU-B", "2026-01", predicted=0.0, actual=0.0, tool="edge_case_tool"),
    ]

    report = build_reliability_report("edge_case_tool", obs)

    assert report.n_decisions == 2
    assert report.n_excluded_zero_actual == 2
    assert report.n_hits == 0
    assert report.hit_rate is None
    assert report.headline_precision is None
    assert report.meets_threshold is False
    assert math.isinf(report.mean_wape)  # honest: wape is undefined (+inf), not silently 0


def test_partial_zero_actual_observations_are_excluded_from_the_ratio_but_counted():
    """Mixed tool: 3 verifiable hits, 1 verifiable miss, 1 unverifiable (actual=0)
    -> hit_rate is computed over the 4 verifiable rows only (3/4 = 0.75), and the
    excluded row is reported, not silently dropped."""
    obs = [
        MatchedObservation("SKU-1", "2026-01", predicted=100.0, actual=100.0, tool="mixed_tool"),
        MatchedObservation("SKU-2", "2026-01", predicted=100.0, actual=100.0, tool="mixed_tool"),
        MatchedObservation("SKU-3", "2026-01", predicted=100.0, actual=100.0, tool="mixed_tool"),
        MatchedObservation("SKU-4", "2026-01", predicted=200.0, actual=100.0, tool="mixed_tool"),  # miss
        MatchedObservation("SKU-5", "2026-01", predicted=5.0, actual=0.0, tool="mixed_tool"),  # unverifiable
    ]

    report = build_reliability_report("mixed_tool", obs)

    assert report.n_decisions == 5
    assert report.n_excluded_zero_actual == 1
    assert report.n_hits == 3
    assert report.hit_rate == pytest.approx(0.75)


def test_tolerance_boundary_is_inclusive():
    """A relative error exactly equal to ``tolerance`` counts as a hit (<=, not <)."""
    obs = [MatchedObservation("SKU-A", "2026-01", predicted=110.0, actual=100.0, tool="t")]  # exactly 10% over
    report = build_reliability_report("t", obs, tolerance=0.10)
    assert report.n_hits == 1
    assert report.hit_rate == pytest.approx(1.0)


def test_build_reliability_report_rejects_non_positive_tolerance():
    with pytest.raises(ValueError):
        build_reliability_report("t", [], tolerance=0.0)


def test_build_reliability_report_rejects_threshold_out_of_range():
    with pytest.raises(ValueError):
        build_reliability_report("t", [], threshold=0.0)
    with pytest.raises(ValueError):
        build_reliability_report("t", [], threshold=1.5)


def test_build_reliability_report_empty_observations_for_a_tool_reports_zero_not_a_crash():
    report = build_reliability_report("nonexistent_tool", [])
    assert report.n_decisions == 0
    assert report.hit_rate is None
    assert report.meets_threshold is False
