"""Vehicle routing & scheduling (offline) - Ballou Cap. 7 "Diseno de rutas para los vehiculos".

Given a depot and a set of stops with demand, groups stops into capacity-feasible routes and
sequences each route, using two classic construction heuristics:

- savings (Clarke & Wright, 1964): start with one round trip per stop, then greedily merge the
  pair of routes with the largest "savings" (depot-i + depot-j - i-j) whenever both stops are
  still route endpoints and the merged load fits the vehicle.
- sweep (Gillett & Miller, 1974): sort stops by polar angle around the depot, fill vehicles in
  that angular order until capacity is hit, then sequence each cluster nearest-neighbor.

Coordinates are abstract (lat/long, grid km, ...), matching ``src.facility_location``. Distance
is Euclidean. Time windows are an optional, lightweight extension beyond the book (arrival-time
accumulation + late-arrival flagging) - not a full VRPTW solver.
"""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass(frozen=True)
class Stop:
    stop_id: str
    x: float
    y: float
    demand: float = 1.0
    service_time: float = 0.0
    tw_start: float | None = None    # earliest allowed arrival; early arrivals wait
    tw_end: float | None = None      # latest allowed arrival; later is a violation


@dataclass(frozen=True)
class Depot:
    x: float = 0.0
    y: float = 0.0


@dataclass(frozen=True)
class Route:
    stop_ids: tuple[str, ...]
    distance: float
    load: float
    arrivals: tuple[float, ...] = ()
    late_stops: tuple[str, ...] = ()


@dataclass(frozen=True)
class RoutingPlan:
    method: str
    routes: tuple[Route, ...]
    total_distance: float
    n_vehicles: int


def _dist(ax: float, ay: float, bx: float, by: float) -> float:
    return math.hypot(ax - bx, ay - by)


def naive_distance(stops: list[Stop], depot: Depot) -> float:
    """Baseline: one dedicated round trip per stop, no consolidation."""
    return sum(2 * _dist(depot.x, depot.y, s.x, s.y) for s in stops)


def route_distance(stop_ids: tuple[str, ...], stops_by_id: dict[str, Stop], depot: Depot) -> float:
    """Total distance of depot -> stop_ids in order -> depot."""
    points = [(depot.x, depot.y)]
    points += [(stops_by_id[sid].x, stops_by_id[sid].y) for sid in stop_ids]
    points.append((depot.x, depot.y))
    return sum(_dist(*points[i], *points[i + 1]) for i in range(len(points) - 1))


def arrival_times(
    stop_ids: tuple[str, ...], stops_by_id: dict[str, Stop], depot: Depot, speed: float = 1.0,
) -> list[float]:
    """Cumulative arrival time at each stop, departing the depot at t=0.

    Travel time is distance / speed. An early arrival waits until ``tw_start``; the vehicle
    then departs after ``service_time`` at the (possibly waited-for) arrival instant.
    """
    times: list[float] = []
    t = 0.0
    cur_x, cur_y = depot.x, depot.y
    for sid in stop_ids:
        stop = stops_by_id[sid]
        travel = _dist(cur_x, cur_y, stop.x, stop.y) / speed
        arrival = t + travel
        if stop.tw_start is not None and arrival < stop.tw_start:
            arrival = stop.tw_start
        times.append(arrival)
        t = arrival + stop.service_time
        cur_x, cur_y = stop.x, stop.y
    return times


def time_window_violations(
    stop_ids: tuple[str, ...], arrivals: list[float], stops_by_id: dict[str, Stop],
) -> list[str]:
    """Stop ids where the arrival is later than the stop's ``tw_end`` (empty if none/no windows)."""
    return [
        sid for sid, arr in zip(stop_ids, arrivals)
        if stops_by_id[sid].tw_end is not None and arr > stops_by_id[sid].tw_end
    ]


def _build_route(
    stop_ids: tuple[str, ...], stops_by_id: dict[str, Stop], depot: Depot, speed: float,
) -> Route:
    arrivals = arrival_times(stop_ids, stops_by_id, depot, speed=speed)
    late = time_window_violations(stop_ids, arrivals, stops_by_id)
    load = sum(stops_by_id[sid].demand for sid in stop_ids)
    return Route(
        stop_ids=stop_ids, distance=route_distance(stop_ids, stops_by_id, depot), load=load,
        arrivals=tuple(arrivals), late_stops=tuple(late),
    )


_CAPACITY_TOL = 1e-9


def _validate(stops: list[Stop], capacity: float, speed: float) -> None:
    if not stops:
        raise ValueError("at least one stop is required")
    if capacity <= 0:
        raise ValueError("capacity must be positive")
    if speed <= 0:
        raise ValueError("speed must be positive")
    too_big = [s.stop_id for s in stops if s.demand > capacity + _CAPACITY_TOL]
    if too_big:
        raise ValueError(f"stop demand exceeds vehicle capacity {capacity}: {', '.join(too_big)}")


def clarke_wright_savings(
    stops: list[Stop], depot: Depot, capacity: float, speed: float = 1.0,
) -> RoutingPlan:
    """Clarke-Wright savings algorithm: greedy route merging under a capacity constraint."""
    _validate(stops, capacity, speed)
    stops_by_id = {s.stop_id: s for s in stops}
    ids = [s.stop_id for s in stops]

    d_depot = {sid: _dist(depot.x, depot.y, s.x, s.y) for sid, s in stops_by_id.items()}

    def d(a: str, b: str) -> float:
        sa, sb = stops_by_id[a], stops_by_id[b]
        return _dist(sa.x, sa.y, sb.x, sb.y)

    routes: dict[int, list[str]] = {i: [sid] for i, sid in enumerate(ids)}
    route_of: dict[str, int] = {sid: i for i, sid in enumerate(ids)}
    loads: dict[int, float] = {i: stops_by_id[sid].demand for i, sid in enumerate(ids)}

    savings = [
        (d_depot[ids[i]] + d_depot[ids[j]] - d(ids[i], ids[j]), ids[i], ids[j])
        for i in range(len(ids)) for j in range(i + 1, len(ids))
    ]
    savings.sort(key=lambda t: t[0], reverse=True)

    for s, a, b in savings:
        if s <= 0:
            continue
        ra, rb = route_of[a], route_of[b]
        if ra == rb:
            continue
        route_a, route_b = routes[ra], routes[rb]
        if a not in (route_a[0], route_a[-1]) or b not in (route_b[0], route_b[-1]):
            continue
        if loads[ra] + loads[rb] > capacity + _CAPACITY_TOL:
            continue
        if route_a[0] == a and len(route_a) > 1:
            route_a = list(reversed(route_a))
        if route_b[-1] == b and len(route_b) > 1:
            route_b = list(reversed(route_b))
        if route_a[-1] != a or route_b[0] != b:
            continue  # both endpoints already used up on this side; not mergeable this way
        merged = route_a + route_b
        routes[ra] = merged
        loads[ra] += loads[rb]
        for sid in merged:
            route_of[sid] = ra
        del routes[rb]
        del loads[rb]

    final_routes = tuple(_build_route(tuple(r), stops_by_id, depot, speed) for r in routes.values())
    return RoutingPlan(
        method="savings", routes=final_routes,
        total_distance=sum(r.distance for r in final_routes), n_vehicles=len(final_routes),
    )


def _nearest_neighbor_order(stop_ids: list[str], stops_by_id: dict[str, Stop], depot: Depot) -> list[str]:
    remaining = list(stop_ids)
    seq: list[str] = []
    cur_x, cur_y = depot.x, depot.y
    while remaining:
        nxt = min(remaining, key=lambda sid: _dist(cur_x, cur_y, stops_by_id[sid].x, stops_by_id[sid].y))
        seq.append(nxt)
        remaining.remove(nxt)
        cur_x, cur_y = stops_by_id[nxt].x, stops_by_id[nxt].y
    return seq


def sweep(stops: list[Stop], depot: Depot, capacity: float, speed: float = 1.0) -> RoutingPlan:
    """Sweep algorithm: cluster stops by polar angle around the depot, then sequence each cluster."""
    _validate(stops, capacity, speed)
    stops_by_id = {s.stop_id: s for s in stops}

    def angle(s: Stop) -> float:
        return math.atan2(s.y - depot.y, s.x - depot.x) % (2 * math.pi)

    ordered = sorted(stops, key=angle)

    clusters: list[list[str]] = []
    current: list[str] = []
    current_load = 0.0
    for s in ordered:
        if current and current_load + s.demand > capacity + _CAPACITY_TOL:
            clusters.append(current)
            current, current_load = [], 0.0
        current.append(s.stop_id)
        current_load += s.demand
    if current:
        clusters.append(current)

    final_routes = tuple(
        _build_route(tuple(_nearest_neighbor_order(cluster, stops_by_id, depot)), stops_by_id, depot, speed)
        for cluster in clusters
    )
    return RoutingPlan(
        method="sweep", routes=final_routes,
        total_distance=sum(r.distance for r in final_routes), n_vehicles=len(final_routes),
    )
