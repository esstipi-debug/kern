"""Tests for the procedure_audit job: alias-tolerant intake, the as_of/cadence
resolution, the QA gate, and deliverables."""

from datetime import date

import pandas as pd
import pytest
from nosilo_core.guided import HANDOFF, HandoffPacket, Residual, as_executed, as_handoff

from jobs import procedure_audit_job
from src.procedures.models import ProcedureFinding, RegisterAudit
from src.procedures.register import (
    ALL_CODES,
    NO_OWNER,
    OVERDUE_REVIEW,
    REVIEW_DUE_SOON,
)

AS_OF_ISO = "2026-09-03"
SAMPLE_PATH = "data/sample_procedure_register.csv"


def _frame(rows):
    return pd.DataFrame(rows)


def _write_csv(tmp_path, name, rows):
    path = tmp_path / name
    _frame(rows).to_csv(path, index=False)
    return path


def _minimal_outcome():
    """A GuidedOutcome that honors the never-unprotected contract, for hand-built reports."""
    return as_handoff(
        "one packet to exercise the QA gate",
        [HandoffPacket(title="Do the thing", steps=["step 1"], risk_if_skipped="something bad happens")],
    )


def _bare_report(**overrides) -> RegisterAudit:
    """A RegisterAudit with every field defaulted to an empty/harmless value."""
    fields = dict(
        as_of=date(2026, 9, 3),
        n_procedures=1,
        n_retired=0,
        n_overdue=0,
        n_due_soon=0,
        n_without_owner=0,
        median_days_since_review=None,
        findings=(),
        outcome=_minimal_outcome(),
        summary="hand-built report",
        default_cadence_days=365,
        cadence_source="convention",
    )
    fields.update(overrides)
    return RegisterAudit(**fields)


# ---- prepare() ---------------------------------------------------------------------


def test_prepare_reads_spanish_headers(tmp_path):
    rows = [{
        "procedimiento": "Recepcion de mercaderia", "responsable": "Jefe de Bodega",
        "aprobador": "Gerente de Operaciones", "version": "1.0",
        "ultima_revision": "2026-06-01", "cadencia_dias": "365",
        "kpi": "fill_rate", "estado": "aprobado",
    }]
    path = _write_csv(tmp_path, "espanol.csv", rows)
    prepared = procedure_audit_job.prepare(str(path))
    assert len(prepared.records) == 1
    record = prepared.records[0]
    assert record.name == "Recepcion de mercaderia"
    assert record.owner == "Jefe de Bodega"
    assert record.approver == "Gerente de Operaciones"
    assert record.version == "1.0"
    assert record.last_reviewed_at == date(2026, 6, 1)
    assert record.review_cadence_days == 365
    assert record.linked_kpi == "fill_rate"
    assert record.status == "approved"


def test_prepare_reads_english_headers(tmp_path):
    rows = [{
        "name": "Cycle count", "owner": "Ops lead", "approver": "Ops manager",
        "version": "3", "last_reviewed_at": "2026-01-15", "status": "draft",
    }]
    path = _write_csv(tmp_path, "english.csv", rows)
    prepared = procedure_audit_job.prepare(str(path))
    (record,) = prepared.records
    assert record.name == "Cycle count"
    assert record.owner == "Ops lead"
    assert record.approver == "Ops manager"
    assert record.last_reviewed_at == date(2026, 1, 15)
    assert record.status == "draft"


def test_prepare_names_missing_required_columns(tmp_path):
    path = _write_csv(tmp_path, "sin_columnas.csv", [{"owner": "Someone"}])
    with pytest.raises(ValueError, match="name"):
        procedure_audit_job.prepare(str(path))


def test_prepare_accepts_blank_dates_as_none(tmp_path):
    rows = [
        {"name": "Con fecha", "last_reviewed_at": "2026-06-01"},
        {"name": "Sin fecha", "last_reviewed_at": ""},
    ]
    path = _write_csv(tmp_path, "fechas_en_blanco.csv", rows)
    prepared = procedure_audit_job.prepare(str(path))
    by_name = {r.name: r for r in prepared.records}
    assert by_name["Sin fecha"].last_reviewed_at is None
    assert by_name["Sin fecha"].raw_last_reviewed_at == ""
    assert prepared.n_dates_unparsed == 1


def test_prepare_parses_dayfirst_and_iso(tmp_path):
    rows = [
        {"name": "Fecha ISO", "last_reviewed_at": "2026-06-01"},
        {"name": "Fecha DD/MM ambigua", "last_reviewed_at": "05/03/2026"},
    ]
    path = _write_csv(tmp_path, "mixto.csv", rows)
    prepared = procedure_audit_job.prepare(str(path))
    by_name = {r.name: r for r in prepared.records}
    # ISO stays June 1st -- NOT swapped to January 6th (see _parse_date's docstring:
    # a naive dayfirst=True on an already-unambiguous ISO string corrupts it).
    assert by_name["Fecha ISO"].last_reviewed_at == date(2026, 6, 1)
    # A genuinely ambiguous slash date reads day-first: March 5th, not May 3rd.
    assert by_name["Fecha DD/MM ambigua"].last_reviewed_at == date(2026, 3, 5)


def test_prepare_refuses_when_no_date_parses_at_all(tmp_path):
    rows = [
        {"name": "Proc 1", "last_reviewed_at": "n/a"},
        {"name": "Proc 2", "last_reviewed_at": ""},
    ]
    path = _write_csv(tmp_path, "sin_fechas.csv", rows)
    with pytest.raises(ValueError, match="ninguna fila trae una fecha"):
        procedure_audit_job.prepare(str(path))


def test_prepare_skips_blank_name_rows_and_counts_them(tmp_path):
    rows = [
        {"name": "", "last_reviewed_at": "2026-06-01"},
        {"name": "Real", "last_reviewed_at": "2026-06-01"},
    ]
    path = _write_csv(tmp_path, "nombre_vacio.csv", rows)
    prepared = procedure_audit_job.prepare(str(path))
    assert [r.name for r in prepared.records] == ["Real"]
    assert prepared.n_dropped_blank_name == 1


# ---- run() --------------------------------------------------------------------------


def _prepared_one_row(tmp_path):
    rows = [{"name": "Proc A", "owner": "Ana", "last_reviewed_at": "2026-06-01"}]
    path = _write_csv(tmp_path, "una_fila.csv", rows)
    return procedure_audit_job.prepare(str(path))


def test_run_takes_as_of_from_params_and_records_source(tmp_path):
    prepared = _prepared_one_row(tmp_path)
    report = procedure_audit_job.run(prepared, {"as_of": AS_OF_ISO})
    assert report.as_of == date(2026, 9, 3)
    assert report.as_of_source == "param"
    assert report.records == tuple(prepared.records)
    assert not any("reloj" in r.description for r in report.outcome.residuals)


def test_run_without_as_of_uses_clock_and_adds_residual(tmp_path):
    prepared = _prepared_one_row(tmp_path)
    report = procedure_audit_job.run(prepared, {})
    assert report.as_of_source == "clock"
    assert report.as_of == date.today()
    clock_residuals = [r for r in report.outcome.residuals if "reloj" in r.description]
    assert len(clock_residuals) == 1
    assert clock_residuals[0].risk_if_skipped


def test_run_rejects_malformed_as_of(tmp_path):
    prepared = _prepared_one_row(tmp_path)
    with pytest.raises(ValueError, match="as_of"):
        procedure_audit_job.run(prepared, {"as_of": "not-a-date"})


def test_run_rejects_non_numeric_review_cadence_days(tmp_path):
    """A direct job caller (bypassing scm_agent.tools' prepare-time guard) must still
    get a ValueError naming the bad param -- see validate_params() below for the
    boundary copy of this same guard."""
    prepared = _prepared_one_row(tmp_path)
    with pytest.raises(ValueError, match="review_cadence_days"):
        procedure_audit_job.run(prepared, {"as_of": AS_OF_ISO, "review_cadence_days": "abc"})


def test_run_rejects_zero_review_cadence_days(tmp_path):
    """cadence=0 used to be accepted silently: days_overdue >= 2 * cadence is then
    trivially true for every overdue row, flipping OVERDUE_REVIEW's severity mix on
    a nonsense parameter instead of refusing it."""
    prepared = _prepared_one_row(tmp_path)
    with pytest.raises(ValueError, match="review_cadence_days"):
        procedure_audit_job.run(prepared, {"as_of": AS_OF_ISO, "review_cadence_days": 0})


def test_run_rejects_negative_review_cadence_days(tmp_path):
    prepared = _prepared_one_row(tmp_path)
    with pytest.raises(ValueError, match="review_cadence_days"):
        procedure_audit_job.run(prepared, {"as_of": AS_OF_ISO, "review_cadence_days": -30})


def test_run_rejects_negative_due_soon_days(tmp_path):
    prepared = _prepared_one_row(tmp_path)
    with pytest.raises(ValueError, match="due_soon_days"):
        procedure_audit_job.run(prepared, {"as_of": AS_OF_ISO, "due_soon_days": -1})


def test_validate_params_accepts_a_well_formed_request():
    """No exception, and the resolved values match what run() would use -- the sample
    fixture's own working params, so this stays honest about what "valid" means."""
    procedure_audit_job.validate_params(
        {"as_of": AS_OF_ISO, "review_cadence_days": 180, "due_soon_days": 15}
    )
    procedure_audit_job.validate_params(None)
    procedure_audit_job.validate_params({})


def test_validate_params_rejects_malformed_as_of():
    with pytest.raises(ValueError, match="as_of"):
        procedure_audit_job.validate_params({"as_of": "03/09/2026"})


def test_validate_params_rejects_non_numeric_review_cadence_days():
    with pytest.raises(ValueError, match="review_cadence_days"):
        procedure_audit_job.validate_params({"review_cadence_days": "abc"})


def test_validate_params_rejects_zero_or_negative_review_cadence_days():
    with pytest.raises(ValueError, match="review_cadence_days"):
        procedure_audit_job.validate_params({"review_cadence_days": 0})
    with pytest.raises(ValueError, match="review_cadence_days"):
        procedure_audit_job.validate_params({"review_cadence_days": -1})


def test_validate_params_rejects_zero_or_negative_due_soon_days():
    with pytest.raises(ValueError, match="due_soon_days"):
        procedure_audit_job.validate_params({"due_soon_days": 0})
    with pytest.raises(ValueError, match="due_soon_days"):
        procedure_audit_job.validate_params({"due_soon_days": -5})


def test_run_adds_residual_for_dropped_blank_name_rows(tmp_path):
    rows = [
        {"name": "", "last_reviewed_at": "2026-06-01"},
        {"name": "Real", "last_reviewed_at": "2026-06-01"},
    ]
    path = _write_csv(tmp_path, "con_descarte.csv", rows)
    prepared = procedure_audit_job.prepare(str(path))
    report = procedure_audit_job.run(prepared, {"as_of": AS_OF_ISO})
    dropped_residuals = [r for r in report.outcome.residuals if "descartaron" in r.description]
    assert len(dropped_residuals) == 1
    assert dropped_residuals[0].risk_if_skipped


# ---- verify() -------------------------------------------------------------------------


def test_verify_passes_on_the_sample_fixture(tmp_path):
    prepared = procedure_audit_job.prepare(SAMPLE_PATH)
    report = procedure_audit_job.run(prepared, {"as_of": AS_OF_ISO})
    assert procedure_audit_job.verify(report) == []


def test_verify_flags_overdue_without_parsed_date():
    bad_finding = ProcedureFinding(
        procedure_id="X", name="Hand-built", code=OVERDUE_REVIEW, severity="high",
        state="no_cumple", detail="hand-built with no due_date", evidence=("bogus",),
        owner="Ana", due_date=None, days_overdue=None,
    )
    report = _bare_report(findings=(bad_finding,), n_overdue=1, outcome=as_handoff(
        "one overdue finding to exercise the QA gate",
        [HandoffPacket(title="Revisar", steps=["revisar Hand-built"], risk_if_skipped="riesgo")],
    ))
    issues = procedure_audit_job.verify(report)
    assert any("without a parsed date" in i for i in issues)


def test_verify_flags_executed():
    report = _bare_report(outcome=as_executed("nothing to hand off"))
    issues = procedure_audit_job.verify(report)
    assert any("EXECUTED" in i for i in issues)


def test_verify_flags_handoff_without_risk_if_skipped():
    """Ruling: verify_guided only checks Residual.risk_if_skipped, never a
    HandoffPacket's -- this job's own verify() must close that gap."""
    outcome = as_handoff(
        "one packet missing its risk_if_skipped",
        [HandoffPacket(title="Sin riesgo declarado", steps=["hacer algo"], risk_if_skipped="")],
    )
    report = _bare_report(outcome=outcome)
    issues = procedure_audit_job.verify(report)
    assert any("risk_if_skipped" in i and "handoff" in i.lower() for i in issues)


def test_verify_flags_residual_without_risk_if_skipped():
    outcome = as_handoff(
        "one packet, one bare residual",
        [HandoffPacket(title="Hacer algo", steps=["paso 1"], risk_if_skipped="riesgo")],
        residuals=[Residual(description="algo sin riesgo declarado", risk_if_skipped="")],
    )
    report = _bare_report(outcome=outcome)
    issues = procedure_audit_job.verify(report)
    assert any("risk_if_skipped" in i for i in issues)


# ---- write_operational() ---------------------------------------------------------------


def test_write_operational_csv_has_owner_and_due_date_columns_populated(tmp_path):
    rows = [
        {"name": "Proc vencido", "owner": "Ana Diaz", "last_reviewed_at": "2024-01-01",
         "review_cadence_days": "365"},
    ]
    path = _write_csv(tmp_path, "vencido.csv", rows)
    prepared = procedure_audit_job.prepare(str(path))
    report = procedure_audit_job.run(prepared, {"as_of": AS_OF_ISO})
    assert report.n_overdue == 1

    paths = procedure_audit_job.write_operational(report, tmp_path / "out", client="Acme")
    findings_body = paths["csv"].read_text(encoding="utf-8")
    assert "Ana Diaz" in findings_body
    assert OVERDUE_REVIEW in findings_body
    # due_date is next_review = last_reviewed_at + 365 days; 2024 is a leap year,
    # so 2024-01-01 + 365 days lands on 2024-12-31, not 2025-01-01 -- a parsed ISO
    # date either way, not an empty cell.
    assert "2024-12-31" in findings_body

    calendar_body = paths["calendar_csv"].read_text(encoding="utf-8")
    assert "Proc vencido" in calendar_body


def test_write_operational_findings_csv_evidence_never_prints_the_python_none_literal(tmp_path):
    """Minor 8: the value COLUMNS were already blank for sin_dato/missing data (that
    constraint is unchanged) -- but the 'evidence' column's free-text sentence used to
    interpolate a bare Python None (e.g. 'approved_at=None') whenever a NO_APPROVAL or
    NO_REVIEW_DATE finding's underlying date was missing. The sample fixture's own
    rows (aprobado with no aprobador/fecha_aprobacion, and a blank ultima_revision)
    cover both cases -- see src/procedures/register.py::_evidence_date."""
    prepared = procedure_audit_job.prepare(SAMPLE_PATH)
    report = procedure_audit_job.run(prepared, {"as_of": AS_OF_ISO})
    paths = procedure_audit_job.write_operational(report, tmp_path / "out", client="Acme")
    findings_body = paths["csv"].read_text(encoding="utf-8")
    assert "None" not in findings_body
    assert "sin dato" in findings_body


def test_write_operational_calendar_excludes_retired_rows(tmp_path):
    rows = [
        {"name": "Activo", "owner": "Ana", "last_reviewed_at": "2026-06-01"},
        {"name": "Retirado", "owner": "Beto", "last_reviewed_at": "2020-01-01", "status": "retirado"},
    ]
    path = _write_csv(tmp_path, "con_retirado.csv", rows)
    prepared = procedure_audit_job.prepare(str(path))
    report = procedure_audit_job.run(prepared, {"as_of": AS_OF_ISO})
    paths = procedure_audit_job.write_operational(report, tmp_path / "out")
    calendar_body = paths["calendar_csv"].read_text(encoding="utf-8")
    assert "Activo" in calendar_body
    assert "Retirado" not in calendar_body


# ---- build_deck() -----------------------------------------------------------------------


def test_deck_writes_md_and_xlsx(tmp_path):
    rows = [{"name": "Proc sin dueno", "last_reviewed_at": "2026-06-01"}]
    path = _write_csv(tmp_path, "deck.csv", rows)
    prepared = procedure_audit_job.prepare(str(path))
    report = procedure_audit_job.run(prepared, {"as_of": AS_OF_ISO})
    deck = procedure_audit_job.build_deck(report, client="Acme", citations=("iso_9000",))
    assert deck.client == "Acme"
    assert deck.options == tuple(report.outcome.options)
    assert "iso_9000" in deck.citations

    written = deck.write_all(tmp_path / "deck_out")
    assert written["report"].exists()
    assert written["workbook"].exists()
    assert "Acme" in written["report"].read_text(encoding="utf-8")


# ---- fixture coverage ----------------------------------------------------------------


def test_sample_fixture_covers_every_finding_code():
    prepared = procedure_audit_job.prepare(SAMPLE_PATH)
    report = procedure_audit_job.run(prepared, {"as_of": AS_OF_ISO})
    assert {f.code for f in report.findings} == set(ALL_CODES)
    # the fixture's own dedicated rows for OVERDUE_REVIEW / NO_OWNER / REVIEW_DUE_SOON
    assert report.n_overdue >= 2
    assert report.n_without_owner >= 1
    codes_present = {f.code for f in report.findings}
    assert OVERDUE_REVIEW in codes_present
    assert NO_OWNER in codes_present
    assert REVIEW_DUE_SOON in codes_present
    assert report.outcome.status == HANDOFF
