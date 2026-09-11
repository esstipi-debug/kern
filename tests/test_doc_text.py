"""PDF -> text for the doc reader, including how it refuses rather than guesses.

The fixtures are PDFs assembled byte by byte (``tests/_pdf_builder.py``) instead
of committed binaries: a logistics document's shape (labels laid out in columns)
is the thing under test, and building it here keeps that shape in the diff.
"""

import pytest
from _pdf_builder import _pdf

from src.voice import doc_text


def _write(tmp_path, name, pages):
    path = tmp_path / name
    path.write_bytes(_pdf(pages))
    return path


def test_text_is_extracted_from_a_pdf(tmp_path):
    path = _write(tmp_path, "bl.pdf", [[(72, 720, "BILL OF LADING"), (72, 700, "MAEU998")]])

    text = doc_text.document_text(path)

    assert "BILL OF LADING" in text
    assert "MAEU998" in text


def test_the_layout_of_a_row_survives(tmp_path):
    # A logistics document is columns: "Container No." and its value sit on one
    # line. read_document() gets a string, so that adjacency is the only thing
    # telling the model which value belongs to which label.
    path = _write(tmp_path, "bl.pdf", [[
        (72, 720, "Container No."), (300, 720, "MSCU5285725"),
        (72, 700, "Gross Weight"), (300, 700, "1.234,56"),
    ]])

    lines = [ln for ln in doc_text.document_text(path).splitlines() if ln.strip()]

    container = next(ln for ln in lines if "Container No." in ln)
    assert "MSCU5285725" in container
    weight = next(ln for ln in lines if "Gross Weight" in ln)
    assert "1.234,56" in weight


def test_every_page_is_read(tmp_path):
    path = _write(tmp_path, "two.pdf", [
        [(72, 720, "PAGE ONE MARKER")],
        [(72, 720, "PAGE TWO MARKER")],
    ])

    text = doc_text.document_text(path)

    assert "PAGE ONE MARKER" in text
    assert "PAGE TWO MARKER" in text


def test_a_pdf_without_extractable_text_refuses_instead_of_returning_empty(tmp_path):
    # A scan is an image: pdfplumber finds no text objects. Returning "" here
    # would hand merge_case an empty document and report zero conflicts on a
    # shipment nobody read - the silent-degradation shape this repo keeps eating.
    path = _write(tmp_path, "scan.pdf", [[]])

    with pytest.raises(doc_text.UnreadableDocument) as excinfo:
        doc_text.document_text(path)

    assert "no extractable text" in str(excinfo.value)
    assert "OCR" in str(excinfo.value)


def test_a_missing_extra_names_the_extra(monkeypatch, tmp_path):
    def _absent():
        raise ImportError("no module named pdfplumber")

    monkeypatch.setattr(doc_text, "_pdfplumber", _absent)
    path = _write(tmp_path, "bl.pdf", [[(72, 720, "X")]])

    with pytest.raises(doc_text.UnreadableDocument) as excinfo:
        doc_text.document_text(path)

    assert "doc-reading" in str(excinfo.value)


def test_a_missing_file_says_so(tmp_path):
    with pytest.raises(FileNotFoundError):
        doc_text.document_text(tmp_path / "nope.pdf")


def test_a_non_pdf_is_refused_by_extension(tmp_path):
    path = tmp_path / "invoice.xlsx"
    path.write_bytes(b"not a pdf")

    with pytest.raises(doc_text.UnreadableDocument) as excinfo:
        doc_text.document_text(path)

    assert ".xlsx" in str(excinfo.value)
