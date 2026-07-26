"""Second-round hardening: inputs that still produced a confidently-false number.

An adversarial review of the first fix found that refusing *absent* columns was not enough.
Present-but-degenerate data walked straight through the new derivations and out the other
side as a clean scorecard: a blank promised date read as "late", a blank received quantity
as "short-shipped", a status mapping that matched nothing as "0% in full for everyone".

Each test here is one such input. The rule they encode: a derivation may only produce a
number it can actually support.
"""

from __future__ import annotations

import pandas as pd
import pytest

from jobs import sourcing_job as srcj

SLA = 14


def _erp(tmp_path, **overrides):
    frame = {
        "Supplier": ["Alpha", "Alpha", "Beta", "Beta"],
        "Order_Date": ["2024-01-01", "2024-01-02", "2024-01-01", "2024-01-02"],
        "Delivery_Date": ["2024-01-08", "2024-01-09", "2024-01-20", "2024-01-22"],
        "Order_Status": ["Delivered", "Delivered", "Delivered", "Partial"],
        "Quantity": [100, 100, 100, 100],
        "Unit_Price": [10.0, 10.0, 9.0, 9.0],
        "Defective_Units": [0.0, 1.0, 2.0, 3.0],
    }
    frame.update(overrides)
    csv = tmp_path / "erp.csv"
    pd.DataFrame(frame).to_csv(csv, index=False)
    return csv


def _params(**extra):
    base = {"sla_days": SLA, "in_full_status_col": "Order_Status", "in_full_ok_values": ["Delivered"]}
    base.update(extra)
    return base


# -- degenerate derivations ---------------------------------------------------


def test_a_blank_promised_date_is_excluded_not_scored_as_a_late_delivery(tmp_path):
    """``NaT`` comparisons are False, so an unknown promise silently became a miss.

    A handful of unusable rows is a data-hygiene fact, not a reason to refuse the study: drop
    them and say so. Refusal is for an input that is broken wholesale.
    """
    csv = _erp(tmp_path, Promised_Date=["2024-01-10", None, "2024-01-15", "2024-01-15"])

    payload = srcj.prepare(str(csv), _params(promised_date_col="Promised_Date"))

    by = {c.supplier: c for c in payload["scorecards"]}
    assert by["Alpha"].deliveries == 1                      # the blank-promise row is dropped
    assert by["Alpha"].on_time_rate == pytest.approx(1.0)   # not dragged to 50% by a fake miss
    assert any("promised" in n.lower() for n in payload["notes"])


def test_a_blank_received_quantity_is_excluded_not_scored_as_a_short_shipment(tmp_path):
    """``NaN >= ordered`` is False, so an unknown receipt silently became not-in-full."""
    csv = _erp(tmp_path, Received_Qty=[100, None, 100, 80])

    payload = srcj.prepare(str(csv), {"sla_days": SLA, "received_qty_col": "Received_Qty"})

    by = {c.supplier: c for c in payload["scorecards"]}
    assert by["Alpha"].deliveries == 1
    assert by["Alpha"].in_full_rate == pytest.approx(1.0)
    assert any("received" in n.lower() for n in payload["notes"])


def test_a_status_mapping_that_matches_nothing_is_refused(tmp_path):
    """in_full = 0% for 100% of suppliers is a broken mapping, not a service-level finding."""
    with pytest.raises(ValueError, match="in_full_ok_values|matched no"):
        srcj.prepare(str(_erp(tmp_path)), _params(in_full_ok_values=["Complete"]))


def test_a_delivery_dated_before_its_order_is_excluded_not_crowned_the_fastest(tmp_path):
    """A negative lead time is the ideal point on both criteria: fastest, and inside any SLA."""
    csv = _erp(tmp_path, Delivery_Date=["2023-12-20", "2024-01-09", "2024-01-20", "2024-01-22"])

    payload = srcj.prepare(str(csv), _params())

    by = {c.supplier: c for c in payload["scorecards"]}
    assert by["Alpha"].deliveries == 1
    assert by["Alpha"].avg_lead_time > 0
    assert any("negative lead time" in n for n in payload["notes"])


def test_a_supplier_with_no_price_at_all_is_refused_not_ranked_by_input_order(tmp_path):
    """A NaN average price propagates through TOPSIS and every closeness score becomes NaN.

    This one IS wholesale: the supplier has no price to compare, so the price criterion cannot
    be evaluated for it at all.
    """
    csv = _erp(tmp_path, Unit_Price=[None, None, 9.0, 9.0])

    with pytest.raises(ValueError, match="price"):
        srcj.prepare(str(csv), _params())


def test_a_stray_blank_price_is_excluded_from_the_average(tmp_path):
    """One missing price on a supplier that has others is a stray row, not a broken input."""
    csv = _erp(tmp_path, Unit_Price=[10.0, None, 9.0, 9.0])

    payload = srcj.prepare(str(csv), _params())

    assert payload["prices"]["Alpha"] == pytest.approx(10.0)
    assert any("price" in n.lower() for n in payload["notes"])


def test_a_mistyped_column_override_is_refused_not_silently_ignored(tmp_path):
    """``_pick_column`` returned None for an unmatched override - identical to 'column absent'."""
    with pytest.raises(ValueError, match="Quantitiy|not a column|units_col"):
        srcj.prepare(str(_erp(tmp_path)), _params(units_col="Quantitiy"))


# -- the promised date must actually be used when it is there -----------------


def test_a_promised_date_is_used_even_when_a_lead_time_column_exists(tmp_path):
    """The promised-date branch hung off the date-derivation path, so a lead column disabled it -
    and the deck then printed 'the data carries no promised-delivery date', which was false."""
    csv = _erp(
        tmp_path,
        lead_time_days=[7, 7, 19, 20],
        Promised_Date=["2024-01-06", "2024-01-15", "2024-01-15", "2024-01-15"],
    )

    payload = srcj.prepare(str(csv), _params(promised_date_col="Promised_Date"))

    by = {c.supplier: c for c in payload["scorecards"]}
    assert by["Alpha"].on_time_rate == pytest.approx(0.5)   # 01-08 late vs 01-06; 01-09 early vs 01-15
    assert by["Beta"].on_time_rate == pytest.approx(0.0)
    joined = " ".join(payload["notes"])
    assert "Promised_Date" in joined
    assert "carries no promised-delivery date" not in joined


# -- defect coverage is stated as coverage, not folded into the number --------


def test_ppm_is_measured_only_over_rows_whose_defect_count_is_known(tmp_path):
    """Zero-filling blanks understates PPM; on the evidence dataset that is 136 of 777 rows."""
    csv = _erp(tmp_path, Defective_Units=[1.0, None, 2.0, 3.0])

    payload = srcj.prepare(str(csv), _params())

    by = {c.supplier: c for c in payload["scorecards"]}
    # Alpha: 1 defect over the ONE row whose count is known (100 units), not spread over 200.
    assert by["Alpha"].ppm == pytest.approx(1 / 100 * 1_000_000)
    assert by["Beta"].ppm == pytest.approx(5 / 200 * 1_000_000)
    assert any("defect count" in n for n in payload["notes"])
