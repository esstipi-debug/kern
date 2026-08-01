"""Tests for the pure intake_scan engine: profiles, circuit breakers, verdict."""
from src.guided import EXECUTED, OPTIONS
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
    run_breakers,
    verdicts_for,
)


def _file(name, columns, coverage=None, n_rows=100):
    cov = coverage or {c: 1.0 for c in columns}
    return FileColumns(filename=name, columns=tuple(columns), coverage=cov, n_rows=n_rows)


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
    available = {"product_id": {"maestro.csv"}, "unit_cost": {"maestro.csv"}}
    states = {v.tool_key: v.state for v in verdicts_for(available, frozenset(), tools_)}
    assert states == {
        "green_tool": GREEN, "yellow_tool": YELLOW,
        "red_tool": RED, "undeclared_tool": UNDECLARED,
    }


def test_an_undeclared_tool_is_never_green_even_with_every_column_present():
    (verdict,) = verdicts_for({"product_id": {"a.csv"}}, frozenset(), [_FakeTool("x")])
    assert verdict.state == UNDECLARED


def test_a_blocked_file_cannot_support_a_green():
    tools_ = [_FakeTool("t", ("product_id",))]
    (verdict,) = verdicts_for({"product_id": {"malo.csv"}}, frozenset({"malo.csv"}), tools_)
    assert verdict.state == RED
    assert verdict.blocked_by == ("malo.csv",)
    # The column IS present -- it's the file that's disqualified, not the column
    # that's absent. Must land in blocked_required, never in missing_required.
    assert verdict.blocked_required == ("product_id",)
    assert verdict.missing_required == ()


def test_abc_xyz_style_tool_goes_red_without_unit_cost():
    """The real case: a sales file without cost is a legitimate red for abc_xyz."""
    tools_ = [_FakeTool("abc_xyz", ("product_id", "quantity", "unit_cost"))]
    available = {"product_id": {"ventas.csv"}, "quantity": {"ventas.csv"}}
    (verdict,) = verdicts_for(available, frozenset(), tools_)
    assert verdict.state == RED
    assert verdict.missing_required == ("unit_cost",)


def test_gaps_rank_by_how_many_tools_each_column_unlocks():
    verdicts = verdicts_for(
        {"product_id": {"a.csv"}},
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
        available={"product_id": {"a.csv"}}, blocked=frozenset(),
        n_files_read=1, n_unclassified=0,
    )
    assert report.outcome.status == OPTIONS
    assert "unit_cost" in report.outcome.options[0].label


def test_report_with_nothing_to_ask_for_is_executed_not_an_empty_menu():
    tools_ = [_FakeTool("t", ("product_id",))]
    report = build_report(
        classified=(), findings=(), tools=tools_,
        available={"product_id": {"a.csv"}}, blocked=frozenset(),
        n_files_read=1, n_unclassified=0,
    )
    assert report.outcome.status == EXECUTED


def test_report_counts_evaluated_against_total():
    tools_ = [_FakeTool("declared", ("product_id",)), _FakeTool("undeclared")]
    report = build_report(
        classified=(), findings=(), tools=tools_,
        available={"product_id": {"a.csv"}}, blocked=frozenset(),
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
        available={"product_id": {"good.csv"}, "unit_cost": {"bad.csv"}},
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
    available = {"product_id": {"bad.csv"}}
    verdicts = verdicts_for(available, frozenset({"bad.csv"}), tools_)
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
        available={}, blocked=frozenset(),
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
        available={"product_id": {"a.csv"}}, blocked=frozenset(),
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
    available = {"colA": {"fileX.csv"}, "colB": {"fileY.csv"}}
    blocked = frozenset({"fileX.csv", "fileY.csv"})
    verdicts = verdicts_for(available, blocked, tools_)
    fixes = rank_blocked_fixes(verdicts)
    assert all("two_blocked" not in fix.tools for fix in fixes)
