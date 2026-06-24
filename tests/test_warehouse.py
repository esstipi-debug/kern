import json
from dataclasses import replace

from jobs.warehouse_job import run as run_warehouse
from warehouse.generator import generate_layout
from warehouse.html_export import to_html
from warehouse.model import Aisle, Building, Dock, Gate, Layout, Rack, Site, Slot, TruckPath, Yard
from warehouse.qa import MIN_AISLE_WIDTH_M, validate


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


# --- Task 3: Geometry QA ---


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


def test_qa_flags_yard_building_overlap() -> None:
    layout = generate_layout({})
    b = layout.building
    # Shift yard polygon so it overlaps the building interior
    overlapping_poly = (
        (b.x, b.y + 1.0),
        (b.x + b.width_m, b.y + 1.0),
        (b.x + b.width_m, b.y + b.depth_m / 2),
        (b.x, b.y + b.depth_m / 2),
    )
    bad_yard = replace(layout.yard, polygon=overlapping_poly)
    layout = replace(layout, yard=bad_yard)
    issues = validate(layout)
    assert any("yard overlaps" in i for i in issues)


def test_qa_flags_yard_past_boundary() -> None:
    layout = generate_layout({})
    site = layout.site
    # Push one polygon point past site.width_m
    orig = layout.yard.polygon
    shifted_poly = orig[:-1] + ((site.width_m + 1.0, orig[-1][1]),)
    bad_yard = replace(layout.yard, polygon=shifted_poly)
    layout = replace(layout, yard=bad_yard)
    issues = validate(layout)
    assert any("yard extends past" in i for i in issues)


def test_qa_flags_rack_rack_overlap() -> None:
    layout = generate_layout({})
    r0 = layout.racks[0]
    # Place rack 1 at the exact same position as rack 0 -> guaranteed overlap
    duplicate = replace(layout.racks[1], x=r0.x, y=r0.y)
    layout = replace(layout, racks=(r0, duplicate) + layout.racks[2:])
    issues = validate(layout)
    assert any("overlap" in i for i in issues)


def test_qa_flags_building_outside_site() -> None:
    layout = generate_layout({})
    site = layout.site
    # Move building so its right edge exceeds site width
    bad_building = replace(layout.building, x=site.width_m - 1.0)
    layout = replace(layout, building=bad_building)
    issues = validate(layout)
    assert any("building extends outside" in i for i in issues)


def test_qa_flags_docks_on_two_faces() -> None:
    layout = generate_layout({})
    extra = replace(layout.docks[0], id="D_extra", face="north")
    layout = replace(layout, docks=layout.docks + (extra,))
    issues = validate(layout)
    assert any("multiple" in i or "faces" in i for i in issues)


def test_qa_flags_invalid_dock_face():
    layout = generate_layout({})
    bad = replace(layout.docks[0], face="up")
    layout = replace(layout, docks=(bad,) + layout.docks[1:])
    assert any("invalid face" in i for i in validate(layout))


def test_qa_flags_rack_with_no_slots() -> None:
    layout = generate_layout({})
    # Remove all slots for rack 0
    r0_id = layout.racks[0].id
    remaining_slots = tuple(s for s in layout.slots if s.rack_id != r0_id)
    layout = replace(layout, slots=remaining_slots)
    issues = validate(layout)
    assert any("no slots" in i for i in issues)


# --- Task 4: Self-contained 3D viewer ---


def test_to_html_is_self_contained_and_embeds_layout():
    layout = generate_layout({})
    html = to_html(layout, title="Demo WH")
    assert "<html" in html and "Demo WH" in html
    assert "importmap" in html and "three" in html
    # layout data is embedded (with "<" escaped to < for XSS safety)
    assert "window.__LAYOUT__" in html
    assert "__LAYOUT__" in html


def test_to_html_escapes_script_injection_in_params():
    evil = "</script><script>alert(1)</script>"
    html = to_html(generate_layout({"evil_key": evil}))
    assert evil not in html            # verbatim injection must not survive
    assert "\\u003c" in html           # the "<" was escaped in the embedded data


# --- Task 5: Job playbook ---


def test_warehouse_job_returns_layout_and_report():
    layout, report = run_warehouse({"racks": {"modules": 3}})
    assert len(layout.racks) == 3
    assert report.startswith("# Warehouse layout")
    assert "Racks: 3" in report


# --- Task 6: Agent capability ---


from pathlib import Path  # noqa: E402

from scm_agent import Orchestrator  # noqa: E402


def test_warehouse_capability_end_to_end(tmp_path: Path) -> None:
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


def test_warehouse_capability_qa_fails_on_bad_params(tmp_path: Path) -> None:
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


# --- Task 7: Webapp routes ---

from fastapi.testclient import TestClient  # noqa: E402

from webapp.app import app  # noqa: E402


def test_api_warehouse_returns_layout_json() -> None:
    client = TestClient(app)
    resp = client.get("/api/warehouse", params={"modules": 4, "levels": 3})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["racks"]) == 4
    assert data["building"]["levels"] == 3


def test_api_warehouse_rejects_invalid_geometry() -> None:
    client = TestClient(app)
    resp = client.get("/api/warehouse", params={"modules": 400})
    assert resp.status_code == 400
    assert "qa_issues" in resp.json()["detail"]


def test_warehouse_page_renders_html() -> None:
    client = TestClient(app)
    resp = client.get("/warehouse", params={"modules": 4})
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "<html" in resp.text


# --- Task 8: Blender export ---

from warehouse.blender_export import to_bpy_script  # noqa: E402


def test_bpy_script_mentions_layout_and_export():
    layout = generate_layout({"racks": {"modules": 2}})
    script = to_bpy_script(layout, gltf_path="wh.glb")
    assert "import bpy" in script
    assert "wh.glb" in script
    assert script.count("primitive_cube_add") >= len(layout.racks)
