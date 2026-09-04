"""Tests for the pure procedures register-audit engine: detectors, ranking, HANDOFF verdict."""
from datetime import date, timedelta
from pathlib import Path

import pytest
from nosilo_core.guided import HANDOFF, verify_guided

from src.procedures.models import ProcedureRecord
from src.procedures.register import (
    DEFAULT_REVIEW_CADENCE_DAYS,
    DUE_SOON_DAYS,
    NO_APPROVAL,
    NO_KPI_LINK,
    NO_OWNER,
    NO_REVIEW_DATE,
    OVERDUE_REVIEW,
    OWNER_IS_APPROVER,
    REVIEW_DUE_SOON,
    STATE_NO_CUMPLE,
    STATE_SIN_DATO,
    UNVERSIONED,
    audit_register,
    days_overdue,
    detect,
    next_review_date,
)

AS_OF = date(2026, 9, 3)


def _record(
    name="Recepcion de mercaderia",
    *,
    owner="Ana",
    approver="Beto",
    version="1",
    linked_kpi="fill_rate",
    status="",
    last_reviewed_at=AS_OF,
    review_cadence_days=None,
    approved_at=None,
    procedure_id="",
) -> ProcedureRecord:
    """A fully compliant row by default: only the field(s) a test overrides misbehave."""
    return ProcedureRecord(
        name=name,
        procedure_id=procedure_id,
        owner=owner,
        approver=approver,
        version=version,
        linked_kpi=linked_kpi,
        status=status,
        last_reviewed_at=last_reviewed_at,
        review_cadence_days=review_cadence_days,
        approved_at=approved_at,
    )


def test_clean_record_produces_no_findings():
    assert detect([_record()], as_of=AS_OF) == ()


# ---- overdue / due soon / missing date -----------------------------------------


def test_overdue_fires_when_days_since_review_exceed_cadence():
    record = _record(last_reviewed_at=AS_OF - timedelta(days=400))
    findings = detect([record], as_of=AS_OF)
    assert len(findings) == 1
    finding = findings[0]
    assert finding.code == OVERDUE_REVIEW
    assert finding.state == STATE_NO_CUMPLE
    assert finding.severity == "medium"  # has an owner, and 35 < 2 * 365
    assert finding.days_overdue == 35
    assert finding.due_date == AS_OF - timedelta(days=35)
    assert finding.owner == "Ana"


def test_overdue_uses_per_row_cadence_before_the_default():
    with_row_cadence = _record(last_reviewed_at=AS_OF - timedelta(days=40), review_cadence_days=30)
    (finding,) = detect([with_row_cadence], as_of=AS_OF, default_cadence_days=365)
    assert finding.code == OVERDUE_REVIEW
    assert finding.days_overdue == 10

    # sanity: the very same last_reviewed_at is NOT overdue under the default alone
    without_row_cadence = _record(last_reviewed_at=AS_OF - timedelta(days=40))
    assert detect([without_row_cadence], as_of=AS_OF, default_cadence_days=365) == ()


def test_missing_review_date_is_sin_dato_never_overdue_and_never_today():
    record = _record(last_reviewed_at=None)
    (finding,) = detect([record], as_of=AS_OF)
    assert finding.code == NO_REVIEW_DATE
    assert finding.state == STATE_SIN_DATO
    assert finding.severity == "low"
    assert finding.due_date is None
    assert finding.days_overdue is None
    # the engine never guesses "today" (or any date) as a stand-in for a missing one
    assert next_review_date(record, DEFAULT_REVIEW_CADENCE_DAYS) is None
    assert days_overdue(record, AS_OF, DEFAULT_REVIEW_CADENCE_DAYS) is None


def test_due_soon_fires_inside_thirty_days_only():
    at_boundary = _record(last_reviewed_at=AS_OF - timedelta(days=DEFAULT_REVIEW_CADENCE_DAYS - DUE_SOON_DAYS))
    just_outside = _record(
        last_reviewed_at=AS_OF - timedelta(days=DEFAULT_REVIEW_CADENCE_DAYS - DUE_SOON_DAYS - 1)
    )
    due_today = _record(last_reviewed_at=AS_OF - timedelta(days=DEFAULT_REVIEW_CADENCE_DAYS))

    (boundary_finding,) = detect([at_boundary], as_of=AS_OF)
    assert boundary_finding.code == REVIEW_DUE_SOON
    assert boundary_finding.days_overdue == -DUE_SOON_DAYS
    assert boundary_finding.severity == "low"

    assert detect([just_outside], as_of=AS_OF) == ()

    (today_finding,) = detect([due_today], as_of=AS_OF)
    assert today_finding.code == REVIEW_DUE_SOON
    assert today_finding.days_overdue == 0


# ---- owner / approver / approval / version / kpi -------------------------------


def test_no_owner_fires_on_blank_or_whitespace_owner():
    for owner in ("", "   "):
        findings = [f for f in detect([_record(owner=owner)], as_of=AS_OF) if f.code == NO_OWNER]
        assert len(findings) == 1
        assert findings[0].severity == "medium"
        assert findings[0].state == STATE_NO_CUMPLE


def test_owner_equal_to_approver_fires_case_insensitive():
    record = _record(owner="Ana Diaz", approver="ANA diaz")
    findings = [f for f in detect([record], as_of=AS_OF) if f.code == OWNER_IS_APPROVER]
    assert len(findings) == 1
    assert findings[0].severity == "medium"
    assert findings[0].state == STATE_NO_CUMPLE


def test_no_approval_fires_only_for_status_approved():
    approved_missing_data = _record(status="approved", approver="", approved_at=None)
    approved_with_data = _record(status="approved", approver="Beto", approved_at=AS_OF)
    draft_missing_data = _record(status="draft", approver="", approved_at=None)

    findings = [f for f in detect([approved_missing_data], as_of=AS_OF) if f.code == NO_APPROVAL]
    assert len(findings) == 1
    assert findings[0].severity == "high"

    assert not any(f.code == NO_APPROVAL for f in detect([approved_with_data], as_of=AS_OF))
    assert not any(f.code == NO_APPROVAL for f in detect([draft_missing_data], as_of=AS_OF))


def test_unversioned_and_no_kpi_link_are_low():
    findings = detect([_record(version="", linked_kpi="")], as_of=AS_OF)
    by_code = {f.code: f for f in findings}
    assert by_code[UNVERSIONED].severity == "low"
    assert by_code[UNVERSIONED].state == STATE_NO_CUMPLE
    assert by_code[NO_KPI_LINK].severity == "low"
    assert by_code[NO_KPI_LINK].state == STATE_NO_CUMPLE


# ---- retired rows ---------------------------------------------------------------


def test_retired_procedures_skip_vigencia_checks():
    retired = ProcedureRecord(
        name="Viejo proceso",
        status="retired",
        owner="",
        approver="",
        version="",
        linked_kpi="",
        last_reviewed_at=AS_OF - timedelta(days=1000),  # would be badly overdue if active
    )
    assert detect([retired], as_of=AS_OF) == ()


# ---- ranking / evidence -----------------------------------------------------------


def test_ranking_overdue_without_owner_first():
    overdue_without_owner = _record(
        name="Z ultimo alfabeticamente", owner="", last_reviewed_at=AS_OF - timedelta(days=400)
    )
    overdue_with_owner = _record(
        name="A primero alfabeticamente", owner="Ana", last_reviewed_at=AS_OF - timedelta(days=400)
    )
    findings = detect([overdue_without_owner, overdue_with_owner], as_of=AS_OF)
    overdue_findings = [f for f in findings if f.code == OVERDUE_REVIEW]
    assert [f.name for f in overdue_findings] == ["Z ultimo alfabeticamente", "A primero alfabeticamente"]
    assert overdue_findings[0].severity == "high"
    assert overdue_findings[1].severity == "medium"


def test_every_finding_carries_evidence():
    records = [
        _record(name="Proc 1", last_reviewed_at=None),
        _record(name="Proc 2", owner="", version="", linked_kpi=""),
        _record(name="Proc 3", status="approved", approver="", approved_at=None),
        _record(name="Proc 4", owner="Ana", approver="ana"),
        _record(name="Proc 5", last_reviewed_at=AS_OF - timedelta(days=400)),
    ]
    findings = detect(records, as_of=AS_OF)
    assert findings  # sanity: this scenario does produce findings
    assert all(isinstance(f.evidence, tuple) and f.evidence for f in findings)


# ---- audit_register / GuidedOutcome ----------------------------------------------


def test_outcome_is_always_handoff_never_executed():
    scenarios = [
        [_record()],  # fully compliant -> no findings at all
        [_record(last_reviewed_at=AS_OF - timedelta(days=400))],  # overdue
        [_record(owner="")],  # no owner
        [_record(last_reviewed_at=AS_OF - timedelta(days=DEFAULT_REVIEW_CADENCE_DAYS))],  # due soon
    ]
    for records in scenarios:
        audit = audit_register(records, as_of=AS_OF)
        assert audit.outcome.status == HANDOFF
        assert verify_guided(audit.outcome) == []


def test_handoff_has_one_step_per_overdue_with_owner_and_date():
    records = [
        _record(name="Proc A", owner="Ana", last_reviewed_at=AS_OF - timedelta(days=400)),
        _record(name="Proc B", owner="Beto", last_reviewed_at=AS_OF - timedelta(days=500)),
    ]
    audit = audit_register(records, as_of=AS_OF)
    overdue_packet = audit.outcome.handoffs[0]
    assert len(overdue_packet.steps) == 2
    joined_steps = " | ".join(overdue_packet.steps)
    for record in records:
        assert record.name in joined_steps
        assert record.owner in joined_steps
    assert overdue_packet.deadline
    assert overdue_packet.risk_if_skipped
    assert len(overdue_packet.data["items"]) == 2


def test_handoff_step_says_sin_asignar_when_no_owner():
    records = [_record(name="Proc sin dueno", owner="", last_reviewed_at=AS_OF - timedelta(days=400))]
    audit = audit_register(records, as_of=AS_OF)
    overdue_packet = audit.outcome.handoffs[0]
    assert "SIN ASIGNAR" in overdue_packet.steps[0]
    assert overdue_packet.risk_if_skipped


def test_empty_register_raises_value_error():
    with pytest.raises(ValueError, match="empty register"):
        audit_register([], as_of=AS_OF)


def test_median_days_since_review_is_none_when_no_dates():
    records = [
        _record(name="Proc 1", last_reviewed_at=None),
        _record(name="Proc 2 retirado", status="retired", last_reviewed_at=AS_OF - timedelta(days=10)),
    ]
    audit = audit_register(records, as_of=AS_OF)
    assert audit.median_days_since_review is None


def test_median_days_since_review_computes_correctly():
    records = [
        _record(name="Proc 1", last_reviewed_at=AS_OF - timedelta(days=10)),
        _record(name="Proc 2", last_reviewed_at=AS_OF - timedelta(days=20)),
        _record(name="Proc 3", last_reviewed_at=AS_OF - timedelta(days=30)),
    ]
    audit = audit_register(records, as_of=AS_OF)
    assert audit.median_days_since_review == 20.0


def test_registeraudit_carries_as_of_and_cadence_transport_defaults():
    default_audit = audit_register([_record()], as_of=AS_OF)
    assert default_audit.cadence_source == "convention"
    assert default_audit.as_of_source == "param"
    assert default_audit.records == ()

    param_audit = audit_register([_record()], as_of=AS_OF, cadence_source="param")
    assert param_audit.cadence_source == "param"


def test_engine_is_clock_free():
    src_dir = Path(__file__).resolve().parent.parent / "src" / "procedures"
    for filename in ("register.py", "models.py"):
        text = (src_dir / filename).read_text(encoding="utf-8")
        assert "today()" not in text
        assert "datetime.now" not in text
        assert "import pandas" not in text
