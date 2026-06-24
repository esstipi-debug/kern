"""Tests for the supply-chain risk engine (Linchpin capability M-risk).

Scores a risk register on the standard SCM dimensions (likelihood x impact), ranks by
expected loss (EMV) and FMEA RPN, buckets into a 5x5 heatmap, flags TTR>TTS exposure
(Simchi-Levi), and ranks mitigation options by net benefit. Pure - no external deps.
"""

import math

import pytest

from src.risk import (
    MitigationOption,
    RiskFactor,
    assess,
    assess_portfolio,
    rank_mitigations,
)


def _factor(**overrides) -> RiskFactor:
    base = dict(name="r", category="supply", likelihood=0.2, impact_value=20_000.0)
    base.update(overrides)
    return RiskFactor(**base)


# -- occurrence / severity / detection ratings at their boundaries ------------


@pytest.mark.parametrize(
    "likelihood,expected",
    [(0.04, 1), (0.05, 2), (0.14, 2), (0.15, 3), (0.29, 3), (0.30, 4), (0.49, 4), (0.50, 5), (0.95, 5)],
)
def test_occurrence_rating_at_boundaries(likelihood, expected):
    assert assess(_factor(likelihood=likelihood)).occurrence == expected


@pytest.mark.parametrize(
    "impact,expected",
    [
        (9_999.0, 1), (10_000.0, 2), (49_999.0, 2), (50_000.0, 3),
        (249_999.0, 3), (250_000.0, 4), (999_999.0, 4), (1_000_000.0, 5),
    ],
)
def test_severity_rating_at_boundaries(impact, expected):
    assert assess(_factor(impact_value=impact)).severity == expected


@pytest.mark.parametrize(
    "days,expected",
    [(0.5, 1), (1.0, 2), (6.9, 2), (7.0, 3), (29.0, 3), (30.0, 4), (89.0, 4), (90.0, 5)],
)
def test_detection_rating_at_boundaries(days, expected):
    assert assess(_factor(detectability_days=days)).detection == expected


# -- zone bucketing over the 5x5 (occurrence x severity) score ----------------


@pytest.mark.parametrize(
    "likelihood,impact,score,zone",
    [
        (0.10, 20_000.0, 4, "low"),
        (0.80, 5_000.0, 5, "medium"),
        (0.20, 100_000.0, 9, "medium"),
        (0.10, 2_000_000.0, 10, "high"),
        (0.20, 2_000_000.0, 15, "high"),
        (0.40, 300_000.0, 16, "critical"),
        (0.80, 2_000_000.0, 25, "critical"),
    ],
)
def test_zone_bucketing(likelihood, impact, score, zone):
    a = assess(_factor(likelihood=likelihood, impact_value=impact))
    assert a.score == score
    assert a.zone == zone


# -- the core scalar identities -----------------------------------------------


def test_emv_is_likelihood_times_impact():
    assert assess(_factor(likelihood=0.2, impact_value=100_000.0)).emv == pytest.approx(20_000.0)


def test_rpn_is_severity_times_occurrence_times_detection():
    # likelihood 0.2 -> O=3, impact 100k -> S=3, detect 10 days -> D=3 ; RPN = 27
    a = assess(_factor(likelihood=0.2, impact_value=100_000.0, detectability_days=10.0))
    assert (a.occurrence, a.severity, a.detection) == (3, 3, 3)
    assert a.rpn == 27


def test_exposure_gap_is_ttr_minus_tts_floored_at_zero():
    assert assess(_factor(time_to_recover=30.0, time_to_survive=10.0)).exposure_gap_days == 20.0
    assert assess(_factor(time_to_recover=5.0, time_to_survive=10.0)).exposure_gap_days == 0.0


# -- mitigation ranking -------------------------------------------------------


def test_rank_mitigations_picks_positive_net_benefit_and_always_includes_accept():
    risk = _factor(
        likelihood=0.5, impact_value=100_000.0,  # base EMV 50,000
        mitigations=(
            MitigationOption("Dual source", "redundancy", cost=5_000.0, likelihood_reduction=0.5),
            MitigationOption("Gold plating", "control", cost=80_000.0, impact_reduction=0.9),
        ),
    )

    ranked = rank_mitigations(risk)

    assert any(m.name == "Accept / monitor" for m in ranked)        # accept always offered
    recommended = [m for m in ranked if m.recommended]
    assert len(recommended) == 1                                    # exactly one default
    assert recommended[0].name == "Dual source"                    # best net benefit
    assert recommended[0].net_benefit == pytest.approx(20_000.0)    # 25,000 cut - 5,000 cost
    assert [m.net_benefit for m in ranked] == sorted((m.net_benefit for m in ranked), reverse=True)


def test_rank_mitigations_with_no_options_is_just_accept_monitor():
    ranked = rank_mitigations(_factor(mitigations=()))
    assert len(ranked) == 1
    assert ranked[0].name == "Accept / monitor"
    assert ranked[0].recommended is True
    assert ranked[0].net_benefit == 0.0


# -- portfolio roll-up --------------------------------------------------------


def test_assess_portfolio_sorts_by_emv_and_sums_residual_under_recommended():
    big = _factor(
        name="big", likelihood=0.5, impact_value=200_000.0,  # EMV 100,000
        mitigations=(MitigationOption("Hedge", "transfer", cost=1_000.0, impact_reduction=0.5),),
    )
    small = _factor(name="small", likelihood=0.1, impact_value=50_000.0)  # EMV 5,000, no mitigation

    report = assess_portfolio([small, big])

    assert [a.name for a in report.assessments] == ["big", "small"]    # ranked by EMV desc
    assert report.total_emv == pytest.approx(105_000.0)
    # big buys down to 50,000 via Hedge (net 49,000 > 0); small only "Accept" (= its 5,000 EMV)
    assert report.residual_emv == pytest.approx(55_000.0)
    assert set(report.heatmap) == {"critical", "high", "medium", "low"}
    assert sum(report.heatmap.values()) == 2


def test_top_exposure_gap_picks_the_widest_ttr_over_tts():
    a = _factor(name="a", likelihood=0.3, impact_value=20_000.0, time_to_recover=40.0, time_to_survive=10.0)
    b = _factor(name="b", likelihood=0.3, impact_value=20_000.0, time_to_recover=15.0, time_to_survive=12.0)

    report = assess_portfolio([a, b])

    assert report.top_exposure_gap[0] == "a"
    assert report.top_exposure_gap[1] == pytest.approx(30.0)


def test_assess_portfolio_total_emv_is_finite():
    report = assess_portfolio([_factor(name="x"), _factor(name="y")])
    assert math.isfinite(report.total_emv)
