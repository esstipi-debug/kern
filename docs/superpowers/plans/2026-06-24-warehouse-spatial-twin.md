# Warehouse Spatial Twin (3D) — Foundation (capa 1a) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a parametric, navigable 3D warehouse (building shell, yard, gates, docks, racks, slots) to Linchpin, surfaced as the `warehouse_layout` agent capability and a webapp page, with the data model carrying hooks for later slotting/simulation.

**Architecture:** A pure-Python core package `warehouse/` (model + generator + qa + html_export, no 3D deps) is consumed by two surfaces — the `scm_agent` capability (`prepare -> run -> qa -> deliver`) and FastAPI webapp routes — exactly as the engine feeds CLI + agent + webapp today. The 3D is a thin Three.js (vanilla, CDN/importmap, no build step) rendering of the serialized `Layout`; both surfaces share the same renderer via `html_export.to_html`.

**Tech Stack:** Python 3.11 (frozen dataclasses, stdlib `json`), FastAPI (existing `web` extra), Three.js 0.160 via CDN importmap. No new runtime dependencies.

Spec: `docs/superpowers/specs/2026-06-24-warehouse-3d-foundation-design.md`.

## Global Constraints

- Python `>=3.11`; `frozen` dataclasses; type annotations on every function signature.
- **No new runtime dependencies** for capa 1a (Three.js via CDN; stdlib `json` only; core has no 3D deps). FastAPI comes from the existing `web` extra.
- `warehouse/` is a **top-level package** (sibling of `jobs/`, `scm_agent/`, `src/`), importable in tests via `pyproject` `pythonpath = "."`.
- Capability pattern: a `*_tool()` factory returning `Tool`, registered with one `reg.register(...)` in `scm_agent/tools.py::build_default_registry()`.
- QA contract: `qa(report) -> list[str]`; empty list = pass; any issue = orchestrator emits `qa_failed` and writes **no deliverable**.
- Tests: `.venv/Scripts/python.exe -m pytest` (the `.venv` is uv-managed, no `pip`). Lint: `ruff check` with `select = ["E","F","I"]`, line-length 120, `E501` ignored; **do not** run `ruff format`.
- **ASCII-only in console prints** (Windows cp1252 — em dashes break it). Markdown/docstrings may use Unicode.
- Work on branch `feat/warehouse-spatial-twin` (already created). Commit **only your files**. For final branch-isolated verification, stash the 4 not-mine files (`jobs/intake.py`, `src/batch.py`, `tests/test_batch.py`, `tests/test_jobs.py`), confirm green, pop, then open the PR (`gh pr merge --squash --delete-branch`).

## File Structure

- Create: `warehouse/__init__.py` — package marker / public re-exports.
- Create: `warehouse/model.py` — frozen dataclasses + JSON (`to_dict`/`from_dict`).
- Create: `warehouse/generator.py` — `generate_layout(params) -> Layout` (outside-in, deterministic).
- Create: `warehouse/qa.py` — `validate(layout) -> list[str]` geometry invariants.
- Create: `warehouse/html_export.py` — `to_html(layout) -> str` (Three.js renderer + embedded layout).
- Create: `warehouse/blender_export.py` *(optional, Task 8)* — `to_bpy_script(layout) -> str`.
- Create: `jobs/warehouse_job.py` — `run(params) -> (Layout, report_md)`.
- Modify: `scm_agent/tools.py` — add `warehouse_layout_tool()` + register it.
- Modify: `webapp/app.py` — add `GET /api/warehouse` and `GET /warehouse`.
- Create: `tests/test_warehouse.py` — all tests for the above.
- Modify: `README.md`, `CHANGELOG.md` — document the capability (Task 9).

---

### Task 1: Spatial data model (`warehouse/model.py`)

**Files:**
- Create: `warehouse/__init__.py`
- Create: `warehouse/model.py`
- Test: `tests/test_warehouse.py`

**Interfaces:**
- Consumes: nothing.
- Produces: frozen dataclasses `Site`, `Building`, `Yard`, `Gate`, `Dock`, `Aisle`, `Rack`, `Slot`, `TruckPath`, `Layout`. `Layout.to_dict() -> dict` and `Layout.from_dict(d: dict) -> Layout` round-trip (tuples preserved), JSON-serializable.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_warehouse.py`:

```python
import json

from warehouse.model import (
    Aisle, Building, Dock, Gate, Layout, Rack, Site, Slot, TruckPath, Yard,
)


def _sample_layout() -> Layout:
    return Layout(
        site=Site(width_m=200.0, depth_m=150.0),
        building=Building(x=60.0, y=70.0, width_m=80.0, depth_m=80.0, height_m=12.0, levels=4),
        yard=Yard(depth_m=40.0, polygon=((60.0, 30.0), (140.0, 30.0), (140.0, 70.0), (60.0, 70.0))),
        gates=(Gate(id="G1", x=100.0, y=0.0, width_m=6.0),),
        docks=(Dock(id="D1", x=80.0, y=70.0, face="south"),),
        aisles=(Aisle(id="A1", x=70.0, y=72.0, length_m=76.0, width_m=3.5, orientation="y"),),
        racks=(Rack(id="R1", x=66.0, y=72.0, width_m=1.2, depth_m=76.0, orientation="y", bays=20, levels=4),),
        slots=(Slot(rack_id="R1", bay=0, level=0, capacity_units=100.0),),
        truck_paths=(TruckPath(kind="in", points=((100.0, 0.0), (80.0, 70.0))),),
        params={"note": "sample"},
    )


def test_layout_round_trips_through_dict():
    layout = _sample_layout()
    assert Layout.from_dict(layout.to_dict()) == layout


def test_layout_dict_is_json_serializable_and_round_trips():
    layout = _sample_layout()
    restored = Layout.from_dict(json.loads(json.dumps(layout.to_dict())))
    assert restored == layout
    assert restored.yard.polygon == layout.yard.polygon  # tuples preserved, not lists
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_warehouse.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'warehouse'`.

- [ ] **Step 3: Write minimal implementation**

Create `warehouse/__init__.py` (keep it import-light — docstring only — so the package never eagerly imports modules that arrive in later tasks):

```python
"""Warehouse spatial twin (capa 1a): parametric geometry + 3D viewer.

Import from submodules: warehouse.model / .generator / .qa / .html_export.
"""
```

Create `warehouse/model.py`:

```python
"""Spatial data model for the warehouse twin (capa 1a). All units in meters."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Face = Literal["north", "south", "east", "west"]
Orientation = Literal["x", "y"]


@dataclass(frozen=True)
class Site:
    width_m: float
    depth_m: float


@dataclass(frozen=True)
class Building:
    x: float
    y: float
    width_m: float
    depth_m: float
    height_m: float
    levels: int


@dataclass(frozen=True)
class Yard:
    depth_m: float
    polygon: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class Gate:
    id: str
    x: float
    y: float
    width_m: float


@dataclass(frozen=True)
class Dock:
    id: str
    x: float
    y: float
    face: str


@dataclass(frozen=True)
class Aisle:
    id: str
    x: float
    y: float
    length_m: float
    width_m: float
    orientation: str


@dataclass(frozen=True)
class Rack:
    id: str
    x: float
    y: float
    width_m: float
    depth_m: float
    orientation: str
    bays: int
    levels: int


@dataclass(frozen=True)
class Slot:
    rack_id: str
    bay: int
    level: int
    capacity_units: float


@dataclass(frozen=True)
class TruckPath:
    kind: str
    points: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class Layout:
    site: Site
    building: Building
    yard: Yard
    gates: tuple[Gate, ...]
    docks: tuple[Dock, ...]
    aisles: tuple[Aisle, ...]
    racks: tuple[Rack, ...]
    slots: tuple[Slot, ...]
    truck_paths: tuple[TruckPath, ...]
    params: dict

    def to_dict(self) -> dict:
        return {
            "site": dict(vars(self.site)),
            "building": dict(vars(self.building)),
            "yard": {"depth_m": self.yard.depth_m, "polygon": [list(p) for p in self.yard.polygon]},
            "gates": [dict(vars(g)) for g in self.gates],
            "docks": [dict(vars(d)) for d in self.docks],
            "aisles": [dict(vars(a)) for a in self.aisles],
            "racks": [dict(vars(r)) for r in self.racks],
            "slots": [dict(vars(s)) for s in self.slots],
            "truck_paths": [{"kind": t.kind, "points": [list(p) for p in t.points]} for t in self.truck_paths],
            "params": self.params,
        }

    @staticmethod
    def from_dict(d: dict) -> "Layout":
        return Layout(
            site=Site(**d["site"]),
            building=Building(**d["building"]),
            yard=Yard(depth_m=d["yard"]["depth_m"], polygon=tuple(tuple(p) for p in d["yard"]["polygon"])),
            gates=tuple(Gate(**g) for g in d["gates"]),
            docks=tuple(Dock(**dd) for dd in d["docks"]),
            aisles=tuple(Aisle(**a) for a in d["aisles"]),
            racks=tuple(Rack(**r) for r in d["racks"]),
            slots=tuple(Slot(**s) for s in d["slots"]),
            truck_paths=tuple(
                TruckPath(kind=t["kind"], points=tuple(tuple(p) for p in t["points"])) for t in d["truck_paths"]
            ),
            params=d.get("params", {}),
        )
```

Note: keep `warehouse/__init__.py` import-light (docstring only). All code and tests import from submodules (`warehouse.model`, `warehouse.generator`, etc.), so the package never eagerly imports modules that arrive in later tasks. The per-task test command below targets the model tests directly (`warehouse.model`).

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_warehouse.py -v -k round_trip`
Expected: PASS (2 passed).

- [ ] **Step 5: Commit**

```bash
git add warehouse/__init__.py warehouse/model.py tests/test_warehouse.py
git commit -m "feat: warehouse spatial data model with JSON round-trip (capa 1a)"
```

---

### Task 2: Parametric generator (`warehouse/generator.py`)

**Files:**
- Create: `warehouse/generator.py`
- Test: `tests/test_warehouse.py`

**Interfaces:**
- Consumes: all `warehouse.model` dataclasses.
- Produces: `generate_layout(params: dict | None = None) -> Layout` — deterministic (same params -> identical `Layout`), outside-in (site -> building -> yard -> gates -> docks -> aisles -> racks -> slots). Module-level `DEFAULTS: dict`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_warehouse.py`:

```python
from warehouse.generator import generate_layout


def test_generate_is_deterministic():
    a = generate_layout({})
    b = generate_layout({})
    assert a == b


def test_generated_default_layout_is_well_formed():
    layout = generate_layout({})
    assert layout.building.width_m > 0 and layout.building.depth_m > 0
    assert len(layout.racks) == 6  # default modules
    assert len(layout.slots) == 6 * 20 * 4  # modules * bays * levels
    assert len(layout.docks) == 8 and len(layout.gates) == 2
    # racks lie inside the building footprint
    b = layout.building
    for r in layout.racks:
        assert r.x >= b.x and r.y >= b.y
        assert r.x + r.width_m <= b.x + b.width_m
        assert r.y + r.depth_m <= b.y + b.depth_m


def test_params_override_defaults():
    layout = generate_layout({"racks": {"modules": 3}, "building": {"levels": 2}})
    assert len(layout.racks) == 3
    assert layout.building.levels == 2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_warehouse.py -v -k generate or override`
Expected: FAIL with `ModuleNotFoundError: No module named 'warehouse.generator'`.

- [ ] **Step 3: Write minimal implementation**

Create `warehouse/generator.py`:

```python
"""Deterministic, parametric warehouse generator (capa 1a), built outside-in."""

from __future__ import annotations

from .model import Aisle, Building, Dock, Gate, Layout, Rack, Site, Slot, TruckPath, Yard

DEFAULTS: dict = {
    "site": {"width_m": 200.0, "depth_m": 150.0},
    "building": {"width_m": 80.0, "depth_m": 80.0, "height_m": 12.0, "levels": 4},
    "racks": {"modules": 6, "bays_per_rack": 20, "aisle_width_m": 3.5},
    "docks": {"count": 8, "face": "south"},
    "gates": {"count": 2},
    "yard_depth_m": 40.0,
    "slot_capacity_units": 100.0,
    "rack_depth_m": 1.2,
    "margin_m": 2.0,
}


def _merged(params: dict) -> dict:
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    for key, value in (params or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key].update(value)
        else:
            out[key] = value
    return out


def generate_layout(params: dict | None = None) -> Layout:
    p = _merged(params or {})
    site = Site(width_m=float(p["site"]["width_m"]), depth_m=float(p["site"]["depth_m"]))

    b = p["building"]
    bw, bd = float(b["width_m"]), float(b["depth_m"])
    yard_depth = float(p["yard_depth_m"])
    bx = (site.width_m - bw) / 2.0
    by = site.depth_m - bd  # building at the back; yard in front (toward y=0)
    building = Building(x=bx, y=by, width_m=bw, depth_m=bd, height_m=float(b["height_m"]), levels=int(b["levels"]))

    yard = Yard(
        depth_m=yard_depth,
        polygon=((bx, by - yard_depth), (bx + bw, by - yard_depth), (bx + bw, by), (bx, by)),
    )

    gates = _gates(site, int(p["gates"]["count"]))
    docks = _docks(building, p["docks"])
    aisles, racks, slots = _interior(building, p["racks"], float(p["slot_capacity_units"]),
                                     float(p["rack_depth_m"]), float(p["margin_m"]))
    truck_paths = _truck_paths(building, gates, docks)

    return Layout(site=site, building=building, yard=yard, gates=gates, docks=docks,
                  aisles=aisles, racks=racks, slots=slots, truck_paths=truck_paths, params=p)


def _gates(site: Site, count: int) -> tuple[Gate, ...]:
    count = max(1, count)
    return tuple(
        Gate(id=f"G{i + 1}", x=site.width_m * (i + 1) / (count + 1), y=0.0, width_m=6.0)
        for i in range(count)
    )


def _docks(building: Building, dp: dict) -> tuple[Dock, ...]:
    count = max(1, int(dp["count"]))
    face = str(dp.get("face", "south"))
    return tuple(
        Dock(id=f"D{i + 1}", x=building.x + building.width_m * (i + 1) / (count + 1), y=building.y, face=face)
        for i in range(count)
    )


def _interior(building: Building, rp: dict, slot_cap: float, rack_depth: float, margin: float):
    modules = max(1, int(rp["modules"]))
    bays = max(1, int(rp["bays_per_rack"]))
    levels = max(1, int(building.levels))
    aisle_w = float(rp["aisle_width_m"])
    rack_len = max(1.0, building.depth_m - 2 * margin)
    pitch = rack_depth + aisle_w
    block_w = modules * pitch - aisle_w  # last module has no trailing aisle
    start_x = building.x + max(margin, (building.width_m - block_w) / 2.0)
    ry = building.y + margin

    racks: list[Rack] = []
    aisles: list[Aisle] = []
    slots: list[Slot] = []
    for m in range(modules):
        rx = start_x + m * pitch
        rid = f"R{m + 1}"
        racks.append(Rack(id=rid, x=rx, y=ry, width_m=rack_depth, depth_m=rack_len,
                          orientation="y", bays=bays, levels=levels))
        for bay in range(bays):
            for level in range(levels):
                slots.append(Slot(rack_id=rid, bay=bay, level=level, capacity_units=slot_cap))
        if m < modules - 1:
            aisles.append(Aisle(id=f"A{m + 1}", x=rx + rack_depth, y=ry,
                                length_m=rack_len, width_m=aisle_w, orientation="y"))
    return tuple(aisles), tuple(racks), tuple(slots)


def _truck_paths(building: Building, gates: tuple[Gate, ...], docks: tuple[Dock, ...]) -> tuple[TruckPath, ...]:
    g, d = gates[0], docks[0]
    approach = building.y - 5.0
    return (
        TruckPath(kind="in", points=((g.x, g.y), (g.x, approach), (d.x, d.y))),
        TruckPath(kind="out", points=((d.x, d.y), (g.x, approach), (g.x, g.y))),
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_warehouse.py -v`
Expected: PASS (all tests so far green).

- [ ] **Step 5: Commit**

```bash
git add warehouse/generator.py tests/test_warehouse.py
git commit -m "feat: parametric outside-in warehouse generator (capa 1a)"
```

---

### Task 3: Geometry QA (`warehouse/qa.py`)

**Files:**
- Create: `warehouse/qa.py`
- Test: `tests/test_warehouse.py`

**Interfaces:**
- Consumes: `warehouse.model.Layout`, `Rack`.
- Produces: `validate(layout: Layout) -> list[str]` (empty = valid). Constant `MIN_AISLE_WIDTH_M: float`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_warehouse.py`:

```python
from dataclasses import replace

from warehouse.qa import MIN_AISLE_WIDTH_M, validate


def test_default_layout_passes_qa():
    assert validate(generate_layout({})) == []


def test_qa_flags_rack_outside_building():
    layout = generate_layout({})
    moved = replace(layout.racks[0], x=layout.building.x + layout.building.width_m + 5.0)
    layout = replace(layout, racks=(moved,) + layout.racks[1:])
    issues = validate(layout)
    assert any("outside" in i for i in issues)


def test_qa_flags_narrow_aisle():
    layout = generate_layout({})
    narrow = replace(layout.aisles[0], width_m=MIN_AISLE_WIDTH_M - 0.5)
    layout = replace(layout, aisles=(narrow,) + layout.aisles[1:])
    assert any("aisle" in i and "minimum" in i for i in validate(layout))


def test_qa_flags_missing_gates_and_bad_capacity():
    layout = generate_layout({})
    no_gates = replace(layout, gates=())
    assert any("gate" in i for i in validate(no_gates))
    bad_slot = replace(layout.slots[0], capacity_units=0.0)
    bad = replace(layout, slots=(bad_slot,) + layout.slots[1:])
    assert any("capacity" in i for i in validate(bad))
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_warehouse.py -v -k qa`
Expected: FAIL with `ModuleNotFoundError: No module named 'warehouse.qa'`.

- [ ] **Step 3: Write minimal implementation**

Create `warehouse/qa.py`:

```python
"""Geometry validation for a Layout (capa 1a). validate(layout) -> list of issues."""

from __future__ import annotations

from .model import Layout, Rack

MIN_AISLE_WIDTH_M: float = 1.5

Box = tuple[float, float, float, float]


def _rack_box(r: Rack) -> Box:
    return (r.x, r.y, r.x + r.width_m, r.y + r.depth_m)


def _overlap(a: Box, b: Box) -> bool:
    ax0, ay0, ax1, ay1 = a
    bx0, by0, bx1, by1 = b
    return ax0 < bx1 and bx0 < ax1 and ay0 < by1 and by0 < ay1


def validate(layout: Layout) -> list[str]:
    issues: list[str] = []
    b = layout.building
    bbox: Box = (b.x, b.y, b.x + b.width_m, b.y + b.depth_m)
    site = layout.site

    if b.x < 0 or b.y < 0 or b.x + b.width_m > site.width_m or b.y + b.depth_m > site.depth_m:
        issues.append("building extends outside the site")

    for r in layout.racks:
        rx0, ry0, rx1, ry1 = _rack_box(r)
        if rx0 < bbox[0] or ry0 < bbox[1] or rx1 > bbox[2] or ry1 > bbox[3]:
            issues.append(f"rack {r.id} extends outside the building footprint")

    boxes = [(r.id, _rack_box(r)) for r in layout.racks]
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            if _overlap(boxes[i][1], boxes[j][1]):
                issues.append(f"racks {boxes[i][0]} and {boxes[j][0]} overlap")

    for a in layout.aisles:
        if a.width_m < MIN_AISLE_WIDTH_M:
            issues.append(f"aisle {a.id} width {a.width_m:.2f} m below minimum {MIN_AISLE_WIDTH_M} m")

    if not layout.gates:
        issues.append("no gates defined")

    faces = {d.face for d in layout.docks}
    if len(faces) > 1:
        issues.append(f"docks span multiple building faces: {sorted(faces)}")

    slotted = {s.rack_id for s in layout.slots}
    for rid in {r.id for r in layout.racks} - slotted:
        issues.append(f"rack {rid} has no slots")
    if any(s.capacity_units <= 0 for s in layout.slots):
        issues.append("a slot has non-positive capacity")

    if layout.yard.polygon and min(p[1] for p in layout.yard.polygon) < 0:
        issues.append("yard extends past the site boundary")

    return issues
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_warehouse.py -v`
Expected: PASS (all green).

- [ ] **Step 5: Commit**

```bash
git add warehouse/qa.py tests/test_warehouse.py
git commit -m "feat: warehouse geometry QA invariants (capa 1a)"
```

---

### Task 4: Self-contained 3D viewer (`warehouse/html_export.py`)

**Files:**
- Create: `warehouse/html_export.py`
- Test: `tests/test_warehouse.py`

**Interfaces:**
- Consumes: `warehouse.model.Layout` (uses `Layout.to_dict()`).
- Produces: `to_html(layout: Layout, *, title: str = "Warehouse 3D") -> str` — a complete HTML document embedding the layout JSON and a Three.js (importmap) scene; opens with no server.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_warehouse.py`:

```python
from warehouse.html_export import to_html


def test_to_html_is_self_contained_and_embeds_layout():
    layout = generate_layout({})
    html = to_html(layout, title="Demo WH")
    assert "<html" in html and "Demo WH" in html
    assert "importmap" in html and "three" in html
    # the exact serialized layout is embedded for the in-page renderer
    assert json.dumps(layout.to_dict()) in html
    assert "__LAYOUT__" in html
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_warehouse.py -v -k to_html`
Expected: FAIL with `ModuleNotFoundError: No module named 'warehouse.html_export'`.

- [ ] **Step 3: Write minimal implementation**

Create `warehouse/html_export.py`:

```python
"""Render a Layout to a self-contained, navigable 3D HTML page (Three.js, no build)."""

from __future__ import annotations

import json

from .model import Layout

_SCENE_JS = """
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';

const L = window.__LAYOUT__;
const site = L.site, b = L.building;
const cx = site.width_m / 2, cz = site.depth_m / 2;

const scene = new THREE.Scene();
scene.background = new THREE.Color(0x0e1116);
const camera = new THREE.PerspectiveCamera(55, innerWidth / innerHeight, 0.1, 8000);
camera.position.set(cx, Math.max(site.width_m, site.depth_m) * 0.9, cz + site.depth_m);
const renderer = new THREE.WebGLRenderer({ antialias: true });
renderer.setSize(innerWidth, innerHeight);
document.body.appendChild(renderer.domElement);
const controls = new OrbitControls(camera, renderer.domElement);
controls.target.set(cx, 0, cz);

scene.add(new THREE.HemisphereLight(0xffffff, 0x404040, 1.1));
const dir = new THREE.DirectionalLight(0xffffff, 0.6);
dir.position.set(cx, 250, cz);
scene.add(dir);

function box(x, z, w, d, h, color, y0) {
  const m = new THREE.Mesh(new THREE.BoxGeometry(w, h, d), new THREE.MeshStandardMaterial({ color }));
  m.position.set(x + w / 2, (y0 || 0) + h / 2, z + d / 2);
  return m;
}

const ground = new THREE.Mesh(
  new THREE.PlaneGeometry(site.width_m, site.depth_m),
  new THREE.MeshStandardMaterial({ color: 0x1b2026 })
);
ground.rotation.x = -Math.PI / 2;
ground.position.set(cx, 0, cz);
scene.add(ground);

const yxs = L.yard.polygon.map(p => p[0]), yys = L.yard.polygon.map(p => p[1]);
const yx0 = Math.min(...yxs), yy0 = Math.min(...yys);
scene.add(box(yx0, yy0, Math.max(...yxs) - yx0, Math.max(...yys) - yy0, 0.1, 0x232a31));

const shell = box(b.x, b.y, b.width_m, b.depth_m, b.height_m, 0x3b4754);
shell.material.transparent = true;
shell.material.opacity = 0.16;
scene.add(shell);

const pickable = [];
for (const r of L.racks) {
  const m = box(r.x, r.y, r.width_m, r.depth_m, b.height_m * 0.8, 0x6f86b3);
  m.userData = { kind: 'rack', id: r.id, info: r.bays + ' bays x ' + r.levels + ' levels' };
  scene.add(m); pickable.push(m);
}
for (const d of L.docks) {
  const m = box(d.x - 1.5, d.y - 1.0, 3.0, 1.0, 1.4, 0x2f7fd8);
  m.userData = { kind: 'dock', id: d.id, info: 'face ' + d.face };
  scene.add(m); pickable.push(m);
}
for (const g of L.gates) {
  const m = box(g.x - g.width_m / 2, g.y, g.width_m, 0.6, 2.2, 0x2f7fd8);
  m.userData = { kind: 'gate', id: g.id, info: '' };
  scene.add(m); pickable.push(m);
}
for (const t of L.truck_paths) {
  const pts = t.points.map(p => new THREE.Vector3(p[0], 0.5, p[1]));
  scene.add(new THREE.Line(
    new THREE.BufferGeometry().setFromPoints(pts),
    new THREE.LineBasicMaterial({ color: t.kind === 'in' ? 0x4cd07a : 0xd0734c })
  ));
}

const raycaster = new THREE.Raycaster(), mouse = new THREE.Vector2();
const panel = document.getElementById('panel');
addEventListener('pointerdown', e => {
  mouse.x = (e.clientX / innerWidth) * 2 - 1;
  mouse.y = -(e.clientY / innerHeight) * 2 + 1;
  raycaster.setFromCamera(mouse, camera);
  const hit = raycaster.intersectObjects(pickable)[0];
  if (hit) { const u = hit.object.userData; panel.textContent = u.kind + ' ' + u.id + (u.info ? ' - ' + u.info : ''); }
});
addEventListener('resize', () => {
  camera.aspect = innerWidth / innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth, innerHeight);
});
(function loop() { requestAnimationFrame(loop); controls.update(); renderer.render(scene, camera); })();
"""

_HTML_TEMPLATE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>__TITLE__</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
html,body{margin:0;height:100%;background:#0e1116;color:#cfd8e3;font-family:system-ui,sans-serif}
#panel{position:fixed;left:12px;top:12px;padding:8px 12px;background:rgba(20,26,32,.85);
border:1px solid #2b333d;border-radius:8px;font-size:14px}
</style>
<script type="importmap">
{"imports":{"three":"https://cdn.jsdelivr.net/npm/three@0.160.0/build/three.module.js",
"three/addons/":"https://cdn.jsdelivr.net/npm/three@0.160.0/examples/jsm/"}}
</script></head>
<body><div id="panel">click a rack / dock / gate</div>
<script>window.__LAYOUT__ = __DATA__;</script>
<script type="module">__SCENE__</script>
</body></html>"""


def to_html(layout: Layout, *, title: str = "Warehouse 3D") -> str:
    return (
        _HTML_TEMPLATE
        .replace("__TITLE__", title)
        .replace("__DATA__", json.dumps(layout.to_dict()))
        .replace("__SCENE__", _SCENE_JS)
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_warehouse.py -v -k to_html`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add warehouse/html_export.py tests/test_warehouse.py
git commit -m "feat: self-contained Three.js warehouse viewer (capa 1a)"
```

---

### Task 5: Job playbook (`jobs/warehouse_job.py`)

**Files:**
- Create: `jobs/warehouse_job.py`
- Test: `tests/test_warehouse.py`

**Interfaces:**
- Consumes: `warehouse.generator.generate_layout`, `warehouse.model.Layout`.
- Produces: `run(params: dict | None = None) -> tuple[Layout, str]` — returns the layout and a short markdown report.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_warehouse.py`:

```python
from jobs.warehouse_job import run as run_warehouse


def test_warehouse_job_returns_layout_and_report():
    layout, report = run_warehouse({"racks": {"modules": 3}})
    assert len(layout.racks) == 3
    assert report.startswith("# Warehouse layout")
    assert "Racks: 3" in report
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_warehouse.py -v -k job`
Expected: FAIL with `ModuleNotFoundError: No module named 'jobs.warehouse_job'`.

- [ ] **Step 3: Write minimal implementation**

Create `jobs/warehouse_job.py`:

```python
"""Playbook: turn warehouse params into a Layout + a short markdown report."""

from __future__ import annotations

from warehouse.generator import generate_layout
from warehouse.model import Layout


def run(params: dict | None = None) -> tuple[Layout, str]:
    layout = generate_layout(params or {})
    return layout, _report(layout)


def _report(layout: Layout) -> str:
    b = layout.building
    capacity = sum(s.capacity_units for s in layout.slots)
    lines = [
        "# Warehouse layout",
        "",
        f"- Site: {layout.site.width_m:.0f} x {layout.site.depth_m:.0f} m",
        f"- Building: {b.width_m:.0f} x {b.depth_m:.0f} m, {b.levels} levels, {b.height_m:.0f} m high",
        f"- Racks: {len(layout.racks)} | Aisles: {len(layout.aisles)} | "
        f"Docks: {len(layout.docks)} | Gates: {len(layout.gates)}",
        f"- Slots: {len(layout.slots)} (capacity {capacity:.0f} units)",
    ]
    return "\n".join(lines)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_warehouse.py -v -k job`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jobs/warehouse_job.py tests/test_warehouse.py
git commit -m "feat: warehouse_job playbook (layout + report)"
```

---

### Task 6: Agent capability (`scm_agent/tools.py`)

**Files:**
- Modify: `scm_agent/tools.py` (add `warehouse_layout_tool()`; register in `build_default_registry()`)
- Test: `tests/test_warehouse.py`

**Interfaces:**
- Consumes: `Tool`, `Prepared`, `Produced` from `scm_agent.registry`; `jobs.warehouse_job.run`; `warehouse.qa.validate`; `warehouse.html_export.to_html`; `JobRequest`/`LLMProvider` typing already imported in `tools.py`.
- Produces: `warehouse_layout_tool() -> Tool` with `key="warehouse_layout"`; registered as the 10th tool. Deliverables dict keys: `layout`, `report`, `viewer`.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_warehouse.py`:

```python
from pathlib import Path

from scm_agent import Orchestrator


def test_warehouse_capability_end_to_end(tmp_path):
    orch = Orchestrator()
    result = orch.run(
        "generate a 3d warehouse layout",
        overrides={"building": {"levels": 3}, "racks": {"modules": 4}},
        job_type="warehouse_layout",
        client="Test",
        out_dir=tmp_path,
    )
    assert result.status == "ok"
    assert result.tool == "warehouse_layout"
    assert {"layout", "report", "viewer"} <= set(result.deliverables)
    assert Path(result.deliverables["viewer"]).exists()


def test_warehouse_capability_qa_fails_on_bad_params(tmp_path):
    orch = Orchestrator()
    result = orch.run(
        "warehouse layout",
        overrides={"racks": {"modules": 400}},  # racks overflow the building
        job_type="warehouse_layout",
        client="Test",
        out_dir=tmp_path,
    )
    assert result.status == "qa_failed"
    assert result.qa_issues
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_warehouse.py -v -k capability`
Expected: FAIL — orchestrator raises `KeyError`/unknown tool `warehouse_layout` (not registered yet).

- [ ] **Step 3: Write minimal implementation**

In `scm_agent/tools.py`, add these functions above `build_default_registry()`:

```python
def _warehouse_prepare(request: JobRequest, provider: LLMProvider) -> Prepared:
    return Prepared(status="ok", payload=dict(request.params or {}))


def _warehouse_run(payload: object, params: dict) -> Produced:
    from jobs.warehouse_job import run as run_warehouse

    layout, report_md = run_warehouse(payload if isinstance(payload, dict) else {})
    summary = (
        f"Generated a {layout.building.width_m:.0f}x{layout.building.depth_m:.0f} m warehouse: "
        f"{len(layout.racks)} racks, {len(layout.slots)} slots, "
        f"{len(layout.docks)} docks, {len(layout.gates)} gates."
    )
    return Produced(report=(layout, report_md), summary=summary)


def _warehouse_deliver(report: object, out_dir, client: str) -> dict:
    import json as _json
    from pathlib import Path

    from warehouse.html_export import to_html

    layout, report_md = report
    target = Path(out_dir) / "warehouse_layout"
    target.mkdir(parents=True, exist_ok=True)
    (target / "layout.json").write_text(_json.dumps(layout.to_dict(), indent=2), encoding="utf-8")
    (target / "report.md").write_text(report_md, encoding="utf-8")
    (target / "warehouse.html").write_text(to_html(layout, title=f"Warehouse - {client}"), encoding="utf-8")
    return {
        "layout": target / "layout.json",
        "report": target / "report.md",
        "viewer": target / "warehouse.html",
    }


def warehouse_layout_tool() -> Tool:
    from warehouse.qa import validate as validate_layout

    return Tool(
        key="warehouse_layout",
        title="Warehouse Layout (3D)",
        description="Generate a parametric, navigable 3D warehouse: building, yard, docks, gates, racks and slots.",
        intent_keywords=(
            "warehouse", "layout", "bodega", "almacen", "almacen 3d", "3d",
            "rack", "racks", "estanteria", "dock", "anden", "patio", "yard", "floor plan",
        ),
        requires_data=False,
        prepare=_warehouse_prepare,
        run=_warehouse_run,
        qa=lambda report: validate_layout(report[0]),
        deliver=_warehouse_deliver,
    )
```

Then add the registration line in `build_default_registry()` (after `reg.register(landed_cost_tool())`):

```python
    reg.register(warehouse_layout_tool())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_warehouse.py -v -k capability`
Expected: PASS (both capability tests).

- [ ] **Step 5: Commit**

```bash
git add scm_agent/tools.py tests/test_warehouse.py
git commit -m "feat: register warehouse_layout as the 10th agent capability"
```

---

### Task 7: Webapp routes (`webapp/app.py`)

**Files:**
- Modify: `webapp/app.py` (imports + `_warehouse_params` helper + `GET /api/warehouse` + `GET /warehouse`)
- Test: `tests/test_warehouse.py`

**Interfaces:**
- Consumes: `warehouse.generator.generate_layout`, `warehouse.qa.validate`, `warehouse.html_export.to_html`; FastAPI `Query`, `HTTPException`, `HTMLResponse`.
- Produces: `GET /api/warehouse` -> `Layout` JSON (400 with `{"qa_issues": [...]}` on invalid geometry); `GET /warehouse` -> HTML viewer page.

- [ ] **Step 1: Write the failing test**

Add to `tests/test_warehouse.py`:

```python
from fastapi.testclient import TestClient

from webapp.app import app


def test_api_warehouse_returns_layout_json():
    client = TestClient(app)
    resp = client.get("/api/warehouse", params={"modules": 4, "levels": 3})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["racks"]) == 4
    assert data["building"]["levels"] == 3


def test_api_warehouse_rejects_invalid_geometry():
    client = TestClient(app)
    resp = client.get("/api/warehouse", params={"modules": 400})
    assert resp.status_code == 400
    assert "qa_issues" in resp.json()["detail"]


def test_warehouse_page_renders_html():
    client = TestClient(app)
    resp = client.get("/warehouse", params={"modules": 4})
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "<html" in resp.text
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_warehouse.py -v -k "api_warehouse or warehouse_page"`
Expected: FAIL — `/api/warehouse` returns 404 (route not defined).

- [ ] **Step 3: Write minimal implementation**

In `webapp/app.py`, add to the imports near the other `from fastapi...` / engine imports:

```python
from fastapi.responses import HTMLResponse  # noqa: E402

from warehouse.generator import generate_layout  # noqa: E402
from warehouse.html_export import to_html  # noqa: E402
from warehouse.qa import validate as validate_layout  # noqa: E402
```

Then add (e.g., just before the `@app.get("/")` route):

```python
def _warehouse_params(
    building_w: float, building_d: float, height: float, levels: int,
    modules: int, aisle_width: float, docks: int, gates: int, yard_depth: float,
) -> dict:
    return {
        "building": {"width_m": building_w, "depth_m": building_d, "height_m": height, "levels": levels},
        "racks": {"modules": modules, "aisle_width_m": aisle_width},
        "docks": {"count": docks, "face": "south"},
        "gates": {"count": gates},
        "yard_depth_m": yard_depth,
    }


@app.get("/api/warehouse")
def api_warehouse(
    building_w: float = Query(80.0, gt=0, le=1000),
    building_d: float = Query(80.0, gt=0, le=1000),
    height: float = Query(12.0, gt=0, le=100),
    levels: int = Query(4, ge=1, le=20),
    modules: int = Query(6, ge=1, le=200),
    aisle_width: float = Query(3.5, gt=0, le=20),
    docks: int = Query(8, ge=1, le=500),
    gates: int = Query(2, ge=1, le=100),
    yard_depth: float = Query(40.0, ge=0, le=500),
) -> dict:
    params = _warehouse_params(building_w, building_d, height, levels, modules, aisle_width, docks, gates, yard_depth)
    layout = generate_layout(params)
    issues = validate_layout(layout)
    if issues:
        raise HTTPException(status_code=400, detail={"qa_issues": issues})
    return layout.to_dict()


@app.get("/warehouse")
def warehouse_page(
    building_w: float = Query(80.0, gt=0, le=1000),
    building_d: float = Query(80.0, gt=0, le=1000),
    height: float = Query(12.0, gt=0, le=100),
    levels: int = Query(4, ge=1, le=20),
    modules: int = Query(6, ge=1, le=200),
    aisle_width: float = Query(3.5, gt=0, le=20),
    docks: int = Query(8, ge=1, le=500),
    gates: int = Query(2, ge=1, le=100),
    yard_depth: float = Query(40.0, ge=0, le=500),
) -> HTMLResponse:
    params = _warehouse_params(building_w, building_d, height, levels, modules, aisle_width, docks, gates, yard_depth)
    return HTMLResponse(to_html(generate_layout(params)))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_warehouse.py -v -k "api_warehouse or warehouse_page"`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add webapp/app.py tests/test_warehouse.py
git commit -m "feat: webapp /api/warehouse (JSON) and /warehouse (3D page)"
```

---

### Task 8 (optional): Blender export (`warehouse/blender_export.py`)

Only do this task if AI-driven / export-quality 3D authoring is wanted now (the user chose "repo + Blender via MCP"). It is off the critical path; the live twin works without it.

**Files:**
- Create: `warehouse/blender_export.py`
- Test: `tests/test_warehouse.py`

**Interfaces:**
- Consumes: `warehouse.model.Layout`.
- Produces: `to_bpy_script(layout: Layout, *, gltf_path: str = "warehouse.glb") -> str` — a standalone `bpy` Python script string that, run via `blender --background --python <file>` (or pasted through `blender-mcp`), builds the boxes and exports glTF.

- [ ] **Step 1: Write the failing test**

```python
from warehouse.blender_export import to_bpy_script


def test_bpy_script_mentions_layout_and_export():
    layout = generate_layout({"racks": {"modules": 2}})
    script = to_bpy_script(layout, gltf_path="wh.glb")
    assert "import bpy" in script
    assert "wh.glb" in script
    assert script.count("primitive_cube_add") >= len(layout.racks)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/Scripts/python.exe -m pytest tests/test_warehouse.py -v -k bpy`
Expected: FAIL with `ModuleNotFoundError: No module named 'warehouse.blender_export'`.

- [ ] **Step 3: Write minimal implementation**

Create `warehouse/blender_export.py`:

```python
"""Emit a standalone bpy script that rebuilds a Layout in Blender and exports glTF.

Run with:  blender --background --python <script.py>
or paste the body through the blender-mcp server (ahujasid/blender-mcp).
"""

from __future__ import annotations

from .model import Layout

_HEADER = """import bpy

def _clear():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete()

def _box(name, x, z, w, d, h, y0=0.0):
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(x + w / 2.0, z + d / 2.0, y0 + h / 2.0))
    o = bpy.context.active_object
    o.name = name
    o.scale = (w, d, h)
    return o

_clear()
"""


def to_bpy_script(layout: Layout, *, gltf_path: str = "warehouse.glb") -> str:
    b = layout.building
    lines = [_HEADER]
    lines.append(f'_box("building", {b.x}, {b.y}, {b.width_m}, {b.depth_m}, {b.height_m})')
    for r in layout.racks:
        lines.append(
            f'_box("{r.id}", {r.x}, {r.y}, {r.width_m}, {r.depth_m}, {b.height_m * 0.8})'
        )
    for d in layout.docks:
        lines.append(f'_box("{d.id}", {d.x - 1.5}, {d.y - 1.0}, 3.0, 1.0, 1.4)')
    safe_path = gltf_path.replace("\\", "/")
    lines.append(f'bpy.ops.export_scene.gltf(filepath="{safe_path}")')
    return "\n".join(lines) + "\n"
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/Scripts/python.exe -m pytest tests/test_warehouse.py -v -k bpy`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add warehouse/blender_export.py tests/test_warehouse.py
git commit -m "feat: optional Blender (bpy/blender-mcp) glTF export from a Layout"
```

---

### Task 9: Docs + full verification + PR

**Files:**
- Create: `warehouse/README.md`
- Modify: `README.md` (capability table + quick start), `CHANGELOG.md`

**Interfaces:**
- Consumes: everything above. Produces: documentation + a green branch-isolated suite.

- [ ] **Step 1: Write `warehouse/README.md`**

```markdown
# warehouse — spatial twin (capa 1a)

Parametric, navigable 3D warehouse. Pure-Python core (`model`, `generator`, `qa`,
`html_export`) consumed by the `warehouse_layout` agent capability and the webapp
(`GET /api/warehouse`, `GET /warehouse`).

- `generate_layout(params) -> Layout` — outside-in: site, building, yard, gates, docks, aisles, racks, slots.
- `validate(layout) -> list[str]` — geometry QA (empty = ok).
- `to_html(layout) -> str` — self-contained Three.js viewer (no build step).
- Optional `blender_export.to_bpy_script(layout)` — export-quality glTF via Blender / blender-mcp.

Roadmap: 1b slotting (place real SKUs via `src/space.py`), capa 3 simulation
(SimPy/Salabim over `TruckPath` + slots), capa 4 animation. Hooks: `Slot.capacity_units`, `TruckPath`.
```

- [ ] **Step 2: Update `README.md` and `CHANGELOG.md`**

Add a row to the capabilities table in `README.md`:

```markdown
| 🏗️ `warehouse_layout` | params / brief | 3D HTML + layout.json + report — navigable warehouse (building, yard, docks, gates, racks) |
```

Add a `CHANGELOG.md` entry under a new version heading (bump the patch/minor per repo convention):

```markdown
### Added
- `warehouse_layout` capability (10th tool) + `warehouse/` core: parametric 3D warehouse
  (building, yard, gates, docks, racks, slots), geometry QA, self-contained Three.js viewer,
  webapp `/api/warehouse` + `/warehouse`. Foundation (capa 1a) of the warehouse spatial twin.
```

- [ ] **Step 3: Commit docs**

```bash
git add warehouse/README.md README.md CHANGELOG.md
git commit -m "docs: document warehouse_layout capability (capa 1a)"
```

- [ ] **Step 4: Branch-isolated verification (repo deploy loop)**

```bash
git stash push -- jobs/intake.py src/batch.py tests/test_batch.py tests/test_jobs.py
.venv/Scripts/python.exe -m pytest -q
.venv/Scripts/python.exe -m ruff check warehouse jobs/warehouse_job.py scm_agent/tools.py webapp/app.py tests/test_warehouse.py
git stash pop
```

Expected: full suite PASS (existing ~475 tests + new warehouse tests); `ruff check` clean.
If `ruff` flags import order (I) or unused (F), fix and re-run before proceeding.

- [ ] **Step 5: Push + PR**

```bash
git push -u origin feat/warehouse-spatial-twin
gh pr create --fill --title "feat: warehouse spatial twin (3D) - foundation (capa 1a)"
```

Do not auto-merge — leave the PR for review (the user runs the squash-merge per their deploy loop).

---

## Self-Review

**1. Spec coverage:**
- Parametric generator (spec §2, §3) -> Task 2.
- Pure Python core `warehouse/` (spec §4) -> Tasks 1-4.
- Data model with hooks `Slot.capacity_units`, `TruckPath` (spec §5) -> Task 1.
- QA invariants (spec §6) -> Task 3 (building-in-site, rack-in-building, overlaps, aisle min, gates, single dock face, slots/capacity, yard boundary — all present).
- Capability `warehouse_layout` prepare/run/qa/deliver + register (spec §4, §10) -> Task 6.
- Error handling `ok`/`qa_failed`/`needs_clarification` (spec §7) -> Task 6 tests cover `ok` + `qa_failed`; `needs_clarification`/`error` come from the orchestrator generically (no tool code needed).
- Webapp `/api/warehouse` + viewer (spec §4) -> Task 7 (serves `to_html`, DRY: no separate `warehouse3d.js`; reconciles spec's viewer note).
- Self-contained 3D HTML deliverable (spec §3) -> Task 4 + Task 6 deliver.
- Optional Blender export (spec §2, §9) -> Task 8.
- Tests ≥80% / determinism / round-trip / invariants (spec §8) -> tests across Tasks 1-7.
- Out-of-scope 1b/3/4 left undone with hooks in place (spec §9) -> confirmed; `Slot.capacity_units` + `TruckPath` carried, no slotting/sim code.

**2. Placeholder scan:** No `TBD`/`TODO`/"add error handling"; every code step has complete code. Task 8 is explicitly optional, not a placeholder.

**3. Type consistency:** `generate_layout(params)`/`Layout.to_dict`/`Layout.from_dict`/`validate(layout)->list[str]`/`MIN_AISLE_WIDTH_M`/`to_html(layout, *, title=)`/`run(params)->(Layout,str)`/`warehouse_layout_tool()`/deliver keys `layout,report,viewer` are used identically wherever referenced across tasks. `Dock.face`/`Aisle.orientation`/`Rack.orientation` typed as `str` (Literal aliases kept only for `Face`/`Orientation` documentation) so `dataclasses.replace`/`**kwargs` round-trips stay simple.

One reconciliation noted vs spec: the webapp renders via the shared `to_html` (Task 7) instead of a separate `static/warehouse3d.js`, to keep a single renderer (DRY). Same user-visible result.

---

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-06-24-warehouse-spatial-twin.md`.
