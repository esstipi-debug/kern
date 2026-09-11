"""PDF -> text with its layout preserved, for ``doc_reader.read_document``.

``read_document(doc_type, document_text)`` takes a STRING; this module is what
produces it. Layout matters more than raw characters here: a logistics document
is columns, and the adjacency of "Container No." to its value on one line is the
only thing telling the model which value belongs to which label. So this uses
``extract_text(layout=True)``, not the default reading-order dump.

Scanned documents are deliberately NOT handled. A scan carries no text objects,
and returning ``""`` for one would hand ``merge_case`` an empty document that
reports zero conflicts on a shipment nobody read. This raises instead.

``pdfplumber`` lives in the optional ``doc-reading`` extra and is imported inside
the function: ``jobs/`` is on the production boot chain (``webapp.app`` ->
``scm_agent`` -> ``tools`` -> ``jobs`` -> ``src``) and prod installs only
``.[web,mcp]``, so a module-level import would kill the app at boot.
"""

from __future__ import annotations

from pathlib import Path

_EXTRA_HINT = (
    "PDF reading needs the optional 'doc-reading' extra: "
    "pip install -e '.[doc-reading]' (or pip install pdfplumber)"
)


class UnreadableDocument(RuntimeError):
    """The file cannot be turned into text, and the reason is worth saying out loud."""


def _pdfplumber():
    """Import site isolated so tests can simulate the extra being absent."""
    import pdfplumber

    return pdfplumber


def document_text(path: str | Path) -> str:
    """Return the PDF's text with its page layout preserved.

    Raises ``FileNotFoundError`` if the file is missing, and
    ``UnreadableDocument`` when the extra is absent, the file is not a PDF, or
    the PDF carries no extractable text (i.e. it is a scan).
    """
    target = Path(path)
    if not target.is_file():
        raise FileNotFoundError(str(target))
    if target.suffix.lower() != ".pdf":
        raise UnreadableDocument(
            f"{target.name}: only PDF is read here, not '{target.suffix}'"
        )
    try:
        pdfplumber = _pdfplumber()
    except ImportError as exc:
        raise UnreadableDocument(f"{target.name}: {_EXTRA_HINT}") from exc

    pages: list[str] = []
    try:
        with pdfplumber.open(str(target)) as pdf:
            for page in pdf.pages:
                pages.append(page.extract_text(layout=True) or "")
    except UnreadableDocument:
        raise
    except Exception as exc:  # pdfminer raises a wide family on malformed files
        raise UnreadableDocument(f"{target.name}: cannot be parsed as a PDF ({exc})") from exc

    text = "\n".join(pages)
    if not text.strip():
        raise UnreadableDocument(
            f"{target.name}: no extractable text -- the file is most likely a scan "
            "or a photo, and OCR is not installed. Supply a text PDF, or type the "
            "fields into a JSON slice instead of guessing at an empty document."
        )
    return text
