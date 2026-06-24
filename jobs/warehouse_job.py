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
