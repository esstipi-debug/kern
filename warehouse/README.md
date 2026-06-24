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
