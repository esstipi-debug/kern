"""Split a large book .txt into chapter-ish chunks for graphify extract."""

from __future__ import annotations

import argparse
import re
from pathlib import Path

_CHUNK = re.compile(r"^={10,}\s*$", re.M)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("txt", type=Path)
    p.add_argument("--out-dir", type=Path, required=True)
    p.add_argument("--max-chars", type=int, default=120_000)
    args = p.parse_args()
    text = args.txt.read_text(encoding="utf-8", errors="replace")
    args.out_dir.mkdir(parents=True, exist_ok=True)
    # split on page markers when present, else fixed windows
    parts = re.split(r"\n={10,}\nPAGE \d+\n={10,}\n", text)
    if len(parts) <= 1:
        parts = [text[i : i + args.max_chars] for i in range(0, len(text), args.max_chars)]
    buf, idx, n = [], 0, 0
    for part in parts:
        if not part.strip():
            continue
        if sum(len(x) for x in buf) + len(part) > args.max_chars and buf:
            out = args.out_dir / f"{args.txt.stem}-part{idx:02d}.txt"
            out.write_text("\n\n".join(buf), encoding="utf-8")
            idx += 1
            buf, n = [], 0
        buf.append(part)
        n += 1
    if buf:
        out = args.out_dir / f"{args.txt.stem}-part{idx:02d}.txt"
        out.write_text("\n\n".join(buf), encoding="utf-8")
    print(f"split {args.txt.name} -> {len(list(args.out_dir.glob('*.txt')))} parts in {args.out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
