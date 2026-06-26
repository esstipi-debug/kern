"""Prepare plain-text extracts from scm-books-corpus for graphify L3 rebuild."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_CORPUS = Path(r"C:\Users\Gamer\Documents\scm-books-corpus")
_OUT = Path(__file__).resolve().parents[1] / "knowledge" / "scm-books-rebuild" / "corpus-raw"


def _extract_pdf(pdf: Path, out_txt: Path) -> bool:
    try:
        from pypdf import PdfReader
    except ImportError:
        print("install pypdf: uv pip install pypdf", file=sys.stderr)
        return False
    reader = PdfReader(str(pdf))
    parts: list[str] = []
    for i, page in enumerate(reader.pages, 1):
        text = page.extract_text() or ""
        if text.strip():
            parts.append(f"\n{'=' * 72}\nPAGE {i}\n{'=' * 72}\n{text}")
    if not parts:
        print(f"  no text layer: {pdf.name}", file=sys.stderr)
        return False
    out_txt.write_text("\n".join(parts), encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="PDF corpus -> .txt for graphify extract")
    p.add_argument("--corpus", type=Path, default=_CORPUS)
    p.add_argument("--out", type=Path, default=_OUT)
    p.add_argument("--only", nargs="*", help="basename stems to process (default: all)")
    args = p.parse_args(argv)
    args.out.mkdir(parents=True, exist_ok=True)
    pdfs = sorted(args.corpus.glob("*.pdf"))
    if args.only:
        want = {s.lower() for s in args.only}
        pdfs = [f for f in pdfs if f.stem.lower() in want or f.name.lower() in want]
    ok = 0
    for pdf in pdfs:
        out_txt = args.out / f"{pdf.stem}.txt"
        if out_txt.exists() and out_txt.stat().st_size > 500:
            print(f"skip {pdf.name} (txt exists)")
            ok += 1
            continue
        print(f"extract {pdf.name} ...")
        try:
            ok_one = _extract_pdf(pdf, out_txt)
        except Exception as exc:
            print(f"  failed: {exc}", file=sys.stderr)
            ok_one = False
        if ok_one:
            ok += 1
    print(f"done: {ok}/{len(pdfs)} -> {args.out}")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
