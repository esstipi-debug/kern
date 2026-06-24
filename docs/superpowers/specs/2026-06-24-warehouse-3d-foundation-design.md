# Design Spec — Warehouse Spatial Twin (3D) — Foundation (capa 1a)

**Status:** APPROVED (green-lit by user 2026-06-24). Ready for implementation plan.
**Sub-project:** New capability in the Linchpin program — the *spatial / visual* layer.
Capa 1a (spatial foundation) of a layered path toward an operational warehouse twin.
**Builds on:** the engine (`src/`, incl. existing `space.py` COI slotting + `slotting_affinity.py`),
the `scm_agent` registry (9 tools today), `jobs/` playbooks, the FastAPI webapp.

---

## 1. Purpose

Give Linchpin a **navigable 3D warehouse** built from parameters and grounded in real engine
output. Today slotting/space is purely analytical (`space.py`: COI, `slot_skus`, utilization;
`slotting_affinity.py`: co-location groups). This adds the **physical geometry + a navigable 3D
twin**, and lays the data foundation for an **operational simulation** (truck/gate/dock flow,
picking travel, congestion, throughput) — the user's stated end goal.

Built in layers; this spec is **capa 1a only** (the spatial foundation). Later sub-projects:
1b slotting (place real SKUs via existing `space.py`), capa 3 simulation (SimPy/Salabim),
capa 4 animation. The 1a data model already carries the hooks for those.

## 2. Decisions (from brainstorming)

| Decision | Choice |
|---|---|
| End goal | Operational warehouse twin — **simulate** truck/dock/picking flow. Built in layers. |
| First sub-project | **Capa 1a** — spatial foundation (geometry + 3D viewer + data model). |
| Geometry source | **Parametric generator** (map/Google approach dropped). Params -> full site in meters, JSON editable. Idea ported from `Elabar/warehouse-layout`. |
| Modeling order | **Outside-in:** site -> building shell -> yard -> gates -> docks -> aisles -> racks -> slots. |
| Scope of 1a | Building shell + **yard + gates + docks + truck I/O points** + interior racks/slots; navigable 3D. |
| In-browser viewer | **Three.js vanilla** via CDN / importmap — **no build step** (matches the webapp's "no Node/build" rule). |
| Simulation engine (later) | **SimPy** (or **Salabim** for built-in animation) — in-process Python. Verified: no reusable OSS warehouse sim exists to fork. |
| AI 3D authoring (optional) | **blender-mcp** (`ahujasid/blender-mcp`) or headless `bpy` — bake export-quality glTF from the same `Layout`. Off the critical path. |
| Surfaces | Pure Python `warehouse/` core -> capability **`warehouse_layout`** in `scm_agent` (one `register()`) + a **webapp tab**. |
| Reuse | Capa 1b feeds existing `space.slot_skus` / `slotting_affinity` outputs into the geometry (fills `Slot` occupancy). |

### Parameters (example)

```json
{
  "site":     {"width_m": 200, "depth_m": 150},
  "building": {"width_m": 80, "depth_m": 80, "height_m": 12, "levels": 4},
  "racks":    {"modules": 6, "bays_per_rack": 20, "aisle_width_m": 3.5},
  "docks":    {"count": 8, "face": "south"},
  "gates":    {"count": 2},
  "yard_depth_m": 40
}
```

## 3. Architecture

```
params (brief / JSON)
  -> warehouse.generator.generate_layout(params)      # outside-in, deterministic
        site -> building -> yard -> gates -> docks -> aisles -> racks -> slots
  -> warehouse.qa.validate(layout)                    # geometry invariants; fail => no deliverable
  -> Layout (frozen dataclasses, JSON round-trippable)
        |
        +-- capability `warehouse_layout` (scm_agent): prepare -> run -> qa -> deliver
        |       deliver: layout.json + report.md + self-contained 3D HTML (Three.js inline)
        |       [optional] glTF via warehouse.blender_export (bpy / blender-mcp)
        |
        +-- webapp: GET /api/warehouse -> Layout JSON
                 static/warehouse3d.js (Three.js, importmap) renders it navigable
```

**Approach:** keep all geometry in a **pure Python core** (`warehouse/`, no 3D deps) that two
surfaces consume — exactly how the engine feeds CLI + agent + webapp today. The 3D is a thin
rendering of the `Layout`; the agent deliverable and the webapp tab share one source of truth.
Chosen over a JS-first or external-tool-first approach so the model stays testable in Python,
deterministic, and reusable by the later simulation layers.

## 4. Components

| Module | Responsibility | Key shapes / notes |
|---|---|---|
| `warehouse/model.py` | spatial data model | `frozen` dataclasses (see §5). All in meters. `Layout.to_dict()/from_dict()` for JSON. |
| `warehouse/generator.py` | build geometry | `generate_layout(params: dict) -> Layout`, outside-in, **deterministic** (same params -> same Layout). |
| `warehouse/qa.py` | validate geometry | `validate(layout) -> list[str]` (issues). Invariants in §6. |
| `warehouse/html_export.py` | self-contained viewer | `to_html(layout) -> str` — Three.js inline + embedded `Layout` JSON; opens with no server. |
| `warehouse/blender_export.py` *(optional)* | AI / 3D authoring | emit a `bpy` script (headless `blender --python`) or guidance for `blender-mcp` to bake glTF from a `Layout`. Off critical path. |
| `jobs/warehouse_job.py` | playbook | `run(params) -> (Layout, report_md)`; mirrors other `jobs/*_job.py`. |
| `scm_agent/tools.py` | capability | `warehouse_layout_tool()` (prepare/run/qa/deliver) + `reg.register(...)` in `build_default_registry()`; intent keywords (warehouse, layout, bodega, 3d, rack, dock, patio...). |
| `webapp/app.py` | HTTP | `GET /api/warehouse?<params>` -> `Layout` JSON (validated; reuses param-parsing guards). |
| `webapp/static/warehouse3d.js` + tab | viewer | Three.js vanilla (importmap); orbit camera; click rack/dock -> attribute panel. New dashboard tab. |
| `tests/test_warehouse.py` | tests | generator determinism, geometry invariants, JSON round-trip, qa rejects invalid, capability end-to-end. |

## 5. Data model (capa 1a)

`frozen` dataclasses, meters, `Layout` is the JSON-serializable aggregate root:

- `Site(width_m, depth_m)` — terreno.
- `Building(x, y, width_m, depth_m, height_m, levels)` — shell, placed inside the site.
- `Yard(polygon, depth_m)` — maneuvering / parking area in front of the building.
- `Gate(id, x, y, width_m)` — portón on the site boundary.
- `Dock(id, x, y, face)` — andén on one building face.
- `Aisle(id, x, y, length_m, width_m, orientation)` — pasillo.
- `Rack(id, x, y, orientation, bays, levels)` — rack module.
- `Slot(rack_id, bay, level, capacity_units)` — location; `capacity_units` is the **hook for 1b**
  (occupancy from `space.slot_skus`) and for the simulation.
- `TruckPath(points, kind)` — `kind in {"in", "out"}`; the **hook for capa 3** (yard/gate/dock flow).
- `Layout(site, building, yard, gates, docks, aisles, racks, slots, truck_paths, params)` — root.

## 6. QA gate (fail => no deliverable)

- every rack lies fully inside the building footprint;
- aisle width >= configured minimum;
- no rack/rack overlaps;
- all docks on a single building face; at least one gate;
- every rack has >= 1 slot; every slot capacity > 0;
- the yard does not overlap the building footprint.

## 7. Error handling — `JobResult.status` (existing taxonomy)

- `ok` — layout + deliverables written.
- `needs_clarification` — params missing / ambiguous and no LLM to infer -> asks for the few
  required dimensions.
- `qa_failed` — geometry invalid -> lists issues, no deliverable (orchestrator rule).
- `error` — exception -> message.

LLM is optional (only to parse loose params from a brief), never required — same as the other tools.

## 8. Testing (pytest, >= 80%, repo convention)

- generator determinism (same params -> identical `Layout`);
- geometry invariants (no overlaps, correct counts, racks inside building);
- `Layout` JSON round-trip (`to_dict` -> `from_dict` -> equal);
- `qa.validate` flags each invalid case (narrow aisle, rack outside, zero-capacity slot, yard overlap);
- capability end-to-end: brief/params -> `Layout` -> qa pass -> deliverables (layout.json, report.md, html);
- webapp `GET /api/warehouse` returns valid `Layout` JSON.

Run with `.venv/Scripts/python.exe -m pytest`; `ruff` `select=E,F,I` clean (no `ruff format`);
**ASCII-only in any console prints** (Windows cp1252).

## 9. Scope (YAGNI)

- **In (1a):** `model` + `generator` + `qa` + `html_export` + capability `warehouse_layout`
  + webapp tab/viewer + tests. (+ optional `blender_export`.)
- **Out (next sub-projects, hooks already in the model):**
  - **1b slotting** — place real SKUs via `space.slot_skus` / `slotting_affinity` -> fill
    `Slot` occupancy; color the 3D by occupancy / ABC / risk.
  - **capa 3 simulation** — SimPy/Salabim over `TruckPath` + slots -> travel distance, congestion,
    throughput; compare layout variants.
  - **capa 4 animation** — trucks/pickers moving in the viewer.

## 10. Implementation build order

1. `warehouse/model.py` — dataclasses + JSON (`to_dict`/`from_dict`).
2. `warehouse/generator.py` — `generate_layout` (outside-in).
3. `warehouse/qa.py` — invariants.
4. `tests/test_warehouse.py` — model/generator/qa (TDD: write alongside 1-3).
5. `warehouse/html_export.py` — self-contained Three.js HTML.
6. `jobs/warehouse_job.py` — playbook (run + report).
7. `scm_agent/tools.py` — `warehouse_layout_tool()` + register + intent keywords.
8. `webapp/app.py` — `GET /api/warehouse`.
9. `webapp/static/warehouse3d.js` + new dashboard tab.
10. *(optional)* `warehouse/blender_export.py`.
11. Docs (`warehouse/README.md`, README/CHANGELOG, version bump).
12. Branch-isolated suite + ruff + coverage green -> commit -> PR (per repo deploy loop).

## 11. Acceptance criteria

- `run_agent.py --brief "generate a 3D warehouse: 80x80 building, 6 aisles, 4 levels, 8 docks"`
  -> `layout.json` + `report.md` + 3D HTML, QA pass.
- Opening the HTML (no server) shows a navigable warehouse: shell, yard, gates, docks, racks;
  click a rack -> its attributes.
- The webapp tab renders the same `Layout` from `GET /api/warehouse`.
- Invalid params (e.g., aisle width below minimum, racks outside the building) -> `qa_failed`
  with issues, no deliverable.
- Runs with and without `ANTHROPIC_API_KEY`. Full test suite + `ruff` clean; coverage >= 80%.
