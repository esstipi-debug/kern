"""Deterministic data-quality core (capability M11).

GTIN/UPC/EAN check-digit validation (GS1 mod-10), SKU normalization, and canonical
column mapping from arbitrary client headers. Dependency-free; the fuzzy/probabilistic
dedup layer (rapidfuzz / Splink) sits on top as an optional extra.
Reference: GS1 General Specifications (check digit); canonical schema in src/data_loader.
"""

from __future__ import annotations

import re

_VALID_GTIN_LENGTHS = {8, 12, 13, 14}

# canonical field -> accepted normalized header aliases
_ALIASES: dict[str, set[str]] = {
    "date": {"date", "day", "week", "period", "orderdate", "txndate"},
    "product_id": {"productid", "sku", "item", "itemno", "itemnumber", "product", "article", "upc", "gtin"},
    "quantity": {"quantity", "qty", "units", "demand", "sales", "volume"},
    "unit_cost": {"unitcost", "cost", "price", "unitprice", "cogs"},
    "lead_time_days": {"leadtime", "leadtimedays", "lt", "leadtimedays"},
}


def gtin_check_digit(payload: str) -> int:
    """GS1 mod-10 check digit for a GTIN payload (the code without its check digit)."""
    total = 0
    for i, ch in enumerate(reversed(payload)):
        weight = 3 if i % 2 == 0 else 1
        total += int(ch) * weight
    return (10 - (total % 10)) % 10


def is_valid_gtin(code: str) -> bool:
    """Validate a full GTIN/UPC/EAN (8/12/13/14 digits) by its check digit."""
    code = code.strip()
    if not code.isdigit() or len(code) not in _VALID_GTIN_LENGTHS:
        return False
    return gtin_check_digit(code[:-1]) == int(code[-1])


def normalize_sku(value: str) -> str:
    """Trim, uppercase, and collapse internal whitespace."""
    return re.sub(r"\s+", " ", value.strip()).upper()


def _normalize_header(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", header.lower())


def map_columns(headers: list[str], aliases: dict[str, set[str]] | None = None) -> dict[str, str]:
    """Map canonical field -> the original header that matches it (first match wins)."""
    table = aliases or _ALIASES
    mapping: dict[str, str] = {}
    for header in headers:
        norm = _normalize_header(header)
        for canonical, names in table.items():
            if canonical in mapping:
                continue
            if norm in names:
                mapping[canonical] = header
                break
    return mapping
