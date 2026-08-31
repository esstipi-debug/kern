"""Tests for the intake_scan job: folder walk, coverage, the QA gate."""
from dataclasses import replace

import pandas as pd
import pytest
from nosilo_core.guided import ExecutionOption, as_options

from jobs import intake_scan_job
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
    # Deliberately NO unit_cost here -- the canonical first-contact folder from
    # the spec: maestro carries the cost, ventas carries the sale. A file that
    # duplicated unit_cost into ventas too would mask the CRITICAL-1 split bug
    # this module's fix closes (see test_a_master_plus_sales_folder_where_
    # abc_xyz_columns_are_split_across_files below).
    return {
        "date": ["2026-01-01"] * n,
        "product_id": [f"SKU-{i}" for i in range(n)],
        "quantity": [10] * n,
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


# -- IMPORTANT 3: one unreadable file must never abort the whole scan --------


def test_an_excel_lock_file_is_skipped_by_name_and_the_rest_of_the_folder_still_scans(tmp_path):
    """A locked Excel workbook leaves a "~$foo.xlsx" temp file behind with
    binary garbage content -- present in essentially every real client folder
    where a workbook was open. It must not raise and abort the whole scan; it
    must be listed by name in `unreadable`, and the rest of the folder (a
    perfectly good maestro.csv) must still classify and evaluate normally."""
    folder = _folder(tmp_path, **{"maestro.csv": _maestro()})
    (folder / "~$maestro.xlsx").write_bytes(b"not a real xlsx file, just garbage bytes")
    intake = intake_scan_job.prepare(str(folder))
    names = [name for name, _ in intake.unreadable]
    assert names == ["~$maestro.xlsx"]
    assert intake.unreadable[0][1]  # a non-empty reason travels with the name
    # The rest of the folder is unaffected.
    assert {c.profile for c in intake.classified} == {"maestro"}
    assert "product_id" in intake.available


def test_a_zero_byte_csv_is_skipped_by_name_and_the_rest_of_the_folder_still_scans(tmp_path):
    folder = _folder(tmp_path, **{"maestro.csv": _maestro()})
    (folder / "vacio.csv").write_bytes(b"")
    intake = intake_scan_job.prepare(str(folder))
    names = [name for name, _ in intake.unreadable]
    assert names == ["vacio.csv"]
    assert {c.profile for c in intake.classified} == {"maestro"}


def test_prepare_raises_only_when_nothing_at_all_is_readable_and_names_both_ignored_and_unreadable(tmp_path):
    """Only when NOT ONE file in the folder is readable does this raise -- and
    the message must list the unsupported-extension files (ignored) AND the
    readable-extension files that failed to parse (unreadable) separately,
    not conflate the two."""
    d = tmp_path / "vacia"
    d.mkdir()
    (d / "notas.txt").write_text("hola", encoding="utf-8")
    (d / "~$roto.xlsx").write_bytes(b"garbage, not a real workbook")
    with pytest.raises(ValueError) as exc_info:
        intake_scan_job.prepare(str(d))
    message = str(exc_info.value)
    assert "notas.txt" in message
    assert "~$roto.xlsx" in message


def test_a_subdirectory_is_named_when_the_scan_finds_nothing_readable(tmp_path):
    """_list_dir only ever reads files, never walks into subdirectories -- but
    a subdirectory silently skipped with no trace makes an empty scan read as
    "found: nothing" when there was actually a folder sitting right there."""
    d = tmp_path / "vacia"
    d.mkdir()
    (d / "subcarpeta").mkdir()
    with pytest.raises(ValueError, match="subcarpeta"):
        intake_scan_job.prepare(str(d))


def test_a_subdirectory_next_to_good_files_is_named_even_on_a_successful_scan(tmp_path):
    """The re-review's own MINOR finding: `subdirs` used to be computed but
    only ever read inside the "nothing readable" error branch -- a scan that
    DID find readable files (the common case: a client zip with maestro.csv
    plus a dated "2025/" subfolder) silently dropped the subdirectory with no
    trace anywhere. It must now travel to FolderIntake, ScanReport, the files
    CSV, and the deck, exactly like an unreadable file."""
    folder = _folder(tmp_path, **{"maestro.csv": _maestro()})
    (folder / "2025").mkdir()
    intake = intake_scan_job.prepare(str(folder))
    assert intake.skipped_subdirs == ("2025",)

    report = intake_scan_job.run(intake)
    assert report.skipped_subdirs == ("2025",)

    deck = intake_scan_job.build_deck(report, client="Acme")
    full_text = " ".join(f.title + " " + f.detail for f in deck.findings)
    assert "2025" in full_text
    assert any(k.name == "Subdirectories skipped" for k in deck.kpis)

    paths = intake_scan_job.write_operational(report, tmp_path / "out", client="Acme")
    body = paths["files_csv"].read_text(encoding="utf-8")
    assert "2025" in body
    assert "subdirectory" in body.lower()


def test_the_deck_and_files_csv_surface_unreadable_files(tmp_path):
    folder = _folder(tmp_path, **{"maestro.csv": _maestro()})
    (folder / "~$maestro.xlsx").write_bytes(b"garbage")
    report = intake_scan_job.run(intake_scan_job.prepare(str(folder)))
    assert report.unreadable == (("~$maestro.xlsx", report.unreadable[0][1]),)

    deck = intake_scan_job.build_deck(report, client="Acme")
    full_text = " ".join(f.title + " " + f.detail for f in deck.findings)
    assert "~$maestro.xlsx" in full_text

    paths = intake_scan_job.write_operational(report, tmp_path / "out", client="Acme")
    body = paths["files_csv"].read_text(encoding="utf-8")
    assert "~$maestro.xlsx" in body
    assert "unreadable" in body.lower()


# -- CRITICAL 2: a column the client already sent, but under-populated -------


def test_under_covered_required_column_is_red_never_missing_and_never_re_asked(tmp_path):
    """stock.csv carries daily_demand, populated in only 10 of 100 rows -- well
    under the 50% coverage threshold. excess_obsolete needs daily_demand: the
    honest verdict names it under_covered_required (not missing_required), and
    the report must not offer to "request" the column again -- it needs to be
    POPULATED, not re-sent."""
    n = 100
    stock = {
        "product_id": [f"SKU-{i}" for i in range(n)],
        "on_hand": [10] * n,
        "daily_demand": [1.0] * 10 + [None] * (n - 10),
    }
    folder = _folder(tmp_path, **{"stock.csv": stock})
    intake = intake_scan_job.prepare(str(folder))
    assert any(f == "stock.csv" and c == "daily_demand" for f, c, _ in intake.under_covered)

    report = intake_scan_job.run(intake)
    (verdict,) = [v for v in report.verdicts if v.tool_key == "excess_obsolete"]
    assert verdict.state == RED
    assert "daily_demand" not in verdict.missing_required
    assert "daily_demand" in verdict.under_covered_required
    column, filename, coverage = verdict.under_covered_detail[0]
    assert (column, filename) == ("daily_demand", "stock.csv")
    assert round(coverage, 2) == 0.10

    # No "request the column" ask for a column that's already there.
    assert "daily_demand" not in [g.column for g in report.gaps]
    labels = [o.label for o in report.outcome.options]
    assert not any("Request 'daily_demand'" in label for label in labels)
    assert any("Populate 'daily_demand'" in label for label in labels)

    # The measured coverage percentage must appear in the deliverable, not
    # just an internal count.
    deck = intake_scan_job.build_deck(report, client="Acme")
    full_text = " ".join(f.title + " " + f.detail for f in deck.findings)
    assert "10%" in full_text


def test_populating_an_under_covered_column_is_never_offered_when_the_tool_is_also_missing_a_different_column(tmp_path):
    """The re-review's own real repro through the actual registry: stock.csv
    carries product_id and daily_demand (populated in only 10 of 100 rows),
    but NOT on_hand at all. excess_obsolete needs both on_hand and
    daily_demand -- it is red for TWO independent reasons at once
    (missing_required=('on_hand',) AND under_covered_required=('daily_demand',)).
    Populating daily_demand alone would not unblock it (on_hand was never
    sent), so the 'Populate daily_demand' option must never be offered, and
    the deck must name BOTH causes on this tool, not just one."""
    n = 100
    stock = {
        "product_id": [f"SKU-{i}" for i in range(n)],
        "daily_demand": [1.0] * 10 + [None] * (n - 10),
    }
    folder = _folder(tmp_path, **{"stock.csv": stock})
    report = intake_scan_job.run(intake_scan_job.prepare(str(folder)))
    (verdict,) = [v for v in report.verdicts if v.tool_key == "excess_obsolete"]
    assert verdict.state == RED
    assert verdict.missing_required == ("on_hand",)
    assert verdict.under_covered_required == ("daily_demand",)

    # No option promises an unlock that populating daily_demand cannot
    # deliver by itself.
    labels = [o.label for o in report.outcome.options]
    assert not any("Populate 'daily_demand'" in label for label in labels)
    assert not any(g.column == "daily_demand" for g in report.undercovered_fixes)

    deck = intake_scan_job.build_deck(report, client="Acme")
    multi = [f for f in deck.findings if f.title == "Tools blocked for two or more distinct reasons"]
    assert len(multi) == 1
    assert "excess_obsolete" in multi[0].detail
    assert "missing" in multi[0].detail.lower()
    assert "under-populated" in multi[0].detail.lower()


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
    """maestro.csv carries unit_cost, ventas.csv does not -- the canonical
    first-contact folder from the spec. abc_xyz needs (product_id, quantity,
    unit_cost) together in ONE file; neither file alone has all three, so the
    honest verdict is RED with the split cause named, not the false GREEN a
    folder-wide union used to report (see CRITICAL 1 in the 2026-07-31 fix
    wave report, and test_a_master_plus_sales_folder_where_abc_xyz_columns_
    are_split_across_files below for the dedicated repro)."""
    folder = _folder(tmp_path, **{"maestro.csv": _maestro(), "ventas.csv": _ventas()})
    report = intake_scan_job.run(intake_scan_job.prepare(str(folder)))
    assert intake_scan_job.verify(report) == []
    states = {v.tool_key: v.state for v in report.verdicts}
    # data_quality's registry declaration also lists "gtin" as optional (added after
    # this brief's fixtures were written); neither fixture carries a gtin column, so
    # the honest verdict is YELLOW (missing_optional=("gtin",)), not GREEN. See the
    # task-5 report for this discrepancy against the original brief.
    assert states["data_quality"] == YELLOW
    assert states["abc_xyz"] == RED
    assert states["excess_obsolete"] == RED  # no stock file in this folder
    assert states["risk"] == UNDECLARED
    # Derived, not pinned: this number is exactly the count of data tools that have
    # declared their columns, and that count is designed to grow as the contracts
    # land batch by batch. A literal here would fail on the first batch and teach
    # whoever hits it to bump a number instead of checking the invariant, which is
    # that the scan evaluates every declared tool and no undeclared one.
    from scm_agent.tools import build_default_registry

    declared = [t for t in build_default_registry().list() if t.requires_data and t.required_columns]
    assert report.n_tools_evaluated == len(declared)
    assert {v.tool_key for v in report.verdicts if v.state != UNDECLARED} == {t.key for t in declared}


def test_a_master_plus_sales_folder_where_abc_xyz_columns_are_split_across_files(tmp_path):
    """CRITICAL 1's own reproduction, verbatim: maestro carries product_id +
    unit_cost, ventas carries product_id + quantity. abc_xyz needs all three
    together and no single file has them -- the honest verdict is RED with
    split_required naming all three, and split_files naming both files. This
    is the exact false-green the whole-branch review found: verdicts_for used
    to union columns across the folder instead of checking one file at a
    time, and every declared tool's prepare() only ever reads one file."""
    folder = _folder(tmp_path, **{"maestro.csv": _maestro(), "ventas.csv": _ventas()})
    report = intake_scan_job.run(intake_scan_job.prepare(str(folder)))
    (verdict,) = [v for v in report.verdicts if v.tool_key == "abc_xyz"]
    assert verdict.state == RED
    assert verdict.missing_required == ()
    assert verdict.blocked_required == ()
    assert set(verdict.split_required) == {"product_id", "quantity", "unit_cost"}
    assert set(verdict.split_files) == {"maestro.csv", "ventas.csv"}
    # The split is ranked alongside every other ask (this folder also has
    # column gaps for excess_obsolete/financial_kpis, so the split fix does
    # not necessarily make the top-3 menu -- but it must be RANKED, never
    # silently dropped from the report).
    (split_fix,) = report.split_fixes
    assert split_fix.tools == ("abc_xyz",)
    assert split_fix.unlocks == 1


def test_a_single_file_that_carries_every_required_column_is_still_green(tmp_path):
    """Guards the fix against over-correcting into "never green again": when
    ventas.csv alone carries product_id, quantity AND unit_cost, abc_xyz is
    genuinely GREEN, sourced from that one file."""
    ventas_with_cost = _ventas()
    ventas_with_cost["unit_cost"] = [5.0] * 20
    folder = _folder(tmp_path, **{"maestro.csv": _maestro(), "ventas.csv": ventas_with_cost})
    report = intake_scan_job.run(intake_scan_job.prepare(str(folder)))
    (verdict,) = [v for v in report.verdicts if v.tool_key == "abc_xyz"]
    assert verdict.state == GREEN
    assert verdict.source_files == ("ventas.csv",)


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


# -- Deliverables: write_operational (two CSVs) and build_deck (client-ready deck) --


def test_write_operational_emits_both_csvs_with_the_undeclared_rows(tmp_path):
    folder = _folder(tmp_path, **{"maestro.csv": _maestro(), "ventas.csv": _ventas()})
    report = intake_scan_job.run(intake_scan_job.prepare(str(folder)))
    paths = intake_scan_job.write_operational(report, tmp_path / "out", client="Acme")
    assert paths["csv"].exists() and paths["files_csv"].exists()
    body = paths["csv"].read_text(encoding="utf-8")
    assert "no_declarado" in body
    assert "data_quality" in body
    # Both column tuples of ToolVerdict travel as SEPARATE csv columns -- a tool
    # red for a genuinely missing column and a tool red only because its file is
    # blocked must never collapse into one "missing" column.
    header = body.splitlines()[0]
    assert "missing_required" in header
    assert "blocked_required" in header


def test_write_operational_files_csv_carries_one_column_per_breaker_fraction(tmp_path):
    folder = _folder(tmp_path, **{"maestro.csv": _maestro(), "ventas.csv": _ventas()})
    report = intake_scan_job.run(intake_scan_job.prepare(str(folder)))
    paths = intake_scan_job.write_operational(report, tmp_path / "out", client="Acme")
    body = paths["files_csv"].read_text(encoding="utf-8")
    header = body.splitlines()[0]
    for breaker in ("impossible_dates", "negative_quantities", "duplicate_rows", "orphan_keys"):
        assert breaker in header
    assert "maestro.csv" in body
    assert "n_rows" in header


def test_deck_states_the_coverage_as_a_fraction_not_as_a_verdict(tmp_path):
    folder = _folder(tmp_path, **{"maestro.csv": _maestro(), "ventas.csv": _ventas()})
    report = intake_scan_job.run(intake_scan_job.prepare(str(folder)))
    deck = intake_scan_job.build_deck(report, client="Acme", citations=("Fuente X",))
    assert deck.client == "Acme"
    assert deck.options == tuple(report.outcome.options)
    assert any(str(report.n_tools_total) in k.value or str(report.n_tools_total) in k.rationale
               for k in deck.kpis)
    assert "undeclared" in " ".join(f.detail for f in deck.findings).lower()


def test_deck_residual_says_shape_not_truth(tmp_path):
    folder = _folder(tmp_path, **{"maestro.csv": _maestro(), "ventas.csv": _ventas()})
    report = intake_scan_job.run(intake_scan_job.prepare(str(folder)))
    deck = intake_scan_job.build_deck(report, client="Acme")
    assert "shape" in deck.residual.lower()
    assert "not" in deck.residual.lower()


def test_deck_names_the_top_ranked_ask_when_options_exist(tmp_path):
    """maestro+ventas has no stock/finanzas file, so at least one column gap or
    blocked-file fix is ranked; the deck's first finding names it, matching the
    same label the guided outcome itself ranked #1."""
    folder = _folder(tmp_path, **{"maestro.csv": _maestro(), "ventas.csv": _ventas()})
    report = intake_scan_job.run(intake_scan_job.prepare(str(folder)))
    deck = intake_scan_job.build_deck(report, client="Acme")
    assert report.outcome.options  # sanity: this fixture does produce ranked options
    top = report.outcome.options[0]
    assert any(top.label in f.title or top.label in f.detail for f in deck.findings)


def test_deck_names_both_causes_when_a_tool_is_red_for_two_distinct_reasons():
    """A tool can be RED for TWO independent reasons at once: a genuinely
    missing required column AND a required column that is present only in a
    file a circuit breaker blocked. Fixing only the blocked file still leaves
    it red -- the deck must name both asks, not just whichever one it noticed
    first. Hand-built (rather than driven through a real folder) because no
    fixture folder happens to produce both at once on the same tool; a second
    tool shares the same blocked file but is missing no column, to prove the
    deck does not over-name tools that only have one problem."""
    both = ToolVerdict(
        tool_key="tool_a", state=RED,
        missing_required=("cogs",), blocked_required=("date",),
        blocked_by=("sucio.csv",),
    )
    only_blocked = ToolVerdict(
        tool_key="tool_b", state=RED,
        blocked_required=("date",), blocked_by=("sucio.csv",),
    )
    report = ScanReport(
        classified=(), findings=(), verdicts=(both, only_blocked), gaps=(), blocked_fixes=(),
        n_files_read=1, n_unclassified=0, n_tools_evaluated=2, n_tools_total=2,
        outcome=_dummy_outcome(), summary="test",
    )
    deck = intake_scan_job.build_deck(report, client="Acme")

    two_reasons = [f for f in deck.findings if f.title == "Tools blocked for two or more distinct reasons"]
    assert len(two_reasons) == 1
    detail = two_reasons[0].detail
    assert "tool_a" in detail
    assert "tool_b" not in detail  # only one problem each -- must not be over-named


def test_multi_cause_finding_fires_for_any_pair_of_the_four_causes_not_just_missing_plus_blocked():
    """The re-review's finding: the multi-cause finding used to test ONLY
    missing_required AND blocked_required. A tool red because of a missing
    column AND an under-populated column (or ANY other pair among the four
    causes) is just as real a "fixing one alone won't help" case, and must be
    named too."""
    missing_plus_undercovered = ToolVerdict(
        tool_key="tool_c", state=RED,
        missing_required=("on_hand",),
        under_covered_required=("daily_demand",),
        under_covered_detail=(("daily_demand", "stock.csv", 0.1),),
    )
    blocked_plus_split = ToolVerdict(
        tool_key="tool_d", state=RED,
        blocked_required=("cost",), blocked_by=("sucio.csv",),
        split_required=("product_id", "quantity"), split_files=("a.csv", "b.csv"),
    )
    only_one_cause = ToolVerdict(
        tool_key="tool_e", state=RED, under_covered_required=("daily_demand",),
        under_covered_detail=(("daily_demand", "stock.csv", 0.1),),
    )
    report = ScanReport(
        classified=(), findings=(),
        verdicts=(missing_plus_undercovered, blocked_plus_split, only_one_cause),
        gaps=(), blocked_fixes=(),
        n_files_read=1, n_unclassified=0, n_tools_evaluated=3, n_tools_total=3,
        outcome=_dummy_outcome(), summary="test",
    )
    deck = intake_scan_job.build_deck(report, client="Acme")

    multi = [f for f in deck.findings if f.title == "Tools blocked for two or more distinct reasons"]
    assert len(multi) == 1
    detail = multi[0].detail
    assert "tool_c" in detail
    assert "tool_d" in detail
    assert "tool_e" not in detail  # single-cause tool must not be over-named


def test_deck_reports_a_blocking_breaker_using_the_evaluated_field(tmp_path):
    """A blocking circuit-breaker finding must appear in the deck, with its
    count and fraction -- and the deck must decide whether a breaker ran via
    BreakerFinding.evaluated, never by string-matching detail."""
    dirty = _ventas(n=20)
    dirty["quantity"] = [-1] * 20  # every row negative -> breaks the 1% threshold
    folder = _folder(tmp_path, **{"maestro.csv": _maestro(), "ventas.csv": dirty})
    report = intake_scan_job.run(intake_scan_job.prepare(str(folder)))
    blocking = [f for f in report.findings if f.blocking]
    assert blocking  # sanity: this fixture does trigger a breaker
    deck = intake_scan_job.build_deck(report, client="Acme")
    full_text = " ".join(f.title + " " + f.detail for f in deck.findings)
    assert "negative_quantities" in full_text
    assert "ventas.csv" in full_text


def test_deck_confidence_is_derived_from_registry_coverage_not_hardcoded(tmp_path):
    """A scan that cannot speak for most of the registry must not claim certainty.

    Stated as an INVARIANT, not a headcount. The previous version asserted
    ``n_tools_evaluated * 2 < n_tools_total`` with the rationale "undeclared tools
    dominate" -- true when 4 of 45 tools had declared their columns, and false the
    moment the data contracts land. A test whose premise expires is worse than no
    test: it goes red for the right reason at the wrong time, and the cheapest way
    out is to delete the assertion. What must hold at every point along the way is
    that confidence FOLLOWS coverage, in both directions, and never reaches base
    while the scan is blind to part of the registry.
    """
    folder = _folder(tmp_path, **{"maestro.csv": _maestro(), "ventas.csv": _ventas()})
    report = intake_scan_job.run(intake_scan_job.prepare(str(folder)))
    deck = intake_scan_job.build_deck(report, client="Acme")

    assert 0 < report.n_tools_evaluated < report.n_tools_total  # sanity on the fixture
    assert deck.confidence < intake_scan_job._DECK_BASE_CONFIDENCE, (
        "the scan is blind to part of the registry; the deck must price that in"
    )

    # It moves WITH coverage rather than flipping at one threshold. Better coverage
    # is never worth less confidence, and full coverage is the only way to base.
    def _confidence_at(evaluated, total):
        stub = replace(report, n_tools_evaluated=evaluated, n_tools_total=total)
        return intake_scan_job.build_deck(stub, client="Acme").confidence

    ladder = [_confidence_at(n, 45) for n in (0, 11, 23, 34, 45)]
    assert ladder == sorted(ladder), f"confidence must not fall as coverage rises: {ladder}"
    assert ladder[0] < ladder[-1], "coverage must actually change the number"
    assert ladder[-1] == intake_scan_job._DECK_BASE_CONFIDENCE
    # The specific regression this replaces: crossing half the registry used to
    # remove the whole penalty in one step, so 24/45 claimed exactly as much
    # certainty as 45/45.
    assert _confidence_at(24, 45) < _confidence_at(45, 45)

    # And it is a real derivation, not a second hardcode: pass an explicit value and
    # it is honoured verbatim.
    assert intake_scan_job.build_deck(report, client="Acme", confidence=0.42).confidence == 0.42
