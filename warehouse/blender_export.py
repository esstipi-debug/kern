"""Emit a standalone bpy script that rebuilds a Layout in Blender and exports glTF.

Run with:  blender --background --python <script.py>
or paste the body through the blender-mcp server (ahujasid/blender-mcp).
"""

from __future__ import annotations

from .model import Layout

_HEADER = """import bpy

bpy.ops.object.select_all(action='SELECT')
bpy.ops.object.delete()

"""

_BOX_TPL = (
    "bpy.ops.mesh.primitive_cube_add("
    "size=1.0, location=({cx}, {cy}, {cz}))\n"
    "bpy.context.active_object.name = '{name}'\n"
    "bpy.context.active_object.scale = ({w}, {d}, {h})\n"
)


def _box_lines(name: str, x: float, y: float, w: float, d: float, h: float) -> str:
    return _BOX_TPL.format(
        name=name,
        cx=x + w / 2.0,
        cy=y + d / 2.0,
        cz=h / 2.0,
        w=w,
        d=d,
        h=h,
    )


def to_bpy_script(layout: Layout, *, gltf_path: str = "warehouse.glb") -> str:
    b = layout.building
    parts = [_HEADER]
    parts.append(_box_lines("building", b.x, b.y, b.width_m, b.depth_m, b.height_m))
    for r in layout.racks:
        parts.append(_box_lines(r.id, r.x, r.y, r.width_m, r.depth_m, b.height_m * 0.8))
    for d in layout.docks:
        parts.append(_box_lines(d.id, d.x - 1.5, d.y - 1.0, 3.0, 1.0, 1.4))
    safe_path = gltf_path.replace("\\", "/")
    parts.append(f'bpy.ops.export_scene.gltf(filepath="{safe_path}")\n')
    return "".join(parts)
