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
