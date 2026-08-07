"""The risk job must never certify resilience from a TTR nobody measured.

``src.risk.RiskFactor`` defaults ``time_to_recover`` and ``time_to_survive`` to 0.0, and
``max(0, TTR - TTS)`` turns that pair into a zero exposure gap that is byte-identical to a
measured "recovers well within its survival time". A register with no TTR/TTS columns at
all therefore used to produce the single most favourable statement the tool can make --
"resilient to the listed disruptions" -- from no data whatsoever.

These tests pin the rule: the absence is declared with a reason and the risks that lack the
data are named; no resilience verdict is issued from it; and verify() vetoes the clean
verdict rather than shipping it.
"""

from dataclasses import replace

import pandas as pd
import pytest

from jobs import risk_job as rj

_CERTIFICATE = "resilient to the listed disruptions"


def _register(**extra) -> pd.DataFrame:
    """The base register: three risks, ranked Port strike > Single-source MCU > FX swing."""
    base = {
        "name": ["Port strike", "Single-source MCU", "FX swing"],
        "category": ["logistics", "supply", "financial"],
        "likelihood": [0.5, 0.3, 0.2],
        "impact_value": [400_000.0, 120_000.0, 60_000.0],
        "detectability_days": [14.0, 3.0, 45.0],
    }
    base.update(extra)
    return pd.DataFrame(base)


def _no_ttr() -> rj.RiskJobReport:
    """A register that carries no TTR/TTS columns at all -- the reproduced defect."""
    return rj.run(rj.prepare_records(_register()))


def _all_measured() -> rj.RiskJobReport:
    """Every risk carries both numbers; Port strike genuinely blows its survival window."""
    return rj.run(rj.prepare_records(_register(
        time_to_recover=[60.0, 10.0, 20.0],
        time_to_survive=[20.0, 15.0, 30.0],
    )))


# -- the absence is declared, never filled ------------------------------------


def test_no_ttr_column_leaves_the_gap_absent_with_a_reason():
    res = _no_ttr().resilience

    assert res.gap_days is None, "an unmeasured TTR>TTS gap must be absent, not 0.0"
    assert res.worst_risk is None
    assert res.n_evaluated == 0
    assert res.unmeasured == ("Port strike", "Single-source MCU", "FX swing")
    assert res.note, "the absence must carry a legible reason"


def test_no_ttr_column_names_every_risk_without_data_in_the_note():
    note = _no_ttr().resilience.note

    for name in ("Port strike", "Single-source MCU", "FX swing"):
        assert name in note, f"{name} lacks TTR/TTS and must be named in the note"


def test_nan_cells_are_an_absence_not_a_zero():
    df = _register(
        time_to_recover=[60.0, float("nan"), 20.0],
        time_to_survive=[20.0, 15.0, float("nan")],
    )
    res = rj.run(rj.prepare_records(df)).resilience

    assert res.n_evaluated == 1
    assert res.unmeasured == ("Single-source MCU", "FX swing")


def test_an_explicit_zero_in_the_register_is_a_measurement_not_an_absence():
    """0 written by the operator is their declaration; 0 invented by us is the defect."""
    df = _register(time_to_recover=[0.0, 0.0, 0.0], time_to_survive=[20.0, 15.0, 30.0])
    report = rj.run(rj.prepare_records(df))

    assert report.resilience.unmeasured == ()
    assert report.resilience.n_evaluated == 3
    assert report.resilience_verdict == rj.RESILIENT


# -- the text of the deliverable ----------------------------------------------


def test_deck_does_not_certify_resilience_without_a_measured_ttr():
    md = rj.build_deck(_no_ttr(), client="Acme").to_markdown()

    assert _CERTIFICATE not in md
    assert "No TTR>TTS resilience gap" not in md
    assert "NOT evaluated" in md


def test_deck_names_the_risks_without_ttr():
    md = rj.build_deck(_no_ttr(), client="Acme").to_markdown()

    for name in ("Port strike", "Single-source MCU", "FX swing"):
        assert name in md


def test_worst_gap_kpi_is_never_a_fabricated_zero_days():
    md = rj.build_deck(_no_ttr(), client="Acme").to_markdown()

    assert "Port strike: 0d" not in md
    assert "| Worst TTR>TTS gap | not evaluated" in md


def test_summary_declares_the_missing_basis():
    report = _no_ttr()

    assert report.resilience.note in report.summary
    assert _CERTIFICATE not in report.summary


def test_partial_coverage_still_refuses_the_certificate():
    """Two risks measured and clean, one unmeasured: 'resilient' is still unsupported."""
    df = _register(
        time_to_recover=[float("nan"), 10.0, 20.0],
        time_to_survive=[float("nan"), 15.0, 30.0],
    )
    report = rj.run(rj.prepare_records(df))
    md = rj.build_deck(report, client="Acme").to_markdown()

    assert report.resilience_verdict == rj.RESILIENCE_NO_EVALUABLE
    assert _CERTIFICATE not in md
    assert "Port strike" in report.resilience.note


def test_confidence_degrades_when_resilience_cannot_be_evaluated():
    unmeasured = _no_ttr()
    measured = _all_measured()

    assert unmeasured.outcome.confidence < measured.outcome.confidence
    assert rj.build_deck(unmeasured, confidence=0.8).confidence < 0.8
    assert rj.build_deck(measured, confidence=0.8).confidence == pytest.approx(0.8)


# -- the honest cases must keep working ---------------------------------------


def test_a_measured_gap_is_still_reported_in_full():
    report = _all_measured()

    assert report.resilience_verdict == rj.RESILIENCE_GAP
    assert report.resilience.worst_risk == "Port strike"
    assert report.resilience.gap_days == pytest.approx(40.0)
    md = rj.build_deck(report, client="Acme").to_markdown()
    assert "Resilience gap (TTR>TTS): Port strike" in md
    assert rj.verify(report) == []


def test_a_fully_measured_clean_register_may_still_be_certified():
    df = _register(
        time_to_recover=[10.0, 5.0, 2.0],
        time_to_survive=[20.0, 15.0, 30.0],
    )
    report = rj.run(rj.prepare_records(df))
    md = rj.build_deck(report, client="Acme").to_markdown()

    assert report.resilience_verdict == rj.RESILIENT
    assert _CERTIFICATE in md
    assert rj.verify(report) == []


# -- verify() vetoes the clean verdict ----------------------------------------


def test_verify_vetoes_a_resilient_verdict_while_risks_lack_ttr():
    dishonest = replace(_no_ttr(), resilience_verdict=rj.RESILIENT)

    issues = rj.verify(dishonest)

    assert any("resilien" in i.lower() for i in issues), issues


def test_verify_vetoes_a_gap_verdict_with_no_gap_measured():
    dishonest = replace(_no_ttr(), resilience_verdict=rj.RESILIENCE_GAP)

    assert rj.verify(dishonest) != []


def test_verify_vetoes_an_unknown_resilience_verdict():
    assert rj.verify(replace(_no_ttr(), resilience_verdict="fine")) != []


def test_verify_vetoes_a_summary_that_hides_the_gap_in_the_basis():
    silent = replace(_no_ttr(), summary="3 risks, $248,000/yr expected loss; top: Port strike.")

    issues = rj.verify(silent)

    assert any("summary" in i.lower() for i in issues), issues


def test_verify_vetoes_undegraded_confidence_when_resilience_is_unevaluable():
    report = _no_ttr()
    overconfident = replace(report, outcome=replace(report.outcome, confidence=0.95))

    issues = rj.verify(overconfident)

    assert any("confidence" in i.lower() for i in issues), issues


def test_verify_passes_the_unevaluable_run_that_declares_itself():
    """The gap is not the defect -- shipping it silently is. A declared gap is deliverable."""
    assert rj.verify(_no_ttr()) == []


# -- the operational CSV ------------------------------------------------------


def test_operational_csv_leaves_the_gap_cell_empty_for_unmeasured_risks(tmp_path):
    out = rj.write_operational(_no_ttr(), tmp_path, "Acme")

    df = pd.read_csv(out["csv"])
    assert df["exposure_gap_days"].isna().all(), "an unmeasured gap must be blank, never 0"
    assert set(df["resilience"]) == {rj.UNMEASURED_CELL}


def test_operational_csv_keeps_the_number_for_measured_risks(tmp_path):
    out = rj.write_operational(_all_measured(), tmp_path, "Acme")

    df = pd.read_csv(out["csv"]).set_index("name")
    assert df.loc["Port strike", "exposure_gap_days"] == pytest.approx(40.0)
    assert df.loc["Port strike", "resilience"] == "measured"


# -- the inconstructible dishonest record -------------------------------------


def test_a_resilience_gap_whose_counts_do_not_reconcile_is_unconstructible():
    with pytest.raises(ValueError, match="reconcile|n_evaluated"):
        rj.ResilienceGap(
            worst_risk=None, gap_days=None, n_evaluated=0, n_risks=3,
            unmeasured=("Port strike",), note="two risks vanished",
        )


def test_an_absent_gap_without_a_reason_is_unconstructible():
    with pytest.raises(ValueError, match="note|reason"):
        rj.ResilienceGap(
            worst_risk=None, gap_days=None, n_evaluated=0, n_risks=1,
            unmeasured=("Port strike",), note="",
        )


def test_a_gap_in_days_without_the_risk_that_carries_it_is_unconstructible():
    with pytest.raises(ValueError, match="worst_risk"):
        rj.ResilienceGap(
            worst_risk=None, gap_days=40.0, n_evaluated=1, n_risks=1,
            unmeasured=(), note="measured",
        )
