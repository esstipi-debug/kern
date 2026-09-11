"""Assemble a minimal, valid PDF in memory, so PDF tests need no binary fixture.

Shared via ``tests/`` being on sys.path (see conftest.py), the same way
``_no_core`` is. Pages are lists of (x, y, text) placements in Helvetica, so a
logistics document's column layout stays readable in the diff.
"""

from __future__ import annotations


def _pdf(pages: list[list[tuple[int, int, str]]]) -> bytes:
    """A minimal, valid PDF: pages of (x, y, text) placements in Helvetica."""
    objects: list[bytes] = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"",  # 2: the page tree, filled in once the page objects are numbered
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    page_refs = []
    for placements in pages:
        stream = "\n".join(
            f"BT /F1 12 Tf 1 0 0 1 {x} {y} Tm ({text}) Tj ET"
            for x, y, text in placements
        ).encode("ascii")
        content_num = len(objects) + 2
        objects.append(
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources "
            b"<< /Font << /F1 3 0 R >> >> /Contents %d 0 R >>" % content_num
        )
        page_refs.append(len(objects))
        objects.append(b"<< /Length %d >>\nstream\n%s\nendstream" % (len(stream), stream))
    objects[1] = b"<< /Type /Pages /Kids [%s] /Count %d >>" % (
        b" ".join(b"%d 0 R" % n for n in page_refs),
        len(page_refs),
    )

    out = bytearray(b"%PDF-1.4\n")
    offsets = []
    for number, body in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n%s\nendobj\n" % (number, body)
    xref_at = len(out)
    out += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (
        len(objects) + 1, xref_at,
    )
    return bytes(out)
