"""An unexported minimum is not a dirty SKU master.

``jobs/stocky_importer.py`` exists to stop absence being filled in, and it said
so at the top: a merchant who never exported a currency column had their profile
sealed in dollars and read that it had been *detected*. Two of its three parsers
were fixed. The third -- :func:`parse_stocky_reorder_points_csv` -- kept
``int(row.get("Min Stock") or row.get("Reorder Point", 0))`` behind a ``NOTE
(known gap, deliberately not fixed here)`` that pointed at a handoff note nobody
ever wrote.

The bill for that gap was paid by the merchant, in the report they bought. With
no minimum column in the export, every SKU arrived at
``src.stocky_migration.audit_sku_master`` with ``min_reorder_point=0``, landed in
``nonpositive_reorder``, and the "Chequeo de migracion Stocky" (USD 350-400)
printed *"3 SKUs, 3 con defectos ... Limpiar antes de migrar."* over data that
had nothing wrong with it. Their problem was a missing column, which is fixed by
re-exporting; they were told to go clean their master instead.

These tests pin both halves, because fixing the first without the second is just
switching the rule off:

  ABSENCE  no column, blank cell and unparseable cell each come back ``None``
           with a legible reason, and none of them reaches the defect list.
  DEFECT   a minimum that really is 0, a duplicated SKU, a min above its max and
           a maximum that really is 0 all still get caught.

The last one is the same bug seen from the other side: ``if
rp.max_reorder_point and ...`` skipped a genuine maximum of zero. Confusing
"absent" with "zero" invents defects that do not exist AND swallows the ones
that do.
"""

from __future__ import annotations

import pytest

from jobs.stocky_importer import (
    FieldAbsence,
    StockyMigrationBatch,
    StockyReorderPoint,
    absence_reason,
    absent_fields,
    parse_stocky_reorder_points_csv,
    to_intake_frame,
)
from src.stocky_migration import audit_sku_master

# A merchant who tracked maxima but never a reorder minimum. The column is
# simply not in the file -- the commonest shape of all, and the one that used to
# produce a 100% dirty verdict.
SIN_COLUMNA_MINIMO = "SKU,Max Stock,Target Stock\nSKU-1,120,100\nSKU-2,80,60\nSKU-3,45,40\n"


# --------------------------------------------------------------------------
# absence: measured or not, never both, never filled in
# --------------------------------------------------------------------------


def test_missing_minimum_column_is_absent_not_zero():
    rows = parse_stocky_reorder_points_csv(SIN_COLUMNA_MINIMO)

    assert [r.sku for r in rows] == ["SKU-1", "SKU-2", "SKU-3"]
    for row in rows:
        assert row.min_reorder_point is None
        assert "min_reorder_point" in absent_fields(row)
        assert "no hay columna" in absence_reason(row, "min_reorder_point")
        # The maxima WERE exported: an absence must not spread to its neighbours.
        assert row.max_reorder_point is not None


def test_missing_minimum_column_is_not_a_defect():
    batch = StockyMigrationBatch(reorder_points=parse_stocky_reorder_points_csv(SIN_COLUMNA_MINIMO))
    audit = audit_sku_master(batch)

    assert audit.nonpositive_reorder == ()
    assert audit.unmeasured_reorder == ("SKU-1", "SKU-2", "SKU-3")
    assert "Limpiar antes de migrar" not in audit.summary
    # ...and it does not hand out a clean bill either. Nothing was measured, so
    # "sin defectos" would be reassurance rather than a finding.
    assert "ningun minimo se pudo medir" in audit.summary
    # The coverage still travels with the verdict: silence would be its own lie.
    assert audit.unmeasured_reasons == (
        "no hay columna Min Stock / Reorder Point en el export: SKU-1, SKU-2, SKU-3",)


def test_a_partly_measured_master_says_how_partly():
    """Between "nothing was measured" and "everything was" there is the common
    case, and it is the one where a bare "sin defectos" would be a half-truth."""
    batch = StockyMigrationBatch(reorder_points=parse_stocky_reorder_points_csv(
        "SKU,Min Stock,Max Stock\nSKU-1,30,120\nSKU-2,,80\n"))
    audit = audit_sku_master(batch)

    assert "sin defectos en el 1 con minimo medido" in audit.summary
    assert "1 SKU(s) sin minimo medible en el export" in audit.summary


def test_blank_cell_and_missing_column_are_different_problems():
    """One is fixed by re-exporting, the other by fixing a row. The merchant
    cannot choose between them if both say the same thing."""
    rows = parse_stocky_reorder_points_csv(
        "SKU,Min Stock,Max Stock\nSKU-1,30,120\nSKU-2,,80\n")

    assert rows[0].min_reorder_point == 30
    assert absent_fields(rows[0]) == ()
    assert rows[1].min_reorder_point is None
    assert "vino vacia" in absence_reason(rows[1], "min_reorder_point")


def test_unparseable_cell_declares_the_offending_value_instead_of_crashing():
    """``int("por confirmar")`` used to raise straight out of the parser and take
    the whole paid job with it. Its sibling parsers quote the cell instead."""
    rows = parse_stocky_reorder_points_csv(
        "SKU,Min Stock,Max Stock\nSKU-1,por confirmar,120\nSKU-2,15,80\n")

    assert rows[0].min_reorder_point is None
    assert "por confirmar" in absence_reason(rows[0], "min_reorder_point")
    assert rows[1].min_reorder_point == 15


def test_target_stock_still_derives_from_the_maximum():
    """A derivation over data the export really carried is not a filled hole --
    same standing as the supplier slug derived from a supplier's name. This
    behaviour predates the fix and must survive it."""
    rows = parse_stocky_reorder_points_csv("SKU,Min Stock,Max Stock\nSKU-1,30,120\n")

    assert rows[0].target_stock == 120
    assert absent_fields(rows[0]) == ()


def test_target_stock_absent_only_when_nothing_was_measured():
    rows = parse_stocky_reorder_points_csv("SKU,Notas\nSKU-1,revisar\n")

    assert rows[0].target_stock is None
    assert absence_reason(rows[0], "target_stock")


def test_a_dishonest_reorder_record_cannot_be_built():
    """Same guarantee its two sibling dataclasses already gave."""
    with pytest.raises(ValueError, match="no reason was recorded"):
        StockyReorderPoint(sku="SKU-1", min_reorder_point=None)

    with pytest.raises(ValueError, match="measured and missing"):
        StockyReorderPoint(
            sku="SKU-1", min_reorder_point=30, max_reorder_point=120, target_stock=120,
            absences=(FieldAbsence("min_reorder_point", "no hay columna"),))

    with pytest.raises(ValueError, match="not a measurement"):
        StockyReorderPoint(sku="SKU-1", min_reorder_point=-5, max_reorder_point=120,
                           target_stock=120)


def test_intake_frame_carries_the_hole_as_a_hole():
    batch = StockyMigrationBatch(reorder_points=parse_stocky_reorder_points_csv(SIN_COLUMNA_MINIMO))
    frame = to_intake_frame(batch)

    assert list(frame["sku"]) == ["SKU-1", "SKU-2", "SKU-3"]
    assert frame["reorder_point"].isna().all()
    assert list(frame["max_reorder_point"]) == [120, 80, 45]


# --------------------------------------------------------------------------
# defect: the canaries. If these stop firing, the fix above was a mute button
# --------------------------------------------------------------------------


def test_an_unusable_cell_is_reported_with_the_cell_in_it():
    """The motive is what separates "go re-export" from "go fix your data", and
    the audit reports it verbatim instead of ruling on it. Collapsing the two
    into one reassuring sentence would move the wrong claim down a line rather
    than remove it: a minimum of -5 is not "a column you did not export"."""
    batch = StockyMigrationBatch(reorder_points=parse_stocky_reorder_points_csv(
        "SKU,Min Stock,Max Stock\nSKU-1,-5,120\nSKU-2,por confirmar,80\nSKU-3,30,90\n"))
    audit = audit_sku_master(batch)

    assert audit.unmeasured_reorder == ("SKU-1", "SKU-2")
    motivos = "\n".join(audit.unmeasured_reasons)
    assert "'-5'" in motivos and "SKU-1" in motivos
    assert "'por confirmar'" in motivos and "SKU-2" in motivos
    # And the one that WAS measured is not swept in with them.
    assert "SKU-3" not in motivos


def test_a_minimum_that_really_is_zero_is_still_a_defect():
    batch = StockyMigrationBatch(
        reorder_points=parse_stocky_reorder_points_csv("SKU,Min Stock,Max Stock\nSKU-1,0,50\nSKU-2,15,80\n"))
    audit = audit_sku_master(batch)

    assert audit.nonpositive_reorder == ("SKU-1",)
    assert audit.unmeasured_reorder == ()
    assert not audit.clean
    assert "Limpiar antes de migrar" in audit.summary


def test_a_duplicated_sku_is_still_a_defect():
    batch = StockyMigrationBatch(
        reorder_points=parse_stocky_reorder_points_csv(
            "SKU,Min Stock,Max Stock\nSKU-1,30,120\nSKU-1,35,120\nSKU-2,15,80\n"))

    assert audit_sku_master(batch).duplicate_skus == ("SKU-1",)


def test_a_minimum_above_its_maximum_is_still_a_defect():
    batch = StockyMigrationBatch(
        reorder_points=parse_stocky_reorder_points_csv("SKU,Min Stock,Max Stock\nSKU-1,90,40\nSKU-2,15,80\n"))

    assert audit_sku_master(batch).inconsistent_minmax == ("SKU-1",)


def test_a_maximum_that_really_is_zero_is_now_caught():
    """The other half of the same bug. ``if rp.max_reorder_point and ...`` read a
    genuine 0 as falsy and skipped the comparison, so a min of 5 against a max of
    0 -- a rule that can never be satisfied -- was reported as clean."""
    batch = StockyMigrationBatch(
        reorder_points=parse_stocky_reorder_points_csv("SKU,Min Stock,Max Stock\nSKU-1,5,0\nSKU-2,15,80\n"))
    audit = audit_sku_master(batch)

    assert audit.inconsistent_minmax == ("SKU-1",)
    assert audit.nonpositive_reorder == ()


def test_an_unmeasured_minimum_never_reaches_the_minmax_check():
    """No column at all: there is no comparison to make, and inventing one is how
    the first version of this bug was written."""
    batch = StockyMigrationBatch(reorder_points=parse_stocky_reorder_points_csv(SIN_COLUMNA_MINIMO))
    audit = audit_sku_master(batch)

    assert audit.inconsistent_minmax == ()
    assert audit.nonpositive_reorder == ()
