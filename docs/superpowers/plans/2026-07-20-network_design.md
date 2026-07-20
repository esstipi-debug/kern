> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax where present. Part of the CSCP/SCPro gap-closing initiative -- see docs/superpowers/specs/2026-07-20-cscp-scpro-gap-closing-design.md.

## Goal

Add a **new** agent-routable tool `network_design` to Kern that solves the multi-facility **p-median** problem via `scipy.optimize.milp` (HiGHS branch-and-bound). Given demand points (id, x, y, weight) + candidate sites (id, x, y, optional fixed_cost, optional capacity) + a target facility count `p`, it chooses which `p` sites to open, assigns each demand point to one open site to minimize total weighted distance (plus any fixed costs, honoring any capacities), and reports the saving versus the best single-facility (p=1) baseline. It returns a ranked, protected `GuidedOutcome` (the open/assign decision). This closes both the CSCP "chain design at MILP scale" and SCPro "network analysis at scale" gaps. **Zero new dependencies** — scipy is already a core dependency (`scipy>=1.10`, present 1.17.1; `src/dea.py` already uses `scipy.optimize`).

### Decision: NEW tool `network_design`, not an extension of `facility_location`

Confirmed by reading the code:
- `src/facility_location.py` answers a **different question**: where to put *one* facility (center-of-gravity closed form + Weiszfeld continuous 1-median), on a *continuous* plane. There is no site-selection, no assignment, no capacity, no fixed cost.
- p-median is a **discrete combinatorial** decision (which `p` of N candidates, plus an assignment map), solved by MILP — a distinct report shape (`open_sites`, `assignment`) and a distinct engine.
- The repo's own recipe makes a new tool cheap: "Adding a capability = one `register()` call + a `jobs/<x>_job.py`; no routing edits." Overloading `facility_location` would fork its report dataclass, its `verify`, its deck, and its options builder on a mode flag — strictly worse than a sibling tool.
- Reuse is still maximized: the new engine imports `DemandPoint`, `Location`, and `total_weighted_distance` from `src.facility_location`; the job mirrors `jobs/facility_location_job.py`; the citation anchors are the *same three* already validated for `facility_location`.

The existing `facility_location`'s own deck already points the way: its `residual` reads "Multi-facility networks need clustering / p-median (a separate step)."

## Architecture

- **Engine** `src/network_design.py`: pure, deterministic, frozen dataclasses in / frozen result out, no I/O, no pandas. Builds and solves the p-median MILP with `scipy.optimize.milp`. Reuses `src.facility_location` dataclasses + `total_weighted_distance`.
- **Job** `jobs/network_design_job.py`: pandas-only `prepare()` reading its OWN CSV (never `jobs/intake.py`), plus `run`/`verify`/`write_operational`/`build_deck`, mirroring `jobs/facility_location_job.py` exactly.
- **Wiring** `scm_agent/tools.py`: `_network_design_prepare`/`_network_design_run` + `network_design_tool()` factory registered in `build_default_registry()`.
- **Guided outcome** `scm_agent/tool_options.py`: `network_design_options(report)` -> ranked `GuidedOutcome` (OPTIONS) via the existing `_ranked` helper.
- **Citations** `scm_agent/citation_gate.py`: one `TOOL_CONCEPTS["network_design"]` entry reusing the proven `facility_location` anchors (3 concepts, well under the ~8 pool ceiling).
- **Tests**: `tests/test_network_design.py` (engine, known-optimum instances) + `tests/test_network_design_tool.py` (job + routing + orchestrator level).

## Tech Stack

Python 3.11+, `scipy.optimize.milp` / `LinearConstraint` / `Bounds`, `numpy`, `pandas` (job layer only), frozen `@dataclass`, pytest. All ASCII in console/deck strings (Windows cp1252). Lint scope `ruff check src tests examples`.

## Global Constraints

- **Frozen dataclasses, full type hints** on every signature.
- **ASCII-only** in any string that can reach a console print or a deck rendered via `to_markdown()` (use `x` for "times", `->`, never em dashes / unicode).
- **Module-level `from scipy.optimize import ...` is safe** — scipy is a *core* requirement (not an optional extra), so it does not violate the prod-boot rule (mirrors `src/dea.py`). Do NOT lazily import scipy.
- **No `intake.py`** in the job — `prepare()` reads its own CSV with pandas.
- **TDD**: write the failing test, run it (RED), minimal impl, run it (GREEN), commit. Each Task ends in an independently committable, green deliverable.
- **Branch workflow**: feature branch `feat/network-design-pmedian` -> draft PR -> CI green (py3.11/3.12/3.13) -> squash-merge. Never push to `main`.

---

## Task 1 — p-median MILP engine (`src/network_design.py`)

**Files**
- Create `src/network_design.py`
- Create `tests/test_network_design.py`

**Interfaces**
- Consumes (existing, verified): `src.facility_location.DemandPoint(name: str, x: float, y: float, weight: float = 1.0)`, `src.facility_location.Location(x: float, y: float)`, `src.facility_location.total_weighted_distance(points: list[DemandPoint], location: Location) -> float`; `scipy.optimize.milp`, `scipy.optimize.LinearConstraint`, `scipy.optimize.Bounds`.
- Produces (new):
  - `CandidateSite(name: str, x: float, y: float, fixed_cost: float = 0.0, capacity: float | None = None)` (frozen)
  - `NetworkDesign(feasible: bool, p: int, open_sites: tuple[str, ...], assignment: dict[str, str], total_weighted_distance: float, total_fixed_cost: float, objective: float, baseline_distance: float, saving_vs_baseline: float, saving_pct: float)` (frozen)
  - `solve_p_median(demands: list[DemandPoint], sites: list[CandidateSite], p: int, *, respect_capacity: bool = True) -> NetworkDesign`

### Steps

1. **Write the failing engine tests** in `tests/test_network_design.py`:

```python
"""Tests for src/network_design.py - the p-median multi-facility MILP engine.

Every instance is small enough to verify by hand / enumeration.
"""
import math

import pytest

from src.facility_location import DemandPoint
from src.network_design import CandidateSite, solve_p_median


def _line(*xs_w: tuple[float, float]) -> list[DemandPoint]:
    return [DemandPoint(f"D{i}", x, 0.0, w) for i, (x, w) in enumerate(xs_w)]


def test_p1_picks_the_weighted_median_site():
    demands = _line((0, 1), (1, 1), (2, 1))
    sites = [CandidateSite("S0", 0, 0), CandidateSite("S1", 1, 0), CandidateSite("S2", 2, 0)]
    d = solve_p_median(demands, sites, 1)
    assert d.feasible
    assert d.open_sites == ("S1",)                       # x=1 is the median of {0,1,2}
    assert d.total_weighted_distance == pytest.approx(2.0)   # 1 + 0 + 1
    assert d.saving_vs_baseline == pytest.approx(0.0)        # p=1 == single-facility baseline


def test_p2_two_clusters_open_the_two_cluster_medians():
    demands = [
        DemandPoint("D0", 0, 0, 1), DemandPoint("D1", 1, 0, 1), DemandPoint("D2", 2, 0, 1),
        DemandPoint("D3", 10, 0, 1), DemandPoint("D4", 11, 0, 1), DemandPoint("D5", 12, 0, 1),
    ]
    sites = [CandidateSite(f"S{x}", x, 0) for x in (0, 1, 2, 10, 11, 12)]
    d = solve_p_median(demands, sites, 2)
    assert d.feasible
    assert set(d.open_sites) == {"S1", "S11"}            # unique optimum: the two cluster medians
    assert d.total_weighted_distance == pytest.approx(4.0)   # (1+0+1) per cluster
    assert d.assignment["D0"] == "S1" and d.assignment["D5"] == "S11"
    assert d.baseline_distance == pytest.approx(30.0)        # best single site (x=2 or x=10): 30
    assert d.saving_vs_baseline == pytest.approx(26.0)
    assert d.saving_pct == pytest.approx(26.0 / 30.0)


def test_capacity_forces_a_non_nearest_assignment():
    demands = [DemandPoint("A", 0, 0, 10), DemandPoint("B", 0.5, 0, 10)]
    sites = [CandidateSite("near", 0, 0, capacity=10), CandidateSite("far", 10, 0, capacity=100)]
    d = solve_p_median(demands, sites, 2)                 # only 2 sites, p=2 -> both open
    assert d.feasible
    assert d.assignment["A"] == "near"                   # near is full (cap 10) after A
    assert d.assignment["B"] == "far"                    # B pushed to far by the cap
    assert d.total_weighted_distance == pytest.approx(95.0)  # 0 + 9.5*10


def test_infeasible_when_capacity_cannot_cover_demand():
    demands = [DemandPoint("A", 0, 0, 10), DemandPoint("B", 1, 0, 10), DemandPoint("C", 2, 0, 10)]
    sites = [CandidateSite("only", 0, 0, capacity=20)]   # 20 < 30 total demand
    d = solve_p_median(demands, sites, 1)
    assert d.feasible is False
    assert d.open_sites == ()
    assert not math.isfinite(d.total_weighted_distance)


def test_fixed_cost_breaks_a_distance_tie():
    demands = _line((0, 1), (10, 1))
    sites = [CandidateSite("cheap_mid", 5, 0, fixed_cost=1.0),
             CandidateSite("dear_mid", 5, 0, fixed_cost=5.0)]   # same location, cheaper wins
    d = solve_p_median(demands, sites, 1)
    assert d.open_sites == ("cheap_mid",)
    assert d.total_weighted_distance == pytest.approx(10.0)     # 5 + 5
    assert d.total_fixed_cost == pytest.approx(1.0)
    assert d.objective == pytest.approx(11.0)                   # distance 10 + fixed 1


def test_rejects_p_out_of_range():
    demands = _line((0, 1), (1, 1))
    sites = [CandidateSite("S0", 0, 0), CandidateSite("S1", 1, 0)]
    with pytest.raises(ValueError):
        solve_p_median(demands, sites, 0)
    with pytest.raises(ValueError):
        solve_p_median(demands, sites, 3)
```

2. **Run the tests — they fail** (`ModuleNotFoundError: src.network_design`). `PYTHONPATH=. py -m pytest tests/test_network_design.py -q`.

3. **Write the engine** `src/network_design.py`:

```python
"""Multi-facility network design - the p-median problem via MILP (offline).

Single-facility siting (center of gravity / Weiszfeld, src.facility_location) answers "where
should the one DC go" on a continuous plane. This module answers the distinct network-design
question "which p of these candidate sites should we open, and which demand point does each
serve, to minimize total weighted travel" - the classic p-median model (Chopra & Meindl,
Network Design in the Supply Chain). It is solved exactly as a mixed-integer linear program
via scipy.optimize.milp (HiGHS branch-and-bound) - the same scipy.optimize backend that
src.dea already uses for LP, so no new dependency.

Pure and deterministic: frozen dataclasses in, a frozen NetworkDesign out, no I/O.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from src.facility_location import DemandPoint, Location, total_weighted_distance


@dataclass(frozen=True)
class CandidateSite:
    """A site that may be opened; fixed_cost and capacity are optional."""

    name: str
    x: float
    y: float
    fixed_cost: float = 0.0
    capacity: float | None = None


@dataclass(frozen=True)
class NetworkDesign:
    """The p-median solution: which sites open, who each demand is served by, and the prize."""

    feasible: bool
    p: int
    open_sites: tuple[str, ...]
    assignment: dict[str, str]          # demand name -> open site name
    total_weighted_distance: float
    total_fixed_cost: float
    objective: float                    # weighted distance + fixed cost (the MILP objective)
    baseline_distance: float            # best single facility (p=1) - the "value of >1 DC" anchor
    saving_vs_baseline: float
    saving_pct: float


def _distance(point: DemandPoint, site: CandidateSite) -> float:
    return math.hypot(point.x - site.x, point.y - site.y)


def _best_single_facility_distance(
    demands: list[DemandPoint], sites: list[CandidateSite]
) -> float:
    """The p=1 baseline: lowest total weighted distance from any single candidate site.

    Reuses src.facility_location.total_weighted_distance - every demand served by one site.
    A pure distance reference (capacity/fixed cost are ignored here on purpose).
    """
    return min(
        total_weighted_distance(demands, Location(s.x, s.y)) for s in sites
    )


def solve_p_median(
    demands: list[DemandPoint],
    sites: list[CandidateSite],
    p: int,
    *,
    respect_capacity: bool = True,
) -> NetworkDesign:
    """Choose which p of `sites` to open and assign each demand to one open site, minimizing
    total weighted distance (plus any fixed costs), via scipy.optimize.milp.

    Raises ValueError for structurally impossible inputs (no demands/sites, p out of
    1..len(sites)). A run the solver finds infeasible (e.g. capacities too tight for p)
    returns NetworkDesign(feasible=False, ...) rather than raising - the job's verify()
    turns that into a QA failure with a clear message.
    """
    if not demands:
        raise ValueError("at least one demand point is required")
    if not sites:
        raise ValueError("at least one candidate site is required")
    if not 1 <= p <= len(sites):
        raise ValueError(
            f"p must be between 1 and the number of candidate sites ({len(sites)}); got {p}"
        )

    n_d = len(demands)
    n_s = len(sites)
    n_x = n_d * n_s
    n_var = n_s + n_x

    # Flattened decision variables: [ y_0..y_{n_s-1}, x_00..x_{n_d-1,n_s-1} ].
    #   y_j = 1 if site j is opened; x_ij = 1 if demand i is served by site j.
    def xi(i: int, j: int) -> int:
        return n_s + i * n_s + j

    # Objective: sum_j fixed_cost_j y_j + sum_ij w_i d_ij x_ij.
    c = np.zeros(n_var)
    for j, s in enumerate(sites):
        c[j] = s.fixed_cost
    for i, d in enumerate(demands):
        for j, s in enumerate(sites):
            c[xi(i, j)] = d.weight * _distance(d, s)

    constraints: list[LinearConstraint] = []

    # (1) each demand assigned to exactly one site: sum_j x_ij = 1.
    a_assign = np.zeros((n_d, n_var))
    for i in range(n_d):
        for j in range(n_s):
            a_assign[i, xi(i, j)] = 1.0
    constraints.append(LinearConstraint(a_assign, lb=1.0, ub=1.0))

    # (2) can only assign to an open site: x_ij - y_j <= 0.
    a_link = np.zeros((n_x, n_var))
    row = 0
    for i in range(n_d):
        for j in range(n_s):
            a_link[row, xi(i, j)] = 1.0
            a_link[row, j] = -1.0
            row += 1
    constraints.append(LinearConstraint(a_link, lb=-np.inf, ub=0.0))

    # (3) open exactly p sites: sum_j y_j = p.
    a_count = np.zeros((1, n_var))
    a_count[0, :n_s] = 1.0
    constraints.append(LinearConstraint(a_count, lb=float(p), ub=float(p)))

    # (4) capacity, only for sites that declare one: sum_i w_i x_ij - cap_j y_j <= 0.
    cap_rows = [
        (j, s.capacity)
        for j, s in enumerate(sites)
        if respect_capacity and s.capacity is not None
    ]
    if cap_rows:
        a_cap = np.zeros((len(cap_rows), n_var))
        for r, (j, cap) in enumerate(cap_rows):
            for i, d in enumerate(demands):
                a_cap[r, xi(i, j)] = d.weight
            a_cap[r, j] = -float(cap)
        constraints.append(LinearConstraint(a_cap, lb=-np.inf, ub=0.0))

    integrality = np.ones(n_var)        # all variables binary
    bounds = Bounds(lb=0.0, ub=1.0)

    res = milp(c=c, constraints=constraints, integrality=integrality, bounds=bounds)

    baseline = _best_single_facility_distance(demands, sites)

    if not res.success or res.x is None:
        return NetworkDesign(
            feasible=False, p=p, open_sites=(), assignment={},
            total_weighted_distance=math.inf, total_fixed_cost=math.inf,
            objective=math.inf, baseline_distance=baseline,
            saving_vs_baseline=0.0, saving_pct=0.0,
        )

    x = res.x
    open_sites = tuple(sites[j].name for j in range(n_s) if x[j] > 0.5)
    assignment: dict[str, str] = {}
    weighted = 0.0
    for i, d in enumerate(demands):
        j = max(range(n_s), key=lambda jj: x[xi(i, jj)])
        assignment[d.name] = sites[j].name
        weighted += d.weight * _distance(d, sites[j])
    total_fixed = sum(sites[j].fixed_cost for j in range(n_s) if x[j] > 0.5)
    saving = baseline - weighted
    saving_pct = (saving / baseline) if baseline > 0 else 0.0
    return NetworkDesign(
        feasible=True, p=p, open_sites=open_sites, assignment=assignment,
        total_weighted_distance=weighted, total_fixed_cost=total_fixed,
        objective=float(res.fun), baseline_distance=baseline,
        saving_vs_baseline=saving, saving_pct=saving_pct,
    )
```

4. **Run the tests — they pass.** `PYTHONPATH=. py -m pytest tests/test_network_design.py -q`.

5. **Lint**: `ruff check src/network_design.py tests/test_network_design.py`. (Note: the `lambda jj:` closure over the loop's `x`/`i` is evaluated immediately inside the loop body, so the late-binding cell is not a bug; if ruff's `B023` flags it, hoist to `row_i = i` and index `x[xi(i, jj)]` via a small local `def pick(jj): return x[xi(i, jj)]` defined per-iteration, or use `np.argmax(x[n_s + i * n_s : n_s + (i + 1) * n_s])`. Prefer the `np.argmax` form to keep ruff clean.)

6. **Commit**: `feat: p-median multi-facility network-design MILP engine (src/network_design.py)`.

**Deliverable**: a pure, tested MILP engine with known-optimum coverage.

---

## Task 2 — Data-prep + deck job (`jobs/network_design_job.py`)

**Files**
- Create `jobs/network_design_job.py`
- Create `tests/test_network_design_tool.py` (job-level portion first; routing/orchestrator added in Task 3)

**Interfaces**
- Consumes (verified): `src.network_design.{CandidateSite, NetworkDesign, solve_p_median}`; `src.facility_location.DemandPoint`; `src.deliverable.{DataSource, Deliverable, Finding, Kpi}` where `Finding(title, detail, impact="")`, `Kpi(name, value, target="", rationale="")`, `DataSource(field, source, cadence="")`, `Deliverable.write_all(out_dir) -> {"report": Path, "workbook": Path}`; `src.export.write_summary_csv(rows: list[dict], path) -> Path`.
- Produces (new):
  - `NetworkDesignReport` (frozen) — see fields below
  - `prepare_records(df: pd.DataFrame, params: dict | None = None) -> dict`
  - `prepare(data_path: str, params: dict | None = None) -> dict`
  - `run(payload: dict) -> NetworkDesignReport`
  - `verify(report: NetworkDesignReport) -> list[str]`
  - `write_operational(report: NetworkDesignReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]`
  - `build_deck(report: NetworkDesignReport, *, client: str = "Client", prepared: str = "", citations: tuple[str, ...] = (), confidence: float = 0.85) -> Deliverable`

### Steps

1. **Write the failing job-level tests** at the top of `tests/test_network_design_tool.py`:

```python
"""Tests for the network-design (p-median) agent tool: a nodes CSV -> which p sites to open."""
from pathlib import Path

import pandas as pd
import pytest

from jobs import network_design_job as ndj
from scm_agent import intent, llm, tools
from scm_agent.orchestrator import Orchestrator
from src.deliverable import Deliverable
from src.guided import OPTIONS


def _nodes_df() -> pd.DataFrame:
    # two clusters of three; no role column -> candidates default to the demand nodes
    return pd.DataFrame({
        "name": ["D0", "D1", "D2", "D3", "D4", "D5"],
        "x": [0, 1, 2, 10, 11, 12],
        "y": [0, 0, 0, 0, 0, 0],
        "weight": [1, 1, 1, 1, 1, 1],
    })


def test_prepare_defaults_candidates_to_demand_nodes(tmp_path):
    csv = tmp_path / "nodes.csv"
    _nodes_df().to_csv(csv, index=False)
    payload = ndj.prepare(str(csv), {"p": 2})
    assert len(payload["demands"]) == 6
    assert len(payload["sites"]) == 6
    assert payload["p"] == 2


def test_prepare_errors_without_coordinates(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1]}).to_csv(csv, index=False)
    with pytest.raises(ValueError, match="x|y"):
        ndj.prepare(str(csv), {})


def test_prepare_splits_demand_and_candidate_roles(tmp_path):
    df = pd.DataFrame({
        "name": ["cust", "siteA", "siteB"],
        "x": [0, 0, 10], "y": [0, 0, 0],
        "weight": [5, 0, 0],
        "role": ["demand", "candidate", "candidate"],
    })
    csv = tmp_path / "roles.csv"
    df.to_csv(csv, index=False)
    payload = ndj.prepare(str(csv), {"p": 1})
    assert len(payload["demands"]) == 1
    assert len(payload["sites"]) == 2


def test_run_opens_the_two_cluster_medians():
    report = ndj.run(ndj.prepare_records(_nodes_df(), {"p": 2}))
    assert report.feasible
    assert set(report.open_sites) == {"D1", "D4"}
    assert report.total_weighted_distance == pytest.approx(4.0)
    assert report.saving_vs_baseline == pytest.approx(26.0)
    assert report.p == 2
    assert ndj.verify(report) == []


def test_verify_flags_an_infeasible_network():
    df = pd.DataFrame({
        "name": ["A", "B", "C", "only"],
        "x": [0, 1, 2, 0], "y": [0, 0, 0, 0],
        "weight": [10, 10, 10, 0],
        "role": ["demand", "demand", "demand", "candidate"],
        "capacity": [None, None, None, 20],   # 20 < 30 total demand
    })
    report = ndj.run(ndj.prepare_records(df, {"p": 1}))
    assert report.feasible is False
    assert ndj.verify(report) != []


def test_build_deck_is_ascii_deliverable():
    report = ndj.run(ndj.prepare_records(_nodes_df(), {"p": 2}))
    deck = ndj.build_deck(report, client="Acme", citations=("Chopra network design",), confidence=0.85)
    assert isinstance(deck, Deliverable)
    md = deck.to_markdown()
    assert md.isascii()
    assert "Network Design" in md and "## Coverage & handoff" in md
```

2. **Run — they fail** (`ImportError: cannot import name 'network_design_job'`). `PYTHONPATH=. py -m pytest tests/test_network_design_tool.py -q`.

3. **Write the job** `jobs/network_design_job.py`:

```python
"""Multi-facility network-design agent job: a nodes CSV -> which p sites to open.

The data-prep + deck half of the p-median network-design tool. Reads a single CSV of network
nodes with pandas directly (deliberately not via jobs/intake.py, which the parallel loop owns)
and solves the p-median problem via src.network_design: choose which p candidate sites to open
and assign each demand point to one, minimizing total weighted travel, then quantify the saving
against the best single-facility (p=1) baseline.

The CSV carries demand points and candidate sites. An optional `role` column
(demand / candidate) separates them; with no role column every row is BOTH a demand point and a
candidate site (candidate set = the demand nodes, the classic p-median setup).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv
from src.facility_location import DemandPoint
from src.network_design import CandidateSite, solve_p_median

_NAME_COLS = ("name", "location", "city", "node", "point", "site", "label")
_X_COLS = ("x", "lon", "longitude", "x_coord", "easting")
_Y_COLS = ("y", "lat", "latitude", "y_coord", "northing")
_WEIGHT_COLS = ("weight", "demand", "volume", "load", "units", "tons")
_ROLE_COLS = ("role", "type", "kind", "node_type")
_FIXED_COST_COLS = ("fixed_cost", "fixed", "open_cost", "annual_cost")
_CAPACITY_COLS = ("capacity", "cap", "throughput", "max_load")

_DEMAND_ROLES = {"demand", "customer", "store", "d"}
_CANDIDATE_ROLES = {"candidate", "site", "facility", "dc", "warehouse", "c"}


@dataclass(frozen=True)
class NetworkDesignReport:
    n_demand: int
    n_sites: int
    p: int
    feasible: bool
    open_sites: tuple[str, ...]
    total_weighted_distance: float
    total_fixed_cost: float
    baseline_distance: float
    saving_vs_baseline: float
    saving_pct: float
    assignment: dict[str, str]
    summary: str


def _pick_column(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> dict:
    """Sniff coordinate / load / role columns, build demand points + candidate sites + p."""
    params = params or {}
    x = _pick_column(df, params.get("x_col"), _X_COLS)
    y = _pick_column(df, params.get("y_col"), _Y_COLS)
    missing = [n for n, c in (("x", x), ("y", y)) if c is None]
    if missing:
        cols = list(df.columns)[:10]
        raise ValueError(
            f"could not find {', '.join(missing)}; pass them in params (columns seen: {cols})"
        )

    name = _pick_column(df, params.get("name_col"), _NAME_COLS)
    weight = _pick_column(df, params.get("weight_col"), _WEIGHT_COLS)
    role = _pick_column(df, params.get("role_col"), _ROLE_COLS)
    fixed = _pick_column(df, params.get("fixed_cost_col"), _FIXED_COST_COLS)
    capacity = _pick_column(df, params.get("capacity_col"), _CAPACITY_COLS)

    demands: list[DemandPoint] = []
    sites: list[CandidateSite] = []
    for i, (_, row) in enumerate(df.iterrows()):
        label = str(row[name]) if name else f"N{i + 1}"
        px, py = float(row[x]), float(row[y])
        role_val = str(row[role]).strip().lower() if role and pd.notna(row[role]) else ""
        if role:
            is_demand = role_val in _DEMAND_ROLES
            is_site = role_val in _CANDIDATE_ROLES
            # an unrecognized role label counts as BOTH, so a typo never silently drops a node
            if not is_demand and not is_site:
                is_demand = is_site = True
        else:
            is_demand = is_site = True
        if is_demand:
            w = float(row[weight]) if weight and pd.notna(row[weight]) else 1.0
            demands.append(DemandPoint(name=label, x=px, y=py, weight=w))
        if is_site:
            fc = float(row[fixed]) if fixed and pd.notna(row[fixed]) else 0.0
            cap = float(row[capacity]) if capacity and pd.notna(row[capacity]) else None
            sites.append(CandidateSite(name=label, x=px, y=py, fixed_cost=fc, capacity=cap))

    n_sites = len(sites)
    default_p = 2 if n_sites >= 2 else 1
    raw_p = params.get("p", params.get("facilities", params.get("num_facilities", default_p)))
    p = int(raw_p)
    if n_sites:
        p = max(1, min(p, n_sites))
    return {"demands": demands, "sites": sites, "p": p}


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Read a network-nodes CSV and build the p-median payload."""
    return prepare_records(pd.read_csv(data_path), params)


def run(payload: dict) -> NetworkDesignReport:
    """Solve the p-median network and quantify the saving vs a single facility."""
    demands: list[DemandPoint] = payload["demands"]
    sites: list[CandidateSite] = payload["sites"]
    p: int = payload["p"]
    design = solve_p_median(demands, sites, p)
    if design.feasible:
        summary = (
            f"p-median over {len(demands)} demand point(s) and {len(sites)} candidate site(s): "
            f"open {p} site(s) [{', '.join(design.open_sites)}], "
            f"{design.total_weighted_distance:,.0f} total weighted distance, "
            f"{design.saving_vs_baseline:,.0f} less than the best single facility "
            f"({design.saving_pct * 100:.0f}% saving)."
        )
    else:
        summary = (
            f"p-median over {len(demands)} demand point(s) and {len(sites)} candidate site(s) with "
            f"p={p} is infeasible: the capacities are too tight to serve all demand from {p} site(s)."
        )
    return NetworkDesignReport(
        n_demand=len(demands), n_sites=len(sites), p=p, feasible=design.feasible,
        open_sites=design.open_sites, total_weighted_distance=design.total_weighted_distance,
        total_fixed_cost=design.total_fixed_cost, baseline_distance=design.baseline_distance,
        saving_vs_baseline=design.saving_vs_baseline, saving_pct=design.saving_pct,
        assignment=design.assignment, summary=summary,
    )


def verify(report: NetworkDesignReport) -> list[str]:
    """QA gate: nodes present, a feasible network, exactly p open sites, every demand assigned."""
    issues: list[str] = []
    if report.n_demand <= 0:
        issues.append("no demand points to serve")
    if report.n_sites <= 0:
        issues.append("no candidate sites to choose from")
    if not report.feasible:
        issues.append(f"no feasible network opens {report.p} site(s) under the given capacities")
        return issues
    if len(report.open_sites) != report.p:
        issues.append(f"expected {report.p} open site(s), got {len(report.open_sites)}")
    if not math.isfinite(report.total_weighted_distance) or report.total_weighted_distance < 0:
        issues.append("total weighted distance is negative or non-finite")
    if len(report.assignment) != report.n_demand:
        issues.append("not every demand point was assigned to an open site")
    return issues


def write_operational(
    report: NetworkDesignReport, out_dir: str | Path, client: str = "Client"
) -> dict[str, Path]:
    """The machine-readable deliverable: which open site serves each demand point."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {"demand_point": name, "assigned_site": site}
        for name, site in sorted(report.assignment.items())
    ]
    return {"csv": write_summary_csv(rows, d / "network_design.csv")}


def build_deck(
    report: NetworkDesignReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.85,
) -> Deliverable:
    """Compose the multi-facility network-design study: which p sites to open and the travel saved."""
    summary = (
        f"p-median network design over {report.n_demand} demand point(s) and {report.n_sites} "
        f"candidate site(s): open {report.p} site(s) [{', '.join(report.open_sites)}], "
        f"{report.total_weighted_distance:,.0f} total weighted travel, "
        f"{report.saving_pct * 100:.0f}% less than the best single facility."
    )
    findings = [
        Finding(
            f"Open {report.p} site(s)",
            f"[{', '.join(report.open_sites)}]; {report.total_weighted_distance:,.0f} total weighted travel.",
            impact="minimizes total load x distance across the whole network",
        ),
        Finding(
            "Saving vs a single facility",
            f"A single DC would incur {report.baseline_distance:,.0f} weighted travel; opening "
            f"{report.p} cuts {report.saving_vs_baseline:,.0f} ({report.saving_pct * 100:.0f}%).",
            impact="the prize from a multi-facility network - weigh against fixed + running cost",
        ),
    ]
    if report.total_fixed_cost > 0:
        findings.append(Finding(
            "Fixed cost of the opened sites",
            f"{report.total_fixed_cost:,.0f} in fixed cost across the {report.p} opened site(s).",
            impact="netted into the objective when comparing network configurations",
        ))

    kpis = [
        Kpi("Demand points", f"{report.n_demand}", rationale="Nodes the network serves"),
        Kpi("Candidate sites", f"{report.n_sites}", rationale="Sites the model could open"),
        Kpi("Facilities opened", f"{report.p}", rationale="p in the p-median model"),
        Kpi("Total weighted distance", f"{report.total_weighted_distance:,.0f}", target="minimize",
            rationale="Total load x distance at the chosen network"),
        Kpi("Saving vs single facility", f"{report.saving_vs_baseline:,.0f}", target="maximize",
            rationale="Weighted-travel reduction from opening p sites instead of one"),
    ]

    data_sources = (
        DataSource("Demand points (coordinates + load)", "Customer / store master + volumes", "per network review"),
        DataSource("Candidate sites (coordinates + fixed cost / capacity)", "Real-estate / DC option list", "per network review"),
    )

    recommendations = (
        "Open the p sites the model selected (or the nearest feasible real locations to them).",
        "Weigh the weighted-travel saving against the fixed and running cost of each extra facility.",
        "Confirm capacities and that real road distance, land and labour don't override the geometric optimum.",
    )

    return Deliverable(
        title="Network Design (Multi-Facility p-Median)",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=tuple(kpis),
        data_sources=data_sources,
        recommendations=recommendations,
        citations=tuple(citations),
        confidence=confidence,
        residual="p-median on straight-line distance with single-source assignment: confirm the "
                 "candidate coordinates, loads, capacities and fixed costs, and that road distance and "
                 "site availability match. Splitting a demand point across sites, or road / time "
                 "distance, needs a richer model.",
        prepared=prepared,
    )
```

4. **Run the job-level tests — they pass.** `PYTHONPATH=. py -m pytest tests/test_network_design_tool.py -q` (routing/orchestrator tests are added in Task 3; they will error until then — acceptable, or run only the named job tests with `-k "prepare or run or verify or build_deck"`).

5. **Lint**: `ruff check jobs/network_design_job.py tests/test_network_design_tool.py`.

6. **Commit**: `feat: network-design job (prepare/run/verify/deck) over the p-median engine`.

**Deliverable**: a job that turns a CSV into a QA-gated network-design report + operational CSV + deck.

---

## Task 3 — Register the tool, guided options, citation anchor, routing tests

**Files**
- Modify `scm_agent/tool_options.py` (add `network_design_options`)
- Modify `scm_agent/tools.py` (import the job, add `_network_design_prepare`/`_network_design_run`/`network_design_tool()`, register in `build_default_registry()`)
- Modify `scm_agent/citation_gate.py` (add one `TOOL_CONCEPTS` entry)
- Modify `tests/test_network_design_tool.py` (add routing + orchestrator tests — already drafted in Task 2's file; they light up here)

**Interfaces**
- Consumes (verified): `scm_agent.tool_options._ranked(summary: str, items: list[_Item], *, confidence: float = 0.85) -> GuidedOutcome` where `_Item = tuple[label, summary, action, tradeoffs]` (first item = recommended); `scm_agent.registry.{Tool, Prepared, Produced}`; `scm_agent.tools.build_default_registry`; `scm_agent.citation_gate.TOOL_CONCEPTS`; `scm_agent.intent.classify(brief, registry, provider, *, job_type_override=None) -> IntentResult`(`.job_type`); `scm_agent.orchestrator.Orchestrator(registry=, provider=).run(brief, *, data_path=None, overrides=None, job_type=None, client="Client", strict_params=False, out_dir=...) -> JobResult`(`.status, .tool, .deliverables, .guided`); `scm_agent.llm.RulesFallback`; `src.guided.OPTIONS`.
- Produces: `network_design_options(report: object) -> GuidedOutcome`; `network_design_tool() -> Tool`.

### Steps

1. **Add the routing + orchestrator tests** to `tests/test_network_design_tool.py` (append):

```python
def test_brief_routes_to_network_design():
    reg = tools.build_default_registry()
    res = intent.classify(
        "p-median network optimization: how many distribution centers to open and which sites",
        reg, llm.RulesFallback(),
    )
    assert res.job_type == "network_design"


def test_facility_location_brief_still_routes_to_facility_location():
    # guard: the new tool's keywords must not steal single-facility briefs
    reg = tools.build_default_registry()
    res = intent.classify(
        "facility location / network design: center of gravity for the optimal DC location",
        reg, llm.RulesFallback(),
    )
    assert res.job_type == "facility_location"


def test_orchestrator_runs_network_design_with_ranked_options(tmp_path):
    csv = tmp_path / "nodes.csv"
    _nodes_df().to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())
    res = orch.run(
        "p-median multi-facility: how many dcs to open and which sites",
        data_path=str(csv), overrides={"p": 2}, client="Acme", out_dir=tmp_path,
    )
    assert res.status == "ok" and res.tool == "network_design"
    assert Path(res.deliverables["deck_report"]).exists()
    assert Path(res.deliverables["csv"]).exists()
    assert res.guided is not None and res.guided.status == OPTIONS
    assert sum(1 for o in res.guided.options if o.recommended) == 1
```

2. **Run — they fail** (`KeyError`/no `network_design` in registry, `network_design_options` missing).

3. **Add `network_design_options`** to `scm_agent/tool_options.py` (place it right after `facility_location_options`, before `drp_options`):

```python
def network_design_options(report: object) -> GuidedOutcome:
    sites = ", ".join(report.open_sites)
    open_all = (
        f"Open the {report.p} optimal site(s)",
        f"Open [{sites}] - {report.total_weighted_distance:,.0f} total weighted travel, "
        f"{report.saving_vs_baseline:,.0f} less than the best single facility "
        f"({report.saving_pct * 100:.0f}%).",
        f"open sites {sites} and assign each demand to its nearest open site",
        "minimum load x distance across the network",
    )
    phase = (
        "Phase the rollout",
        f"Stand up the {report.p} site(s) in waves, busiest cluster first, to spread the capex.",
        "open the busiest site first, then the rest on a schedule",
        "slower to the full saving; smooths the investment",
    )
    single = (
        "Keep a single facility",
        f"Stay at one DC ({report.baseline_distance:,.0f} weighted travel) if the "
        f"{report.saving_pct * 100:.0f}% saving doesn't beat the cost of running more sites.",
        "keep one facility",
        f"forgoes {report.saving_vs_baseline:,.0f} weighted travel; avoids multi-site overhead",
    )
    validate = (
        "Validate the chosen site",
        "Field-check the selected site against real road distance, land and labour before opening.",
        "confirm the geometric optimum on the ground before committing",
        "de-risks the straight-line model",
    )
    items: list[_Item] = (
        [open_all, phase, single] if report.p > 1 else [open_all, validate]
    )
    return _ranked(
        f"Network design over {report.n_demand} demand point(s): choose how many DCs and which.",
        items,
    )
```

4. **Wire the tool** in `scm_agent/tools.py`:
   - Add `network_design_job` to the alphabetized `from jobs import (...)` block (between `newsvendor_job` and `odoo_job`, keeping sort order — insert `network_design_job,`).
   - Add the tool section (place after the `facility_location_tool()` block, before the `drp` section):

```python
# ---- network_design (multi-facility p-median MILP) ---------------------------

def _network_design_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(
            status="needs_data",
            messages=["a network-nodes CSV (x, y + optional name/weight/role/fixed_cost/capacity) "
                      "and a facility count (params.p) are required"],
        )
    try:
        payload = network_design_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not payload["demands"]:
        return Prepared(status="needs_data", messages=["no demand points found in the data"])
    if not payload["sites"]:
        return Prepared(status="needs_data", messages=["no candidate sites found in the data"])
    return Prepared(status="ok", payload=payload)


def _network_design_run(payload: object, params: dict) -> Produced:
    report = network_design_job.run(payload)
    return Produced(report=report, summary=report.summary)


def network_design_tool() -> Tool:
    return Tool(
        key="network_design",
        title="Network Design (Multi-Facility p-Median)",
        description="Choose which p of several candidate sites to open and assign each demand point "
                    "to one, minimizing total weighted travel (with optional fixed costs and "
                    "capacities), via an exact MILP - the multi-facility counterpart to single-site "
                    "facility location. Offline, straight-line distance.",
        intent_keywords=(
            "p-median", "p median", "multi-facility", "multiple facilities",
            "how many warehouses", "how many distribution centers", "how many dcs",
            "number of dcs", "which sites to open", "network optimization",
            "open facilities", "consolidate distribution centers",
        ),
        requires_data=True,
        options=tool_options.network_design_options,
        prepare=_network_design_prepare,
        run=_network_design_run,
        qa=lambda report: network_design_job.verify(report),
        deliver=lambda report, out_dir, client: network_design_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            network_design_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
    )
```

   - Register it in `build_default_registry()` (add after `reg.register(facility_location_tool())`):

```python
    reg.register(network_design_tool())
```

5. **Add the citation anchor** in `scm_agent/citation_gate.py` `TOOL_CONCEPTS` (after the `facility_location` line). Reuses the exact anchors the `facility_location` tool already validates against — verified present in the committed books graph as `knowledge::facility_location`, `knowledge::network_design`, `knowledge::distribution_network_design` (the gate is prefix-tolerant, so the bare ids resolve, exactly as they do for `facility_location`). Three concepts — well under the ~8 pool ceiling:

```python
    "network_design": ("facility_location", "network_design", "distribution_network_design"),
```

6. **Run the full new-tool suite — all green.** `PYTHONPATH=. py -m pytest tests/test_network_design.py tests/test_network_design_tool.py -q`.

7. **Run the routing-sensitive existing suites** to prove no regression from the shared `network_design`/`facility_location` domain: `PYTHONPATH=. py -m pytest tests/test_facility_location_tool.py tests/test_multi_echelon_tool.py tests/test_drp_tool.py -q`. (The new keyword list deliberately omits the bare phrases "network design" and "distribution network design" so it never ties with `facility_location` on generic briefs; "network optimization", "multi-facility", "p-median", "how many dcs/warehouses/distribution centers" are distinctive and multi-word.)

8. **Run the whole suite** to catch any "registered tool count" or citation-gate invariant tests: `PYTHONPATH=. py -m pytest tests/ -q`. In particular, if a test asserts a fixed tool count or `test_every_tool_has_concepts`-style coverage, update it in this same commit.

9. **Lint the full scope**: `ruff check src tests examples`.

10. **Commit**: `feat: register network_design (p-median) tool with ranked options + citation anchor`.

**Deliverable**: `network_design` is agent-routable end-to-end (brief -> classify -> prepare -> run -> QA -> deck + operational CSV -> ranked GuidedOutcome), grounded in L3, fully tested.

---

## Finalize

- Update `CHANGELOG.md` (a `feat` entry) and the tool-count / capability list in `CLAUDE.md` ("41 agent-routable tools" -> 42; add "multi-facility network design (p-median)" to the parenthetical). Same PR that changed the fact, per CLAUDE.md's rule.
- Optionally record the graphify structural finding (`graphify save-result ... --nodes "solve_p_median" "facility_location.total_weighted_distance"`) and regenerate `documentation/GRAPH_LESSONS.md`, committing both.
- Push the feature branch, open a **draft PR**, let CI go green on py3.11/3.12/3.13, then squash-merge.

## Open questions / decisions flagged during drafting

- Tool granularity: the plan recommends a NEW tool `network_design` rather than extending `facility_location` (distinct question, distinct report shape, cheap per the repo recipe). Confirm this over overloading the existing tool with a mode flag.
- Input shape: the plan uses a SINGLE CSV (the tool contract passes one `data_path`) with an optional `role` column (demand/candidate); with no role column every node is both a demand point and a candidate site (classic p-median). Alternative would be two CSVs via `params['sites_path']`. Confirm the single-CSV+role design.
- Default `p`: when `params.p` (or `facilities`/`num_facilities`) is absent, the plan defaults to p=2 (clamped to 1..n_sites). Alternative: block with `needs_clarification`. Confirm the default-to-2 choice.
- Baseline definition: `saving_vs_baseline` is measured against the best single candidate site by pure weighted distance (reusing `total_weighted_distance`, ignoring capacity/fixed cost as a reference point). Confirm this is the intended 'single-facility baseline' rather than a caller-supplied current network.
- Infeasible handling: a capacity/p-infeasible instance is surfaced as `feasible=False` -> `verify()` issue -> orchestrator `qa_failed` (no deliverable), consistent with the repo's 'QA fails => no deliverable'. It is NOT turned into a guided HANDOFF suggesting 'relax p / add capacity'. Confirm qa_failed is acceptable, or request a HANDOFF-on-infeasible path instead.
- Single-source assignment: the MILP assigns each demand point wholly to one open site (classic p-median). Demand splitting across multiple sites is explicitly out of scope (noted in the deck residual). Confirm single-sourcing is the intended model.
