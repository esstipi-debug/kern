"""Tests for the pure org_friction engine: the four detectors, ranking, verdict."""
import pytest

from src.guided import EXECUTED, HANDOFF, OPTIONS, verify_guided
from src.org_friction import (
    ACCOUNTABILITY_VACUUM,
    DEFAULT_SHADOW_DELTA_THRESHOLD,
    ESSENTIAL,
    GOODHART_RATCHET,
    HIDDEN_FACTORY,
    HINDRANCE,
    UNCLASSIFIED_WORKAROUND,
    ControlPoint,
    TargetPeriod,
    analyze,
    detect,
)


def _cp(name, official=None, shadow=None, owner="", workaround=False, classification="", history=()):
    return ControlPoint(
        name=name, official_value=official, shadow_value=shadow, owner=owner,
        workaround_present=workaround, workaround_classification=classification,
        history=tuple(history),
    )


def _tp(period, actual, target):
    return TargetPeriod(period=period, actual=actual, target=target)


# ---- hidden factory ----------------------------------------------------------

def test_hidden_factory_does_not_fire_with_only_one_channel():
    cp = _cp("Fill rate", official=0.95, owner="x")
    assert not any(f.pattern == HIDDEN_FACTORY for f in detect([cp]))


def test_hidden_factory_fires_when_channels_disagree_beyond_threshold():
    cp = _cp("Fill rate", official=0.95, shadow=0.70, owner="Ops Manager")
    hf = next(f for f in detect([cp]) if f.pattern == HIDDEN_FACTORY)
    assert hf.severity in {"high", "medium"}
    assert "0.95" in hf.detail and "0.7" in hf.detail
    assert len(hf.evidence) == 2


def test_hidden_factory_does_not_fire_within_threshold():
    cp = _cp("Fill rate", official=0.95, shadow=0.94, owner="Ops Manager")
    assert not any(f.pattern == HIDDEN_FACTORY for f in detect([cp]))


def test_hidden_factory_severity_escalates_at_double_the_threshold():
    just_over = _cp("A", official=1.0, shadow=1.0 - (DEFAULT_SHADOW_DELTA_THRESHOLD + 0.01), owner="x")
    way_over = _cp("B", official=1.0, shadow=1.0 - (2 * DEFAULT_SHADOW_DELTA_THRESHOLD + 0.01), owner="x")
    (finding_a,) = [f for f in detect([just_over]) if f.pattern == HIDDEN_FACTORY]
    (finding_b,) = [f for f in detect([way_over]) if f.pattern == HIDDEN_FACTORY]
    assert finding_a.severity == "medium"
    assert finding_b.severity == "high"


def test_hidden_factory_handles_a_zero_shadow_value_without_dividing_by_zero():
    cp = _cp("Returns", official=5.0, shadow=0.0, owner="x")
    (finding,) = [f for f in detect([cp]) if f.pattern == HIDDEN_FACTORY]
    assert finding.severity == "high"


def test_hidden_factory_does_not_fire_when_both_channels_are_genuinely_zero():
    cp = _cp("Returns", official=0.0, shadow=0.0, owner="x")
    assert not any(f.pattern == HIDDEN_FACTORY for f in detect([cp]))


def test_shadow_delta_threshold_is_configurable():
    cp = _cp("Fill rate", official=1.0, shadow=0.7, owner="x")
    tight = [f for f in detect([cp], shadow_delta_threshold=0.05) if f.pattern == HIDDEN_FACTORY]
    loose = [f for f in detect([cp], shadow_delta_threshold=0.5) if f.pattern == HIDDEN_FACTORY]
    assert tight and not loose


# ---- goodhart ratchet ----------------------------------------------------------

def test_goodhart_ratchet_needs_at_least_two_periods():
    cp = _cp("On-time delivery", owner="x", history=[_tp("2026-01", 0.97, 0.95)])
    assert not any(f.pattern == GOODHART_RATCHET for f in detect([cp]))


def test_goodhart_ratchet_fires_when_a_beaten_target_is_then_raised():
    cp = _cp("On-time delivery", owner="x", history=[
        _tp("2026-01", 0.97, 0.95),
        _tp("2026-02", 0.94, 0.98),
    ])
    findings = [f for f in detect([cp]) if f.pattern == GOODHART_RATCHET]
    assert len(findings) == 1
    assert "2026-01" in findings[0].detail and "2026-02" in findings[0].detail
    assert len(findings[0].evidence) == 2


def test_goodhart_ratchet_does_not_fire_when_the_target_was_missed():
    cp = _cp("On-time delivery", owner="x", history=[
        _tp("2026-01", 0.90, 0.95),  # missed the prior target
        _tp("2026-02", 0.94, 0.98),  # raised anyway, but the prior wasn't beaten
    ])
    assert not any(f.pattern == GOODHART_RATCHET for f in detect([cp]))


def test_goodhart_ratchet_does_not_fire_when_the_target_is_held_or_lowered():
    cp = _cp("On-time delivery", owner="x", history=[
        _tp("2026-01", 0.97, 0.95),
        _tp("2026-02", 0.96, 0.95),
    ])
    assert not any(f.pattern == GOODHART_RATCHET for f in detect([cp]))


def test_goodhart_ratchet_fires_on_the_first_qualifying_pair_in_a_longer_history():
    cp = _cp("Quality", owner="x", history=[
        _tp("2026-01", 0.80, 0.80),
        _tp("2026-02", 0.85, 0.90),  # qualifying pair
        _tp("2026-03", 0.99, 0.99),  # would also qualify with a 2026-04, never reached
    ])
    findings = [f for f in detect([cp]) if f.pattern == GOODHART_RATCHET]
    assert len(findings) == 1
    assert "2026-01" in findings[0].detail


# ---- accountability vacuum -----------------------------------------------------

def test_accountability_vacuum_fires_when_owner_is_blank():
    cp = _cp("Cycle count accuracy", owner="")
    findings = [f for f in detect([cp]) if f.pattern == ACCOUNTABILITY_VACUUM]
    assert len(findings) == 1 and findings[0].severity == "low"


def test_accountability_vacuum_does_not_fire_when_owner_is_present():
    cp = _cp("Cycle count accuracy", owner="Warehouse Lead")
    assert not any(f.pattern == ACCOUNTABILITY_VACUUM for f in detect([cp]))


def test_accountability_vacuum_treats_whitespace_only_owner_as_blank():
    cp = _cp("Cycle count accuracy", owner="   ")
    assert any(f.pattern == ACCOUNTABILITY_VACUUM for f in detect([cp]))


# ---- unclassified workaround ----------------------------------------------------

def test_unclassified_workaround_fires_when_present_and_uncategorised():
    cp = _cp("Data load", owner="x", workaround=True, classification="")
    findings = [f for f in detect([cp]) if f.pattern == UNCLASSIFIED_WORKAROUND]
    assert len(findings) == 1 and findings[0].severity == "medium"


def test_unclassified_workaround_does_not_fire_when_classified_essential():
    cp = _cp("Data load", owner="x", workaround=True, classification=ESSENTIAL)
    assert not any(f.pattern == UNCLASSIFIED_WORKAROUND for f in detect([cp]))


def test_unclassified_workaround_does_not_fire_when_classified_hindrance():
    cp = _cp("Data load", owner="x", workaround=True, classification=HINDRANCE)
    assert not any(f.pattern == UNCLASSIFIED_WORKAROUND for f in detect([cp]))


def test_unclassified_workaround_does_not_fire_when_no_workaround_is_present():
    cp = _cp("Data load", owner="x", workaround=False, classification="")
    assert not any(f.pattern == UNCLASSIFIED_WORKAROUND for f in detect([cp]))


# ---- ranking / detect -----------------------------------------------------------

def test_detect_ranks_high_severity_before_medium_before_low():
    cp = _cp(
        "Fill rate", official=1.0, shadow=0.5, owner="",         # HIDDEN_FACTORY (high) + VACUUM (low)
        workaround=True, classification="",                      # + UNCLASSIFIED_WORKAROUND (medium)
    )
    findings = detect([cp])
    assert [f.pattern for f in findings] == [HIDDEN_FACTORY, UNCLASSIFIED_WORKAROUND, ACCOUNTABILITY_VACUUM]


def test_detect_returns_empty_tuple_when_nothing_triangulates():
    cp = _cp("Untouched metric", owner="Someone")
    assert detect([cp]) == ()


# ---- analyze: the guided verdict -------------------------------------------------

def test_analyze_requires_at_least_one_control_point():
    with pytest.raises(ValueError):
        analyze([])


def test_analyze_returns_handoff_when_nothing_triangulates():
    report = analyze([_cp("Untouched metric", owner="Someone")])
    assert report.outcome.status == HANDOFF
    assert report.n_flagged == 0
    assert report.outcome.handoffs[0].steps
    assert verify_guided(report.outcome) == []


def test_analyze_returns_options_ranked_with_the_top_finding_recommended():
    cps = [
        _cp("A", official=1.0, shadow=0.5, owner="x"),  # HIDDEN_FACTORY, high
        _cp("B", owner=""),                              # ACCOUNTABILITY_VACUUM, low
    ]
    report = analyze(cps)
    assert report.outcome.status == OPTIONS
    assert report.outcome.options[0].recommended is True
    assert report.findings[0].severity == "high"


def test_analyze_never_emits_executed():
    scenarios = [
        [_cp("Untouched", owner="Someone")],
        [_cp("A", official=1.0, shadow=0.1, owner="", workaround=True, classification="")],
    ]
    for cps in scenarios:
        assert analyze(cps).outcome.status != EXECUTED


def test_analyze_options_are_capped_at_five():
    cps = [_cp(f"Point {i}", owner="") for i in range(8)]  # 8 accountability vacuums
    report = analyze(cps)
    assert len(report.outcome.options) == 5


def test_every_finding_carries_at_least_one_piece_of_evidence():
    cps = [
        _cp("A", official=1.0, shadow=0.5, owner=""),
        _cp("B", owner="x", workaround=True, classification=""),
        _cp("C", owner="x", history=[_tp("p1", 0.9, 0.9), _tp("p2", 0.95, 0.99)]),
    ]
    report = analyze(cps)
    assert report.findings
    assert all(f.evidence for f in report.findings)


def test_summary_names_the_top_priority_control_point():
    report = analyze([_cp("Fill rate", official=1.0, shadow=0.5, owner="")])
    assert "Fill rate" in report.summary
