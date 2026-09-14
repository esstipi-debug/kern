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
        "container_no": "MSCU5285725",
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


# --- number conventions -----------------------------------------------------
# The pack is "DIN Chile": the local convention is decimal comma / thousands dot,
# which is exactly the one a US-only parser silently corrupts. The comparison
# token decides whether a conflict exists, so a wrong token suppresses conflicts.


def test_chilean_decimal_comma_is_not_a_thousands_separator():
    # '0,5' is half a unit in Chile. Stripping the comma made it '5' -> same
    # token as the packing list's '5' -> zero conflicts on a factor-10 gap.
    case = merge_case([
        _invoice(total_qty="0,5"),
        _packing(total_qty="5"),
        _bl(),
    ])

    assert case.n_conflicts == 1
    qty = next(c for c in case.conflicts if c.field == "total_qty")
    assert dict(qty.values)["commercial_invoice"] == "0,5"
    assert next(r for r in case.pack if r.canonical == "total_qty").contested is True


def test_decimal_comma_and_decimal_point_are_the_same_number():
    case = merge_case([_invoice(total_qty="0,5"), _packing(total_qty="0.5"), _bl()])

    assert case.n_conflicts == 0
    assert case.unified["total_qty"] == "0,5"  # raw text survives for the broker


def test_mixed_separators_agree_across_conventions():
    # '1.234,56' (CL) and '1,234.56' (US) are the same number: the LAST
    # separator is the decimal mark under either convention.
    case = merge_case([
        _invoice(total_value="1.234,56"),
        _packing(total_value="1,234.56"),
        _bl(),
    ])

    assert case.n_conflicts == 0
    assert case.unified["total_value"] == "1.234,56"


def test_repeated_separator_can_only_be_thousands():
    case = merge_case([
        _invoice(total_value="1.234.567"),
        _packing(total_value="1234567"),
        _bl(),
    ])

    assert case.n_conflicts == 0


def test_ambiguous_number_is_a_conflict_even_when_the_documents_match():
    # '12.500' is 12500 in Chile and 12.5 in the US. The convention cannot be
    # recovered from the text, so it is a human pick, never a silent tie.
    case = merge_case([
        _invoice(total_value="12.500"),
        _packing(total_value="12.500"),
        _bl(),
    ])

    conflict = next(c for c in case.conflicts if c.field == "total_value")
    assert "ambiguous" in conflict.reason
    assert next(r for r in case.pack if r.canonical == "total_value").contested is True
    assert case.unified["total_value"] == "12.500"  # value still shown, just contested


def test_ambiguous_number_conflicts_with_its_own_resolution():
    case = merge_case([
        _invoice(total_value="12.500"),
        _packing(total_value="12500"),
        _bl(),
    ])

    assert any(c.field == "total_value" for c in case.conflicts)


def test_plain_numbers_keep_agreeing():
    case = merge_case([_invoice(total_value="1000"), _packing(total_value="1000.00"), _bl()])

    assert case.n_conflicts == 0


def test_hs_code_segments_are_compared_as_text_not_as_a_number():
    # '8481.80.00' is not a number with an invalid thousands grouping; it is an
    # identifier. It must compare to itself and differ from the 6-digit form.
    same = merge_case([_invoice(hs_code="8481.80.00"), _packing(hs_code="8481.80.00"), _bl()])
    assert not any(c.field == "hs_code" for c in same.conflicts)

    other = merge_case([_invoice(hs_code="8481.80.00"), _packing(hs_code="8481.80"), _bl()])
    assert any(c.field == "hs_code" for c in other.conflicts)


def test_value_mismatch_still_names_the_plain_reason():
    case = merge_case([_invoice(total_qty="1000"), _packing(total_qty="1200"), _bl()])

    qty = next(c for c in case.conflicts if c.field == "total_qty")
    assert "ambiguous" not in qty.reason
    assert qty.reason


# --- self-verifying identifiers ---------------------------------------------
# A container number carries its own check digit, so a typo is detectable from
# the value alone - no second document needed. Until this landed, both fixtures
# in this repo carried MSKU1234567, which fails that check: nobody had ever
# looked. See src/doc_identifiers.py for why the arithmetic is hand-rolled.


def test_a_container_number_that_fails_its_check_digit_is_contested():
    case = merge_case([_invoice(), _packing(), _bl(container_no="MSKU1234567")])

    conflict = next(c for c in case.conflicts if c.field == "container_no")
    assert "check digit" in conflict.reason
    assert "bill_of_lading" in conflict.reason
    assert next(r for r in case.pack if r.canonical == "container_no").contested is True
    assert case.unified["container_no"] == "MSKU1234567"  # still shown, just flagged


def test_a_valid_container_number_raises_nothing():
    case = merge_case([_invoice(), _packing(), _bl()])

    assert not any(c.field == "container_no" for c in case.conflicts)


def test_an_air_waybill_check_digit_is_verified_too():
    case = merge_case([
        _invoice(),
        _packing(),
        DocumentSlice("air_waybill", {"awb_no": "020-12345670"}, "awb"),
    ])

    conflict = next(c for c in case.conflicts if c.field == "awb_no")
    assert "IATA check digit" in conflict.reason


def test_a_valid_air_waybill_raises_nothing():
    case = merge_case([
        _invoice(),
        _packing(),
        DocumentSlice("air_waybill", {"awb_no": "020-12345675"}, "awb"),
    ])

    assert not any(c.field == "awb_no" for c in case.conflicts)


def test_an_identifier_fault_and_a_mismatch_travel_in_ONE_conflict():
    # Two different container numbers, one of them also malformed: the broker
    # gets a single row to resolve, carrying both reasons.
    case = merge_case([
        _invoice(),
        _packing(container_no="MSKU1234567"),
        _bl(),  # MSCU5285725, valid
    ])

    matching = [c for c in case.conflicts if c.field == "container_no"]
    assert len(matching) == 1
    assert "different values" in matching[0].reason
    assert "check digit" in matching[0].reason


def test_identifiers_without_a_standard_are_left_alone():
    # A B/L number's format belongs to the carrier; there is nothing to verify,
    # so an odd-looking one must not manufacture a conflict.
    case = merge_case([_invoice(), _packing(), _bl(bl_no="ZZ//9-QQ")])

    assert not any(c.field == "bl_no" for c in case.conflicts)
