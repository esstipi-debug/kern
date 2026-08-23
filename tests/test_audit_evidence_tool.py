"""Wiring tests for the audit-evidence capability: routing, the never-EXECUTED contract, and
the property that makes the wiring worth having -- the output moves when the coverage moves.

The last one is the point. `src/audit_evidence.py` had 30 passing tests and no consumer for
as long as it has existed; cabling it is only an improvement if what reaches the client
changes with what was actually checked. A deck that prints the same confidence whether or
not the listing was ever tied to a control total is the defect
`fix/confianza-refleja-cobertura` was opened for in the sourcing job, and it is worse in this
vertical, whose buyer employs a reviewer paid to find precisely that.
"""

from __future__ import annotations

import pytest
from nosilo_core import llm
from nosilo_core.guided import EXECUTED, as_executed
from nosilo_core.types import JobRequest

from jobs import audit_evidence_job as job
from jobs.qa import audit_evidence_gate
from scm_agent import intent, tools


def _write(path, rows):
    path.write_text("sku,amount\n" + "\n".join(f"{k},{v}" for k, v in rows), encoding="utf-8")
    return path


def _listing(tmp_path, name="listing.csv", n=20):
    return _write(tmp_path / name, [(f"S{i}", 100 * i) for i in range(1, n + 1)])


def _total(n=20):
    return float(sum(100 * i for i in range(1, n + 1)))


# -- routing ------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "phrase",
    [
        "monetary unit sampling",
        "mus sample",
        "audit sampling",
        "audit workpaper",
        "audit evidence",
        "substantive testing",
        "test of details",
        "build the audit workpaper and audit evidence for this listing",
        "tie out the inventory listing to the general ledger trial balance",
        # unique to this tool, so it wins outright; "gl reconciliation" does not -- see below
        "trial balance",
    ],
)
def test_registry_routes_audit_phrases_to_audit_evidence(phrase):
    reg = tools.build_default_registry()
    assert intent.classify(phrase, reg, llm.RulesFallback()).job_type == "audit_evidence"


@pytest.mark.parametrize(
    "phrase",
    ["reconciliation", "book vs physical", "acceptance sampling", "aql", "cycle count",
     "sampling plan", "inventory count"],
)
def test_audit_tool_does_not_steal_its_neighbours_phrases(phrase):
    """The collision guard the design doc asked for.

    This asserts only what is this tool's business -- that it does not answer to a neighbour's
    phrase. Which neighbour wins is deliberately NOT pinned here: "cycle count" currently
    resolves to `reconciliation`, and pinning that would make this file fail on a change it
    has no stake in.
    """
    reg = tools.build_default_registry()
    assert intent.classify(phrase, reg, llm.RulesFallback()).job_type != "audit_evidence"


@pytest.mark.parametrize("phrase", ["gl reconciliation"])
def test_phrases_shared_with_the_ira_tool_ask_rather_than_guess(phrase):
    """A documented ambiguity, pinned as a fact rather than tuned away.

    The design doc gave `audit_evidence` "gl reconciliation" and "trial balance" while leaving
    bare "reconciliation" with the IRA tool. In practice the whole-phrase scorer ties on these
    and the classifier returns no job type, i.e. it asks. That is the better failure -- a GL
    tie-out and a book-vs-physical count are different procedures and guessing between them
    silently would be worse than a question. Raising this tool's score until it won would
    take phrases away from a tool that has answered to them for longer.
    """
    reg = tools.build_default_registry()
    assert intent.classify(phrase, reg, llm.RulesFallback()).job_type is None


def test_tool_is_registered_with_a_declared_data_contract():
    tool = next(t for t in tools.build_default_registry().list() if t.key == "audit_evidence")
    assert tool.requires_data is True
    # tests/test_tool_data_contract.py::UNDECLARED_TOOLS never grows: a new data tool
    # declares its columns on the way in.
    assert tool.required_columns


# -- the never-EXECUTED contract, at all three layers -------------------------------- #


def test_prepare_refuses_to_default_materiality(tmp_path):
    """A default nobody supplied is not a measurement -- and materiality is the auditor's."""
    with pytest.raises(ValueError, match="tolerable_misstatement is required"):
        job.prepare(str(_listing(tmp_path)), {})


def test_tool_asks_for_materiality_instead_of_guessing(tmp_path):
    tool = next(t for t in tools.build_default_registry().list() if t.key == "audit_evidence")
    prepared = tool.prepare(
        JobRequest(brief="audit sampling", data_path=str(_listing(tmp_path)), params={}),
        llm.RulesFallback(),
    )
    assert prepared.status == "needs_data"
    assert any("tolerable_misstatement" in m for m in prepared.messages)


def test_outcome_is_never_executed_on_the_happy_path(tmp_path):
    """The happy path is where an EXECUTED would slip through unnoticed."""
    payload = job.prepare(str(_listing(tmp_path)), {"tolerable_misstatement": 2000})
    report = job.run(payload, expected_total=_total(), run_id="R", produced_at="2026-08-23T00:00:00Z")
    assert report.outcome.status != EXECUTED
    assert job.verify(report) == []


def test_the_gate_rejects_an_executed_outcome():
    """The bait that has to fall: an EXECUTED audit outcome is a QA failure even when it is
    otherwise well formed, so the orchestrator's "QA fails => no deliverable" gate stops it."""
    issues = audit_evidence_gate(as_executed("workpaper complete", confidence=1.0))
    assert any("never be EXECUTED" in i for i in issues)


def test_verify_fails_when_the_report_carries_an_executed_outcome(tmp_path):
    """Layer two, reached through the job rather than the gate directly: if a future edit
    gives `_outcome` an as_executed branch, verify() refuses the report."""
    import dataclasses

    payload = job.prepare(str(_listing(tmp_path)), {"tolerable_misstatement": 2000})
    report = job.run(payload, expected_total=_total(), run_id="R", produced_at="2026-08-23T00:00:00Z")
    mutated = dataclasses.replace(report, outcome=as_executed(report.summary))
    assert any("never be EXECUTED" in i for i in job.verify(mutated))


def test_the_tool_surfaces_the_reports_own_outcome(tmp_path):
    """Layer three. The orchestrator fills JobResult.guided from `tool.options` and falls back
    to as_executed(...) when it is None, so an unset `options` would breach the contract on
    success. One source of truth: what verify() gates is what the caller receives."""
    tool = next(t for t in tools.build_default_registry().list() if t.key == "audit_evidence")
    assert tool.options is not None
    payload = job.prepare(str(_listing(tmp_path)), {"tolerable_misstatement": 2000})
    report = job.run(payload, expected_total=_total(), run_id="R", produced_at="2026-08-23T00:00:00Z")
    assert tool.options(report) is report.outcome


# -- the value moves with the coverage ----------------------------------------------- #


def test_confidence_falls_when_nothing_was_tied_out(tmp_path):
    """The differential test. Same listing, same materiality; the only difference is whether
    the caller supplied a GL export and an independent control total."""
    listing = _listing(tmp_path)
    gl = _write(tmp_path / "gl.csv", [(f"S{i}", 100 * i) for i in range(1, 21)])

    thin = job.run(
        job.prepare(str(listing), {"tolerable_misstatement": 2000}),
        run_id="A", produced_at="2026-08-23T00:00:00Z",
    )
    full = job.run(
        job.prepare(str(listing), {"tolerable_misstatement": 2000, "gl_path": str(gl)}),
        expected_total=_total(), run_id="B", produced_at="2026-08-23T00:00:00Z",
    )

    assert set(thin.coverage_gaps) == {"untied_ipe", "no_subledger_tie_out"}
    assert full.coverage_gaps == ()
    assert thin.outcome.confidence < full.outcome.confidence
    assert job.build_deck(thin).confidence < job.build_deck(full).confidence
    # Both are still valid deliverables -- the thin one is honest about being thin, not broken.
    assert job.verify(thin) == [] and job.verify(full) == []


def test_the_callers_confidence_is_a_ceiling_not_an_override(tmp_path):
    """scm_agent passes a flat number in and cannot know which comparisons this run made."""
    thin = job.run(
        job.prepare(str(_listing(tmp_path)), {"tolerable_misstatement": 2000}),
        run_id="A", produced_at="2026-08-23T00:00:00Z",
    )
    assert job.build_deck(thin, confidence=0.95).confidence == pytest.approx(0.4)
    assert job.build_deck(thin, confidence=0.2).confidence == pytest.approx(0.2)


def test_an_untied_listing_is_reported_as_not_tested_not_as_a_pass(tmp_path):
    """The control case: the deck must say the AS 1105.10 test was not performed, and must not
    print a tolerance it never measured."""
    thin = job.run(
        job.prepare(str(_listing(tmp_path)), {"tolerable_misstatement": 2000}),
        run_id="A", produced_at="2026-08-23T00:00:00Z",
    )
    assert thin.ipe.tied is False and thin.ipe.within_tolerance is False
    deck = job.build_deck(thin)
    titles = " | ".join(f.title for f in deck.findings)
    assert "not tested" in titles
    attested = {c.name: c for c in thin.evidence.qa_attestation}
    assert attested["ipe_completeness_accuracy"].passed is False
    assert "NOT PERFORMED" in attested["ipe_completeness_accuracy"].compared


def test_sample_size_responds_to_materiality(tmp_path):
    """A tighter tolerable misstatement must buy a bigger sample, or the plan is decorative."""
    listing = _listing(tmp_path)
    loose = job.run(
        job.prepare(str(listing), {"tolerable_misstatement": 8000}),
        run_id="A", produced_at="2026-08-23T00:00:00Z",
    )
    tight = job.run(
        job.prepare(str(listing), {"tolerable_misstatement": 2000}),
        run_id="B", produced_at="2026-08-23T00:00:00Z",
    )
    assert tight.plan.sample_size > loose.plan.sample_size


def test_an_unexplained_gl_difference_escalates_to_a_named_human(tmp_path):
    listing = _listing(tmp_path)
    gl = _write(tmp_path / "gl.csv", [(f"S{i}", 100 * i) for i in range(1, 21)] + [("S99", 5000)])
    report = job.run(
        job.prepare(str(listing), {"tolerable_misstatement": 2000, "gl_path": str(gl)}),
        expected_total=_total(), run_id="C", produced_at="2026-08-23T00:00:00Z",
    )
    assert report.outcome.status == "escalated"
    assert report.outcome.escalation.route_to.strip()
    assert report.outcome.escalation.sla.strip()
    assert "5,000.00" in report.outcome.escalation.reason
    assert job.verify(report) == []


# -- lineage ------------------------------------------------------------------------- #


def test_evidence_record_fingerprints_every_input_and_echoes_the_params(tmp_path):
    listing = _listing(tmp_path)
    gl = _write(tmp_path / "gl.csv", [(f"S{i}", 100 * i) for i in range(1, 21)])
    report = job.run(
        job.prepare(str(listing), {"tolerable_misstatement": 2000, "gl_path": str(gl)}),
        expected_total=_total(), run_id="RUN-1", produced_at="2026-08-23T00:00:00Z",
    )
    ev = report.evidence
    assert len(ev.inputs) == 2
    assert all(len(a.sha256) == 64 for a in ev.inputs)
    assert dict(ev.params_used)["tolerable_misstatement"] == "2000.0"
    assert dict(ev.formula_versions)["audit_evidence"]
    assert ev.run_id == "RUN-1" and ev.produced_at == "2026-08-23T00:00:00Z"
    # The engine identity prepares; the human signs later. Never a person's name here.
    assert "linchpin/audit_evidence" in ev.prepared_by


def test_the_run_is_re_performable_and_the_hash_notices_a_changed_input(tmp_path):
    """AS 1215 re-performability, expressed as a test: same bytes and same params reproduce
    the output hash exactly; one changed figure does not."""
    listing = _listing(tmp_path)
    params = {"tolerable_misstatement": 2000}
    first = job.run(job.prepare(str(listing), params), expected_total=_total(),
                    run_id="R", produced_at="2026-08-23T00:00:00Z")
    again = job.run(job.prepare(str(listing), params), expected_total=_total(),
                    run_id="R", produced_at="2026-08-23T00:00:00Z")
    assert first.evidence.output_sha256 == again.evidence.output_sha256

    _write(listing, [(f"S{i}", 100 * i) for i in range(1, 20)] + [("S20", 9999)])
    changed = job.run(job.prepare(str(listing), params), expected_total=_total(),
                      run_id="R", produced_at="2026-08-23T00:00:00Z")
    assert changed.evidence.output_sha256 != first.evidence.output_sha256
    assert changed.evidence.inputs[0].sha256 != first.evidence.inputs[0].sha256


def test_write_operational_leaves_the_fieldwork_columns_blank(tmp_path):
    """The selection sheet is deliberately incomplete: those blanks are the handoff."""
    report = job.run(
        job.prepare(str(_listing(tmp_path)), {"tolerable_misstatement": 2000}),
        expected_total=_total(), run_id="R", produced_at="2026-08-23T00:00:00Z",
    )
    paths = job.write_operational(report, tmp_path / "out")
    header, *rows = (tmp_path / "out" / "sampling_selection.csv").read_text(
        encoding="utf-8"
    ).strip().splitlines()
    assert header.split(",")[-3:] == ["audited_value", "tickmark", "exception"]
    assert rows and all(r.endswith(",,,") for r in rows)
    assert paths["evidence"].exists()
