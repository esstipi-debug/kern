"""Absence must be declared with a motive, never filled in.

``jobs/stocky_importer.py`` feeds the "Chequeo de migracion Stocky" -- a fixed
USD 350-400 deliverable. It used to fabricate ``lead_time_days=14``, ``moq=1``
and ``currency='USD'`` whenever the merchant's export lacked those columns, and
``jobs/stocky_migration_job.py`` printed them under the heading "Parametros de
cliente detectados". A Chilean merchant who never exported a currency column
received their profile sealed in dollars and believed it had been *detected*.
The purchase-order reader had the same shape: ``int(row.get(A) or row.get(B, 0))``
made "no column" and "a genuinely free line" indistinguishable, and a PO with no
supplier column was attributed to "Unknown".

These tests pin the honest behaviour: a parameter that was not measured comes
out ``None`` with a legible reason, a record that hides its own absence cannot
be constructed at all, and the QA gate vetoes shipping a partial measurement
without the note that says how partial it is.
"""

from __future__ import annotations

from decimal import Decimal

import pandas as pd
import pytest

from jobs.stocky_importer import (
    StockyMigrationBatch,
    absence_reason,
    client_profile_evidence,
    parse_stocky_purchase_orders_csv,
    parse_stocky_suppliers_csv,
    to_client_profile_params,
    to_intake_frame,
    verify_client_profile_params,
)

# A real Stocky export from a merchant who never tracked lead times, MOQs or a
# currency: the columns simply are not in the file.
BARE_SUPPLIERS_CSV = (
    "Supplier Name,Contact Email\n"
    "Distribuidora Andes,ventas@andes.cl\n"
    "Importadora Sur,sur@sur.cl\n"
)
BARE_POS_CSV = "PO Number,Status,SKU\nOC-1,sent,SKU-1\nOC-2,received,SKU-2\n"
REORDER_CSV = "SKU,Min Stock,Max Stock,Target Stock\nSKU-1,5,20,20\nSKU-2,3,12,12\n"


# --------------------------------------------------------------------------
# suppliers: the three fabricated parameters
# --------------------------------------------------------------------------


def test_a_missing_lead_time_column_is_absent_not_fourteen_days():
    supplier = parse_stocky_suppliers_csv(BARE_SUPPLIERS_CSV)[0]
    assert supplier.lead_time_days is None
    reason = absence_reason(supplier, "lead_time_days")
    assert reason and "columna" in reason.lower()


def test_a_missing_moq_column_is_absent_not_one_unit():
    supplier = parse_stocky_suppliers_csv(BARE_SUPPLIERS_CSV)[0]
    assert supplier.moq is None
    assert absence_reason(supplier, "moq")


def test_a_missing_currency_column_is_absent_not_usd():
    """The worst of the three: a Chilean merchant sealed in dollars."""
    supplier = parse_stocky_suppliers_csv(BARE_SUPPLIERS_CSV)[0]
    assert supplier.currency is None
    assert absence_reason(supplier, "currency")


def test_a_blank_cell_is_an_absence_not_the_default():
    """The column exists but this supplier's cell is empty -- still an absence,
    and the reason must say something different from 'no such column'."""
    supplier = parse_stocky_suppliers_csv(
        "Supplier Name,Lead Time (days),Currency\nAndes,,\n"
    )[0]
    assert supplier.lead_time_days is None
    assert supplier.currency is None
    assert "vacia" in absence_reason(supplier, "lead_time_days").lower()


def test_an_unparseable_lead_time_names_the_offending_value():
    supplier = parse_stocky_suppliers_csv(
        "Supplier Name,Lead Time (days)\nAndes,por confirmar\n"
    )[0]
    assert supplier.lead_time_days is None
    assert "por confirmar" in absence_reason(supplier, "lead_time_days")


def test_a_negative_lead_time_is_an_absence_not_a_measurement():
    supplier = parse_stocky_suppliers_csv("Supplier Name,Lead Time (days)\nAndes,-3\n")[0]
    assert supplier.lead_time_days is None
    assert "-3" in absence_reason(supplier, "lead_time_days")


def test_a_measured_parameter_carries_no_absence():
    """Every optional column present: nothing to withhold, so nothing is declared.

    ``contact_email`` counts. A record goes silent only when the export genuinely
    carried all of it - which is the invariant working, not a fixture detail.
    """
    supplier = parse_stocky_suppliers_csv(
        "Supplier Name,Email,Lead Time (days),MOQ,Currency\n"
        "Andes,compras@andes.cl,21,100,CLP\n"
    )[0]
    assert (supplier.lead_time_days, supplier.moq, supplier.currency) == (21, 100, "CLP")
    assert supplier.contact_email == "compras@andes.cl"
    assert supplier.absences == ()


def test_a_supplier_row_without_a_name_is_rejected_not_called_unknown():
    with pytest.raises(ValueError, match="row 1"):
        parse_stocky_suppliers_csv("Supplier Name,Currency\n,CLP\n")


# --------------------------------------------------------------------------
# purchase orders: absence vs a genuinely zero line
# --------------------------------------------------------------------------


def test_a_po_export_without_qty_or_cost_columns_reports_absence_not_zero():
    po = parse_stocky_purchase_orders_csv(BARE_POS_CSV)[0]
    assert po.quantity_ordered is None
    assert po.quantity_received is None
    assert po.cost_per_unit is None
    assert absence_reason(po, "cost_per_unit")


def test_a_genuinely_free_line_is_distinguishable_from_a_missing_column():
    """int(row.get(A) or row.get(B, 0)) collapsed both to 0. A sample shipped at
    no charge is DATA; a missing column is not."""
    po = parse_stocky_purchase_orders_csv(
        "PO Number,Status,SKU,Quantity Ordered,Cost Price\nOC-9,sent,SKU-1,0,0.00\n"
    )[0]
    assert po.quantity_ordered == 0
    assert po.cost_per_unit == Decimal("0.00")
    assert absence_reason(po, "quantity_ordered") is None
    assert absence_reason(po, "cost_per_unit") is None
    # the column that really is missing still reports its absence
    assert po.quantity_received is None
    assert absence_reason(po, "quantity_received")


def test_a_po_without_a_supplier_column_is_not_attributed_to_unknown():
    po = parse_stocky_purchase_orders_csv(BARE_POS_CSV)[0]
    assert po.supplier_name is None
    assert absence_reason(po, "supplier_name")


def test_a_po_without_a_status_column_is_not_assumed_draft():
    po = parse_stocky_purchase_orders_csv("PO Number,SKU\nOC-1,SKU-1\n")[0]
    assert po.status is None
    assert absence_reason(po, "status")


def test_money_is_decimal_never_float():
    po = parse_stocky_purchase_orders_csv(
        "PO Number,Status,SKU,Cost Price\nOC-1,sent,SKU-1,12.50\n"
    )[0]
    assert isinstance(po.cost_per_unit, Decimal)
    assert po.cost_per_unit == Decimal("12.50")


def test_intake_frame_leaves_a_missing_cost_and_supplier_empty():
    batch = StockyMigrationBatch()
    batch.purchase_orders = parse_stocky_purchase_orders_csv(BARE_POS_CSV)
    frame = to_intake_frame(batch)
    row = frame[frame["sku"] == "SKU-1"].iloc[0]
    assert pd.isna(row["last_unit_cost"])      # not 0.0
    assert pd.isna(row["primary_supplier"])    # not "Unknown"


# --------------------------------------------------------------------------
# a dishonest record must be unconstructible
# --------------------------------------------------------------------------


def test_a_record_that_hides_its_absence_cannot_be_constructed():
    from jobs.stocky_importer import StockySupplier

    # contact_email is supplied so the first offender is lead_time_days: the point is
    # that *some* named field is caught, and naming it proves the error is specific.
    with pytest.raises(ValueError, match="lead_time_days"):
        StockySupplier(supplier_id="a", name="Andes", contact_email="c@andes.cl")


def test_a_record_cannot_claim_a_value_and_its_absence_at_once():
    from jobs.stocky_importer import FieldAbsence, StockySupplier

    every = ("contact_email", "lead_time_days", "moq", "currency")
    with pytest.raises(ValueError, match="lead_time_days"):
        StockySupplier(
            supplier_id="a",
            name="Andes",
            lead_time_days=21,
            absences=tuple(FieldAbsence(f, "sin columna") for f in every),
        )


def test_an_absence_naming_an_unknown_field_is_rejected():
    from jobs.stocky_importer import FieldAbsence, StockySupplier

    with pytest.raises(ValueError, match="lead_tme_days"):
        StockySupplier(
            supplier_id="a",
            name="Andes",
            lead_time_days=21,
            moq=1,
            currency="CLP",
            contact_email="a@b.cl",
            absences=(FieldAbsence("lead_tme_days", "typo"),),
        )


def test_an_absence_without_a_motive_is_rejected():
    from jobs.stocky_importer import FieldAbsence, StockySupplier

    with pytest.raises(ValueError, match="motive|reason"):
        StockySupplier(
            supplier_id="a",
            name="Andes",
            moq=1,
            currency="CLP",
            contact_email="a@b.cl",
            absences=(FieldAbsence("lead_time_days", "  "),),
        )


# --------------------------------------------------------------------------
# the seeded profile, its coverage, and the QA gate
# --------------------------------------------------------------------------


def test_nothing_is_seeded_from_an_export_that_measured_nothing():
    batch = StockyMigrationBatch()
    batch.suppliers = parse_stocky_suppliers_csv(BARE_SUPPLIERS_CSV)
    assert to_client_profile_params(batch) == {}


def test_evidence_reports_partial_coverage_with_a_legible_note():
    batch = StockyMigrationBatch()
    batch.suppliers = parse_stocky_suppliers_csv(
        "Supplier Name,Lead Time (days),Currency\nA,10,CLP\nB,,\nC,,\n"
    )
    ev = client_profile_evidence(batch)
    assert ev.params["lead_time_days"] == 10.0
    assert (ev.n_suppliers, ev.n_with_lead_time) == (3, 1)
    note = " ".join(ev.notes)
    assert "1" in note and "3" in note


def test_verify_vetoes_a_partial_measurement_shipped_without_its_note():
    """The cross_impact rule: a declared gap can never be reported clean. The
    heading says "detectados" -- shipping a median of 1 of 3 suppliers under it,
    with the coverage note dropped, is exactly the dishonest combination."""
    batch = StockyMigrationBatch()
    batch.suppliers = parse_stocky_suppliers_csv(
        "Supplier Name,Lead Time (days),Currency\nA,10,CLP\nB,,\nC,,\n"
    )
    issues = verify_client_profile_params(batch, to_client_profile_params(batch), notes=())
    assert issues
    assert any("lead_time_days" in i for i in issues)


def test_verify_passes_when_the_note_travels_with_the_partial_measurement():
    batch = StockyMigrationBatch()
    batch.suppliers = parse_stocky_suppliers_csv(
        "Supplier Name,Lead Time (days),Currency\nA,10,CLP\nB,,\nC,,\n"
    )
    ev = client_profile_evidence(batch)
    assert verify_client_profile_params(batch, ev.params, notes=ev.notes) == []


def test_verify_vetoes_a_param_no_row_ever_measured():
    batch = StockyMigrationBatch()
    batch.suppliers = parse_stocky_suppliers_csv(BARE_SUPPLIERS_CSV)
    issues = verify_client_profile_params(batch, {"currency": "USD"}, notes=())
    assert any("currency" in i for i in issues)


def test_verify_passes_on_a_fully_measured_export():
    batch = StockyMigrationBatch()
    batch.suppliers = parse_stocky_suppliers_csv(
        "Supplier Name,Lead Time (days),Currency\nA,10,CLP\nB,20,CLP\n"
    )
    ev = client_profile_evidence(batch)
    assert ev.params == {"lead_time_days": 15.0, "currency": "CLP"}
    assert ev.notes == ()
    assert verify_client_profile_params(batch, ev.params, notes=ev.notes) == []


# --------------------------------------------------------------------------
# the paid deliverable itself
# --------------------------------------------------------------------------


def test_the_paid_report_never_claims_detected_params_off_a_bare_export(tmp_path):
    """End to end through the job that a merchant pays USD 350-400 for."""
    from jobs.stocky_migration_job import prepare, run, write_operational

    def write(name: str, text: str):
        p = tmp_path / name
        p.write_text(text, encoding="utf-8")
        return p

    batch = prepare(
        suppliers_csv=write("s.csv", BARE_SUPPLIERS_CSV),
        purchase_orders_csv=write("po.csv", BARE_POS_CSV),
        reorder_points_csv=write("ro.csv", REORDER_CSV),
    )
    report = write_operational(
        run(batch), tmp_path / "out", client="Tienda Andes SpA"
    )["report"].read_text(encoding="utf-8")

    assert "Parametros de cliente detectados" not in report
    assert "currency" not in report
    assert "USD 250-300" in report  # the pricing lines are untouched
    assert "lead_time_days" not in report
