"""Engine vs. known-standard numbers for src/audit_evidence.py.

Anchors the closed-form math to published audit-sampling references:
  - Poisson reliability factors reproduce the AICPA MUS confidence-factor tables
    (RF_0 @ 5% = 3.00, RF_1 = 4.75, RF_2 = 6.30).
  - Attribute sample sizes reproduce the AICPA attribute tables (95% / 10% / 0 -> 29).
The MUS *planning* size with a non-zero expected misstatement is PROVISIONAL (memo Q1): the test
pins the expansion-factor method's own output and documents the confidence-factor alternative.
"""

import pytest

from src.audit_evidence import (
    EvidenceRecord,
    InputArtifact,
    QaCheck,
    ReconcilingItem,
    attribute_evaluate,
    attribute_plan,
    evidence_record,
    gl_tie_out,
    hash_file,
    ipe_attestation,
    mus_evaluate,
    mus_plan,
    mus_select,
    reliability_factor,
)

# -- reliability factors (Poisson) reproduce the AICPA MUS tables -------------------- #


def test_reliability_factor_zero_error_matches_aicpa_tables():
    assert reliability_factor(0, 0.05) == pytest.approx(3.00, abs=0.01)
    assert reliability_factor(0, 0.10) == pytest.approx(2.31, abs=0.01)
    assert reliability_factor(0, 0.01) == pytest.approx(4.61, abs=0.01)


def test_reliability_factor_one_and_two_errors_match_aicpa_tables():
    assert reliability_factor(1, 0.05) == pytest.approx(4.75, abs=0.01)
    assert reliability_factor(2, 0.05) == pytest.approx(6.30, abs=0.01)


def test_reliability_factor_is_minus_log_risk_at_zero_errors():
    # RF_0 = -ln(risk) exactly (exponential quantile).
    import math

    assert reliability_factor(0, 0.05) == pytest.approx(-math.log(0.05), rel=1e-9)


def test_reliability_factor_rejects_bad_inputs():
    with pytest.raises(ValueError):
        reliability_factor(-1, 0.05)
    with pytest.raises(ValueError):
        reliability_factor(0, 0.0)


# -- attribute sampling (exact binomial) reproduces AICPA attribute tables ----------- #


def test_attribute_plan_classic_table_value():
    """95% confidence, tolerable 10%, 0 expected -> 29 (AICPA attribute table)."""
    plan = attribute_plan(confidence_level=0.95, tolerable_deviation_rate=0.10)
    assert plan.sample_size == 29
    assert plan.acceptable_deviations == 0


def test_attribute_plan_tighter_tolerable_rate():
    """95% confidence, tolerable 5%, 0 expected -> 59 (AICPA attribute table)."""
    plan = attribute_plan(confidence_level=0.95, tolerable_deviation_rate=0.05)
    assert plan.sample_size == 59


def test_attribute_plan_finite_population_correction_shrinks_n():
    big = attribute_plan(confidence_level=0.95, tolerable_deviation_rate=0.10)
    small = attribute_plan(
        confidence_level=0.95, tolerable_deviation_rate=0.10, population_size=50
    )
    assert small.sample_size < big.sample_size


def test_attribute_plan_rejects_expected_ge_tolerable():
    with pytest.raises(ValueError):
        attribute_plan(
            confidence_level=0.95, tolerable_deviation_rate=0.05, expected_deviation_rate=0.05
        )


def test_attribute_evaluate_zero_deviations_supports_reliance():
    plan = attribute_plan(confidence_level=0.95, tolerable_deviation_rate=0.10)
    ev = attribute_evaluate(plan, 0)
    # With n=29, d=0 the achieved upper deviation limit is ~9.9% < 10%.
    assert ev.achieved_upper_deviation_limit == pytest.approx(0.099, abs=0.005)
    assert ev.conclusion == "supports_reliance"


def test_attribute_evaluate_extra_deviation_breaks_reliance():
    plan = attribute_plan(confidence_level=0.95, tolerable_deviation_rate=0.10)
    ev = attribute_evaluate(plan, 2)
    assert ev.achieved_upper_deviation_limit > 0.10
    assert ev.conclusion == "does_not_support_reliance"


def test_attribute_evaluate_accepts_bare_size():
    ev = attribute_evaluate(29, 0, confidence_level=0.95, tolerable_deviation_rate=0.10)
    assert ev.conclusion == "supports_reliance"


# -- MUS planning -------------------------------------------------------------------- #


def test_mus_plan_zero_expected_is_exact():
    plan = mus_plan(population_value=1_000_000, tolerable_misstatement=50_000)
    assert plan.reliability_factor == pytest.approx(3.00, abs=0.01)
    assert plan.sample_size == 60  # ceil(1e6 * 3.00 / 50_000)
    assert plan.sampling_interval == pytest.approx(1_000_000 / 60, rel=1e-9)
    assert plan.top_stratum_threshold == plan.sampling_interval


def test_mus_plan_worked_example_expansion_factor_method():
    """PROVISIONAL (memo Q1). Published worked example: PV 94,613,131; TM 4,730,000;
    EM 950,000; 85% confidence. The confidence-factor table method yields n=55 (CF=2.73). The
    expansion-factor method implemented here yields n=53; both are AICPA-sanctioned and the exact
    reconciliation awaits the pinned guide edition."""
    plan = mus_plan(
        population_value=94_613_131,
        tolerable_misstatement=4_730_000,
        expected_misstatement=950_000,
        risk_of_incorrect_acceptance=0.15,
    )
    assert plan.sample_size == 53
    assert 53 <= 55  # documents the confidence-factor-table alternative (n=55)


def test_mus_plan_rejects_em_too_close_to_tm():
    with pytest.raises(ValueError):
        mus_plan(
            population_value=1_000_000,
            tolerable_misstatement=100_000,
            expected_misstatement=90_000,
            risk_of_incorrect_acceptance=0.05,
        )


# -- MUS selection ------------------------------------------------------------------- #


def _population():
    # Mostly small items, one certainty (top-stratum) item well above the interval.
    return [(f"sku-{i}", 1_000.0) for i in range(100)] + [("BIG", 60_000.0)]


def test_mus_select_is_deterministic_for_a_fixed_start():
    plan = mus_plan(population_value=160_000, tolerable_misstatement=8_000)
    a = mus_select(_population(), plan, random_start=100.0)
    b = mus_select(_population(), plan, random_start=100.0)
    assert [i.unit_id for i in a.selected] == [i.unit_id for i in b.selected]


def test_mus_select_certainty_item_is_top_stratum():
    plan = mus_plan(population_value=160_000, tolerable_misstatement=8_000)
    sel = mus_select(_population(), plan, random_start=0.0)
    big = [i for i in sel.selected if i.unit_id == "BIG"]
    assert big and big[0].is_top_stratum


def test_mus_select_excludes_zero_and_negative():
    plan = mus_plan(population_value=160_000, tolerable_misstatement=8_000)
    pop = _population() + [("ZERO", 0.0), ("CREDIT", -500.0)]
    sel = mus_select(pop, plan, random_start=0.0)
    assert sel.excluded_zero_or_negative == 2
    assert all(i.unit_id not in {"ZERO", "CREDIT"} for i in sel.selected)


def test_mus_select_rejects_out_of_range_start():
    plan = mus_plan(population_value=160_000, tolerable_misstatement=8_000)
    with pytest.raises(ValueError):
        mus_select(_population(), plan, random_start=plan.sampling_interval)


# -- MUS evaluation (Stringer bound) ------------------------------------------------- #


def test_mus_evaluate_single_tainting_matches_hand_calc():
    # PV 600,000 / n 60 -> interval 10,000. One 50%-tainted item below the interval.
    plan = mus_plan(population_value=600_000, tolerable_misstatement=30_000)
    assert plan.sampling_interval == pytest.approx(10_000, rel=1e-9)
    ev = mus_evaluate(plan, [("item-1", 8_000.0, 4_000.0)])

    rf0 = reliability_factor(0, 0.05)
    rf1 = reliability_factor(1, 0.05)
    projected = 0.5 * 10_000
    expected_uml = rf0 * 10_000 + projected + (rf1 - rf0 - 1.0) * projected
    assert ev.basic_precision == pytest.approx(rf0 * 10_000, rel=1e-9)
    assert ev.projected_misstatement == pytest.approx(projected, rel=1e-9)
    assert ev.upper_misstatement_limit == pytest.approx(expected_uml, rel=1e-9)
    assert ev.conclusion == "do_not_accept"  # UML ~38,740 > TM 30,000


def test_mus_evaluate_no_errors_accepts_when_bp_below_tm():
    plan = mus_plan(population_value=600_000, tolerable_misstatement=40_000)
    ev = mus_evaluate(plan, [("item-1", 8_000.0, 8_000.0)])
    assert ev.projected_misstatement == pytest.approx(0.0)
    assert ev.upper_misstatement_limit == pytest.approx(ev.basic_precision, rel=1e-9)
    # basic precision = 3.0 * interval(=6,000) = 18,000 < TM 40,000
    assert ev.conclusion == "accept"


def test_mus_evaluate_top_stratum_uses_actual_not_projection():
    plan = mus_plan(population_value=160_000, tolerable_misstatement=8_000)
    interval = plan.sampling_interval
    # BIG is above the interval -> actual misstatement, no projection.
    ev = mus_evaluate(plan, [("BIG", 60_000.0, 50_000.0)])
    big = [i for i in ev.per_item if i.unit_id == "BIG"][0]
    assert big.is_top_stratum
    assert big.projected == pytest.approx(10_000.0)  # actual difference, not tainting*interval
    assert ev.upper_misstatement_limit == pytest.approx(
        reliability_factor(0, 0.05) * interval + 10_000.0, rel=1e-9
    )


# -- GL tie-out ---------------------------------------------------------------------- #


def test_gl_tie_out_identity_holds():
    gl = [{"acct": "1400", "amt": 100_000.0}, {"acct": "1410", "amt": 25_000.0}]
    sub = [{"acct": "1400", "amt": 98_000.0}, {"acct": "1410", "amt": 25_000.0}]
    recon = [ReconcilingItem("in-transit", 2_000.0, "timing")]
    result = gl_tie_out(gl, sub, key_field="acct", amount_field="amt", reconciling_items=recon)
    assert result.difference == pytest.approx(2_000.0)
    # gl_total - sub_total == sum(reconciling) + unexplained
    assert result.difference == pytest.approx(
        sum(i.amount for i in result.reconciling_items) + result.unexplained_value
    )
    assert result.unexplained_value == pytest.approx(0.0)
    assert result.within_tolerance


def test_gl_tie_out_reports_unmatched_both_directions():
    gl = [{"acct": "1400", "amt": 100.0}, {"acct": "ONLY_GL", "amt": 5.0}]
    sub = [{"acct": "1400", "amt": 100.0}, {"acct": "ONLY_SUB", "amt": 7.0}]
    result = gl_tie_out(gl, sub, key_field="acct", amount_field="amt")
    assert [line.key for line in result.unmatched_gl] == ["ONLY_GL"]
    assert [line.key for line in result.unmatched_subledger] == ["ONLY_SUB"]
    assert result.matched == 1


def test_gl_tie_out_tolerance_boundary():
    gl = [{"acct": "1400", "amt": 100.0}]
    sub = [{"acct": "1400", "amt": 95.0}]
    result = gl_tie_out(gl, sub, key_field="acct", amount_field="amt", tolerance=5.0)
    assert result.within_tolerance  # unexplained 5.0 == tolerance 5.0


# -- IPE attestation ----------------------------------------------------------------- #


def test_ipe_attestation_row_count_and_control_total():
    lines = [{"sku": "a", "value": 10.0}, {"sku": "b", "value": 15.0}]
    att = ipe_attestation(
        lines, source_label="listing 2026-06-30", amount_field="value",
        tied_to="GL acct 1400", expected_total=25.0,
    )
    assert att.row_count == 2
    assert att.control_total == pytest.approx(25.0)
    assert att.tie_difference == pytest.approx(0.0)
    assert att.within_tolerance


def test_ipe_attestation_flags_untied_total():
    lines = [{"sku": "a", "value": 10.0}]
    att = ipe_attestation(
        lines, source_label="x", amount_field="value", tied_to="GL", expected_total=12.0
    )
    assert att.tie_difference == pytest.approx(-2.0)
    assert not att.within_tolerance


# -- evidence / lineage -------------------------------------------------------------- #


def test_hash_file_is_stable_and_content_sensitive(tmp_path):
    p = tmp_path / "gl.csv"
    p.write_text("acct,amt\n1400,100\n")
    h1 = hash_file(p)
    h2 = hash_file(p)
    assert h1 == h2
    p.write_text("acct,amt\n1400,101\n")
    assert hash_file(p) != h1


def test_evidence_record_captures_lineage():
    art = InputArtifact("gl.csv", "abc123", 2, ("acct", "amt"), control_total=125_000.0)
    rec = evidence_record(
        run_id="run-1",
        inputs=[art],
        params_used={"tolerable_misstatement": 50_000, "risk_of_incorrect_acceptance": 0.05},
        qa_attestation=[QaCheck("tie_out_identity", "gl-sub == recon+unexplained", True)],
        output_payload={"uml": 38_740.0},
        produced_at="2026-07-09T00:00:00Z",
    )
    assert isinstance(rec, EvidenceRecord)
    assert rec.inputs[0].sha256 == "abc123"
    assert ("risk_of_incorrect_acceptance", "0.05") in rec.params_used
    assert rec.formula_versions == (("audit_evidence", "1"),)
    assert rec.qa_attestation[0].passed
    assert "linchpin/audit_evidence" in rec.prepared_by


def test_evidence_output_hash_changes_with_payload():
    kw = dict(
        run_id="r", inputs=[], params_used={}, qa_attestation=[],
        produced_at="2026-07-09T00:00:00Z",
    )
    a = evidence_record(output_payload={"uml": 1.0}, **kw)
    b = evidence_record(output_payload={"uml": 2.0}, **kw)
    assert a.output_sha256 != b.output_sha256


def test_evidence_record_requires_run_id_and_timestamp():
    with pytest.raises(ValueError):
        evidence_record(
            run_id="", inputs=[], params_used={}, qa_attestation=[],
            output_payload={}, produced_at="2026-07-09T00:00:00Z",
        )
    with pytest.raises(ValueError):
        evidence_record(
            run_id="r", inputs=[], params_used={}, qa_attestation=[],
            output_payload={}, produced_at="",
        )
