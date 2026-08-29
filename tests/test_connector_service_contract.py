"""One declaration, every adapter: which part of customer service each backend can measure.

Why this exists. ``src/order_fulfillment.py`` measures fill rate, outbound OTIF (SCOR
RL.1.1) and lost sales from three OPTIONAL fields on ``OrderLine`` -- ``delivered_qty``,
``promised_date``, ``delivered_date``. Which of the three a backend can actually fill is
the whole competitive question this codebase is built around: the handoff of 2026-08-29
verified that StockTrim's sales-order schema carries none of them and that Netstock
measures only the inbound mirror. That finding is worth exactly as much as its shelf life,
and its shelf life is one adapter written by someone who did not read it.

So the adapters declare it, in code, and this file EXECUTES the declaration. A
``SERVICE_COVERAGE`` constant that says ``delivered_qty`` is unavailable, sitting beside an
adapter that returns a number for it, fails here. So does the reverse -- a connector
claiming to measure something its own fixture never produces. A declaration nobody runs is
a comment, and a comment is what the previous version of this argument was.

The drift guard at the end is what keeps that true past today: a NEW module in
``src/connectors/`` that defines ``orders()`` and does not declare its coverage fails, at
the moment it lands, rather than the first time a client asks what their OTIF was computed
from. Same stance as ``tests/test_connector_contract.py`` takes on the write side.

Scope, stated rather than implied: the fixtures here exercise the four offline-runnable
adapters (simulator, HTTP client, MCP source, Unleashed) plus Odoo through its
``InMemoryOdoo`` stand-in. ``ledger.CanonicalLedger`` is a MERGE of sources rather than a
backend, and ``emulator`` is a FastAPI app that serves one -- both are named in
``_NOT_A_BACKEND`` rather than left to be forgotten.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.connectors import (  # noqa: E402
    COVERAGE_DERIVED,
    COVERAGE_MEASURED,
    COVERAGE_UNAVAILABLE,
    FieldCoverage,
    ServiceCoverage,
)


def test_a_field_that_reports_is_distinguishable_from_one_that_cannot():
    """``reports`` is the single bit the guard branches on. Measured and derived both
    produce a number; unavailable never does, and no fourth status may sneak past by
    being truthy."""
    measured = FieldCoverage(COVERAGE_MEASURED, "SalesOrder.RequiredDate", "the promise")
    derived = FieldCoverage(COVERAGE_DERIVED, "qty - unfulfilled", "deduced")
    absent = FieldCoverage(COVERAGE_UNAVAILABLE, "", "the API has no such field")
    assert measured.reports is True
    assert derived.reports is True
    assert absent.reports is False


def test_an_unavailable_field_must_still_say_why():
    """The whole point is the sentence. ``UNAVAILABLE`` with an empty ``means`` is a
    shrug, and a shrug is what a competitor's spec sheet also says."""
    with pytest.raises(ValueError, match="means"):
        FieldCoverage(COVERAGE_UNAVAILABLE, "", "")


def test_a_reporting_field_must_name_its_source_field():
    """A number with no named backend field cannot be reconciled by a client, and cannot
    be re-verified when the vendor changes its schema."""
    with pytest.raises(ValueError, match="source"):
        FieldCoverage(COVERAGE_MEASURED, "", "the promised date")


def test_an_unknown_status_is_refused_rather_than_treated_as_absent():
    with pytest.raises(ValueError, match="status"):
        FieldCoverage("probably", "X.y", "who knows")


def test_coverage_exposes_exactly_the_three_service_fields_by_name():
    """Keyed by the ``OrderLine`` attribute name, so the guard can compare a declaration
    against an observed line without a translation table that could itself drift."""
    coverage = ServiceCoverage(
        backend="demo",
        delivered_qty=FieldCoverage(COVERAGE_MEASURED, "A.b", "units that left"),
        promised_date=FieldCoverage(COVERAGE_MEASURED, "A.c", "the promise"),
        delivered_date=FieldCoverage(COVERAGE_UNAVAILABLE, "", "no arrival is recorded"),
    )
    assert set(coverage.fields()) == {"delivered_qty", "promised_date", "delivered_date"}
    assert coverage.fields()["delivered_date"].reports is False
