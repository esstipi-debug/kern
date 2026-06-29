"""Facility location / network design - center-of-gravity + Weiszfeld (offline).

Pure, deterministic. Places a single facility (DC / hub / plant) to minimize weighted travel
to a set of demand points:

- center of gravity : the load-weighted centroid (Heizer, Ballou) - a fast closed-form estimate.
- Weiszfeld         : iteratively solves the true 1-median (minimize sum of w_i * Euclidean
                      distance), the cost-minimizing point for transport load.

Coordinates are abstract (lat/long, grid km, ...); ``total_weighted_distance`` lets callers
compare any candidate site (e.g. the current location) against the optimum.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class DemandPoint:
    name: str
    x: float
    y: float
    weight: float = 1.0     # demand / volume / load


@dataclass(frozen=True)
class Location:
    x: float
    y: float


def center_of_gravity(points: list[DemandPoint]) -> Location:
    """Load-weighted centroid: x* = sum(w x) / sum(w), y* likewise."""
    total_w = sum(p.weight for p in points)
    if total_w <= 0:
        raise ValueError("total weight must be positive")
    x = sum(p.weight * p.x for p in points) / total_w
    y = sum(p.weight * p.y for p in points) / total_w
    return Location(x, y)


def total_weighted_distance(points: list[DemandPoint], location: Location) -> float:
    """Sum of weight x Euclidean distance from the location to each demand point."""
    return sum(
        p.weight * math.hypot(p.x - location.x, p.y - location.y)
        for p in points
    )


def weiszfeld(
    points: list[DemandPoint],
    *,
    iterations: int = 200,
    tol: float = 1e-7,
) -> Location:
    """Weiszfeld's algorithm for the weighted 1-median (minimizes total weighted distance).

    Starts from the center of gravity and reweights by inverse distance until it converges
    (or hits ``iterations``). If an iterate lands exactly on a demand point, that point is the
    optimum and is returned.
    """
    if not points:
        raise ValueError("at least one demand point is required")
    loc = center_of_gravity(points)
    for _ in range(iterations):
        num_x = num_y = denom = 0.0
        on_point: Location | None = None
        for p in points:
            d = math.hypot(p.x - loc.x, p.y - loc.y)
            if d <= tol:
                on_point = Location(p.x, p.y)
                break
            w = p.weight / d
            num_x += w * p.x
            num_y += w * p.y
            denom += w
        if on_point is not None:
            return on_point
        if denom <= 0:
            return loc
        new = Location(num_x / denom, num_y / denom)
        if math.hypot(new.x - loc.x, new.y - loc.y) <= tol:
            return new
        loc = new
    return loc
