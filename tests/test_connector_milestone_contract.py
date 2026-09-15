"""One declaration, every milestone source: which hops a backend can actually emit.

Mirrors ``tests/test_connector_service_contract.py``. A ``MILESTONE_COVERAGE``
that nobody runs is a comment.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

from src.connectors import (
    COVERAGE_MEASURED,
    COVERAGE_UNAVAILABLE,
    FieldCoverage,
)
from src.connectors.milestones import (
    MILESTONE_COVERAGE,
    MILESTONE_IDS,
    InMemoryMilestones,
    MilestoneCoverage,
    MilestoneEvent,
    coverage_of_milestones,
    unavailable_coverage,
)

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _measured(name: str) -> FieldCoverage:
    return FieldCoverage(COVERAGE_MEASURED, name, f"the {name} clock")


def _absent() -> FieldCoverage:
    return FieldCoverage(COVERAGE_UNAVAILABLE, "", "this source has no such clock")


def test_coverage_requires_all_six_ids():
    kwargs = {mid: _measured(mid) for mid in MILESTONE_IDS}
    cover = MilestoneCoverage(backend="x", **kwargs)
    assert set(cover.fields()) == set(MILESTONE_IDS)


def test_omitting_an_id_does_not_instantiate():
    kwargs = {mid: _measured(mid) for mid in MILESTONE_IDS if mid != "pack_end"}
    with pytest.raises(TypeError):
        MilestoneCoverage(backend="x", **kwargs)


def _assert_declaration_holds(coverage: MilestoneCoverage, events: list[MilestoneEvent]) -> None:
    emitted = {e.milestone_id for e in events}
    for mid, field in coverage.fields().items():
        if field.reports:
            assert mid in emitted, f"{mid} declared reportable but never arrived"
        else:
            assert mid not in emitted, f"{mid} declared unavailable but was emitted"


def test_stand_in_emits_every_hop_it_declares_measured():
    source = InMemoryMilestones()
    _assert_declaration_holds(source.coverage, source.milestones())


def test_declaring_pack_end_measured_without_emitting_it_fails():
    events = [
        e for e in InMemoryMilestones().milestones() if e.milestone_id != "pack_end"
    ]
    lying = InMemoryMilestones(events, coverage=MILESTONE_COVERAGE)
    with pytest.raises(AssertionError, match="pack_end"):
        _assert_declaration_holds(lying.coverage, lying.milestones())


def test_emitting_a_hop_declared_unavailable_is_a_lie():
    cover = unavailable_coverage("empty")
    events = [MilestoneEvent("SO-1", "pack_end", "2024-04-01T16:00:00", "wms")]
    source = InMemoryMilestones(events, coverage=cover)
    with pytest.raises(AssertionError, match="pack_end"):
        _assert_declaration_holds(source.coverage, source.milestones())


def test_merge_conflict_on_the_same_id_does_not_take_min():
    left = MilestoneCoverage(
        backend="wms",
        order_created=_absent(),
        released_to_warehouse=_absent(),
        pack_end=_measured("wms.pack"),
        load_end=_measured("wms.load"),
        carrier_pickup=_absent(),
        arrived=_absent(),
    )
    right = MilestoneCoverage(
        backend="tms",
        order_created=_absent(),
        released_to_warehouse=_absent(),
        pack_end=_measured("tms.pack"),
        load_end=_absent(),
        carrier_pickup=_measured("tms.pu"),
        arrived=_measured("tms.arr"),
    )
    with pytest.raises(ValueError, match="pack_end"):
        coverage_of_milestones(left, right)


def test_merge_fills_holes_without_touching_a_reported_id():
    warehouse = MilestoneCoverage(
        backend="wms",
        order_created=_measured("wms.created"),
        released_to_warehouse=_measured("wms.rel"),
        pack_end=_measured("wms.pack"),
        load_end=_measured("wms.load"),
        carrier_pickup=_absent(),
        arrived=_absent(),
    )
    carrier = MilestoneCoverage(
        backend="tms",
        order_created=_absent(),
        released_to_warehouse=_absent(),
        pack_end=_absent(),
        load_end=_absent(),
        carrier_pickup=_measured("tms.pu"),
        arrived=_measured("tms.arr"),
    )
    merged = coverage_of_milestones(warehouse, carrier)
    assert merged.pack_end.source == "wms.pack"
    assert merged.arrived.source == "tms.arr"
    assert merged.backend == "wms + tms"


def test_every_connector_that_defines_milestones_declares_coverage():
    connectors_dir = _REPO_ROOT / "src" / "connectors"
    readers = {
        path.stem
        for path in sorted(connectors_dir.glob("*.py"))
        if path.stem != "__init__"
        and re.search(r"def milestones\(", path.read_text(encoding="utf-8"))
    }
    covered = set()
    for stem in readers:
        text = (connectors_dir / f"{stem}.py").read_text(encoding="utf-8")
        if "MILESTONE_COVERAGE" in text:
            covered.add(stem)
    assert readers == covered, (
        "connector(s) defining milestones() without MILESTONE_COVERAGE: "
        f"{sorted(readers - covered)}"
    )
