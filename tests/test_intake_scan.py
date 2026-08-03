"""Tests for the pure intake_scan engine: profiles, circuit breakers, verdict."""
from nosilo_core.guided import EXECUTED, OPTIONS

from src.intake_scan import (
    GREEN,
    RED,
    UNCLASSIFIED,
    UNDECLARED,
    YELLOW,
    BreakerFinding,
    FileColumns,
    FileSignals,
    Thresholds,
    blocking_files,
    build_report,
    classify_all,
    classify_file,
    rank_blocked_fixes,
    rank_gaps,
    rank_split_fixes,
    rank_undercovered_fixes,
    run_breakers,
    verdicts_for,
)


def _file(name, columns, coverage=None, n_rows=100):
    cov = coverage or {c: 1.0 for c in columns}
    return FileColumns(filename=name, columns=tuple(columns), coverage=cov, n_rows=n_rows)


def _files(**by_filename) -> dict[str, frozenset[str]]:
    """filename=[cols...] -> {filename: frozenset(cols)}, the shape verdicts_for
    and build_report expect: PER FILE, never a folder-wide union of columns."""
    return {name: frozenset(cols) for name, cols in by_filename.items()}


def test_file_matching_all_required_columns_gets_its_profile():
    result = classify_file(_file("ventas.csv", ["date", "product_id", "quantity", "unit_cost"]))
    assert result.profile == "ventas"
    assert result.missing == ()


def test_file_missing_one_required_column_is_unclassified_with_the_reason():
    # No product_id at all: maestro and ventas both have gap=1 (missing only
    # product_id); maestro wins the tie alphabetically.
    result = classify_file(_file("algo.csv", ["date", "quantity"]))
    assert result.profile is None
    assert result.reason.startswith(UNCLASSIFIED)
    assert result.missing == ("product_id",)


def test_ambiguous_file_matching_two_profiles_is_not_guessed():
    """Genuine ambiguity: ventas (date, product_id, quantity) and stock
    (product_id, on_hand, daily_demand) are both fully satisfied, and neither
    required set is a subset of the other -- this is NOT the maestro-vs-anything
    case (maestro's lone required column, product_id, is a strict subset of both
    and gets filtered out; see classify_file's subset rule), it is two profiles
    that could genuinely both be right."""
    both = ["date", "product_id", "quantity", "on_hand", "daily_demand"]
    result = classify_file(_file("mezcla.csv", both))
    assert result.profile is None
    assert "ambiguous" in result.reason
    assert "ventas" in result.reason and "stock" in result.reason


def test_a_complete_ventas_file_is_not_ambiguous_with_maestro():
    """Fixes the false-ambiguity the subset rule exists for: a file that fully
    satisfies ventas also trivially satisfies maestro's lone required column
    (product_id), but that does not make it ambiguous -- ventas is strictly MORE
    specific than maestro, so it is not "could genuinely be either", and must
    classify as ventas outright."""
    result = classify_file(_file("ventas.csv", ["date", "product_id", "quantity", "unit_cost"]))
    assert result.profile == "ventas"


def test_classification_is_deterministic_and_sorted_by_filename():
    files = [_file("z.csv", ["product_id"]), _file("a.csv", ["product_id", "cogs", "avg_inventory_value"])]
    first = classify_all(files)
    second = classify_all(list(reversed(files)))
    assert [c.filename for c in first] == ["a.csv", "z.csv"]
    assert first == second


def test_tie_in_no_match_branch_broken_alphabetically_by_profile_name():
    """Verify that a tie in gap size is broken by profile name (alphabetical),
    so reordering PROFILES cannot silently change the result."""
    # No product_id: columns=["date", "quantity", "on_hand", "daily_demand"] creates
    # gap=1 (missing only product_id) for maestro, ventas, AND stock alike.
    # Alphabetically: maestro < stock < ventas, so maestro wins the tie and is reported.
    result = classify_file(_file("tied.csv", ["date", "quantity", "on_hand", "daily_demand"]))
    assert result.profile is None
    assert result.reason.startswith(UNCLASSIFIED)
    # maestro is chosen as closest despite being early in PROFILES, because
    # the tie is broken alphabetically not by tuple order
    assert "maestro" in result.reason
    assert result.missing == ("product_id",)


def test_empty_file_reports_closest_profile():
    """An empty file (no columns) should report the closest profile
    (maestro, with gap=1 because it only requires product_id)."""
    result = classify_file(_file("empty.csv", []))
    assert result.profile is None
    assert result.reason.startswith(UNCLASSIFIED)
    assert result.missing == ("product_id",)


def test_single_date_column_maestro_is_closer_than_ventas():
    """columns=("date",) is missing 1 from maestro (product_id) but missing 2 from
    ventas (product_id, quantity), so maestro is the closest profile."""
    result = classify_file(_file("date_only.csv", ["date"]))
    assert result.profile is None
    assert result.reason.startswith(UNCLASSIFIED)
    assert result.missing == ("product_id",)
    assert "maestro" in result.reason


def _signals(**kwargs):
    base = dict(
        filename="ventas.csv", n_impossible_dates=0, n_negative_quantities=0,
        n_duplicate_rows=0, n_orphan_keys=0, n_rows=1000, orphans_evaluated=True,
    )
    base.update(kwargs)
    return FileSignals(**base)


def test_findings_carry_a_count_and_a_fraction_not_a_boolean():
    (finding,) = [f for f in run_breakers([_signals(n_duplicate_rows=3)], Thresholds())
                  if f.breaker == "duplicate_rows"]
    assert isinstance(finding, BreakerFinding)
    assert finding.n_affected == 3
    assert finding.n_total == 1000
    assert round(finding.fraction, 4) == 0.003
    assert finding.blocking is False
    assert finding.evaluated is True


def test_a_breaker_over_its_threshold_blocks():
    findings = run_breakers([_signals(n_impossible_dates=20)], Thresholds())
    dates = next(f for f in findings if f.breaker == "impossible_dates")
    assert dates.fraction == 0.02
    assert dates.blocking is True
    assert blocking_files(findings) == frozenset({"ventas.csv"})


def test_thresholds_are_configurable():
    findings = run_breakers([_signals(n_impossible_dates=20)], Thresholds(impossible_dates=0.05))
    assert next(f for f in findings if f.breaker == "impossible_dates").blocking is False


def test_orphans_not_evaluated_are_declared_not_reported_as_zero():
    findings = run_breakers([_signals(orphans_evaluated=False)], Thresholds())
    orphans = next(f for f in findings if f.breaker == "orphan_keys")
    assert orphans.blocking is False
    assert "not evaluated" in orphans.detail
    assert orphans.n_affected == 0
    assert orphans.fraction == 0.0
    assert orphans.evaluated is False


def test_empty_file_never_divides_by_zero():
    # Test 0/0: empty file with no defects
    findings = run_breakers([_signals(n_rows=0)], Thresholds())
    assert all(f.fraction == 0.0 for f in findings)
    # Test n/0: non-empty defects count but zero rows (the real division-by-zero risk)
    findings = run_breakers([_signals(n_rows=0, n_duplicate_rows=5)], Thresholds())
    dup = next(f for f in findings if f.breaker == "duplicate_rows")
    assert dup.fraction == 0.0


def test_blocking_threshold_is_strictly_greater_than_not_equal():
    """When fraction equals the threshold exactly, the breaker does not block."""
    findings = run_breakers([_signals(n_impossible_dates=10, n_rows=1000)], Thresholds())
    dates = next(f for f in findings if f.breaker == "impossible_dates")
    assert dates.fraction == 0.01  # exactly at the default threshold
    assert dates.blocking is False  # must not block at the boundary


def test_multiple_signals_with_same_filename_are_handled_deterministically():
    """Each breaker produces one finding per file, sorted deterministically."""
    signals = [
        _signals(filename="data.csv", n_duplicate_rows=5),
        _signals(filename="data.csv", n_impossible_dates=3),
    ]
    findings = run_breakers(signals, Thresholds())
    # Both should process: we get findings for both the duplicate and date breakers
    # even though they come from the same filename (the job layer splits by file+breaker)
    filenames = {f.filename for f in findings}
    assert filenames == {"data.csv"}
    # Verify sorting: findings should be in order by (filename, breaker)
    sorted_findings = sorted(findings, key=lambda f: (f.filename, f.breaker))
    assert findings == tuple(sorted_findings)


class _FakeTool:
    def __init__(self, key, required=(), optional=(), requires_data=True):
        self.key = key
        self.required_columns = tuple(required)
        self.optional_columns = tuple(optional)
        self.requires_data = requires_data


def test_the_four_states():
    tools_ = [
        _FakeTool("green_tool", ("product_id",), ("unit_cost",)),
        _FakeTool("yellow_tool", ("product_id",), ("gtin",)),
        _FakeTool("red_tool", ("product_id", "cogs")),
        _FakeTool("undeclared_tool"),
    ]
    files = _files(**{"maestro.csv": ["product_id", "unit_cost"]})
    states = {v.tool_key: v.state for v in verdicts_for(files, frozenset(), tools_)}
    assert states == {
        "green_tool": GREEN, "yellow_tool": YELLOW,
        "red_tool": RED, "undeclared_tool": UNDECLARED,
    }


def test_an_undeclared_tool_is_never_green_even_with_every_column_present():
    (verdict,) = verdicts_for(_files(**{"a.csv": ["product_id"]}), frozenset(), [_FakeTool("x")])
    assert verdict.state == UNDECLARED


def test_a_blocked_file_cannot_support_a_green():
    tools_ = [_FakeTool("t", ("product_id",))]
    files = _files(**{"malo.csv": ["product_id"]})
    (verdict,) = verdicts_for(files, frozenset({"malo.csv"}), tools_)
    assert verdict.state == RED
    assert verdict.blocked_by == ("malo.csv",)
    # The column IS present -- it's the file that's disqualified, not the column
    # that's absent. Must land in blocked_required, never in missing_required.
    assert verdict.blocked_required == ("product_id",)
    assert verdict.missing_required == ()


def test_abc_xyz_style_tool_goes_red_without_unit_cost():
    """The real case: a sales file without cost is a legitimate red for abc_xyz."""
    tools_ = [_FakeTool("abc_xyz", ("product_id", "quantity", "unit_cost"))]
    files = _files(**{"ventas.csv": ["product_id", "quantity"]})
    (verdict,) = verdicts_for(files, frozenset(), tools_)
    assert verdict.state == RED
    assert verdict.missing_required == ("unit_cost",)


# -- CRITICAL 1: required columns spread across DIFFERENT files ---------------


def test_required_columns_split_across_two_files_is_red_not_a_false_green():
    """THE bug: product_id+unit_cost live in maestro.csv, quantity lives in
    ventas.csv -- the folder's UNION has every column abc_xyz needs, but no
    SINGLE file does, and prepare() always reads exactly one file. The old
    union-based check reported this GREEN with source_files=(maestro, ventas);
    that tool cannot actually run."""
    tools_ = [_FakeTool("abc_xyz", ("product_id", "quantity", "unit_cost"))]
    files = _files(**{
        "maestro.csv": ["product_id", "name", "gtin", "unit_cost"],
        "ventas.csv": ["date", "product_id", "quantity"],
    })
    (verdict,) = verdicts_for(files, frozenset(), tools_)
    assert verdict.state == RED
    assert verdict.missing_required == ()
    assert verdict.blocked_required == ()
    assert set(verdict.split_required) == {"product_id", "quantity", "unit_cost"}
    assert set(verdict.split_files) == {"maestro.csv", "ventas.csv"}
    # Never a union claiming both files "satisfy" the tool -- that claim is
    # exactly what's false; source_files stays empty for a split verdict.
    assert verdict.source_files == ()


def test_a_tool_whose_required_columns_all_live_in_one_file_is_still_green():
    """Guards against over-correcting into "never green again": when one file
    genuinely carries every required column together, the tool is GREEN, and
    source_files names exactly that one file -- not a false red either."""
    tools_ = [_FakeTool("abc_xyz", ("product_id", "quantity", "unit_cost"))]
    files = _files(**{
        "maestro.csv": ["product_id", "name", "unit_cost"],
        # ventas.csv alone carries every required column for abc_xyz.
        "ventas.csv": ["date", "product_id", "quantity", "unit_cost"],
    })
    (verdict,) = verdicts_for(files, frozenset(), tools_)
    assert verdict.state == GREEN
    assert verdict.split_required == ()
    # Only the file that actually satisfies it, never a union of files that
    # jointly do -- maestro.csv never had "quantity", so it never qualifies.
    assert verdict.source_files == ("ventas.csv",)


def test_split_verdict_prefers_the_single_file_that_actually_satisfies_it():
    """When one candidate file satisfies a tool by itself, that tool is never
    reported as split even though a second, unrelated file also happens to
    carry one of the same column names."""
    tools_ = [_FakeTool("t", ("product_id", "quantity"))]
    files = _files(**{
        "ventas.csv": ["product_id", "quantity"],  # satisfies alone
        "otro.csv": ["product_id"],  # partial overlap, irrelevant to the verdict
    })
    (verdict,) = verdicts_for(files, frozenset(), tools_)
    assert verdict.state == GREEN
    assert verdict.source_files == ("ventas.csv",)


def test_rank_split_fixes_ranks_by_tools_unlocked():
    files = _files(**{
        "maestro.csv": ["product_id", "unit_cost"],
        "ventas.csv": ["product_id", "quantity"],
    })
    tools_ = [
        _FakeTool("t1", ("product_id", "quantity", "unit_cost")),
        _FakeTool("t2", ("product_id", "quantity", "unit_cost")),
    ]
    verdicts = verdicts_for(files, frozenset(), tools_)
    (fix,) = rank_split_fixes(verdicts)
    assert fix.unlocks == 2
    assert fix.tools == ("t1", "t2")
    assert set(fix.columns) == {"product_id", "quantity", "unit_cost"}
    assert set(fix.files) == {"maestro.csv", "ventas.csv"}


# -- CRITICAL 2: a column the client already sent, but under-populated --------


def test_under_covered_required_column_is_red_but_not_missing():
    """The column WAS sent -- it's in the file, just too sparsely populated
    (below the coverage threshold) -- so it must never be reported as if it
    had never arrived. Re-asking for it would get the client the exact same
    header back; it needs to be POPULATED."""
    tools_ = [_FakeTool("excess_obsolete", ("product_id", "daily_demand"))]
    files = _files(**{"stock.csv": ["product_id"]})  # daily_demand didn't clear coverage
    under_covered = (("stock.csv", "daily_demand", 0.1),)
    (verdict,) = verdicts_for(files, frozenset(), tools_, under_covered)
    assert verdict.state == RED
    assert verdict.missing_required == ()
    assert verdict.blocked_required == ()
    assert verdict.under_covered_required == ("daily_demand",)
    assert verdict.under_covered_detail == (("daily_demand", "stock.csv", 0.1),)


def test_under_covered_column_picks_the_highest_coverage_occurrence_deterministically():
    tools_ = [_FakeTool("t", ("daily_demand",))]
    files = _files(**{"a.csv": [], "b.csv": []})
    under_covered = (("a.csv", "daily_demand", 0.2), ("b.csv", "daily_demand", 0.4))
    (verdict,) = verdicts_for(files, frozenset(), tools_, under_covered)
    assert verdict.under_covered_detail == (("daily_demand", "b.csv", 0.4),)


def test_rank_undercovered_fixes_ranks_by_tools_unlocked():
    tools_ = [
        _FakeTool("t1", ("daily_demand",)),
        _FakeTool("t2", ("daily_demand",)),
    ]
    files = _files(**{"stock.csv": []})
    under_covered = (("stock.csv", "daily_demand", 0.1),)
    verdicts = verdicts_for(files, frozenset(), tools_, under_covered)
    (fix,) = rank_undercovered_fixes(verdicts)
    assert fix.column == "daily_demand"
    assert fix.filename == "stock.csv"
    assert fix.coverage == 0.1
    assert fix.unlocks == 2
    assert fix.tools == ("t1", "t2")


def test_rank_undercovered_fixes_does_not_credit_a_tool_red_for_a_second_reason_too():
    """The re-review's finding: rank_undercovered_fixes used to credit ANY tool
    with an under_covered_detail entry, even one that is ALSO red because a
    different required column is entirely missing, blocked, or split. That
    promises an unblock populating the sparse column will not deliver. Same
    exclusion discipline rank_blocked_fixes already had (missing_required/
    under_covered_required/split_required exclude a tool from a blocked-file
    credit) now applies symmetrically here.

    Coordinator's own repro: colA lives only in a blocked file, colB is
    under-covered in a different, unblocked file. Neither ranked fix may
    promise an unblock -- populating colB does nothing while colA's file
    stays blocked, and vice versa."""
    tools_ = [_FakeTool("t", ("colA", "colB"))]
    files = _files(**{"bad.csv": ["colA"], "good.csv": []})
    under_covered = (("good.csv", "colB", 0.2),)
    verdicts = verdicts_for(files, frozenset({"bad.csv"}), tools_, under_covered)
    (verdict,) = verdicts
    assert verdict.state == RED
    assert verdict.blocked_required == ("colA",)
    assert verdict.under_covered_required == ("colB",)

    assert rank_blocked_fixes(verdicts) == ()
    assert rank_undercovered_fixes(verdicts) == ()


def test_rank_undercovered_fixes_does_not_credit_a_tool_under_covered_on_two_columns():
    """Symmetric with the two-blocking-files case: a tool under-covered on TWO
    columns is credited to neither individually -- populating just one still
    leaves it red."""
    tools_ = [_FakeTool("t", ("colA", "colB"))]
    files = _files(**{"stock.csv": []})
    under_covered = (("stock.csv", "colA", 0.2), ("stock.csv", "colB", 0.3))
    verdicts = verdicts_for(files, frozenset(), tools_, under_covered)
    assert rank_undercovered_fixes(verdicts) == ()


def test_gaps_rank_by_how_many_tools_each_column_unlocks():
    files = _files(**{"a.csv": ["product_id"]})
    verdicts = verdicts_for(
        files,
        frozenset(),
        [
            _FakeTool("t1", ("product_id", "unit_cost")),
            _FakeTool("t2", ("product_id", "unit_cost")),
            _FakeTool("t3", ("product_id", "cogs")),
        ],
    )
    gaps = rank_gaps(verdicts)
    assert [g.column for g in gaps] == ["unit_cost", "cogs"]
    assert gaps[0].unlocks == 2
    assert gaps[0].tools == ("t1", "t2")


def test_rank_gaps_does_not_credit_a_tool_red_for_a_second_reason_too():
    """The re-review's own repro, driven straight through the pure engine:
    excess_obsolete-style tool needs product_id, on_hand, daily_demand.
    on_hand is entirely missing; daily_demand is present but under-covered.
    Requesting on_hand does not unblock the tool -- daily_demand would still
    sit at 10% coverage -- so rank_gaps must not credit anyone with it. Same
    exclusion discipline rank_blocked_fixes and rank_undercovered_fixes
    already carry, now applied to the fourth ranker."""
    tools_ = [_FakeTool("excess_obsolete", ("product_id", "on_hand", "daily_demand"))]
    files = _files(**{"stock.csv": ["product_id"]})
    under_covered = (("stock.csv", "daily_demand", 0.1),)
    (verdict,) = verdicts_for(files, frozenset(), tools_, under_covered)
    assert verdict.state == RED
    assert verdict.missing_required == ("on_hand",)
    assert verdict.under_covered_required == ("daily_demand",)

    assert rank_gaps((verdict,)) == ()


def test_rank_gaps_does_not_credit_a_tool_missing_two_columns_at_once():
    """Symmetric with the two-blocking-files / two-under-covered-columns
    cases: a tool missing TWO required columns is credited to neither
    individually -- requesting just one still leaves it red."""
    tools_ = [_FakeTool("t", ("colA", "colB"))]
    verdicts = verdicts_for({}, frozenset(), tools_)
    assert rank_gaps(verdicts) == ()


def test_rank_gaps_still_credits_a_single_missing_column_with_no_other_cause():
    """Anti-over-correction proof: a column that IS a tool's sole cause of red
    -- nothing blocked, nothing under-covered, nothing split, exactly one
    missing column -- must still be ranked with its unlock count. "Request
    this column" is the most common ask this capability makes; if the
    exclusion guard silently swallowed the ordinary case it would break the
    capability, not just the edge case it was meant to close. Two tools share
    the same missing column so the unlock count is pinned too."""
    tools_ = [
        _FakeTool("t1", ("product_id", "unit_cost")),
        _FakeTool("t2", ("product_id", "unit_cost")),
    ]
    files = _files(**{"a.csv": ["product_id"]})
    verdicts = verdicts_for(files, frozenset(), tools_)
    (gap,) = rank_gaps(verdicts)
    assert gap.column == "unit_cost"
    assert gap.unlocks == 2
    assert gap.tools == ("t1", "t2")


def test_ties_break_by_column_name_so_two_runs_agree():
    verdicts = verdicts_for(
        {},
        frozenset(),
        [_FakeTool("t1", ("zeta",)), _FakeTool("t2", ("alfa",))],
    )
    assert [g.column for g in rank_gaps(verdicts)] == ["alfa", "zeta"]


def test_report_with_gaps_offers_options():
    tools_ = [_FakeTool("t", ("product_id", "unit_cost"))]
    report = build_report(
        classified=(), findings=(), tools=tools_,
        files=_files(**{"a.csv": ["product_id"]}), blocked=frozenset(),
        n_files_read=1, n_unclassified=0,
    )
    assert report.outcome.status == OPTIONS
    assert "unit_cost" in report.outcome.options[0].label


def test_report_with_nothing_to_ask_for_is_executed_not_an_empty_menu():
    tools_ = [_FakeTool("t", ("product_id",))]
    report = build_report(
        classified=(), findings=(), tools=tools_,
        files=_files(**{"a.csv": ["product_id"]}), blocked=frozenset(),
        n_files_read=1, n_unclassified=0,
    )
    assert report.outcome.status == EXECUTED


def test_report_counts_evaluated_against_total():
    tools_ = [_FakeTool("declared", ("product_id",)), _FakeTool("undeclared")]
    report = build_report(
        classified=(), findings=(), tools=tools_,
        files=_files(**{"a.csv": ["product_id"]}), blocked=frozenset(),
        n_files_read=1, n_unclassified=0,
    )
    assert (report.n_tools_evaluated, report.n_tools_total) == (1, 2)
    assert "1 of 2" in report.summary


def test_a_blocked_column_never_re_asks_the_client_for_data_they_already_sent():
    """The reviewer's repro: unit_cost WAS sent (in bad.csv), but bad.csv is blocked
    by a circuit breaker. Asking the client to send unit_cost again buys nothing --
    the real fix is cleaning up bad.csv. The report must not conflate the two."""
    tools_ = [_FakeTool("t", ("product_id", "unit_cost"))]
    report = build_report(
        classified=(), findings=(), tools=tools_,
        files=_files(**{"good.csv": ["product_id"], "bad.csv": ["unit_cost"]}),
        blocked=frozenset({"bad.csv"}),
        n_files_read=2, n_unclassified=0,
    )
    # The column ranking must not be inflated by a column that's merely blocked.
    assert "unit_cost" not in [g.column for g in report.gaps]
    # The fix-the-file option must be offered instead.
    assert report.outcome.status == OPTIONS
    fix_labels = [o.label for o in report.outcome.options]
    assert any("bad.csv" in label for label in fix_labels)


def test_fixing_a_blocked_file_does_not_count_as_unblocking_a_tool_missing_a_real_column():
    """A tool that is ALSO missing a column that was never sent at all cannot be
    unblocked by cleaning up the blocked file alone -- it still needs the missing
    column. That tool must not inflate the count behind the "fix this file" option."""
    tools_ = [
        _FakeTool("only_blocked", ("product_id",)),
        _FakeTool("blocked_and_missing", ("product_id", "cogs")),
    ]
    files = _files(**{"bad.csv": ["product_id"]})
    verdicts = verdicts_for(files, frozenset({"bad.csv"}), tools_)
    fixes = rank_blocked_fixes(verdicts)
    (fix,) = fixes
    assert fix.filename == "bad.csv"
    assert fix.tools == ("only_blocked",)
    assert "blocked_and_missing" not in fix.tools


def test_no_tools_declared_does_not_falsely_claim_coverage():
    """With zero tools registered, the report must not say 'supports every declared
    tool (0 green)' -- that reads as good news when nothing was evaluated at all."""
    report = build_report(
        classified=(), findings=(), tools=[],
        files={}, blocked=frozenset(),
        n_files_read=0, n_unclassified=0,
    )
    assert report.outcome.status == EXECUTED
    assert "0 green" not in report.outcome.summary
    assert "no declared tool was evaluated" in report.outcome.summary.lower()


def test_all_tools_undeclared_does_not_falsely_claim_coverage_either():
    """The production-real case: tools is NOT empty, but every one of them is
    UNDECLARED (no red, no gaps, no fixes), so the report falls into the same
    'all good' branch as a genuinely green folder. It must not say the folder
    'supports every declared tool' when zero tools were actually evaluated --
    this is the path that will fire for real, since most registered tools
    haven't declared their columns yet."""
    tools_ = [_FakeTool("a"), _FakeTool("b"), _FakeTool("c")]
    report = build_report(
        classified=(), findings=(), tools=tools_,
        files=_files(**{"a.csv": ["product_id"]}), blocked=frozenset(),
        n_files_read=1, n_unclassified=0,
    )
    assert report.outcome.status == EXECUTED
    assert "0 green" not in report.outcome.summary
    assert "supports every declared tool" not in report.outcome.summary
    assert "no declared tool was evaluated" in report.outcome.summary.lower()


def test_a_tool_blocked_by_two_files_is_not_credited_to_either_fix():
    """The round-2 repro: a tool needing columns A and B, where A lives only in
    fileX.csv (blocked) and B lives only in fileY.csv (blocked). Fixing fileX
    alone still leaves the tool blocked by fileY, and vice versa -- neither fix
    option may claim it unlocks this tool."""
    tools_ = [_FakeTool("two_blocked", ("colA", "colB"))]
    files = _files(**{"fileX.csv": ["colA"], "fileY.csv": ["colB"]})
    blocked = frozenset({"fileX.csv", "fileY.csv"})
    verdicts = verdicts_for(files, blocked, tools_)
    fixes = rank_blocked_fixes(verdicts)
    assert all("two_blocked" not in fix.tools for fix in fixes)
