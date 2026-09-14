> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax where present. Part of the CSCP/SCPro gap-closing initiative -- see docs/superpowers/specs/2026-07-20-cscp-scpro-gap-closing-design.md.

# Implementation Plan — Kern tool #42: `carbon_footprint` (computable sustainability)

## Goal
Add a new agent-routable Kern tool, `carbon_footprint`, that reads a shipments/activity CSV and computes greenhouse-gas emissions as `tonne-km x emission-factor-per-mode` (parcel / LTL / FTL / intermodal / air / sea) plus warehouse energy emissions, bucketed into GHG-Protocol Scope 1/2/3, with an optional internal **carbon cost** (`emissions x carbon price`) that can fold into landed cost. It closes the sustainability capability gap for BOTH CSCP and SCPro. The tool follows the repo's standard recipe: pure engine (`src/carbon_footprint.py`) + pandas job (`jobs/carbon_footprint_job.py`) + registered `Tool` + citation anchors + a protected `GuidedOutcome` + tests.

## Architecture
- **`src/carbon_footprint.py`** — pure, deterministic engine. Frozen dataclasses (`EmissionFactors` mirrors `src/logistics/modes.py::FreightRates`; `TransportActivity`, `WarehouseActivity`, `EmissionLine`, `FootprintSummary`) + pure functions (`tonne_km`, `transport_emissions`, `warehouse_emissions`, `carbon_cost`, `summarize`). No pandas, no I/O.
- **`src/landed_cost.py`** — extend `LandedCost` + `landed_cost()` with a backward-compatible `carbon_cost` leg (added to `total`, EXCLUDED from `duty_base`).
- **`jobs/carbon_footprint_job.py`** — pandas-only `prepare()` reading its OWN CSV (never `jobs/intake.py`) + `run()` / `verify()` / `write_operational()` / `build_deck()`.
- **`scm_agent/tool_options.py`** — `carbon_footprint_options(report)` returns a ranked `GuidedOutcome` (OPTIONS) of decarbonization levers.
- **`scm_agent/citation_gate.py`** — a `carbon_footprint` entry in `TOOL_CONCEPTS` with 4 verified L3 anchor node ids (well under the ~8 empirical pool ceiling).
- **`scm_agent/tools.py`** — `_carbon_footprint_prepare` / `_carbon_footprint_run` + `carbon_footprint_tool()` factory, registered in `build_default_registry()`.
- **Tests** — `tests/test_carbon_footprint.py` (engine, hand-checkable numbers), `tests/test_carbon_footprint_job.py` (job numeric + deck ASCII), `tests/test_carbon_footprint_tool.py` (routing + no-steal + orchestrator e2e); existing `tests/test_citation_gate.py::test_every_anchor_concept_exists` auto-covers the new anchors; `tests/test_landed_cost.py` gains carbon-cost cases.

## Tech Stack
Python 3.11+, `dataclasses` (frozen), `pandas` (job layer only), `pytest`. Engine reuses `src/deliverable.py` (`Deliverable/Finding/Kpi/DataSource`), `src/export.py::write_summary_csv`, `src/guided.py` via `tool_options`. Tests run `PYTHONPATH=. py -m pytest`; lint `ruff check src tests examples`.

## Global Constraints
- **ASCII-only in any console/`print` and in every `Deliverable` string** (Windows cp1252; decks assert `.to_markdown().isascii()`). Use `t CO2e`, `->`, `<=`, never em dashes or the degree/subscript glyphs.
- **Frozen `@dataclass`, type hints on all signatures.**
- **No module-level optional-extra imports** on the boot chain — `src/carbon_footprint.py` is pure stdlib (safe to import at the top of `tools.py`); the pandas import lives only in the job (same as every other `*_job.py`).
- **Emission factors are documented public defaults, NOT the licensed GLEC figures** (deferred follow-up — see Open Questions). Provenance goes in code comments; the `Deliverable.residual` states factors are advisory.
- **TDD per task**: write failing test -> run (fails) -> minimal impl -> run (passes) -> commit. Each task ends in an independently committable deliverable.
- **Branch workflow**: feature branch `feat/carbon-footprint` -> draft PR -> CI green (py3.11/3.12/3.13) -> squash-merge. Never push to `main`.

---

## Task 1 — Pure emissions engine (`src/carbon_footprint.py`)

**Files**
- Create `src/carbon_footprint.py`
- Create `tests/test_carbon_footprint.py`

**Interfaces**
- Consumes: nothing (pure stdlib). Mirrors the rate-card shape of `src/logistics/modes.py::FreightRates` (verified: `@dataclass(frozen=True)` with per-mode float fields and illustrative defaults) and the `Shipment` fields `weight_kg`, `distance_km`.
- Produces:
  - `EmissionFactors` frozen dataclass with `.transport_factor(mode: str) -> float`
  - `tonne_km(weight_kg: float, distance_km: float) -> float`
  - `transport_emissions(activity: TransportActivity, factors: EmissionFactors = EmissionFactors()) -> EmissionLine`
  - `warehouse_emissions(activity: WarehouseActivity, factors: EmissionFactors = EmissionFactors()) -> list[EmissionLine]`
  - `carbon_cost(emissions_kg: float, carbon_price_per_tonne: float) -> float`
  - `summarize(lines: list[EmissionLine], *, carbon_price_per_tonne: float = 0.0) -> FootprintSummary`
  - constants `SCOPE_1`, `SCOPE_2`, `SCOPE_3`, `CARBON_MODES`

**Steps**

1. Write `tests/test_carbon_footprint.py` with hand-checkable numbers (fails: module does not exist yet):
```python
"""Tests for the carbon-footprint / GHG-emissions engine (computable sustainability)."""

import math

import pytest

from src.carbon_footprint import (
    SCOPE_1,
    SCOPE_2,
    SCOPE_3,
    EmissionFactors,
    TransportActivity,
    WarehouseActivity,
    carbon_cost,
    summarize,
    tonne_km,
    transport_emissions,
    warehouse_emissions,
)


def test_tonne_km_is_weight_tonnes_times_distance():
    # 10,000 kg over 500 km = 10 t * 500 km = 5000 tonne-km.
    assert tonne_km(10_000.0, 500.0) == pytest.approx(5000.0)


def test_ftl_transport_emissions_use_the_mode_factor():
    line = transport_emissions(TransportActivity("L1", "ftl", 10_000.0, 500.0))
    # 5000 tkm * 0.062 kg/tkm = 310 kg CO2e; third-party carrier -> Scope 3.
    assert line.activity_amount == pytest.approx(5000.0)
    assert line.emissions_kg == pytest.approx(310.0)
    assert line.scope == SCOPE_3
    assert line.kind == "transport"


def test_air_is_far_more_intense_than_sea_for_the_same_tonne_km():
    air = transport_emissions(TransportActivity("A", "air", 100.0, 1000.0))
    sea = transport_emissions(TransportActivity("S", "sea", 100.0, 1000.0))
    # 0.1 t * 1000 km = 100 tkm. air 100*0.602 = 60.2 kg; sea 100*0.008 = 0.8 kg.
    assert air.emissions_kg == pytest.approx(60.2)
    assert sea.emissions_kg == pytest.approx(0.8)
    assert air.emissions_kg > sea.emissions_kg


def test_own_fleet_transport_is_scope_1():
    line = transport_emissions(TransportActivity("F", "ftl", 1000.0, 100.0, own_fleet=True))
    assert line.scope == SCOPE_1


def test_warehouse_splits_electricity_scope2_and_fuel_scope1():
    lines = warehouse_emissions(WarehouseActivity("W1", electricity_kwh=1000.0, fuel_kwh=500.0))
    by = {ln.detail: ln for ln in lines}
    assert by["electricity"].emissions_kg == pytest.approx(400.0)   # 1000 * 0.40
    assert by["electricity"].scope == SCOPE_2
    assert by["fuel"].emissions_kg == pytest.approx(100.0)          # 500 * 0.20
    assert by["fuel"].scope == SCOPE_1


def test_unknown_mode_raises():
    with pytest.raises(ValueError, match="unknown transport mode"):
        transport_emissions(TransportActivity("X", "rocket", 1.0, 1.0))


def test_carbon_cost_is_tonnes_times_price():
    # 770.2 kg = 0.7702 t; at 50/t -> 38.51.
    assert carbon_cost(770.2, 50.0) == pytest.approx(38.51)
    assert carbon_cost(0.0, 50.0) == 0.0


def test_summarize_buckets_scopes_and_totals():
    lines = [
        transport_emissions(TransportActivity("L1", "ftl", 10_000.0, 500.0)),   # 310 Scope 3
        transport_emissions(TransportActivity("A", "air", 100.0, 1000.0)),      # 60.2 Scope 3
        *warehouse_emissions(WarehouseActivity("W1", electricity_kwh=1000.0)),  # 400 Scope 2
    ]
    s = summarize(lines, carbon_price_per_tonne=50.0)
    assert s.total_emissions_kg == pytest.approx(770.2)
    assert s.by_scope[SCOPE_3] == pytest.approx(370.2)
    assert s.by_scope[SCOPE_2] == pytest.approx(400.0)
    assert s.by_scope[SCOPE_1] == pytest.approx(0.0)
    assert set(s.by_scope) == {SCOPE_1, SCOPE_2, SCOPE_3}   # all three keys always present
    assert s.total_tonne_km == pytest.approx(5100.0)        # 5000 + 100 (warehouse contributes 0)
    assert s.carbon_cost == pytest.approx(38.51)
    assert math.isfinite(s.total_emissions_kg)
```
2. Run it: `PYTHONPATH=. py -m pytest tests/test_carbon_footprint.py -q` — confirm it FAILS (ImportError).
3. Create `src/carbon_footprint.py` with the minimal implementation to pass:
```python
"""Carbon-footprint / GHG-emissions engine (computable sustainability).

Greenhouse-gas emissions for supply-chain activity from first principles, closed-form
and deterministic (auditable for the QA gate), mirroring the transport-economics style
of src/logistics/modes.py:

- transport emissions = tonne-km * an emission factor per mode (parcel/ltl/ftl/
  intermodal/air/sea), where tonne-km = weight_kg/1000 * distance_km;
- warehouse/storage emissions = electricity_kwh * grid factor (Scope 2) plus on-site
  fuel_kwh * fuel factor (Scope 1);
- bucketed into the GHG-Protocol Scope 1 / 2 / 3;
- an optional internal carbon COST = emissions(tonnes) * carbon_price, which the
  landed-cost engine (src/landed_cost.py) can fold into delivered cost.

Emission factors: the DEFAULT EmissionFactors below carries ILLUSTRATIVE public
well-to-wheel values (kg CO2e per tonne-km) in the GLEC/DEFRA order of magnitude,
NOT the licensed GLEC Framework figures themselves (those are not in the L3 books
graph yet - a deferred follow-up). Override EmissionFactors per lane/carrier/grid
with audited numbers before relying on the ABSOLUTE totals; the RELATIVE mode
ranking (air >> road parcel > LTL > FTL > rail/intermodal > sea) is robust.

Reference (grounding in L3): Grant, Trautrims & Wong, "Sustainable Logistics and
Supply Chain Management" - Transport Emissions, Warehouse Energy Efficiency,
Greenhouse Gas (GHG) Emissions, Green Logistics.
"""
from __future__ import annotations

from dataclasses import dataclass

# GHG Protocol scopes.
SCOPE_1 = "scope_1"   # direct combustion: own-fleet fuel, on-site warehouse fuel/gas
SCOPE_2 = "scope_2"   # indirect: purchased warehouse electricity
SCOPE_3 = "scope_3"   # value chain: third-party (carrier) freight
_SCOPES = (SCOPE_1, SCOPE_2, SCOPE_3)

CARBON_MODES = ("parcel", "ltl", "ftl", "intermodal", "air", "sea")


@dataclass(frozen=True)
class EmissionFactors:
    """Well-to-wheel GHG intensity per transport mode (kg CO2e per tonne-km) plus
    warehouse energy factors. Defaults are ILLUSTRATIVE public order-of-magnitude
    values (GLEC/DEFRA range), NOT the licensed GLEC figures - override per lane."""

    # kg CO2e per tonne-km, by mode. Public order-of-magnitude values (replace with
    # audited GLEC/DEFRA factors before relying on absolute totals):
    #   air ~ 0.602, parcel/van ~ 0.55, LTL road ~ 0.11, FTL road HGV ~ 0.062,
    #   intermodal rail ~ 0.028, deep-sea container ~ 0.008.
    parcel_kg_co2e_per_tkm: float = 0.55
    ltl_kg_co2e_per_tkm: float = 0.11
    ftl_kg_co2e_per_tkm: float = 0.062
    intermodal_kg_co2e_per_tkm: float = 0.028
    air_kg_co2e_per_tkm: float = 0.602
    sea_kg_co2e_per_tkm: float = 0.008
    # Warehouse energy: purchased electricity (Scope 2) and on-site fuel (Scope 1).
    # Public averages; a client-specific grid factor should override grid_kg_co2e_per_kwh.
    grid_kg_co2e_per_kwh: float = 0.40
    fuel_kg_co2e_per_kwh: float = 0.20

    def transport_factor(self, mode: str) -> float:
        key = mode.strip().lower()
        table = {
            "parcel": self.parcel_kg_co2e_per_tkm,
            "ltl": self.ltl_kg_co2e_per_tkm,
            "ftl": self.ftl_kg_co2e_per_tkm,
            "intermodal": self.intermodal_kg_co2e_per_tkm,
            "air": self.air_kg_co2e_per_tkm,
            "sea": self.sea_kg_co2e_per_tkm,
        }
        if key not in table:
            raise ValueError(f"unknown transport mode {mode!r}; expected one of {CARBON_MODES}")
        return table[key]


@dataclass(frozen=True)
class TransportActivity:
    """One freight movement. own_fleet=True -> Scope 1 (own combustion); else Scope 3."""

    activity_id: str
    mode: str
    weight_kg: float
    distance_km: float
    own_fleet: bool = False


@dataclass(frozen=True)
class WarehouseActivity:
    """One storage site's period energy use. electricity -> Scope 2; fuel -> Scope 1."""

    activity_id: str
    electricity_kwh: float = 0.0
    fuel_kwh: float = 0.0


@dataclass(frozen=True)
class EmissionLine:
    """One activity's emissions, in exactly one scope (a warehouse can yield two lines)."""

    activity_id: str
    kind: str                # "transport" | "warehouse"
    detail: str              # mode ("ftl", ...) or energy carrier ("electricity" | "fuel")
    activity_amount: float   # tonne-km (transport) or kWh (warehouse)
    emissions_kg: float      # kg CO2e
    scope: str               # scope_1 | scope_2 | scope_3


@dataclass(frozen=True)
class FootprintSummary:
    """Rolled-up footprint: total, per-scope split, total activity, and carbon cost."""

    total_emissions_kg: float
    by_scope: dict[str, float]   # every scope key present, even when 0.0
    total_tonne_km: float
    carbon_cost: float           # 0.0 when no price supplied


def tonne_km(weight_kg: float, distance_km: float) -> float:
    """Freight activity in tonne-kilometres: (weight_kg / 1000) * distance_km."""
    return (weight_kg / 1000.0) * distance_km


def transport_emissions(
    activity: TransportActivity, factors: EmissionFactors = EmissionFactors()
) -> EmissionLine:
    """Emissions for one freight movement: tonne-km * the mode's emission factor."""
    tkm = tonne_km(activity.weight_kg, activity.distance_km)
    factor = factors.transport_factor(activity.mode)
    scope = SCOPE_1 if activity.own_fleet else SCOPE_3
    return EmissionLine(
        activity_id=activity.activity_id, kind="transport", detail=activity.mode.strip().lower(),
        activity_amount=tkm, emissions_kg=tkm * factor, scope=scope,
    )


def warehouse_emissions(
    activity: WarehouseActivity, factors: EmissionFactors = EmissionFactors()
) -> list[EmissionLine]:
    """Emissions for one storage site: electricity (Scope 2) and on-site fuel (Scope 1)."""
    lines: list[EmissionLine] = []
    if activity.electricity_kwh:
        lines.append(EmissionLine(
            activity_id=activity.activity_id, kind="warehouse", detail="electricity",
            activity_amount=activity.electricity_kwh,
            emissions_kg=activity.electricity_kwh * factors.grid_kg_co2e_per_kwh, scope=SCOPE_2,
        ))
    if activity.fuel_kwh:
        lines.append(EmissionLine(
            activity_id=activity.activity_id, kind="warehouse", detail="fuel",
            activity_amount=activity.fuel_kwh,
            emissions_kg=activity.fuel_kwh * factors.fuel_kg_co2e_per_kwh, scope=SCOPE_1,
        ))
    return lines


def carbon_cost(emissions_kg: float, carbon_price_per_tonne: float) -> float:
    """Internal carbon cost = emissions (tonnes CO2e) * price per tonne."""
    return (emissions_kg / 1000.0) * carbon_price_per_tonne


def summarize(
    lines: list[EmissionLine], *, carbon_price_per_tonne: float = 0.0
) -> FootprintSummary:
    """Roll up emission lines into per-scope totals, total tonne-km, and carbon cost."""
    by_scope = {s: 0.0 for s in _SCOPES}
    total = 0.0
    total_tkm = 0.0
    for line in lines:
        by_scope[line.scope] += line.emissions_kg
        total += line.emissions_kg
        if line.kind == "transport":
            total_tkm += line.activity_amount
    return FootprintSummary(
        total_emissions_kg=total, by_scope=by_scope, total_tonne_km=total_tkm,
        carbon_cost=carbon_cost(total, carbon_price_per_tonne),
    )
```
4. Run `PYTHONPATH=. py -m pytest tests/test_carbon_footprint.py -q` — confirm all pass. Run `ruff check src tests`.
5. **Commit**: `feat: carbon-footprint GHG engine (tonne-km factors, Scope 1/2/3, carbon cost)`.

---

## Task 2 — Carbon-cost leg in the landed-cost engine (`src/landed_cost.py`)

**Files**
- Modify `src/landed_cost.py`
- Modify `tests/test_landed_cost.py`

**Interfaces** (verified current signatures — must stay backward-compatible)
- Existing: `landed_cost(unit_cost: float, qty: float, *, freight=0.0, insurance=0.0, duty_rate=0.0, handling=0.0, broker_fee=0.0, incoterm="FOB") -> LandedCost`.
- Existing `LandedCost` frozen fields (in order): `incoterm, qty, goods_value, freight, insurance, handling, duty_base, duty, broker_fee, total, per_unit`.
- Verified callers all use keyword args and `LandedCost(...)` is constructed only inside `landed_cost()` (grep-confirmed: `examples/run_scm_olist.py:123`, `examples/run_scm_procurement.py:101`, `examples/run_scm_superstore.py:105` call `landed_cost(...)` by keyword; no other `LandedCost(...)` constructor exists). Adding a trailing defaulted field + a keyword-only param is safe.
- Produces: `LandedCost` gains a `carbon_cost: float = 0.0` field; `landed_cost()` gains `carbon_cost: float = 0.0`, added to `total`, EXCLUDED from `duty_base`.

**Steps**

1. Add failing tests to `tests/test_landed_cost.py` (append):
```python
def test_carbon_cost_adds_to_total_but_not_duty_base():
    base = landed_cost(unit_cost=10.0, qty=100, freight=200, insurance=50,
                       duty_rate=0.05, incoterm="CIF")
    withc = landed_cost(unit_cost=10.0, qty=100, freight=200, insurance=50,
                        duty_rate=0.05, incoterm="CIF", carbon_cost=40.0)
    assert withc.duty == pytest.approx(base.duty)           # carbon is NOT in the duty base
    assert withc.duty_base == pytest.approx(base.duty_base)
    assert withc.carbon_cost == pytest.approx(40.0)
    assert withc.total == pytest.approx(base.total + 40.0)  # one added term
    assert withc.per_unit == pytest.approx(base.per_unit + 0.4)


def test_carbon_cost_defaults_to_zero_and_stays_backward_compatible():
    lc = landed_cost(unit_cost=10.0, qty=10)
    assert lc.carbon_cost == 0.0
    assert lc.total == pytest.approx(100.0)   # unchanged from pre-carbon behaviour
```
2. Run `PYTHONPATH=. py -m pytest tests/test_landed_cost.py -q` — confirm the two new tests FAIL (unexpected `carbon_cost` kwarg / missing attribute).
3. Edit `src/landed_cost.py` — add the field to `LandedCost` (trailing, defaulted):
```python
@dataclass(frozen=True)
class LandedCost:
    incoterm: str
    qty: float
    goods_value: float
    freight: float
    insurance: float
    handling: float
    duty_base: float
    duty: float
    broker_fee: float
    total: float
    per_unit: float
    carbon_cost: float = 0.0   # optional internal carbon cost (emissions * price); NOT dutiable
```
4. Edit `landed_cost()` — add the keyword param, the total term, and the constructor field (carbon cost is intentionally kept out of `duty_base`):
```python
def landed_cost(
    unit_cost: float,
    qty: float,
    *,
    freight: float = 0.0,
    insurance: float = 0.0,
    duty_rate: float = 0.0,
    handling: float = 0.0,
    broker_fee: float = 0.0,
    incoterm: str = "FOB",
    carbon_cost: float = 0.0,
) -> LandedCost:
    """Compute the fully-landed cost and per-unit landed cost.

    ``carbon_cost`` (an internal carbon charge, e.g. from src.carbon_footprint) is
    added to the total but never to the dutiable base - customs value does not
    include a voluntary internal carbon price.
    """
    goods_value = unit_cost * qty
    term = incoterm.upper()
    if term in _GOODS_ONLY_DUTY_BASE:
        duty_base = goods_value
    else:  # CIF/CIP/DAP/DDP... - freight & insurance are in the dutiable value
        duty_base = goods_value + freight + insurance
    duty = duty_base * duty_rate
    total = goods_value + freight + insurance + handling + duty + broker_fee + carbon_cost
    per_unit = (total / qty) if qty > 0 else 0.0
    return LandedCost(
        incoterm=term, qty=qty, goods_value=goods_value, freight=freight,
        insurance=insurance, handling=handling, duty_base=duty_base, duty=duty,
        broker_fee=broker_fee, total=total, per_unit=per_unit, carbon_cost=carbon_cost,
    )
```
5. Run `PYTHONPATH=. py -m pytest tests/test_landed_cost.py tests/test_landed_cost_tool.py -q` — all pass (existing cases unchanged; new pass). Run the full landed-cost-adjacent suite to confirm no regression: `PYTHONPATH=. py -m pytest tests/test_landed_cost_tool.py -q`.
6. **Commit**: `feat: optional carbon-cost leg in landed cost (added to total, excluded from duty base)`.

---

## Task 3 — Pandas job (`jobs/carbon_footprint_job.py`)

**Files**
- Create `jobs/carbon_footprint_job.py`
- Create `tests/test_carbon_footprint_job.py`

**Interfaces**
- Consumes: `pandas`; `src.carbon_footprint` (Task 1); `src.deliverable.{DataSource, Deliverable, Finding, Kpi}` (verified frozen dataclasses: `Finding(title, detail, impact="")`, `Kpi(name, value, target="", rationale="")`, `DataSource(field, source, cadence="")`, `Deliverable(title, client, summary, findings=(), kpis=(), data_sources=(), recommendations=(), options=(), citations=(), confidence=None, residual="", prepared="")` with `.to_markdown()`, `.write_all(out_dir) -> {"report":..., "workbook":...}`); `src.export.write_summary_csv(rows: list[dict], path) -> Path`.
- Produces:
  - `prepare(data_path: str, params: dict | None = None) -> dict` (keys `"transport"`, `"warehouse"`)
  - `prepare_records(df: pd.DataFrame, params: dict | None = None) -> dict`
  - `run(payload: dict, *, factors: EmissionFactors | None = None, carbon_price_per_tonne: float = 0.0) -> CarbonReport`
  - `verify(report: CarbonReport) -> list[str]`
  - `write_operational(report, out_dir, client="Client") -> dict[str, Path]`
  - `build_deck(report, *, client="Client", prepared="", citations=(), confidence=0.8) -> Deliverable`

**Steps**

1. Write `tests/test_carbon_footprint_job.py` (fails: module absent):
```python
"""Tests for the carbon-footprint agent job: a mixed activity CSV -> Scope 1/2/3 GHG report."""

from pathlib import Path

import pandas as pd
import pytest

from jobs import carbon_footprint_job as cfj
from src.carbon_footprint import EmissionFactors
from src.deliverable import Deliverable


def _activity_df() -> pd.DataFrame:
    return pd.DataFrame({
        "activity_id": ["lane-ftl", "lane-air", "dc-main"],
        "mode": ["ftl", "air", ""],
        "weight_kg": [10_000.0, 100.0, 0.0],
        "distance_km": [500.0, 1000.0, 0.0],
        "electricity_kwh": [0.0, 0.0, 1000.0],
    })


def test_prepare_splits_transport_and_warehouse_rows(tmp_path):
    csv = tmp_path / "activity.csv"
    _activity_df().to_csv(csv, index=False)

    payload = cfj.prepare(str(csv), {})

    assert {t.activity_id for t in payload["transport"]} == {"lane-ftl", "lane-air"}
    assert [w.activity_id for w in payload["warehouse"]] == ["dc-main"]


def test_prepare_errors_without_mode_or_energy_columns(tmp_path):
    csv = tmp_path / "bad.csv"
    pd.DataFrame({"foo": [1]}).to_csv(csv, index=False)
    with pytest.raises(ValueError, match="mode|energy"):
        cfj.prepare(str(csv), {})


def test_prepare_rejects_unknown_mode(tmp_path):
    csv = tmp_path / "u.csv"
    pd.DataFrame({"activity_id": ["x"], "mode": ["rocket"],
                  "weight_kg": [1.0], "distance_km": [1.0]}).to_csv(csv, index=False)
    with pytest.raises(ValueError, match="unknown transport mode"):
        cfj.prepare(str(csv), {})


def test_run_totals_and_scopes_are_hand_checkable():
    report = cfj.run(cfj.prepare_records(_activity_df()), carbon_price_per_tonne=50.0)
    s = report.summary
    # ftl 310 (S3) + air 60.2 (S3) + electricity 400 (S2) = 770.2 kg.
    assert s.total_emissions_kg == pytest.approx(770.2)
    assert s.by_scope["scope_3"] == pytest.approx(370.2)
    assert s.by_scope["scope_2"] == pytest.approx(400.0)
    assert s.carbon_cost == pytest.approx(38.51)
    assert report.top_emitter.activity_id == "dc-main"    # 400 kg is the single biggest line
    assert cfj.verify(report) == []


def test_factor_override_changes_the_total():
    payload = cfj.prepare_records(_activity_df())
    greener = EmissionFactors(air_kg_co2e_per_tkm=0.30)   # halve air intensity
    base = cfj.run(payload)
    low = cfj.run(payload, factors=greener)
    assert low.summary.total_emissions_kg < base.summary.total_emissions_kg


def test_write_operational_and_deck(tmp_path):
    report = cfj.run(cfj.prepare_records(_activity_df()), carbon_price_per_tonne=50.0)
    out = cfj.write_operational(report, tmp_path, "Acme")
    assert Path(out["csv"]).exists()

    deck = cfj.build_deck(report, client="Acme", citations=("Grant - Sustainable Logistics",))
    assert isinstance(deck, Deliverable)
    md = deck.to_markdown()
    assert md.isascii()                          # cp1252 safety
    assert "Carbon" in md and "## Coverage & handoff" in md
```
2. Run — confirm FAIL.
3. Create `jobs/carbon_footprint_job.py`:
```python
"""Carbon-footprint agent job (Kern tool #42): an activity CSV -> Scope 1/2/3 GHG report.

The data-prep + deck half of the carbon-footprint tool. Reads mixed activity lines
(transport lanes: mode + weight_kg + distance_km [+ own_fleet]; warehouse sites:
electricity_kwh [+ fuel_kwh]) with pandas directly (deliberately NOT via jobs/intake.py,
which the parallel loop owns), computes each line's GHG emissions via src.carbon_footprint,
buckets Scope 1/2/3, and composes the study deck inline. Column names are sniffed and
overridable via params.

Emission factors are ILLUSTRATIVE public defaults (src.carbon_footprint.EmissionFactors),
NOT the licensed GLEC figures - the deck's residual states they are advisory. Override
per lane via params['emission_factors'].
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from src.carbon_footprint import (
    EmissionFactors,
    EmissionLine,
    FootprintSummary,
    TransportActivity,
    WarehouseActivity,
    summarize,
    transport_emissions,
    warehouse_emissions,
)
from src.deliverable import DataSource, Deliverable, Finding, Kpi
from src.export import write_summary_csv

_ACTIVITY_COLS = ("activity_id", "id", "shipment_id", "lane", "activity")
_MODE_COLS = ("mode", "transport_mode", "Mode")
_WEIGHT_COLS = ("weight_kg", "weight", "kg", "Weight (kg)")
_DIST_COLS = ("distance_km", "distance", "km", "Distance (km)")
_OWNFLEET_COLS = ("own_fleet", "owned", "own")
_ELEC_COLS = ("electricity_kwh", "kwh", "electricity", "power_kwh")
_FUEL_COLS = ("fuel_kwh", "fuel", "gas_kwh")

_TRUTHY = {"1", "true", "yes", "y", "t", "own", "owned"}


@dataclass(frozen=True)
class CarbonReport:
    lines: tuple[EmissionLine, ...]      # sorted by emissions desc
    n_activities: int
    summary: FootprintSummary
    top_emitter: EmissionLine | None
    carbon_price_per_tonne: float
    narrative: str


def _pick(df: pd.DataFrame, override: object, candidates: tuple[str, ...]) -> str | None:
    if override:
        return str(override) if str(override) in df.columns else None
    return next((c for c in candidates if c in df.columns), None)


def _as_bool(value: object) -> bool:
    return str(value).strip().lower() in _TRUTHY


def prepare_records(df: pd.DataFrame, params: dict | None = None) -> dict:
    """Sniff the columns and split rows into transport vs warehouse activities.

    A row is a transport lane when it carries a non-empty mode; otherwise it is a
    warehouse site (electricity/fuel). Raises ValueError on unusable input so the
    tool surfaces needs_data rather than crashing in run().
    """
    params = params or {}
    aid = _pick(df, params.get("activity_col"), _ACTIVITY_COLS)
    mode = _pick(df, params.get("mode_col"), _MODE_COLS)
    weight = _pick(df, params.get("weight_col"), _WEIGHT_COLS)
    dist = _pick(df, params.get("distance_col"), _DIST_COLS)
    elec = _pick(df, params.get("electricity_col"), _ELEC_COLS)
    fuel = _pick(df, params.get("fuel_col"), _FUEL_COLS)
    own = _pick(df, params.get("own_fleet_col"), _OWNFLEET_COLS)

    if mode is None and elec is None and fuel is None:
        cols = list(df.columns)[:10]
        raise ValueError(
            f"need a transport mode column or a warehouse energy column; "
            f"pass them in params (columns seen: {cols})")

    transport: list[TransportActivity] = []
    warehouse: list[WarehouseActivity] = []
    for i, row in df.iterrows():
        rid = str(row[aid]) if aid else f"activity-{i}"
        has_mode = mode is not None and pd.notna(row[mode]) and str(row[mode]).strip() != ""
        if has_mode:
            if weight is None or dist is None:
                raise ValueError("transport rows need weight_kg and distance_km columns")
            transport.append(TransportActivity(
                activity_id=rid, mode=str(row[mode]),
                weight_kg=float(row[weight]), distance_km=float(row[dist]),
                own_fleet=_as_bool(row[own]) if own and pd.notna(row[own]) else False))
        else:
            e = float(row[elec]) if elec and pd.notna(row[elec]) else 0.0
            f = float(row[fuel]) if fuel and pd.notna(row[fuel]) else 0.0
            if e or f:
                warehouse.append(WarehouseActivity(activity_id=rid, electricity_kwh=e, fuel_kwh=f))

    # Validate modes up front so an unknown mode is a data problem, not a run crash.
    factors = EmissionFactors()
    for t in transport:
        factors.transport_factor(t.mode)   # raises ValueError on an unknown mode
    return {"transport": transport, "warehouse": warehouse}


def prepare(data_path: str, params: dict | None = None) -> dict:
    """Read an activity CSV and build the carbon-footprint input payload."""
    return prepare_records(pd.read_csv(data_path), params)


def run(
    payload: dict,
    *,
    factors: EmissionFactors | None = None,
    carbon_price_per_tonne: float = 0.0,
) -> CarbonReport:
    """Compute per-activity emissions, roll up Scope 1/2/3, and the optional carbon cost."""
    factors = factors or EmissionFactors()
    lines: list[EmissionLine] = []
    for t in payload["transport"]:
        lines.append(transport_emissions(t, factors))
    for w in payload["warehouse"]:
        lines.extend(warehouse_emissions(w, factors))
    lines.sort(key=lambda ln: ln.emissions_kg, reverse=True)

    summary = summarize(lines, carbon_price_per_tonne=carbon_price_per_tonne)
    top = lines[0] if lines else None
    narrative = (
        f"Total {summary.total_emissions_kg / 1000:,.2f} t CO2e across {len(lines)} "
        f"activity line(s): Scope 1 {summary.by_scope['scope_1'] / 1000:,.2f} t, "
        f"Scope 2 {summary.by_scope['scope_2'] / 1000:,.2f} t, "
        f"Scope 3 {summary.by_scope['scope_3'] / 1000:,.2f} t."
    )
    return CarbonReport(
        lines=tuple(lines), n_activities=len(lines), summary=summary,
        top_emitter=top, carbon_price_per_tonne=carbon_price_per_tonne, narrative=narrative,
    )


def verify(report: CarbonReport) -> list[str]:
    """QA gate: lines present, emissions finite and non-negative, scopes sum to total."""
    issues: list[str] = []
    if not report.lines:
        issues.append("no activities to compute emissions for")
    s = report.summary
    if not math.isfinite(s.total_emissions_kg):
        issues.append("non-finite total emissions")
    for ln in report.lines:
        if not math.isfinite(ln.emissions_kg):
            issues.append(f"non-finite emissions for {ln.activity_id}")
        if ln.emissions_kg < -1e-9:
            issues.append(f"negative emissions for {ln.activity_id}")
    if abs(sum(s.by_scope.values()) - s.total_emissions_kg) > 1e-6:
        issues.append("scope buckets do not sum to total emissions")
    return issues


def write_operational(report: CarbonReport, out_dir: str | Path, client: str = "Client") -> dict[str, Path]:
    """The machine-readable deliverable: one row per activity emission line."""
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    rows = [
        {
            "activity_id": ln.activity_id,
            "kind": ln.kind,
            "detail": ln.detail,
            "scope": ln.scope,
            "activity_amount": round(ln.activity_amount, 3),
            "emissions_kg_co2e": round(ln.emissions_kg, 3),
            "emissions_t_co2e": round(ln.emissions_kg / 1000.0, 5),
        }
        for ln in report.lines
    ]
    return {"csv": write_summary_csv(rows, d / "carbon_footprint.csv")}


def build_deck(
    report: CarbonReport,
    *,
    client: str = "Client",
    prepared: str = "",
    citations: tuple[str, ...] = (),
    confidence: float = 0.8,
) -> Deliverable:
    """Compose the carbon-footprint study: total CO2e, where it accrues, and the levers."""
    s = report.summary
    total_t = s.total_emissions_kg / 1000.0
    s1, s2, s3 = (s.by_scope["scope_1"] / 1000.0, s.by_scope["scope_2"] / 1000.0,
                  s.by_scope["scope_3"] / 1000.0)
    summary = (
        f"Carbon footprint across {report.n_activities} activity line(s): {total_t:,.2f} t CO2e "
        f"(Scope 1 {s1:,.2f} t, Scope 2 {s2:,.2f} t, Scope 3 {s3:,.2f} t)."
    )

    findings = [
        Finding(
            "Most emissions sit in the value chain (Scope 3)",
            f"Third-party freight accounts for {s3:,.2f} t CO2e - the largest, least-direct lever.",
            impact="target mode shift and carrier selection, not just own operations",
        ),
        Finding(
            "Scope split",
            f"Scope 1 {s1:,.2f} t (own combustion), Scope 2 {s2:,.2f} t (purchased electricity), "
            f"Scope 3 {s3:,.2f} t (carrier freight).",
            impact="report against the GHG-Protocol scopes",
        ),
    ]
    top = report.top_emitter
    if top is not None:
        findings.append(Finding(
            f"Largest single source: {top.activity_id}",
            f"{top.detail} ({top.kind}) emits {top.emissions_kg / 1000:,.2f} t CO2e.",
            impact="biggest single reduction opportunity",
        ))
    if report.carbon_price_per_tonne:
        findings.append(Finding(
            "Internal carbon cost",
            f"At {report.carbon_price_per_tonne:,.0f}/t the footprint carries a "
            f"{s.carbon_cost:,.0f} carbon cost.",
            impact="fold into landed cost so sourcing decisions see the true delivered cost",
        ))

    kpis = (
        Kpi("Total emissions", f"{total_t:,.2f} t CO2e", target="minimize",
            rationale="Total supply-chain greenhouse-gas footprint"),
        Kpi("Scope 1", f"{s1:,.2f} t", rationale="Direct combustion (own fleet + on-site fuel)"),
        Kpi("Scope 2", f"{s2:,.2f} t", rationale="Purchased warehouse electricity"),
        Kpi("Scope 3", f"{s3:,.2f} t", rationale="Third-party (carrier) freight"),
        Kpi("Freight activity", f"{s.total_tonne_km:,.0f} tonne-km",
            rationale="Physical basis for transport emissions"),
    )

    data_sources = (
        DataSource("Transport lanes (mode / weight / distance)", "TMS / freight invoices", "per shipment"),
        DataSource("Warehouse energy (kWh)", "utility bills / BMS", "monthly"),
        DataSource("Emission factors (kg CO2e / tonne-km, grid kg CO2e / kWh)",
                   "public GLEC/DEFRA-range defaults (advisory)", "per run"),
    )

    recommendations = (
        "Shift the highest-emitting lanes to lower-carbon modes (air->sea/rail, LTL->intermodal).",
        "Cut Scope 2 by moving warehouse electricity to a renewable tariff and raising efficiency.",
        "Put an internal price on carbon and fold it into landed cost so decisions see true cost.",
    )

    return Deliverable(
        title="Carbon Footprint (GHG Emissions)",
        client=client,
        summary=summary,
        findings=tuple(findings),
        kpis=kpis,
        data_sources=data_sources,
        recommendations=recommendations,
        citations=tuple(citations),
        confidence=confidence,
        residual="Emission factors are advisory public defaults (GLEC/DEFRA order of magnitude), "
                 "NOT the licensed GLEC Framework figures. Replace EmissionFactors with audited "
                 "per-lane/per-grid factors before reporting absolute totals externally; the "
                 "relative mode ranking is robust.",
        prepared=prepared,
    )
```
4. Run `PYTHONPATH=. py -m pytest tests/test_carbon_footprint_job.py -q` — all pass. `ruff check jobs tests`.
5. **Commit**: `feat: carbon-footprint job (activity CSV -> Scope 1/2/3 report + deck)`.

---

## Task 4 — Wire the tool, options, citations, and orchestrator test

**Files**
- Modify `scm_agent/tool_options.py` (add `carbon_footprint_options`)
- Modify `scm_agent/citation_gate.py` (add `TOOL_CONCEPTS["carbon_footprint"]`)
- Modify `scm_agent/tools.py` (import, prepare/run, `carbon_footprint_tool()`, register)
- Create `tests/test_carbon_footprint_tool.py`

**Interfaces** (verified)
- `Tool(key, title, description, intent_keywords, requires_data, prepare, run, qa, deliver, deck=None, options=None, required_client_params=())`.
- `Prepared(status, payload=None, messages=[])`, `Produced(report, summary)`.
- `tool_options._ranked(summary: str, items: list[_Item], *, confidence=0.85) -> GuidedOutcome` where `_Item = (label, summary, action, tradeoffs)`, first item = recommended.
- Orchestrator path (verified in `tests/test_landed_cost_tool.py`): `Orchestrator(registry=..., provider=llm.RulesFallback())`, `.run(brief, data_path=, client=, out_dir=)` returns a result with `.status`, `.tool`, `.deliverables` (deck files keyed `deck_report` / `deck_workbook`; operational file keyed `csv`).
- Verified L3 anchor node ids (bare, namespace stripped by `KnowledgeBase`): `greenhouse_gas_emissions`, `transport_emissions`, `warehouse_energy_efficiency`, `green_logistics` — all present in `knowledge/scm-books/graph.json` under `source_file` `grant-sustainable-logistics-supply-chain.txt` (checked directly; full ids `knowledge::greenhouse_gas_emissions`, `knowledge::transport_emissions`, `knowledge::warehouse_energy_efficiency`, `knowledge::green_logistics`).

**Steps**

1. Write `tests/test_carbon_footprint_tool.py` (fails: tool not registered):
```python
"""Tests for the carbon-footprint agent job + tool (Kern tool #42): routing + orchestrator e2e."""

from pathlib import Path

import pandas as pd

from scm_agent import citation_gate, intent, llm, tools
from scm_agent.orchestrator import Orchestrator
from src.guided import OPTIONS, verify_guided


def _activity_df() -> pd.DataFrame:
    return pd.DataFrame({
        "activity_id": ["lane-ftl", "lane-air", "dc-main"],
        "mode": ["ftl", "air", ""],
        "weight_kg": [10_000.0, 100.0, 0.0],
        "distance_km": [500.0, 1000.0, 0.0],
        "electricity_kwh": [0.0, 0.0, 1000.0],
    })


def test_brief_routes_to_carbon_footprint():
    reg = tools.build_default_registry()
    res = intent.classify("compute our carbon footprint and CO2e emissions by transport mode",
                          reg, llm.RulesFallback())
    assert res.job_type == "carbon_footprint"


def test_carbon_keywords_do_not_steal_other_briefs():
    reg = tools.build_default_registry()
    p = llm.RulesFallback()
    assert intent.classify("pick the cheapest way to ship this LTL vs FTL", reg, p).job_type == "transportation"
    assert intent.classify("compute the landed cost with duty and freight", reg, p).job_type == "landed_cost"
    assert intent.classify("set up reorder points and safety stock", reg, p).job_type == "inventory_optimization"


def test_options_builder_returns_a_protected_outcome():
    from jobs import carbon_footprint_job as cfj
    report = cfj.run(cfj.prepare_records(_activity_df()), carbon_price_per_tonne=50.0)
    outcome = tools.tool_options.carbon_footprint_options(report)
    assert outcome.status == OPTIONS
    assert verify_guided(outcome) == []
    assert len(outcome.options) >= 2
    assert any(o.recommended for o in outcome.options)


def test_carbon_footprint_anchors_are_mapped():
    assert "carbon_footprint" in citation_gate.TOOL_CONCEPTS
    assert len(citation_gate.TOOL_CONCEPTS["carbon_footprint"]) <= 8


def test_orchestrator_runs_carbon_footprint_and_emits_the_deck(tmp_path):
    csv = tmp_path / "activity.csv"
    _activity_df().to_csv(csv, index=False)
    orch = Orchestrator(registry=tools.build_default_registry(), provider=llm.RulesFallback())

    res = orch.run("compute our supply chain carbon footprint and CO2e emissions",
                   data_path=str(csv), client="Acme", out_dir=tmp_path)

    assert res.status == "ok"
    assert res.tool == "carbon_footprint"
    assert "csv" in res.deliverables
    deck = Path(res.deliverables["deck_report"])
    assert deck.exists()
    assert deck.read_text(encoding="utf-8").isascii()
```
2. Run — confirm FAIL (routing/registration).
3. Add `carbon_footprint_options` to `scm_agent/tool_options.py` (after `landed_cost_options`, reusing the module's `_ranked`/`_Item`):
```python
def carbon_footprint_options(report: object) -> GuidedOutcome:
    top = report.top_emitter
    s = report.summary
    mode_shift: _Item = (
        "Shift the highest-emitting lane to a lower-carbon mode",
        (f"{top.activity_id} ({top.detail}) emits {top.emissions_kg / 1000:,.2f} t CO2e - "
         "air->sea/rail or LTL->intermodal cuts intensity most."
         if top is not None and top.kind == "transport"
         else "Move the biggest freight lane to rail / sea / intermodal."),
        "re-route the top-emitting lane to a lower-carbon mode",
        "biggest single CO2e lever; may add transit time",
    )
    carbon_price: _Item = (
        "Put an internal price on carbon and fold it into landed cost",
        (f"At the current price the carbon cost is {s.carbon_cost:,.0f}; pricing emissions "
         "surfaces the true delivered cost."
         if report.carbon_price_per_tonne
         else "Set a shadow carbon price so emissions show up in landed-cost comparisons."),
        "apply a carbon price via landed_cost(carbon_cost=...)",
        "aligns cost decisions with decarbonization",
    )
    warehouse: _Item = (
        "Cut warehouse energy emissions (Scope 2)",
        f"Scope 2 (purchased electricity) is {s.by_scope.get('scope_2', 0.0) / 1000:,.2f} t CO2e - "
        "a renewable tariff and efficiency reduce it.",
        "switch to renewable electricity / raise warehouse efficiency",
        "structural Scope 2 reduction",
    )
    if top is not None and top.kind == "transport":
        items = [mode_shift, carbon_price, warehouse]
    else:
        items = [warehouse, carbon_price, mode_shift]
    return _ranked(
        f"Carbon footprint {s.total_emissions_kg / 1000:,.2f} t CO2e: choose the reduction lever.",
        items,
    )
```
4. Add the anchor map to `scm_agent/citation_gate.py::TOOL_CONCEPTS` (a new entry; keep alphabetical-ish grouping is not required, append near the other logistics tools):
```python
    "carbon_footprint": (
        "greenhouse_gas_emissions", "transport_emissions",
        "warehouse_energy_efficiency", "green_logistics",
    ),
```
5. Edit `scm_agent/tools.py`:
   - Add `carbon_footprint_job` to the `from jobs import (...)` block (keep it alphabetized: after `acceptance_sampling_job`, before `cost_to_serve_deliverable`).
   - Add the engine import near the other `src` local-skip imports at the top:
```python
from src.carbon_footprint import EmissionFactors  # isort: skip  (local package)
```
   - Add the prepare/run/factory (place after the `landed_cost` section, mirroring its shape):
```python
# ---- carbon_footprint (GHG emissions: Scope 1/2/3 + carbon cost) --------------

def _carbon_footprint_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    if not request.data_path:
        return Prepared(status="needs_data", messages=[
            "an activity CSV (activity_id, mode, weight_kg, distance_km; and/or warehouse "
            "electricity_kwh) is required"])
    try:
        payload = carbon_footprint_job.prepare(request.data_path, request.params)
    except (ValueError, FileNotFoundError) as exc:
        return Prepared(status="needs_data", messages=[str(exc)])
    if not payload["transport"] and not payload["warehouse"]:
        return Prepared(status="needs_data", messages=["no activity lines found in the data"])
    return Prepared(status="ok", payload=payload)


def _carbon_footprint_run(payload: object, params: dict) -> Produced:
    factors = (EmissionFactors(**params["emission_factors"])
               if params.get("emission_factors") else EmissionFactors())
    report = carbon_footprint_job.run(
        payload, factors=factors,
        carbon_price_per_tonne=params.get("carbon_price_per_tonne", 0.0),
    )
    return Produced(report=report, summary=report.narrative)


def carbon_footprint_tool() -> Tool:
    return Tool(
        key="carbon_footprint",
        title="Carbon Footprint (GHG Emissions)",
        description="Compute supply-chain greenhouse-gas emissions (tonne-km x mode emission "
                    "factor + warehouse energy) bucketed into GHG-Protocol Scope 1/2/3, with an "
                    "optional internal carbon cost that folds into landed cost.",
        intent_keywords=(
            "carbon footprint", "carbon emissions", "greenhouse gas", "ghg emissions",
            "co2 emissions", "co2e", "scope 3 emissions", "sustainability report",
            "emissions report", "decarbonization",
        ),
        requires_data=True,
        options=tool_options.carbon_footprint_options,
        prepare=_carbon_footprint_prepare,
        run=_carbon_footprint_run,
        qa=lambda report: carbon_footprint_job.verify(report),
        deliver=lambda report, out_dir, client: carbon_footprint_job.write_operational(report, out_dir, client),
        deck=lambda report, out_dir, client, citations, confidence, options: replace(
            carbon_footprint_job.build_deck(report, client=client, citations=tuple(citations), confidence=confidence),
            options=tuple(options),
        ).write_all(out_dir),
    )
```
   - Register in `build_default_registry()` (append after `launch_readiness_tool()`):
```python
    reg.register(carbon_footprint_tool())
```
6. Run the new + adjacent suites:
```
PYTHONPATH=. py -m pytest tests/test_carbon_footprint_tool.py tests/test_citation_gate.py -q
```
   `test_citation_gate.py::test_every_anchor_concept_exists` must stay green (it validates the 4 new anchor ids against the committed graph). Fix any routing collision surfaced by `test_carbon_keywords_do_not_steal_other_briefs` by trimming a keyword (do NOT add a bare `"carbon"` or `"transport"` keyword — both are collision-prone).
7. Run the FULL suite + lint to confirm no regressions anywhere (registration touches the shared registry):
```
PYTHONPATH=. py -m pytest -q
ruff check src tests examples
```
8. **Commit**: `feat: register carbon_footprint tool (options + L3 citations + orchestrator e2e)`.

---

## Final integration
- Open the draft PR `feat/carbon-footprint` -> `main`; confirm CI green on py3.11/3.12/3.13 (`tests` + `prod-boot`). `prod-boot` is the one to watch: `src/carbon_footprint.py` is pure stdlib and `EmissionFactors` is imported at module top in `tools.py`, so the boot chain stays import-clean (no optional-extra dep added).
- Squash-merge. Tool count goes 41 -> 42.
- Update `CLAUDE.md` "What Kern is" tool count (41 -> 42) and the tool list in the SAME PR (per the CLAUDE.md staleness rule).

## Open questions / decisions flagged during drafting

- Emission-factor provenance: plan ships Option (a) - documented public GLEC/DEFRA-range DEFAULT factors in EmissionFactors with provenance in code comments, factors overridable via params['emission_factors'], and the licensed GLEC Framework numbers deferred to a separate follow-up that needs the LLM-backed books-graph rebuild (Kimi/Gemini key). Confirm (a) over (b) 'make factors a required input'. Recommended: (a).
- Scope-1 vs Scope-3 default for transport: plan treats carrier freight as Scope 3 by default and only pushes a lane to Scope 1 when an explicit own_fleet flag is set. Confirm this default (some clients classify contracted dedicated fleet differently).
- Carbon-cost -> landed-cost coupling: this tool computes carbon_cost and the landed_cost engine now ACCEPTS it, but the plan does NOT auto-mutate landed cost inside the carbon tool (it surfaces 'fold into landed cost' as a guided OPTION + deck recommendation). Confirm we keep the injection as an operator-driven handoff rather than an automatic cross-tool write in v1.
- GLEC follow-up ticket: after merge, a separate task should add GLEC Framework anchors/factors to L3 via the books-graph rebuild and swap EmissionFactors defaults to audited values. Confirm this is tracked as its own PR (spawn_task candidate), not blocking this one.
- The e2e/deck deliverable keys ('deck_report'/'deck_workbook', operational 'csv') are inferred from the working tests/test_landed_cost_tool.py rather than read from orchestrator.py directly; if the orchestrator remaps deck keys differently the e2e assertion in Task 4 step 1 may need the actual key (verify against orchestrator.py when implementing).
