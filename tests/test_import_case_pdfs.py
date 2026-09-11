"""A folder of PDFs becomes an import case: doc_text -> read_document -> merge_case.

This closes the gap between two halves that already existed and never touched:
``src/voice/doc_reader.py`` was written, tested and imported by nobody, and
``prepare()`` only accepted JSON that a human had typed. What a document reader
must never do is guess - so every PDF it cannot place or cannot read is reported
by name, not dropped.
"""

import pytest
from _pdf_builder import _pdf

from jobs import import_case_job as icj

# One superset of fields; doc_reader._clean() filters it down to each schema.
_FIELDS = {
    "bl_no": "MAEU998", "container_no": "MSCU5285725", "vessel_voyage": "V9",
    "pol": "CLPMC", "pod": "CLVAP", "incoterm": "CIF", "shipper": "Pesquera Austral",
    "consignee": "Congelados Andes SpA", "gross_weight": "1.234,56",
    "invoice_no": "F-2211", "invoice_date": "2026-03-04", "total_value": "12500",
    "total_qty": "40", "hs_code": "0303.14", "country_of_origin": "CL",
    "currency": "USD", "seller": "Pesquera Austral", "buyer": "Congelados Andes SpA",
    "carton_count": "40",
}


class _Model:
    """The two-method DocModel shape doc_reader expects."""

    def __init__(self, *, available=True, fields=None):
        self._available = available
        self._fields = _FIELDS if fields is None else fields

    def available(self):
        return self._available

    def extract(self, prompt, schema):
        return dict(self._fields)


def _pdf_at(folder, name, marker="DOCUMENT"):
    path = folder / name
    path.write_bytes(_pdf([[(72, 720, marker)]]))
    return path


@pytest.fixture
def shipment(tmp_path):
    for name in ("commercial_invoice.pdf", "packing_list.pdf", "bill_of_lading.pdf"):
        _pdf_at(tmp_path, name)
    return tmp_path


def test_a_folder_of_pdfs_becomes_slices(shipment):
    payload = icj.prepare(str(shipment), {}, provider=_Model())

    types = {row["doc_type"] for row in payload["slices"]}
    assert types == {"commercial_invoice", "packing_list", "bill_of_lading"}
    assert payload["unread"] == []


def test_the_case_built_from_pdfs_is_a_normal_case(shipment):
    report = icj.run(icj.prepare(str(shipment), {}, provider=_Model()))

    assert icj.verify(report) == []
    assert report.n_documents == 3
    assert report.unified["container_no"] == "MSCU5285725"
    assert report.complete_enough is True


def test_a_filename_that_names_no_document_type_is_reported_not_guessed(tmp_path):
    _pdf_at(tmp_path, "commercial_invoice.pdf")
    _pdf_at(tmp_path, "Escaneo_23.pdf")

    payload = icj.prepare(str(tmp_path), {}, provider=_Model())

    unread = dict(payload["unread"])
    assert "Escaneo_23.pdf" in unread
    assert "doc_types" in unread["Escaneo_23.pdf"]  # says how to fix it
    assert len(payload["slices"]) == 1


def test_an_unplaced_pdf_travels_into_the_case_as_a_missing_document(tmp_path):
    _pdf_at(tmp_path, "commercial_invoice.pdf")
    _pdf_at(tmp_path, "packing_list.pdf")
    _pdf_at(tmp_path, "bill_of_lading.pdf")
    _pdf_at(tmp_path, "Escaneo_23.pdf")

    report = icj.run(icj.prepare(str(tmp_path), {}, provider=_Model()))

    names = {m.name for m in report.missing if m.kind == "document"}
    assert "Escaneo_23.pdf" in names
    assert report.complete_enough is False  # a file nobody read is a hole
    assert icj.verify(report) == []  # ...but not a QA failure: the gap IS the report


def test_an_explicit_mapping_places_a_real_world_filename(tmp_path):
    _pdf_at(tmp_path, "Factura_2211.pdf")

    payload = icj.prepare(
        str(tmp_path),
        {"doc_types": {"Factura_2211.pdf": "commercial_invoice"}},
        provider=_Model(),
    )

    assert [row["doc_type"] for row in payload["slices"]] == ["commercial_invoice"]
    assert payload["unread"] == []


def test_an_ambiguous_filename_is_not_resolved_by_picking_the_first(tmp_path):
    _pdf_at(tmp_path, "commercial_invoice_and_packing_list.pdf")

    with pytest.raises(ValueError) as excinfo:
        icj.prepare(str(tmp_path), {}, provider=_Model())

    # Names BOTH candidates instead of silently applying one of the schemas.
    assert "commercial_invoice" in str(excinfo.value)
    assert "packing_list" in str(excinfo.value)


def test_a_scanned_pdf_is_named_with_its_reason(tmp_path):
    (tmp_path / "bill_of_lading.pdf").write_bytes(_pdf([[]]))  # no text objects

    with pytest.raises(ValueError) as excinfo:
        icj.prepare(str(tmp_path), {}, provider=_Model())

    assert "no extractable text" in str(excinfo.value)
    assert "OCR" in str(excinfo.value)


def test_without_a_document_model_the_pdf_is_reported_not_silently_empty(tmp_path):
    # No API key: doc_reader returns an empty extraction. Turning that into an
    # empty DocumentSlice would report a clean case for a document nobody read.
    _pdf_at(tmp_path, "bill_of_lading.pdf")

    with pytest.raises(ValueError) as excinfo:
        icj.prepare(str(tmp_path), {}, provider=_Model(available=False))

    assert "no document model" in str(excinfo.value)


def test_pdfs_and_json_slices_coexist_in_one_folder(tmp_path):
    _pdf_at(tmp_path, "bill_of_lading.pdf")
    (tmp_path / "commercial_invoice.json").write_text(
        '{"doc_type": "commercial_invoice", "invoice_no": "F-1", "invoice_date": "2026-01-01"}',
        encoding="utf-8",
    )

    payload = icj.prepare(str(tmp_path), {}, provider=_Model())

    assert {row["doc_type"] for row in payload["slices"]} == {
        "bill_of_lading", "commercial_invoice",
    }


def test_unread_documents_reach_the_operational_csv(tmp_path):
    _pdf_at(tmp_path, "commercial_invoice.pdf")
    _pdf_at(tmp_path, "Escaneo_23.pdf")
    report = icj.run(icj.prepare(str(tmp_path), {}, provider=_Model()))

    paths = icj.write_operational(report, tmp_path / "out", "Andes")
    text = paths["missing_csv"].read_text(encoding="utf-8")

    assert "Escaneo_23.pdf" in text
