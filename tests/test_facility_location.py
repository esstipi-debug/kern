"""Tests for the offline facility-location engine: center of gravity + Weiszfeld."""

import math

import pytest

from src.facility_location import (
    DemandPoint,
    Location,
    center_of_gravity,
    total_weighted_distance,
    weiszfeld,
)


def test_center_of_gravity_is_load_weighted():
    cog = center_of_gravity([DemandPoint("a", 0, 0, 1), DemandPoint("b", 2, 0, 3)])
    assert cog.x == pytest.approx(1.5)      # (0*1 + 2*3) / 4
    assert cog.y == pytest.approx(0.0)


def test_center_of_gravity_rejects_zero_weight():
    with pytest.raises(ValueError):
        center_of_gravity([DemandPoint("a", 0, 0, 0)])


def test_total_weighted_distance():
    pts = [DemandPoint("a", 0, 0, 2)]
    assert total_weighted_distance(pts, Location(3, 4)) == pytest.approx(10.0)   # 2 * 5


def test_weiszfeld_single_point_returns_it():
    loc = weiszfeld([DemandPoint("only", 5, 7, 1)])
    assert loc.x == pytest.approx(5.0) and loc.y == pytest.approx(7.0)


def test_weiszfeld_pulls_to_the_heavy_point():
    pts = [DemandPoint("light", 0, 0, 1), DemandPoint("heavy", 10, 0, 10)]
    cog = center_of_gravity(pts)              # x ~ 9.09
    opt = weiszfeld(pts)
    assert opt.x > cog.x                       # 1-median sits at the heavy point
    assert opt.x == pytest.approx(10.0, abs=1e-2)


def test_weiszfeld_minimizes_weighted_distance():
    pts = [DemandPoint("a", 0, 0, 2), DemandPoint("b", 8, 1, 1), DemandPoint("c", 3, 9, 3)]
    opt = weiszfeld(pts)
    cog = center_of_gravity(pts)
    assert total_weighted_distance(pts, opt) <= total_weighted_distance(pts, cog) + 1e-6
    assert math.isfinite(opt.x) and math.isfinite(opt.y)
