"""A rate over zero observations is not zero percent.

The 2026-07-26 autopsy ran ``sourcing`` over 777 real purchase orders whose export
carried no on-time / in-full columns. Every scorecard came back 0.0 and the deck
printed "OTIF 0%" as a measured fact. The cause is arithmetic, not data: dividing
a count by ``len(deliveries)`` answers "how often was it on time" even when the
answer was never observable.

Each criterion now carries its own evaluable count, and a rate with nothing to
divide by is ``None`` -- absent, which the renderer must say out loud.
"""

import pytest

from src.supplier_scorecard import (
    SupplierScore,
    lead_times_by_supplier,
    lead_times_for_skus,
    score_supplier,
)


def _full():
    return [
        {"on_time": True, "in_full": True, "lead_time_days": 30, "units": 1000, "defects": 2},
        {"on_time": True, "in_full": True, "lead_time_days": 32, "units": 1000, "defects": 1},
        {"on_time": True, "in_full": False, "lead_time_days": 28, "units": 1000, "defects": 0},
        {"on_time": False, "in_full": True, "lead_time_days": 40, "units": 1000, "defects": 2},
    ]


# ---- fully observed data keeps its existing answers --------------------------

def test_full_coverage_is_unchanged_and_reports_every_row_evaluable():
    s = score_supplier("Acme", _full())

    assert s.otif == pytest.approx(0.5)
    assert s.on_time_rate == pytest.approx(0.75)
    assert s.in_full_rate == pytest.approx(0.75)
    assert s.avg_lead_time == pytest.approx(32.5)
    assert s.ppm == pytest.approx(1250.0)
    assert s.evaluable.on_time == 4
    assert s.evaluable.in_full == 4
    assert s.evaluable.otif == 4
    assert s.evaluable.lead_time == 4
    assert s.evaluable.ppm == 4


# ---- absent criteria ---------------------------------------------------------

def test_a_criterion_nobody_recorded_is_none_not_zero():
    """The measured defect: no on_time/in_full column -> a confident 'OTIF 0%'."""
    deliveries = [{"lead_time_days": 10}, {"lead_time_days": 12}]

    s = score_supplier("Acme", deliveries)

    assert s.on_time_rate is None
    assert s.in_full_rate is None
    assert s.otif is None
    assert s.evaluable.on_time == 0
    assert s.evaluable.in_full == 0
    assert s.evaluable.otif == 0
    assert s.avg_lead_time == pytest.approx(11.0)   # this one *was* observed


def test_an_explicit_none_is_absence_and_false_is_a_measured_no():
    """`False` means "we looked, it was late". `None` means "we never looked"."""
    observed = score_supplier("Acme", [{"on_time": False}, {"on_time": False}])
    absent = score_supplier("Acme", [{"on_time": None}, {"on_time": None}])

    assert observed.on_time_rate == pytest.approx(0.0)
    assert observed.evaluable.on_time == 2
    assert absent.on_time_rate is None
    assert absent.evaluable.on_time == 0


def test_a_rate_divides_by_what_was_observed_not_by_the_row_count():
    deliveries = [
        {"on_time": True},
        {"on_time": True},
        {"on_time": None},      # unrecorded
        {"on_time": None},      # unrecorded
    ]

    s = score_supplier("Acme", deliveries)

    assert s.deliveries == 4
    assert s.evaluable.on_time == 2
    assert s.on_time_rate == pytest.approx(1.0)   # not 0.5


def test_otif_needs_both_flags_on_the_same_row_to_be_evaluable():
    deliveries = [
        {"on_time": True, "in_full": True},
        {"on_time": True, "in_full": None},     # in-full unknown -> OTIF unknowable here
        {"on_time": None, "in_full": True},
    ]

    s = score_supplier("Acme", deliveries)

    assert s.evaluable.on_time == 2
    assert s.evaluable.in_full == 2
    assert s.evaluable.otif == 1
    assert s.otif == pytest.approx(1.0)


def test_lead_time_averages_only_the_rows_that_carry_one():
    s = score_supplier("Acme", [{"lead_time_days": 10}, {"lead_time_days": None}])

    assert s.evaluable.lead_time == 1
    assert s.avg_lead_time == pytest.approx(10.0)


def test_a_blank_flag_from_pandas_is_absence_not_a_perfect_delivery():
    """`bool(nan)` is True, so a blank cell used to score as the *best* outcome."""
    s = score_supplier("Acme", [{"on_time": float("nan"), "in_full": float("nan")}])

    assert s.on_time_rate is None
    assert s.in_full_rate is None
    assert s.otif is None
    assert s.evaluable.on_time == 0
    assert s.evaluable.otif == 0


def test_a_nan_lead_time_is_absence_not_a_zero_day_delivery():
    s = score_supplier("Acme", [{"lead_time_days": float("nan")}])

    assert s.evaluable.lead_time == 0
    assert s.avg_lead_time is None


def test_ppm_without_a_units_column_is_none_not_zero_defects():
    """0 PPM is the best possible quality score - a great thing to award on by mistake."""
    s = score_supplier("Acme", [{"on_time": True}, {"on_time": True}])

    assert s.ppm is None
    assert s.evaluable.ppm == 0


def test_ppm_over_observed_units_only():
    deliveries = [
        {"units": 1000, "defects": 2},
        {"units": None, "defects": 5},   # unrecorded shipment size
    ]

    s = score_supplier("Acme", deliveries)

    assert s.evaluable.ppm == 1
    assert s.ppm == pytest.approx(2000.0)


def test_no_deliveries_measures_nothing_at_all():
    s = score_supplier("Empty", [])

    assert isinstance(s, SupplierScore)
    assert s.deliveries == 0
    assert (s.otif, s.on_time_rate, s.in_full_rate, s.avg_lead_time, s.ppm) == (
        None, None, None, None, None,
    )


# ---- an unmeasured lead time must not reach the inventory engine -------------

def test_lead_time_joins_skip_suppliers_whose_lead_time_was_never_measured():
    """A 0-day lead time collapses the risk period, i.e. zero safety stock."""
    cards = [
        score_supplier("ACME", [{"lead_time_days": 10.0}]),
        score_supplier("BETA", [{"on_time": True}]),      # no lead time anywhere
    ]

    assert lead_times_by_supplier(cards) == {"ACME": pytest.approx(10.0)}
    assert lead_times_for_skus(cards, {"SKU-1": "ACME", "SKU-2": "BETA"}) == {
        "SKU-1": pytest.approx(10.0)
    }
