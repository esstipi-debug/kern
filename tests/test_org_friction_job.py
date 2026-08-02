"""Tests for the org_friction job: alias-tolerant intake, history joining, the QA
gate, and deliverables."""

import pandas as pd
import pytest

from jobs import org_friction_job
from nosilo_core.guided import HANDOFF, OPTIONS, ExecutionOption, as_options
from src.org_friction import GOODHART_RATCHET, FrictionFinding, FrictionReport


def _frame(rows):
    return pd.DataFrame(rows)


def _write_csv(tmp_path, name, rows):
    path = tmp_path / name
    _frame(rows).to_csv(path, index=False)
    return path


def test_prepare_reads_english_headers(tmp_path):
    rows = [
        {"name": "Fill rate", "official_value": 0.95, "shadow_value": 0.70, "owner": "Ops"},
        {"name": "On-time delivery", "official_value": 0.98, "shadow_value": "", "owner": ""},
    ]
    path = _write_csv(tmp_path, "control_points.csv", rows)
    control_points = org_friction_job.prepare(str(path))
    assert len(control_points) == 2
    fill_rate = next(c for c in control_points if c.name == "Fill rate")
    assert fill_rate.official_value == 0.95
    assert fill_rate.shadow_value == 0.70
    assert fill_rate.owner == "Ops"


def test_prepare_reads_spanish_headers(tmp_path):
    rows = [{
        "nombre": "Tasa de llenado", "valor_oficial": 0.95, "valor_sombra": 0.60,
        "responsable": "", "tiene_workaround": "si", "clasificacion": "",
    }]
    path = _write_csv(tmp_path, "puntos_control.csv", rows)
    (cp,) = org_friction_job.prepare(str(path))
    assert cp.name == "Tasa de llenado"
    assert cp.official_value == 0.95
    assert cp.shadow_value == 0.60
    assert cp.owner == ""
    assert cp.workaround_present is True


def test_prepare_tolerates_a_minimal_file_with_only_a_name_column(tmp_path):
    path = _write_csv(tmp_path, "minimo.csv", [{"name": "Bare KPI"}])
    (cp,) = org_friction_job.prepare(str(path))
    assert cp.official_value is None and cp.shadow_value is None
    assert cp.owner == "" and cp.workaround_present is False


def test_prepare_names_the_missing_required_column(tmp_path):
    path = _write_csv(tmp_path, "sin_nombre.csv", [{"official_value": 0.9}])
    with pytest.raises(ValueError, match="name"):
        org_friction_job.prepare(str(path))


def test_prepare_skips_rows_with_a_blank_name_but_keeps_the_rest(tmp_path):
    rows = [{"name": "", "official_value": 0.9}, {"name": "Real KPI", "official_value": 0.9}]
    path = _write_csv(tmp_path, "fila_vacia.csv", rows)
    control_points = org_friction_job.prepare(str(path))
    assert [c.name for c in control_points] == ["Real KPI"]


def test_prepare_raises_when_every_row_has_a_blank_name(tmp_path):
    path = _write_csv(tmp_path, "todo_vacio.csv", [{"name": "", "official_value": 0.9}])
    with pytest.raises(ValueError, match="no usable control points"):
        org_friction_job.prepare(str(path))


@pytest.mark.parametrize("token,expected", [
    ("true", True), ("Yes", True), ("SI", True), ("1", True), ("x", True),
    ("false", False), ("no", False), ("", False),
])
def test_prepare_parses_workaround_present_leniently(tmp_path, token, expected):
    path = _write_csv(tmp_path, "bool.csv", [{"name": "Data load", "workaround_present": token}])
    (cp,) = org_friction_job.prepare(str(path))
    assert cp.workaround_present is expected


def test_prepare_normalizes_workaround_classification(tmp_path):
    rows = [
        {"name": "A", "workaround_present": "yes", "workaround_classification": "Esencial"},
        {"name": "B", "workaround_present": "yes", "workaround_classification": "parche"},
        {"name": "C", "workaround_present": "yes", "workaround_classification": "unrecognised"},
    ]
    path = _write_csv(tmp_path, "clasif.csv", rows)
    by_name = {c.name: c for c in org_friction_job.prepare(str(path))}
    assert by_name["A"].workaround_classification == "essential"
    assert by_name["B"].workaround_classification == "hindrance"
    assert by_name["C"].workaround_classification == ""  # unrecognised -> unclassified, never guessed


def test_history_file_is_joined_by_control_point_name(tmp_path):
    cp_path = _write_csv(tmp_path, "control_points.csv", [{"name": "On-time delivery", "owner": "x"}])
    history_rows = [
        {"name": "On-time delivery", "period": "2026-01", "actual": 0.97, "target": 0.95},
        {"name": "On-time delivery", "period": "2026-02", "actual": 0.94, "target": 0.98},
        {"name": "Unrelated KPI", "period": "2026-01", "actual": 1.0, "target": 1.0},
    ]
    history_path = _write_csv(tmp_path, "history.csv", history_rows)
    (cp,) = org_friction_job.prepare(str(cp_path), {"history_path": str(history_path)})
    assert [tp.period for tp in cp.history] == ["2026-01", "2026-02"]
    report = org_friction_job.run([cp])
    assert any(f.pattern == GOODHART_RATCHET for f in report.findings)


def test_history_file_names_its_own_missing_column(tmp_path):
    cp_path = _write_csv(tmp_path, "control_points.csv", [{"name": "A"}])
    history_path = _write_csv(tmp_path, "history_sin_target.csv", [
        {"name": "A", "period": "2026-01", "actual": 0.9},
    ])
    with pytest.raises(ValueError, match="target"):
        org_friction_job.prepare(str(cp_path), {"history_path": str(history_path)})


def test_run_and_verify_pass_on_healthy_intake(tmp_path):
    rows = [
        {"name": "Fill rate", "official_value": 0.95, "shadow_value": 0.60, "owner": "Ops"},
        {"name": "Cycle count accuracy", "owner": ""},
    ]
    path = _write_csv(tmp_path, "sano.csv", rows)
    report = org_friction_job.run(org_friction_job.prepare(str(path)))
    assert org_friction_job.verify(report) == []
    assert report.outcome.status == OPTIONS


def test_verify_flags_a_hand_built_finding_with_no_evidence():
    bad_finding = FrictionFinding(
        control_point="X", pattern="hidden_factory", severity="high",
        detail="hand-built with no evidence", evidence=(),
    )
    outcome = as_options("one finding", [
        ExecutionOption(label="Triage X", summary="x", score=3.0, action="interview:X:hidden_factory"),
    ])
    report = FrictionReport(
        n_control_points=1, n_flagged=1, findings=(bad_finding,), outcome=outcome,
        summary="hand-built report to exercise the evidence gate",
    )
    issues = org_friction_job.verify(report)
    assert any("no evidence" in i for i in issues)


def test_no_data_at_all_yields_handoff(tmp_path):
    path = _write_csv(tmp_path, "vacio.csv", [{"name": "Untouched metric", "owner": "Someone"}])
    report = org_friction_job.run(org_friction_job.prepare(str(path)))
    assert report.outcome.status == HANDOFF
    assert org_friction_job.verify(report) == []


def test_write_operational_emits_one_row_per_finding(tmp_path):
    rows = [{"name": "Fill rate", "official_value": 0.95, "shadow_value": 0.5, "owner": ""}]
    path = _write_csv(tmp_path, "wo.csv", rows)
    report = org_friction_job.run(org_friction_job.prepare(str(path)))
    paths = org_friction_job.write_operational(report, tmp_path / "out", client="Acme")
    body = paths["csv"].read_text(encoding="utf-8")
    assert "hidden_factory" in body
    assert "evidence" in body


def test_build_deck_names_the_top_finding_and_flags_residual(tmp_path):
    rows = [{"name": "Fill rate", "official_value": 0.95, "shadow_value": 0.5, "owner": ""}]
    path = _write_csv(tmp_path, "deck.csv", rows)
    report = org_friction_job.run(org_friction_job.prepare(str(path)))
    deck = org_friction_job.build_deck(report, client="Acme", citations=("Feigenbaum, Hidden Factory",))
    assert deck.client == "Acme"
    assert deck.options == tuple(report.outcome.options)
    assert "Feigenbaum, Hidden Factory" in deck.citations
    assert "Fill rate" in deck.findings[0].title
    assert "confirm" in deck.residual


def test_build_deck_on_a_handoff_report_still_produces_a_finding(tmp_path):
    path = _write_csv(tmp_path, "handoff.csv", [{"name": "Untouched", "owner": "Someone"}])
    report = org_friction_job.run(org_friction_job.prepare(str(path)))
    deck = org_friction_job.build_deck(report)
    assert deck.findings
    assert "triangulated" in deck.findings[0].title.lower()
