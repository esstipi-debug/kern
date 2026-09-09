"""Pure import-case engine: merge, conflicts, Chile pack projection."""

from src.import_case import DocumentSlice, merge_case


def _invoice(**fields):
    base = {
        "invoice_no": "F-1",
        "invoice_date": "2026-01-01",
        "incoterm": "FOB",
        "total_value": "1000",
        "total_qty": "10",
        "hs_code": "8481.80",
        "country_of_origin": "CN",
        "currency": "USD",
        "seller": "Exporter Co",
        "buyer": "Importer SpA",
    }
    base.update(fields)
    return DocumentSlice("commercial_invoice", base, "invoice")


def _packing(**fields):
    base = {"total_qty": "10", "gross_weight": "100", "carton_count": "2", "country_of_origin": "CN"}
    base.update(fields)
    return DocumentSlice("packing_list", base, "packing")


def _bl(**fields):
    base = {
        "bl_no": "BL1",
        "container_no": "MSKU1234567",
        "vessel_voyage": "V1",
        "pol": "CNSHA",
        "pod": "CLVAP",
        "incoterm": "FOB",
        "shipper": "Exporter Co",
        "consignee": "Importer SpA",
        "gross_weight": "100",
    }
    base.update(fields)
    return DocumentSlice("bill_of_lading", base, "bl")


def test_merge_unifies_matching_fields():
    case = merge_case([_invoice(), _packing(), _bl()])

    assert case.n_documents == 3
    assert case.n_conflicts == 0
    assert case.unified["incoterm"] == "FOB"
    assert case.unified["total_qty"] == "10"
    assert any(row.label == "importador" and row.value == "Importer SpA" for row in case.pack)
    assert case.complete_enough is True


def test_qty_mismatch_is_a_conflict_not_a_silent_pick():
    case = merge_case([
        _invoice(total_qty="1000"),
        _packing(total_qty="1200"),
        _bl(),
    ])

    qty = next(c for c in case.conflicts if c.field == "total_qty")
    assert len(qty.values) == 2
    pack_qty = next(row for row in case.pack if row.canonical == "total_qty")
    assert pack_qty.contested is True
    assert pack_qty.value == "1000"  # first seen, flagged contested


def test_chile_regime_flags_missing_transport_doc():
    case = merge_case([_invoice(), _packing()])

    names = {m.name for m in case.missing if m.kind == "document"}
    assert "bill_of_lading|air_waybill" in names
    assert case.complete_enough is False


def test_required_schema_fields_surface_as_missing():
    case = merge_case([
        DocumentSlice("bill_of_lading", {"bl_no": "X"}, "thin-bl"),
        _invoice(),
        _packing(),
    ])
    field_names = {m.name for m in case.missing if m.kind == "field"}
    assert "container_no" in field_names
    assert "pol" in field_names


def test_copy_reduction_is_not_one_hundred_without_the_full_six():
    case = merge_case([_invoice(), _packing(), _bl()])
    # invoice + packing + transport + projected customs pack = 4/6
    assert case.copy_reduction_pct == 66.7
