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
