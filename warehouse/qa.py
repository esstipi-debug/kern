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

    if layout.yard.polygon:
        ys = layout.yard.polygon
        if any(p[0] < 0 or p[1] < 0 or p[0] > site.width_m or p[1] > site.depth_m for p in ys):
            issues.append("yard extends past the site boundary")

        y_xmin = min(p[0] for p in ys)
        y_ymin = min(p[1] for p in ys)
        y_xmax = max(p[0] for p in ys)
        y_ymax = max(p[1] for p in ys)
        yard_box: Box = (y_xmin, y_ymin, y_xmax, y_ymax)
        if _overlap(yard_box, bbox):
            issues.append("yard overlaps the building footprint")

    return issues
