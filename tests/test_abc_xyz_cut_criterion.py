"""The class label is worthless without the cut that produced it.

``abc_thresholds`` and ``cv_cuts`` were ``run()`` parameters and stopped there, so the
deliverable said "SKU-A is class A" and never said what A means. A reader who cannot see
the cut cannot disagree with the segmentation, and a segmentation nobody can disagree
with is a label, not a decision.

The subtlety that cost a round: giving the report fields a *default* of the usual
``(0.80, 0.95)`` fixed nothing. Deleting the wiring from ``run()`` left the deliverable
still printing "80/15/5" over a run that cut somewhere else - a fabricated criterion,
indistinguishable from a real one. The default IS the bug. ``None`` is the honest value
for a run that did not record its cuts.
"""

from __future__ import annotations

from jobs.abc_xyz_job import AbcXyzReport, run

_ITEMS = [
    {"product_id": "A1", "unit_cost": 50.0, "demand": [80.0] * 12},
    {"product_id": "B1", "unit_cost": 10.0, "demand": [30.0] * 12},
    {"product_id": "C1", "unit_cost": 2.0,
     "demand": [0.0, 9.0, 0.0, 20.0, 0.0, 1.0, 0.0, 15.0, 0.0, 3.0, 0.0, 2.0]},
]


def test_the_report_states_the_cut_it_actually_used():
    report = run(_ITEMS)

    assert report.abc_thresholds == (0.80, 0.95)
    assert "80/15/5" in report.cut_criterion


def test_a_different_cut_produces_a_different_stated_criterion():
    """The property that makes the claim falsifiable: it has to MOVE with the run."""
    default = run(_ITEMS)
    shifted = run(_ITEMS, abc_thresholds=(0.70, 0.90))

    assert "80/15/5" in default.cut_criterion
    assert "70/20/10" in shifted.cut_criterion
    assert default.cut_criterion != shifted.cut_criterion


def test_the_xyz_cuts_travel_too():
    report = run(_ITEMS, cv_cuts=(0.25, 0.75))

    assert report.cv_cuts == (0.25, 0.75)
    assert "X<0.25" in report.cut_criterion and "Y<0.75" in report.cut_criterion


def test_a_report_that_never_recorded_its_cuts_says_so_instead_of_inventing_one():
    """The whole reason the fields default to None rather than to the usual thresholds."""
    bare = AbcXyzReport(
        classifications=(), summary={}, n_skus=0, n_a=0, n_cz=0, a_value_share=0.0,
    )

    assert bare.abc_thresholds is None
    assert bare.cv_cuts is None
    assert bare.cut_criterion is None, "an unrecorded cut is absent, not 80/15/5"


def test_half_a_cut_is_still_no_cut():
    """ABC without XYZ cannot state the criterion the source asks for."""
    half = AbcXyzReport(
        classifications=(), summary={}, n_skus=0, n_a=0, n_cz=0, a_value_share=0.0,
        abc_thresholds=(0.80, 0.95),
    )

    assert half.cut_criterion is None
