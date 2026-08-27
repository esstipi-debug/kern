"""Read a client CSV without assuming UTF-8 or a comma separator.

A Chilean export saved from Excel is typically ``;``-separated Windows-1252 --
Excel's "CSV UTF-8" option is opt-in and rarely the default a client actually
clicks. ``pd.read_csv`` assumes UTF-8 and a comma; on a real export it raises a
raw ``UnicodeDecodeError`` or ``ParserError`` straight through to whoever is
watching, which in ``webapp/app.py``'s public upload endpoints is an HTTP client
staring at a Python traceback fragment instead of their data.

Delimiter sniffing is safe to do on a Latin-1 decode of the raw bytes regardless
of the file's real encoding: the candidate delimiters are all ASCII, and every
single-byte encoding this module tries maps the ASCII range identically.
"""
from __future__ import annotations

import csv
from pathlib import Path

import pandas as pd

_ENCODINGS = ("utf-8-sig", "cp1252", "latin-1")
_DELIMITERS = (",", ";", "\t", "|")


def _sniff_delimiter(sample: str) -> str:
    try:
        return csv.Sniffer().sniff(sample, delimiters="".join(_DELIMITERS)).delimiter
    except csv.Error:
        return max(_DELIMITERS, key=sample.count)


def read_csv_robust(path: str | Path, *, sep: str | None = None, **kwargs) -> pd.DataFrame:
    """``pd.read_csv``, trying UTF-8 then cp1252 then Latin-1, with the delimiter
    sniffed unless ``sep`` is given explicitly.

    Raises the last ``UnicodeDecodeError`` if no encoding decodes the file, and
    lets any other pandas error (malformed CSV, no columns) propagate as-is --
    this only widens what counts as readable, it does not hide real failures.
    """
    path = Path(path)
    if sep is None:
        sample = path.read_bytes()[:8192].decode("latin-1")
        sep = _sniff_delimiter(sample)
    last_error: UnicodeDecodeError | None = None
    for encoding in _ENCODINGS:
        try:
            return pd.read_csv(path, encoding=encoding, sep=sep, **kwargs)
        except UnicodeDecodeError as exc:
            last_error = exc
            continue
    raise last_error
