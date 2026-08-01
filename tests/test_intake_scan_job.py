"""Tests for the intake_scan job: folder walk, coverage, the QA gate."""
import pandas as pd
import pytest

from jobs import intake_scan_job
from src.guided import ExecutionOption, as_options
from src.intake_scan import (
    GREEN,
    RED,
    UNDECLARED,
    YELLOW,
    BreakerFinding,
    ColumnGap,
    ScanReport,
    ToolVerdict,
)


def _folder(tmp_path, **frames):
    d = tmp_path / "carpeta"
    d.mkdir()
    for name, frame in frames.items():
        pd.DataFrame(frame).to_csv(d / name, index=False)
    return d


def _maestro(n=20):
    return {"sku": [f"SKU-{i}" for i in range(n)], "name": ["x"] * n, "unit_cost": [5.0] * n}


def _ventas(n=20):
    return {
        "date": ["2026-01-01"] * n,
        "product_id": [f"SKU-{i}" for i in range(n)],
        "quantity": [10] * n,
        "unit_cost": [5.0] * n,
    }


def test_prepare_classifies_the_folder(tmp_path):
    folder = _folder(tmp_path, **{"maestro.csv": _maestro(), "ventas.csv": _ventas()})
    intake = intake_scan_job.prepare(str(folder))
    assert {c.profile for c in intake.classified} == {"maestro", "ventas"}
    assert "product_id" in intake.available


def test_prepare_refuses_a_folder_with_nothing_readable(tmp_path):
    d = tmp_path / "vacia"
    d.mkdir()
    (d / "notas.txt").write_text("hola", encoding="utf-8")
    with pytest.raises(ValueError, match="notas.txt"):
        intake_scan_job.prepare(str(d))


def test_prepare_refuses_a_file_path(tmp_path):
    f = tmp_path / "archivo.csv"
    f.write_text("a,b\n1,2\n", encoding="utf-8")
    with pytest.raises(ValueError, match="archivo.csv"):
        intake_scan_job.prepare(str(f))


def test_a_column_present_but_empty_is_not_available(tmp_path):
    frame = _maestro()
    frame["unit_cost"] = [None] * len(frame["sku"])
    folder = _folder(tmp_path, **{"maestro.csv": frame})
    intake = intake_scan_job.prepare(str(folder))
    assert "unit_cost" not in intake.available
    assert any(col == "unit_cost" for _, col, _ in intake.under_covered)


def test_a_non_readable_file_is_listed_by_name_even_when_others_are_readable(tmp_path):
    folder = _folder(tmp_path, **{"maestro.csv": _maestro()})
    (folder / "leame.txt").write_text("hola", encoding="utf-8")
    intake = intake_scan_job.prepare(str(folder))
    assert intake.ignored == ("leame.txt",)


def test_maestros_sku_header_maps_to_canonical_product_id(tmp_path):
    """sku and product_id are the SAME canonical name (product_id) as of the
    unification fix below; a master file whose only identifier column is
    literally named "sku" still contributes to canonical "product_id" via the
    existing "sku" alias in jobs.intake.ALIASES["product_id"] -- no folder
    with a maestro file, however isolated, should end up without product_id
    in `available`. Compare to the false-red regression test further down."""
    folder = _folder(tmp_path, **{"maestro.csv": _maestro()})
    intake = intake_scan_job.prepare(str(folder))
    assert "product_id" in intake.available


def test_a_master_only_folder_does_not_false_red_data_quality(tmp_path):
    """FIXES A FALSE RED. Regression test for the bug this task-5 follow-up
    exists to close: a client sending ONLY their product master (maestro.csv,
    header "sku") used to get data_quality = RED (missing_required=
    ("product_id",)), because "sku" and "product_id" were two different
    canonical names and nothing else in the folder carried a literally-named
    "product_id" column. That verdict was wrong -- the master file plainly
    identifies every product. Do not delete this test because it looks
    redundant with the healthy-folder test above: this one has NO ventas.csv,
    which is exactly the folder shape that used to trigger the false red."""
    folder = _folder(tmp_path, **{"maestro.csv": _maestro()})
    report = intake_scan_job.run(intake_scan_job.prepare(str(folder)))
    states = {v.tool_key: v.state for v in report.verdicts}
    assert states["data_quality"] != RED


def test_gate_blocks_when_nothing_classified(tmp_path):
    folder = _folder(tmp_path, **{"raro.csv": {"alfa": [1, 2], "beta": [3, 4]}})
    report = intake_scan_job.run(intake_scan_job.prepare(str(folder)))
    assert any("no file" in i for i in intake_scan_job.verify(report))


def test_gate_blocks_when_most_files_are_unclassified(tmp_path):
    folder = _folder(tmp_path, **{
        "maestro.csv": _maestro(),
        "raro1.csv": {"alfa": [1]}, "raro2.csv": {"beta": [1]}, "raro3.csv": {"gama": [1]},
    })
    report = intake_scan_job.run(intake_scan_job.prepare(str(folder)))
    assert any("unclassified" in i for i in intake_scan_job.verify(report))


def test_healthy_folder_passes_the_gate_and_evaluates_the_declared_tools(tmp_path):
    folder = _folder(tmp_path, **{"maestro.csv": _maestro(), "ventas.csv": _ventas()})
    report = intake_scan_job.run(intake_scan_job.prepare(str(folder)))
    assert intake_scan_job.verify(report) == []
    states = {v.tool_key: v.state for v in report.verdicts}
    # data_quality's registry declaration also lists "gtin" as optional (added after
    # this brief's fixtures were written); neither fixture carries a gtin column, so
    # the honest verdict is YELLOW (missing_optional=("gtin",)), not GREEN. See the
    # task-5 report for this discrepancy against the original brief.
    assert states["data_quality"] == YELLOW
    assert states["abc_xyz"] == GREEN
    assert states["excess_obsolete"] == RED  # no stock file in this folder
    assert states["risk"] == UNDECLARED
    assert report.n_tools_evaluated == 4


def test_report_always_carries_evaluated_over_total(tmp_path):
    folder = _folder(tmp_path, **{"maestro.csv": _maestro(), "ventas.csv": _ventas()})
    report = intake_scan_job.run(intake_scan_job.prepare(str(folder)))
    assert f"{report.n_tools_evaluated} of {report.n_tools_total}" in report.summary


# -- Defensive invariant guards (unreachable through prepare/run; see verify()'s
# docstring). These construct a ScanReport by hand, deliberately violating the
# engine invariant that makes each condition dead code, and assert verify()
# still catches it if the invariant ever regresses. --

def _dummy_outcome():
    return as_options(
        "test outcome",
        [ExecutionOption(label="x", summary="x", action="a")],
    )


def test_verify_flags_a_green_tool_whose_source_file_is_blocked(tmp_path):
    """Hand-built violation of verdicts_for's blocked_required routing invariant:
    a GREEN verdict should never carry a blocked source file, but this report
    says it does."""
    finding = BreakerFinding(
        filename="sucio.csv", breaker="duplicate_rows", n_affected=10, n_total=10,
        fraction=1.0, blocking=True,
    )
    verdict = ToolVerdict(
        tool_key="fake_tool", state=GREEN, source_files=("sucio.csv",),
    )
    report = ScanReport(
        classified=(), findings=(finding,), verdicts=(verdict,), gaps=(), blocked_fixes=(),
        n_files_read=1, n_unclassified=0, n_tools_evaluated=1, n_tools_total=1,
        outcome=_dummy_outcome(), summary="test",
    )
    issues = intake_scan_job.verify(report)
    assert any("fake_tool" in i and "sucio.csv" in i for i in issues)


def test_verify_flags_a_gap_naming_an_undeclared_tool(tmp_path):
    """Hand-built violation of rank_gaps' missing_required-only walk invariant:
    an UNDECLARED verdict should never be named by a ranked gap, but this report
    says it is."""
    verdict = ToolVerdict(tool_key="fake_tool", state=UNDECLARED)
    gap = ColumnGap(column="date", unlocks=1, tools=("fake_tool",))
    report = ScanReport(
        classified=(), findings=(), verdicts=(verdict,), gaps=(gap,), blocked_fixes=(),
        n_files_read=1, n_unclassified=0, n_tools_evaluated=0, n_tools_total=1,
        outcome=_dummy_outcome(), summary="test",
    )
    issues = intake_scan_job.verify(report)
    assert any("fake_tool" in i and "date" in i for i in issues)
