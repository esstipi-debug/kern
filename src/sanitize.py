"""Defuse CSV/Excel formula-injection payloads before they reach a cell."""

from __future__ import annotations

from typing import Any

_FORMULA_PREFIXES = ("=", "+", "-", "@")


def defuse_formula(value: Any) -> Any:
    """Neutralize OWASP-style CSV/Excel formula injection.

    Excel, LibreOffice and Google Sheets treat any cell string starting with
    ``=``, ``+``, ``-`` or ``@`` as a formula - openpyxl itself maps a leading
    ``=`` to a live formula cell. A leading single quote is the standard fix:
    it forces literal-text display and is stripped from the rendered value, so
    the visible content is unchanged but it can no longer execute.

    Non-string values (numbers, None, ...) are returned unchanged.
    """
    if isinstance(value, str) and value.lstrip().startswith(_FORMULA_PREFIXES):
        return "'" + value
    return value
